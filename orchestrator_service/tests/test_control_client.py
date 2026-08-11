from __future__ import annotations

import json

import httpx
import pytest

from orchestrator_service.app.infrastructure.control_client import (
    ControlLeaseClient,
    LeaseUnavailableError,
)


@pytest.mark.asyncio
async def test_control_client_acquires_and_releases_capacity_lease() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append((request.url.path, payload))
        if request.url.path.endswith("/lease"):
            return httpx.Response(
                200,
                json={
                    "lease_id": "lease-001",
                    "instance_id": "stub-asr-001",
                    "capability": "asr_offline",
                    "service_url": "http://127.0.0.1:19090",
                    "expires_at": "2026-08-11T12:00:00Z",
                },
            )
        return httpx.Response(200, json={"lease_id": "lease-001", "status": "RELEASED"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://control-service:18100",
    ) as http_client:
        client = ControlLeaseClient(http_client, default_ttl_seconds=45)
        lease = await client.acquire("asr_offline")
        await client.release(lease.lease_id)

    assert lease.instance_id == "stub-asr-001"
    assert lease.service_url == "http://127.0.0.1:19090"
    assert requests == [
        (
            "/internal/operator-instances/lease",
            {"capability": "asr_offline", "ttl_seconds": 45},
        ),
        (
            "/internal/operator-instances/release",
            {"lease_id": "lease-001"},
        ),
    ]


@pytest.mark.asyncio
async def test_control_client_maps_only_capacity_503_to_unavailable() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(503, json={"detail": "暂无可用算子容量: ocr"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://control-service:18100",
    ) as http_client:
        client = ControlLeaseClient(http_client, default_ttl_seconds=30)
        with pytest.raises(LeaseUnavailableError, match="暂无可用算子容量"):
            await client.acquire("ocr")


@pytest.mark.asyncio
async def test_control_client_keeps_non_capacity_http_diagnostics() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500, json={"detail": "control 内部错误"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://control-service:18100",
    ) as http_client:
        client = ControlLeaseClient(http_client, default_ttl_seconds=30)
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.acquire("ocr")

    assert exc_info.value.response.json() == {"detail": "control 内部错误"}
