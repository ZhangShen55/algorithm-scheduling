from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from contextlib import suppress
from datetime import UTC, datetime
from functools import partial
from typing import TypeVar

import httpx

from packages.platform_common.lease_resilience import (
    LeaseRenewalPolicy,
    renew_lease_with_retry,
)
from packages.platform_common.operator_registry import CapacityLease, WorkContext

from ..domain.errors import CapacityUnavailableError, LeaseRenewalError

logger = logging.getLogger(__name__)


class LeaseUnavailableError(CapacityUnavailableError):
    pass


ResultT = TypeVar("ResultT")


class ControlLeaseClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        default_ttl_seconds: int,
        renewal_policy: LeaseRenewalPolicy | None = None,
    ) -> None:
        self._http_client = http_client
        self._default_ttl_seconds = default_ttl_seconds
        self._renewal_policy = renewal_policy or LeaseRenewalPolicy(
            safety_margin_seconds=min(5.0, default_ttl_seconds / 2),
        )

    async def acquire(
        self,
        capability: str,
        *,
        ttl_seconds: int | None = None,
        work_context: WorkContext | None = None,
    ) -> CapacityLease:
        request: dict[str, object] = {
            "capability": capability,
            "ttl_seconds": ttl_seconds or self._default_ttl_seconds,
        }
        if work_context is not None:
            request["work_context"] = work_context.as_dict()
        response = await self._http_client.post(
            "/internal/operator-instances/lease",
            json=request,
        )
        if response.status_code == 503:
            detail = self._response_detail(response)
            if detail.startswith("暂无可用算子容量"):
                raise LeaseUnavailableError(detail)
        response.raise_for_status()
        payload = response.json()
        return self._parse_lease(payload)

    async def bind_context(
        self,
        lease_id: str,
        work_context: WorkContext,
    ) -> CapacityLease:
        response = await self._http_client.post(
            "/internal/operator-instances/lease/context",
            json={
                "lease_id": lease_id,
                "work_context": work_context.as_dict(),
            },
        )
        response.raise_for_status()
        return self._parse_lease(response.json())

    async def renew(
        self,
        lease_id: str,
        *,
        ttl_seconds: int | None = None,
    ) -> CapacityLease:
        response = await self._http_client.post(
            "/internal/operator-instances/lease/renew",
            json={
                "lease_id": lease_id,
                "ttl_seconds": ttl_seconds or self._default_ttl_seconds,
            },
        )
        response.raise_for_status()
        return self._parse_lease(response.json())

    async def is_active(self, lease_id: str) -> bool:
        response = await self._http_client.get(
            f"/internal/operator-instances/lease/{lease_id}"
        )
        if response.status_code == 404:
            return False
        response.raise_for_status()
        active = self._parse_lease(response.json())
        if active.lease_id != lease_id:
            raise ValueError("容量租约查询响应 lease_id 不一致")
        return True

    async def run_with_renewal(
        self,
        lease: CapacityLease,
        operation: Awaitable[ResultT],
        *,
        ttl_seconds: int | None = None,
        renew_interval_seconds: float | None = None,
        hard_timeout_seconds: float,
    ) -> ResultT:
        ttl = ttl_seconds or self._default_ttl_seconds
        interval = renew_interval_seconds or max(min(ttl / 3, 20.0), 0.1)
        if interval >= ttl:
            raise ValueError("租约续租周期必须小于租约时长")
        if hard_timeout_seconds <= 0:
            raise ValueError("算子 HTTP 硬超时必须大于 0")

        current_lease = lease

        async def renew_forever() -> None:
            nonlocal current_lease
            while True:
                await asyncio.sleep(interval)
                try:
                    current_lease = await renew_lease_with_retry(
                        lease_id=current_lease.lease_id,
                        confirmed_expires_at=current_lease.expires_at,
                        renew=partial(
                            self.renew,
                            current_lease.lease_id,
                            ttl_seconds=ttl,
                        ),
                        policy=self._renewal_policy,
                    )
                except Exception as exc:
                    raise LeaseRenewalError(
                        f"算子容量租约续租失败: {current_lease.lease_id}"
                    ) from exc

        operation_task = asyncio.ensure_future(operation)
        renewal_task = asyncio.create_task(
            renew_forever(),
            name=f"renew-operator-lease-{lease.lease_id}",
        )
        try:
            done, _ = await asyncio.wait(
                {operation_task, renewal_task},
                timeout=hard_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                operation_task.cancel()
                with suppress(asyncio.CancelledError):
                    await operation_task
                raise TimeoutError(
                    f"算子 HTTP 调用超过硬超时 {hard_timeout_seconds:g} 秒"
                )
            if renewal_task in done:
                operation_task.cancel()
                with suppress(asyncio.CancelledError):
                    await operation_task
                await renewal_task
                raise AssertionError("unreachable")
            return await operation_task
        finally:
            renewal_task.cancel()
            with suppress(asyncio.CancelledError):
                await renewal_task
            if not operation_task.done():
                operation_task.cancel()
                with suppress(asyncio.CancelledError):
                    await operation_task

    async def release(self, lease_id: str) -> None:
        for attempt in range(1, self._renewal_policy.max_attempts + 1):
            try:
                response = await self._http_client.post(
                    "/internal/operator-instances/release",
                    json={"lease_id": lease_id},
                )
                if response.status_code == 404:
                    return
                response.raise_for_status()
                return
            except Exception as exc:
                transient_http = (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code in {500, 502, 503, 504}
                )
                if not isinstance(
                    exc,
                    (httpx.NetworkError, httpx.TimeoutException),
                ) and not transient_http:
                    raise
                if attempt >= self._renewal_policy.max_attempts:
                    logger.warning(
                        "容量租约释放暂未确认，等待 TTL 回收",
                        extra={
                            "lease_id": lease_id,
                            "attempts": attempt,
                            "outcome": "release_failed",
                        },
                    )
                    return
                delay = min(
                    self._renewal_policy.max_delay_seconds,
                    self._renewal_policy.base_delay_seconds * (2 ** (attempt - 1)),
                )
                if delay > 0:
                    await asyncio.sleep(delay)

    @staticmethod
    def _parse_lease(payload: object) -> CapacityLease:
        if not isinstance(payload, dict):
            raise TypeError("容量租约响应不是 JSON 对象")
        raw_context = payload.get("work_context")
        work_context = (
            WorkContext(**raw_context) if isinstance(raw_context, dict) else None
        )
        acquired_at = payload.get("acquired_at")
        return CapacityLease(
            lease_id=str(payload["lease_id"]),
            instance_id=str(payload["instance_id"]),
            capability=str(payload["capability"]),
            service_url=str(payload["service_url"]),
            acquired_at=(
                datetime.fromisoformat(str(acquired_at).replace("Z", "+00:00"))
                if acquired_at is not None
                else datetime.now(UTC)
            ),
            expires_at=datetime.fromisoformat(
                str(payload["expires_at"]).replace("Z", "+00:00")
            ),
            work_context=work_context,
        )

    @staticmethod
    def _response_detail(response: httpx.Response) -> str:
        try:
            detail = response.json().get("detail", "")
        except ValueError:
            return ""
        return str(detail)
