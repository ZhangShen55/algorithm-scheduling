from __future__ import annotations

from datetime import datetime

import httpx

from packages.platform_common.operator_registry import CapacityLease


class LeaseUnavailableError(RuntimeError):
    pass


class ControlLeaseClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        default_ttl_seconds: int,
    ) -> None:
        self._http_client = http_client
        self._default_ttl_seconds = default_ttl_seconds

    async def acquire(
        self,
        capability: str,
        *,
        ttl_seconds: int | None = None,
    ) -> CapacityLease:
        response = await self._http_client.post(
            "/internal/operator-instances/lease",
            json={
                "capability": capability,
                "ttl_seconds": ttl_seconds or self._default_ttl_seconds,
            },
        )
        if response.status_code == 503:
            detail = self._response_detail(response)
            if detail.startswith("暂无可用算子容量"):
                raise LeaseUnavailableError(detail)
        response.raise_for_status()
        payload = response.json()
        return CapacityLease(
            lease_id=str(payload["lease_id"]),
            instance_id=str(payload["instance_id"]),
            capability=str(payload["capability"]),
            service_url=str(payload["service_url"]),
            expires_at=datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00")),
        )

    async def release(self, lease_id: str) -> None:
        response = await self._http_client.post(
            "/internal/operator-instances/release",
            json={"lease_id": lease_id},
        )
        response.raise_for_status()

    @staticmethod
    def _response_detail(response: httpx.Response) -> str:
        try:
            detail = response.json().get("detail", "")
        except ValueError:
            return ""
        return str(detail)
