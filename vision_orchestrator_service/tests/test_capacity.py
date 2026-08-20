from __future__ import annotations

import httpx
import pytest

from vision_orchestrator_service.app.infrastructure.capacity import (
    CapacityLeaseClientError,
    CapacityLeaseHttpClient,
    CapacityUnavailableError,
)


@pytest.mark.asyncio
async def test_lease_http_503_is_classified_as_capacity_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"detail": "暂无可用算子容量: teacher_behavior"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CapacityLeaseHttpClient(http, control_service_url="http://control")

        with pytest.raises(CapacityUnavailableError, match="容量暂不可用"):
            async with client.acquire("teacher_behavior"):
                raise AssertionError("503 must not yield a lease")


@pytest.mark.asyncio
async def test_registry_unavailable_503_is_not_treated_as_capacity_wait() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"detail": "算子注册中心暂不可用"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CapacityLeaseHttpClient(http, control_service_url="http://control")

        with pytest.raises(CapacityLeaseClientError) as captured:
            async with client.acquire("teacher_behavior"):
                raise AssertionError("503 must not yield a lease")

    assert type(captured.value) is CapacityLeaseClientError
