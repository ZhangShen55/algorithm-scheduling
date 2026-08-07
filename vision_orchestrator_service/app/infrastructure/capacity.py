from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

import httpx


@dataclass(frozen=True, slots=True)
class CapacityLease:
    lease_id: str
    instance_id: str
    capability: str
    service_url: str
    expires_at: datetime


class CapacityLeaseClientError(RuntimeError):
    pass


class CapacityLeaseHttpClient:
    def __init__(self, http_client: httpx.AsyncClient, *, control_service_url: str) -> None:
        self._http = http_client
        self._control_service_url = control_service_url.rstrip("/")

    @asynccontextmanager
    async def acquire(
        self,
        capability: str,
        *,
        ttl_seconds: int = 60,
    ) -> AsyncIterator[CapacityLease]:
        try:
            response = await self._http.post(
                f"{self._control_service_url}/internal/operator-instances/lease",
                json={"capability": capability, "ttl_seconds": ttl_seconds},
            )
            response.raise_for_status()
            body = response.json()
            lease = CapacityLease(
                lease_id=str(body["lease_id"]),
                instance_id=str(body["instance_id"]),
                capability=str(body["capability"]),
                service_url=str(body["service_url"]),
                expires_at=datetime.fromisoformat(str(body["expires_at"])),
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise CapacityLeaseClientError(
                f"获取算子容量租约失败: {capability}: {exc}"
            ) from exc

        try:
            yield lease
        finally:
            try:
                release = await self._http.post(
                    f"{self._control_service_url}/internal/operator-instances/release",
                    json={"lease_id": lease.lease_id},
                )
                release.raise_for_status()
            except httpx.HTTPError as exc:
                raise CapacityLeaseClientError(
                    f"释放算子容量租约失败: {lease.lease_id}: {exc}"
                ) from exc
