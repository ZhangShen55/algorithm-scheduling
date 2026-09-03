from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial

import httpx

from packages.platform_common.lease_resilience import (
    ControlDeterministicFailureError,
    ControlTransientFailureError,
    InvalidControlResponseError,
    LeaseAcquireError,
    LeaseAcquireFailureKind,
    LeaseCapacityUnavailableError,
    LeaseRenewalPolicy,
    classify_lease_response,
    classify_lease_transport_error,
    release_lease_with_retry,
    renew_lease_with_retry,
    wait_for_retry,
)
from packages.platform_common.metrics import PlatformMetrics

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OnlineWorkContext:
    source_service: str
    work_type: str
    work_id: str
    task_id: str | None = None
    node_id: str | None = None
    item_id: str | None = None
    trace_id: str | None = None
    capacity_pool: str = "online"

    def as_dict(self) -> dict[str, str]:
        values = {
            "source_service": self.source_service,
            "work_type": self.work_type,
            "work_id": self.work_id,
            "task_id": self.task_id,
            "node_id": self.node_id,
            "item_id": self.item_id,
            "trace_id": self.trace_id,
            "capacity_pool": self.capacity_pool,
        }
        return {key: value for key, value in values.items() if value is not None}


@dataclass(frozen=True, slots=True)
class CapacityLease:
    lease_id: str
    instance_id: str
    capability: str
    service_url: str
    expires_at: datetime
    acquired_at: datetime | None = None
    work_context: OnlineWorkContext | None = None
    capacity_pool: str = "online"


class OnlineCapacityLeaseError(RuntimeError):
    pass


class OnlineCapacityWaitTimeoutError(OnlineCapacityLeaseError):
    pass


class ControlServiceUnavailableError(OnlineCapacityLeaseError):
    pass


class ControlLeaseProtocolError(OnlineCapacityLeaseError):
    pass


class OnlineCapacityLeaseClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        control_service_url: str,
        metrics: PlatformMetrics | None = None,
        renewal_policy: LeaseRenewalPolicy | None = None,
        acquire_wait_timeout_seconds: float = 300.0,
        acquire_retry_interval_seconds: float = 0.2,
    ) -> None:
        self._http = http_client
        self._control_service_url = control_service_url.rstrip("/")
        self._metrics = metrics
        self._renewal_policy = renewal_policy
        self._acquire_wait_timeout_seconds = acquire_wait_timeout_seconds
        self._acquire_retry_interval_seconds = acquire_retry_interval_seconds

    @asynccontextmanager
    async def acquire(
        self,
        capability: str,
        *,
        ttl_seconds: int = 60,
        work_context: OnlineWorkContext | None = None,
        renew_interval_seconds: float | None = None,
        capacity_pool: str = "online",
        deadline: float | None = None,
    ) -> AsyncIterator[CapacityLease]:
        interval = renew_interval_seconds or max(min(ttl_seconds / 3, 20.0), 0.1)
        if interval >= ttl_seconds:
            raise ValueError("租约续租周期必须小于租约时长")
        renewal_policy = self._renewal_policy or LeaseRenewalPolicy(
            safety_margin_seconds=min(5.0, ttl_seconds / 2),
        )
        payload: dict[str, object] = {
            "capability": capability,
            "ttl_seconds": ttl_seconds,
        }
        if work_context is not None:
            payload["work_context"] = work_context.as_dict()
        payload["capacity_pool"] = capacity_pool
        self._record_lease_event(capability=capability, outcome="requested")
        started_at = time.monotonic()
        acquire_deadline = started_at + self._acquire_wait_timeout_seconds
        if deadline is not None:
            acquire_deadline = min(acquire_deadline, deadline)
        attempt = 0
        last_error: LeaseAcquireError | None = None
        while True:
            if time.monotonic() >= acquire_deadline:
                self._raise_wait_exhausted(capability, last_error)
            attempt += 1
            try:
                response = await asyncio.wait_for(
                    self._http.post(
                        f"{self._control_service_url}/internal/operator-instances/lease",
                        json=payload,
                    ),
                    timeout=max(0.0, acquire_deadline - time.monotonic()),
                )
                classify_lease_response(
                    response,
                    capability=capability,
                    capacity_unavailable=self._is_capacity_unavailable(
                        response,
                        capability=capability,
                    ),
                )
                try:
                    lease = self._parse_lease(response.json())
                except (KeyError, TypeError, ValueError) as exc:
                    raise InvalidControlResponseError(
                        f"Control 在线容量租约响应无效: {type(exc).__name__}"
                    ) from exc
                break
            except (ControlDeterministicFailureError, InvalidControlResponseError) as exc:
                self._record_lease_event(capability=capability, outcome="failed")
                raise ControlLeaseProtocolError(
                    f"Control 在线容量租约请求不可恢复: {capability}: {exc}"
                ) from exc
            except (LeaseCapacityUnavailableError, ControlTransientFailureError) as exc:
                last_error = exc
            except TimeoutError:
                last_error = ControlTransientFailureError(
                    "Control 在线容量租约请求超过剩余预算"
                )
            except httpx.HTTPError as exc:
                classified = classify_lease_transport_error(exc)
                if isinstance(classified, ControlDeterministicFailureError):
                    self._record_lease_event(capability=capability, outcome="failed")
                    raise ControlLeaseProtocolError(
                        f"Control 在线容量租约协议不可恢复: {capability}"
                    ) from exc
                last_error = classified

            remaining = max(0.0, acquire_deadline - time.monotonic())
            self._record_lease_event(capability=capability, outcome="waiting")
            self._record_recovery_event(
                capacity_pool=capacity_pool,
                capability=capability,
                stage="lease_acquire",
                exception_type=(
                    last_error.kind.value if last_error is not None else "unknown"
                ),
                outcome="retrying" if remaining > 0 else "timeout",
            )
            logger.warning(
                "在线容量租约申请等待恢复",
                extra={
                    **(work_context.as_dict() if work_context is not None else {}),
                    "capability": capability,
                    "stage": "lease_acquire",
                    "exception_type": (
                        last_error.kind.value if last_error is not None else "unknown"
                    ),
                    "attempt": attempt,
                    "elapsed_seconds": round(time.monotonic() - started_at, 3),
                    "remaining_seconds": round(remaining, 3),
                    "outcome": "retrying" if remaining > 0 else "timeout",
                },
            )
            retry_allowed = await wait_for_retry(
                deadline=acquire_deadline,
                attempt=attempt,
                base_delay_seconds=self._acquire_retry_interval_seconds,
            )
            if not retry_allowed:
                self._raise_wait_exhausted(capability, last_error)
        self._record_lease_event(
            capability=capability,
            outcome="acquired",
            instance_id=lease.instance_id,
        )
        owner_task = asyncio.current_task()
        renewal_error: Exception | None = None
        current_lease = lease

        async def renew_forever() -> None:
            nonlocal renewal_error, current_lease
            try:
                while True:
                    await asyncio.sleep(interval)
                    self._record_lease_event(
                        capability=capability,
                        outcome="renew_requested",
                        instance_id=current_lease.instance_id,
                    )
                    current_lease = await renew_lease_with_retry(
                        lease_id=current_lease.lease_id,
                        confirmed_expires_at=current_lease.expires_at,
                        renew=partial(
                            self._renew_once,
                            current_lease.lease_id,
                            ttl_seconds,
                        ),
                        policy=renewal_policy,
                    )
                    self._record_lease_event(
                        capability=capability,
                        outcome="renewed",
                        instance_id=current_lease.instance_id,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 统一转换跨客户端续租错误
                renewal_error = exc
                if owner_task is not None:
                    owner_task.cancel()

        renewal_task = asyncio.create_task(
            renew_forever(),
            name=f"renew-online-lease-{lease.lease_id}",
        )
        try:
            yield lease
        except asyncio.CancelledError:
            if renewal_error is not None:
                raise OnlineCapacityLeaseError(
                    f"在线算子容量续租失败: {lease.lease_id}"
                ) from renewal_error
            raise
        finally:
            renewal_task.cancel()
            with suppress(asyncio.CancelledError):
                await renewal_task
            try:
                released = await release_lease_with_retry(
                    lease_id=lease.lease_id,
                    release=lambda: self._http.post(
                        f"{self._control_service_url}/internal/operator-instances/release",
                        json={"lease_id": lease.lease_id},
                    ),
                    policy=renewal_policy,
                )
            except Exception as exc:  # noqa: BLE001 - 释放失败不得覆盖业务根因
                released = False
                logger.warning(
                    "在线容量租约释放异常，等待 TTL 回收",
                    extra={
                        "lease_id": lease.lease_id,
                        "capability": capability,
                        "instance_id": lease.instance_id,
                        "stage": "lease_release",
                        "exception_type": type(exc).__name__,
                        "outcome": "release_failed",
                    },
                )
            if not released:
                self._record_lease_event(
                    capability=capability,
                    outcome="release_failed",
                    instance_id=lease.instance_id,
                )
                self._record_recovery_event(
                    capacity_pool=capacity_pool,
                    capability=capability,
                    instance_id=lease.instance_id,
                    stage="lease_release",
                    exception_type="unconfirmed",
                    outcome="release_failed",
                )
            else:
                self._record_lease_event(
                    capability=capability,
                    outcome="released",
                    instance_id=lease.instance_id,
                )
                self._record_recovery_event(
                    capacity_pool=capacity_pool,
                    capability=capability,
                    instance_id=lease.instance_id,
                    stage="lease_release",
                    exception_type="none",
                    outcome="released",
                )

    def _raise_wait_exhausted(
        self,
        capability: str,
        last_error: LeaseAcquireError | None,
    ) -> None:
        self._record_lease_event(capability=capability, outcome="timeout")
        if (
            last_error is not None
            and last_error.kind is LeaseAcquireFailureKind.CAPACITY_UNAVAILABLE
        ):
            raise OnlineCapacityWaitTimeoutError(
                f"等待在线算子容量超过 {self._acquire_wait_timeout_seconds:g} 秒: "
                f"{capability}"
            ) from last_error
        raise ControlServiceUnavailableError(
            f"Control 在线容量服务在等待预算内未恢复: {capability}"
        ) from last_error

    async def _renew_once(
        self,
        lease_id: str,
        ttl_seconds: int,
    ) -> CapacityLease:
        renewal = await self._http.post(
            f"{self._control_service_url}/internal/operator-instances/lease/renew",
            json={"lease_id": lease_id, "ttl_seconds": ttl_seconds},
        )
        renewal.raise_for_status()
        return self._parse_lease(renewal.json())

    def _record_lease_event(
        self,
        *,
        capability: str,
        outcome: str,
        instance_id: str | None = None,
    ) -> None:
        if self._metrics is not None:
            self._metrics.record_capacity_lease_event(
                capability=capability,
                outcome=outcome,
                instance_id=instance_id,
            )

    def _record_recovery_event(
        self,
        *,
        capacity_pool: str,
        capability: str,
        stage: str,
        exception_type: str,
        outcome: str,
        instance_id: str | None = None,
    ) -> None:
        if self._metrics is not None:
            self._metrics.record_capacity_recovery_event(
                capacity_pool=capacity_pool,
                capability=capability,
                instance_id=instance_id,
                stage=stage,
                exception_type=exception_type,
                outcome=outcome,
            )

    @staticmethod
    def _is_capacity_unavailable(
        response: httpx.Response,
        *,
        capability: str,
    ) -> bool:
        if response.status_code != 503:
            return False
        try:
            body = response.json()
        except ValueError:
            return False
        detail = body.get("detail") if isinstance(body, dict) else None
        return detail in {
            "暂无可用算子容量",
            f"暂无可用算子容量: {capability}",
            "暂无可用在线算子容量",
            f"暂无可用在线算子容量: {capability}",
        }

    @staticmethod
    def _parse_lease(body: object) -> CapacityLease:
        if not isinstance(body, dict):
            raise TypeError("容量租约响应不是 JSON 对象")
        raw_context = body.get("work_context")
        acquired_at = body.get("acquired_at")
        return CapacityLease(
            lease_id=str(body["lease_id"]),
            instance_id=str(body["instance_id"]),
            capability=str(body["capability"]),
            service_url=str(body["service_url"]),
            acquired_at=(
                datetime.fromisoformat(str(acquired_at).replace("Z", "+00:00"))
                if acquired_at is not None
                else datetime.now(UTC)
            ),
            expires_at=datetime.fromisoformat(
                str(body["expires_at"]).replace("Z", "+00:00")
            ),
            work_context=(
                OnlineWorkContext(**raw_context)
                if isinstance(raw_context, dict)
                else None
            ),
            capacity_pool=str(body.get("capacity_pool") or "online"),
        )
