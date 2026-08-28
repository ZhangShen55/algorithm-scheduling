from __future__ import annotations

import asyncio

import httpx
import pytest

from online_gateway_service.app.infrastructure.capacity import (
    OnlineCapacityLeaseClient,
)
from packages.platform_common.lease_resilience import LeaseRenewalPolicy


@pytest.mark.asyncio
async def test_first_renew_read_error_recovers_without_cancelling_online_request() -> None:
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
                "lease_id": "lease-online",
                "instance_id": "vbas-gpu0",
                "capability": "student_behavior",
                "service_url": "http://vbas-gpu0:8981",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OnlineCapacityLeaseClient(
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


@pytest.mark.asyncio
async def test_release_404_is_idempotent_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/release"):
            return httpx.Response(404, request=request, json={"detail": "租约不存在"})
        return httpx.Response(
            200,
            request=request,
            json={
                "lease_id": "lease-online",
                "instance_id": "screen-gpu0",
                "capability": "image_quality",
                "service_url": "http://screen-gpu0:8880",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OnlineCapacityLeaseClient(
            http,
            control_service_url="http://control",
        )
        async with client.acquire("image_quality"):
            pass
