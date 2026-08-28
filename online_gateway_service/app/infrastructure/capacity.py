from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial

import httpx

from packages.platform_common.lease_resilience import (
    LeaseRenewalPolicy,
    release_lease_with_retry,
    renew_lease_with_retry,
)
from packages.platform_common.metrics import PlatformMetrics


@dataclass(frozen=True, slots=True)
class OnlineWorkContext:
    source_service: str
    work_type: str
    work_id: str
    task_id: str | None = None
    node_id: str | None = None
    item_id: str | None = None
    trace_id: str | None = None

    def as_dict(self) -> dict[str, str]:
        values = {
            "source_service": self.source_service,
            "work_type": self.work_type,
            "work_id": self.work_id,
            "task_id": self.task_id,
            "node_id": self.node_id,
            "item_id": self.item_id,
            "trace_id": self.trace_id,
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


class OnlineCapacityLeaseError(RuntimeError):
    pass


class OnlineCapacityLeaseClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        control_service_url: str,
        metrics: PlatformMetrics | None = None,
        renewal_policy: LeaseRenewalPolicy | None = None,
    ) -> None:
        self._http = http_client
        self._control_service_url = control_service_url.rstrip("/")
        self._metrics = metrics
        self._renewal_policy = renewal_policy

    @asynccontextmanager
    async def acquire(
        self,
        capability: str,
        *,
        ttl_seconds: int = 60,
        work_context: OnlineWorkContext | None = None,
        renew_interval_seconds: float | None = None,
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
        self._record_lease_event(capability=capability, outcome="requested")
        try:
            response = await self._http.post(
                f"{self._control_service_url}/internal/operator-instances/lease",
                json=payload,
            )
            response.raise_for_status()
            lease = self._parse_lease(response.json())
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            self._record_lease_event(capability=capability, outcome="rejected")
            raise OnlineCapacityLeaseError(
                f"获取在线算子容量失败: {capability}"
            ) from exc
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
            released = await release_lease_with_retry(
                lease_id=lease.lease_id,
                release=lambda: self._http.post(
                    f"{self._control_service_url}/internal/operator-instances/release",
                    json={"lease_id": lease.lease_id},
                ),
                policy=renewal_policy,
            )
            if not released:
                self._record_lease_event(
                    capability=capability,
                    outcome="release_failed",
                    instance_id=lease.instance_id,
                )
            else:
                self._record_lease_event(
                    capability=capability,
                    outcome="released",
                    instance_id=lease.instance_id,
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
        )
