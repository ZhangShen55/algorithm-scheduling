from __future__ import annotations

import asyncio

import httpx
import pytest

from packages.platform_common.lease_resilience import LeaseRenewalPolicy
from vision_orchestrator_service.app.infrastructure.capacity import (
    CapacityLeaseClientError,
    CapacityLeaseHttpClient,
    CapacityUnavailableError,
)


@pytest.mark.asyncio
async def test_lease_http_503_waits_then_returns_capacity_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"detail": "暂无可用算子容量: teacher_behavior"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CapacityLeaseHttpClient(
            http,
            control_service_url="http://control",
            acquire_wait_timeout_seconds=0.02,
            acquire_retry_interval_seconds=0.001,
        )

        with pytest.raises(CapacityLeaseClientError, match="超过"):
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


@pytest.mark.asyncio
async def test_first_renew_read_error_recovers_without_cancelling_visual_batch() -> None:
    renewals = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal renewals
        if request.url.path.endswith("/lease/renew"):
            renewals += 1
            if renewals == 1:
                raise httpx.ReadError("读取续租响应失败", request=request)
        return httpx.Response(
            200,
            request=request,
            json={
                "lease_id": "lease-vision",
                "instance_id": "vbas-gpu0",
                "capability": "student_behavior",
                "service_url": "http://vbas-gpu0:8981",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CapacityLeaseHttpClient(
            http,
            control_service_url="http://control",
            renewal_policy=LeaseRenewalPolicy(
                max_attempts=3,
                base_delay_seconds=0,
                max_delay_seconds=0,
                safety_margin_seconds=1,
            ),
        )
        async with client.acquire(
            "student_behavior",
            ttl_seconds=30,
            renew_interval_seconds=0.01,
        ):
            await asyncio.sleep(0.04)

    assert renewals >= 2
