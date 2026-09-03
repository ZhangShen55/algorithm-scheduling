from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
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
class WorkContext:
    source_service: str
    work_type: str
    work_id: str
    task_id: str | None = None
    node_id: str | None = None
    item_id: str | None = None
    trace_id: str | None = None
    capacity_pool: str = "offline"

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
    acquired_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    work_context: WorkContext | None = None
    capacity_pool: str = "offline"


class CapacityLeaseClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        failure_kind: LeaseAcquireFailureKind | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind


class CapacityUnavailableError(CapacityLeaseClientError):
    pass


class CapacityLeaseHttpClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        control_service_url: str,
        renewal_policy: LeaseRenewalPolicy | None = None,
        metrics: PlatformMetrics | None = None,
        acquire_wait_timeout_seconds: float = 300.0,
        acquire_retry_interval_seconds: float = 0.2,
    ) -> None:
        self._http = http_client
        self._control_service_url = control_service_url.rstrip("/")
        self._renewal_policy = renewal_policy
        self._metrics = metrics
        self._acquire_wait_timeout_seconds = acquire_wait_timeout_seconds
        self._acquire_retry_interval_seconds = acquire_retry_interval_seconds

    @asynccontextmanager
    async def acquire(
        self,
        capability: str,
        *,
        ttl_seconds: int = 60,
        work_context: WorkContext | None = None,
        renew_interval_seconds: float | None = None,
        capacity_pool: str = "offline",
    ) -> AsyncIterator[CapacityLease]:
        payload: dict[str, object] = {
            "capability": capability,
            "ttl_seconds": ttl_seconds,
            "capacity_pool": capacity_pool,
        }
        if work_context is not None:
            payload["work_context"] = work_context.as_dict()
        deadline = time.monotonic() + self._acquire_wait_timeout_seconds
        started_at = time.monotonic()
        attempt = 0
        last_error: LeaseAcquireError | None = None
        while True:
            if time.monotonic() >= deadline:
                raise CapacityLeaseClientError(
                    f"等待算子容量超过 {self._acquire_wait_timeout_seconds:g} 秒: "
                    f"{capability}",
                    failure_kind=(
                        last_error.kind
                        if last_error is not None
                        else LeaseAcquireFailureKind.CONTROL_TRANSIENT_FAILURE
                    ),
                ) from last_error
            attempt += 1
            try:
                response = await asyncio.wait_for(
                    self._http.post(
                        f"{self._control_service_url}/internal/operator-instances/lease",
                        json=payload,
                    ),
                    timeout=max(0.0, deadline - time.monotonic()),
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
                        f"Control 容量租约响应无效: {type(exc).__name__}"
                    ) from exc
                break
            except (ControlDeterministicFailureError, InvalidControlResponseError) as exc:
                raise CapacityLeaseClientError(
                    f"获取算子容量租约不可恢复: {capability}: {exc}",
                    failure_kind=exc.kind,
                ) from exc
            except (LeaseCapacityUnavailableError, ControlTransientFailureError) as exc:
                last_error = exc
            except TimeoutError:
                last_error = ControlTransientFailureError(
                    "Control 视觉容量租约请求超过剩余预算"
                )
            except httpx.HTTPError as exc:
                classified = classify_lease_transport_error(exc)
                if isinstance(classified, ControlDeterministicFailureError):
                    raise CapacityLeaseClientError(
                        f"获取算子容量租约不可恢复: {capability}: {classified}",
                        failure_kind=classified.kind,
                    ) from exc
                last_error = classified

            elapsed = time.monotonic() - started_at
            remaining = max(0.0, deadline - time.monotonic())
            logger.warning(
                "视觉容量租约申请等待恢复",
                extra={
                    **(work_context.as_dict() if work_context is not None else {}),
                    "capability": capability,
                    "stage": "lease_acquire",
                    "exception_type": (
                        last_error.kind.value if last_error is not None else "unknown"
                    ),
                    "attempt": attempt,
                    "elapsed_seconds": round(elapsed, 3),
                    "remaining_seconds": round(remaining, 3),
                    "outcome": "retrying" if remaining > 0 else "timeout",
                },
            )
            self._record_recovery_event(
                capability=capability,
                stage="lease_acquire",
                exception_type=(
                    last_error.kind.value if last_error is not None else "unknown"
                ),
                outcome="retrying" if remaining > 0 else "timeout",
                capacity_pool=capacity_pool,
            )
            retry_allowed = await wait_for_retry(
                deadline=deadline,
                attempt=attempt,
                base_delay_seconds=self._acquire_retry_interval_seconds,
            )
            if not retry_allowed:
                kind = (
                    last_error.kind
                    if last_error is not None
                    else LeaseAcquireFailureKind.CONTROL_TRANSIENT_FAILURE
                )
                if kind is LeaseAcquireFailureKind.CAPACITY_UNAVAILABLE:
                    reason = (
                        f"等待算子容量超过 {self._acquire_wait_timeout_seconds:g} 秒: "
                        f"{capability}"
                    )
                else:
                    reason = (
                        "Control 容量租约服务在等待预算内未恢复: "
                        f"{capability}"
                    )
                raise CapacityLeaseClientError(
                    reason,
                    failure_kind=kind,
                ) from last_error

        self._record_recovery_event(
            capability=capability,
            stage="lease_acquire",
            exception_type="none",
            outcome="acquired",
            instance_id=lease.instance_id,
            capacity_pool=capacity_pool,
        )

        interval = renew_interval_seconds or max(min(ttl_seconds / 3, 20.0), 0.1)
        if interval >= ttl_seconds:
            raise ValueError("租约续租周期必须小于租约时长")
        renewal_policy = self._renewal_policy or LeaseRenewalPolicy(
            safety_margin_seconds=min(5.0, ttl_seconds / 2),
        )
        owner_task = asyncio.current_task()
        renewal_error: Exception | None = None
        current_lease = lease

        async def renew_forever() -> None:
            nonlocal renewal_error, current_lease
            try:
                while True:
                    await asyncio.sleep(interval)
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
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 统一转换跨客户端续租错误
                renewal_error = exc
                if owner_task is not None:
                    owner_task.cancel()

        renewal_task = asyncio.create_task(
            renew_forever(),
            name=f"renew-vision-lease-{lease.lease_id}",
        )
        try:
            yield lease
        except asyncio.CancelledError:
            if renewal_error is not None:
                raise CapacityLeaseClientError(
                    f"算子容量租约续租失败: {lease.lease_id}"
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
            except Exception as exc:  # noqa: BLE001 - 释放失败不得覆盖分析根因
                released = False
                logger.warning(
                    "视觉容量租约释放异常，等待 TTL 回收",
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
                logger.warning(
                    "视觉容量租约释放暂未确认，等待 TTL 回收",
                    extra={
                        "lease_id": lease.lease_id,
                        "capability": capability,
                        "outcome": "release_failed",
                    },
                )
                self._record_recovery_event(
                    capability=capability,
                    stage="lease_release",
                    exception_type="unconfirmed",
                    outcome="release_failed",
                    instance_id=lease.instance_id,
                    capacity_pool=capacity_pool,
                )
            else:
                self._record_recovery_event(
                    capability=capability,
                    stage="lease_release",
                    exception_type="none",
                    outcome="released",
                    instance_id=lease.instance_id,
                    capacity_pool=capacity_pool,
                )

    def _record_recovery_event(
        self,
        *,
        capability: str,
        stage: str,
        exception_type: str,
        outcome: str,
        capacity_pool: str,
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
        return isinstance(body, dict) and body.get("detail") == (
            f"暂无可用算子容量: {capability}"
        )

    @staticmethod
    def _parse_lease(body: object) -> CapacityLease:
        if not isinstance(body, dict):
            raise TypeError("容量租约响应不是 JSON 对象")
        raw_context = body.get("work_context")
        return CapacityLease(
            lease_id=str(body["lease_id"]),
            instance_id=str(body["instance_id"]),
            capability=str(body["capability"]),
            service_url=str(body["service_url"]),
            acquired_at=(
                datetime.fromisoformat(str(body["acquired_at"]).replace("Z", "+00:00"))
                if body.get("acquired_at") is not None
                else datetime.now(UTC)
            ),
            expires_at=datetime.fromisoformat(
                str(body["expires_at"]).replace("Z", "+00:00")
            ),
            work_context=(
                WorkContext(**raw_context) if isinstance(raw_context, dict) else None
            ),
            capacity_pool=str(body.get("capacity_pool") or "offline"),
        )
