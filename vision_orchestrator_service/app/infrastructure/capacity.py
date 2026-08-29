from __future__ import annotations

import asyncio
import random
import time
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial

import httpx

from packages.platform_common.lease_resilience import (
    LeaseRenewalPolicy,
    release_lease_with_retry,
    renew_lease_with_retry,
)

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
    pass


class CapacityUnavailableError(CapacityLeaseClientError):
    pass


class CapacityLeaseHttpClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        control_service_url: str,
        renewal_policy: LeaseRenewalPolicy | None = None,
        acquire_wait_timeout_seconds: float = 300.0,
        acquire_retry_interval_seconds: float = 0.2,
    ) -> None:
        self._http = http_client
        self._control_service_url = control_service_url.rstrip("/")
        self._renewal_policy = renewal_policy
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
        delay = max(0.0, self._acquire_retry_interval_seconds)
        last_error: Exception | None = None
        while True:
            try:
                response = await self._http.post(
                    f"{self._control_service_url}/internal/operator-instances/lease",
                    json=payload,
                )
                if self._is_capacity_unavailable(response, capability=capability):
                    last_error = CapacityUnavailableError(f"算子容量暂不可用: {capability}")
                else:
                    response.raise_for_status()
                    body = response.json()
                    lease = self._parse_lease(body)
                    break
            except httpx.HTTPStatusError as exc:
                raise CapacityLeaseClientError(
                    f"获取算子容量租约失败: {capability}: HTTP {exc.response.status_code}"
                ) from exc
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                last_error = exc
                raise CapacityLeaseClientError(
                    f"获取算子容量租约失败: {capability}: {exc}"
                ) from exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CapacityLeaseClientError(
                    f"等待算子容量超过 {self._acquire_wait_timeout_seconds:g} 秒: {capability}"
                ) from last_error
            await asyncio.sleep(min(remaining, delay + random.uniform(0, delay * 0.25)))
            delay = min(max(delay * 2, 0.2), 2.0)

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
            released = await release_lease_with_retry(
                lease_id=lease.lease_id,
                release=lambda: self._http.post(
                    f"{self._control_service_url}/internal/operator-instances/release",
                    json={"lease_id": lease.lease_id},
                ),
                policy=renewal_policy,
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
