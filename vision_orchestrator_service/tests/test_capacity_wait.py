import httpx
import pytest
import asyncio

from app.infrastructure.capacity import CapacityLeaseClientError, CapacityLeaseHttpClient


@pytest.mark.asyncio
async def test_offline_capacity_client_waits_until_lease_is_available() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, request=request, json={"detail": "暂无可用算子容量: student_behavior"})
        return httpx.Response(200, request=request, json={
            "lease_id": "lease-offline", "instance_id": "vbas-1",
            "capability": "student_behavior", "capacity_pool": "offline",
            "service_url": "http://vbas-1", "expires_at": "2099-01-01T00:00:00Z",
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CapacityLeaseHttpClient(
            http, control_service_url="http://control",
            acquire_wait_timeout_seconds=1, acquire_retry_interval_seconds=0.001,
        )
        async with client.acquire("student_behavior") as lease:
            assert lease.capacity_pool == "offline"
    assert attempts >= 2


@pytest.mark.asyncio
async def test_offline_capacity_wait_timeout_returns_clear_error_without_release() -> None:
    release_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal release_calls
        if request.url.path.endswith("/release"):
            release_calls += 1
        return httpx.Response(
            503,
            request=request,
            json={"detail": "暂无可用算子容量: student_behavior"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CapacityLeaseHttpClient(
            http,
            control_service_url="http://control",
            acquire_wait_timeout_seconds=0.02,
            acquire_retry_interval_seconds=0.001,
        )
        with pytest.raises(CapacityLeaseClientError, match="超过"):
            async with client.acquire("student_behavior"):
                raise AssertionError("容量等待超时前不应进入租约上下文")

    assert release_calls == 0


@pytest.mark.asyncio
async def test_two_offline_batches_wait_for_the_released_instance_slot() -> None:
    active = False
    first_started = asyncio.Event()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, calls
        calls += 1
        if request.url.path.endswith("/release"):
            active = False
            return httpx.Response(200, request=request, json={"released": True})
        if active:
            return httpx.Response(
                503,
                request=request,
                json={"detail": "暂无可用算子容量: student_behavior"},
            )
        active = True
        first_started.set()
        return httpx.Response(
            200,
            request=request,
            json={
                "lease_id": f"offline-{calls}",
                "instance_id": "vbas-1",
                "capability": "student_behavior",
                "capacity_pool": "offline",
                "service_url": "http://vbas-1",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CapacityLeaseHttpClient(
            http,
            control_service_url="http://control",
            acquire_wait_timeout_seconds=2,
            acquire_retry_interval_seconds=0.001,
        )
        second_acquired = asyncio.Event()

        async def first_batch() -> None:
            async with client.acquire("student_behavior"):
                await first_started.wait()
                await asyncio.sleep(0.02)

        async def second_batch() -> None:
            async with client.acquire("student_behavior"):
                second_acquired.set()

        await asyncio.gather(first_batch(), second_batch())

    assert second_acquired.is_set()
    assert calls >= 3
