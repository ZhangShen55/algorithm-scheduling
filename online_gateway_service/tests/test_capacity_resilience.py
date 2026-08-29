from __future__ import annotations

import asyncio
from collections import Counter
import json
from uuid import uuid4

import httpx
import pytest

from online_gateway_service.app.infrastructure.capacity import (
    OnlineCapacityLeaseClient,
    OnlineCapacityLeaseError,
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


@pytest.mark.asyncio
async def test_capacity_wait_retries_after_temporary_unavailability() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, request=request, json={"detail": "暂无可用算子容量"})
        return httpx.Response(
            200,
            request=request,
            json={
                "lease_id": "lease-wait",
                "instance_id": "vbas-gpu1",
                "capability": "student_behavior",
                "capacity_pool": "online",
                "service_url": "http://vbas-gpu1:8981",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OnlineCapacityLeaseClient(
            http,
            control_service_url="http://control",
            acquire_wait_timeout_seconds=1,
            acquire_retry_interval_seconds=0.001,
        )
        async with client.acquire("student_behavior") as lease:
            assert lease.instance_id == "vbas-gpu1"

    assert attempts >= 3


@pytest.mark.asyncio
async def test_capacity_wait_timeout_does_not_attempt_release_without_a_lease() -> None:
    attempts = 0
    release_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts, release_calls
        if request.url.path.endswith("/release"):
            release_calls += 1
        else:
            attempts += 1
        return httpx.Response(
            503,
            request=request,
            json={"detail": "暂无可用算子容量: student_behavior"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OnlineCapacityLeaseClient(
            http,
            control_service_url="http://control",
            acquire_wait_timeout_seconds=0.02,
            acquire_retry_interval_seconds=0.001,
        )
        with pytest.raises(OnlineCapacityLeaseError, match="超过"):
            async with client.acquire("student_behavior"):
                raise AssertionError("容量等待超时前不应进入租约上下文")

    assert attempts >= 1
    assert release_calls == 0


@pytest.mark.asyncio
async def test_512_online_requests_fill_three_instances_and_wait_for_release() -> None:
    per_instance_limit = 24
    instance_ids = ("vbas-gpu0", "vbas-gpu1", "vbas-gpu2")
    active = Counter()
    leases: dict[str, str] = {}
    max_active = Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/release"):
            lease_id = json.loads(request.content)["lease_id"]
            instance_id = leases.pop(lease_id)
            active[instance_id] -= 1
            return httpx.Response(200, request=request, json={"released": True})
        selected = next(
            (instance_id for instance_id in instance_ids if active[instance_id] < per_instance_limit),
            None,
        )
        if selected is None:
            return httpx.Response(
                503,
                request=request,
                json={"detail": "暂无可用算子容量: student_behavior"},
            )
        lease_id = f"lease-{uuid4().hex}"
        active[selected] += 1
        leases[lease_id] = selected
        max_active[selected] = max(max_active[selected], active[selected])
        return httpx.Response(
            200,
            request=request,
            json={
                "lease_id": lease_id,
                "instance_id": selected,
                "capability": "student_behavior",
                "capacity_pool": "online",
                "service_url": f"http://{selected}:8981",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OnlineCapacityLeaseClient(
            http,
            control_service_url="http://control",
            acquire_wait_timeout_seconds=10,
            acquire_retry_interval_seconds=0.001,
        )

        async def one_request() -> str:
            async with client.acquire("student_behavior") as lease:
                await asyncio.sleep(0.002)
                return lease.instance_id

        selected_instances = await asyncio.wait_for(
            asyncio.gather(*(one_request() for _ in range(512))),
            timeout=15,
        )

    assert len(selected_instances) == 512
    assert set(selected_instances) == set(instance_ids)
    assert all(value <= per_instance_limit for value in max_active.values())
    assert not leases
