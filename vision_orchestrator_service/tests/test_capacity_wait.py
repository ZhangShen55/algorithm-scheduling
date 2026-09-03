import asyncio

import httpx
import pytest
from app.infrastructure.capacity import (
    CapacityLeaseClientError,
    CapacityLeaseHttpClient,
)
from packages.platform_common.lease_resilience import LeaseRenewalPolicy


@pytest.mark.asyncio
async def test_offline_capacity_client_waits_until_lease_is_available() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith("/release"):
            return httpx.Response(200, request=request, json={"released": True})
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


@pytest.mark.asyncio
async def test_offline_capacity_recovers_after_control_connection_errors() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith("/release"):
            return httpx.Response(200, request=request, json={"released": True})
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("Control 暂时不可用", request=request)
        return httpx.Response(
            200,
            request=request,
            json={
                "lease_id": "lease-recovered",
                "instance_id": "vbas-gpu2",
                "capability": "student_behavior",
                "capacity_pool": "offline",
                "service_url": "http://vbas-gpu2:8981",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CapacityLeaseHttpClient(
            http,
            control_service_url="http://control",
            acquire_wait_timeout_seconds=1,
            acquire_retry_interval_seconds=0.001,
        )
        async with client.acquire("student_behavior") as lease:
            assert lease.instance_id == "vbas-gpu2"

    assert attempts == 3


@pytest.mark.parametrize("status_code", (502, 503, 504))
@pytest.mark.asyncio
async def test_offline_capacity_recovers_after_control_service_error(
    status_code: int,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith("/release"):
            return httpx.Response(200, request=request, json={"released": True})
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                status_code,
                request=request,
                json={"detail": "Control 正在恢复"},
            )
        return httpx.Response(
            200,
            request=request,
            json={
                "lease_id": "lease-recovered",
                "instance_id": "vbas-gpu1",
                "capability": "teacher_behavior",
                "capacity_pool": "offline",
                "service_url": "http://vbas-gpu1:8981",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CapacityLeaseHttpClient(
            http,
            control_service_url="http://control",
            acquire_wait_timeout_seconds=1,
            acquire_retry_interval_seconds=0.001,
        )
        async with client.acquire("teacher_behavior"):
            pass

    assert attempts == 2


@pytest.mark.parametrize("status_code", (400, 401, 403, 404, 409))
@pytest.mark.asyncio
async def test_offline_capacity_does_not_retry_deterministic_control_error(
    status_code: int,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code, request=request, json={"detail": "请求错误"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CapacityLeaseHttpClient(
            http,
            control_service_url="http://control",
            acquire_wait_timeout_seconds=1,
            acquire_retry_interval_seconds=0.001,
        )
        with pytest.raises(CapacityLeaseClientError, match="不可恢复"):
            async with client.acquire("teacher_behavior"):
                raise AssertionError("确定性错误不得取得租约")

    assert attempts == 1


@pytest.mark.asyncio
async def test_offline_capacity_rejects_invalid_lease_without_retry() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, request=request, json={"lease_id": "missing-fields"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CapacityLeaseHttpClient(
            http,
            control_service_url="http://control",
            acquire_wait_timeout_seconds=1,
            acquire_retry_interval_seconds=0.001,
        )
        with pytest.raises(CapacityLeaseClientError, match="响应无效"):
            async with client.acquire("teacher_behavior"):
                raise AssertionError("非法响应不得取得租约")

    assert attempts == 1


@pytest.mark.asyncio
async def test_offline_capacity_control_call_obeys_cumulative_budget() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, request=request, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CapacityLeaseHttpClient(
            http,
            control_service_url="http://control",
            acquire_wait_timeout_seconds=0.02,
            acquire_retry_interval_seconds=0.001,
        )
        started_at = asyncio.get_running_loop().time()
        with pytest.raises(CapacityLeaseClientError, match="等待预算内未恢复"):
            async with client.acquire("teacher_behavior"):
                raise AssertionError("超时的 Control 调用不得取得租约")

    assert asyncio.get_running_loop().time() - started_at < 0.1


@pytest.mark.asyncio
async def test_offline_release_failure_does_not_replace_analysis_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/release"):
            raise httpx.ConnectError("释放连接失败", request=request)
        return httpx.Response(
            200,
            request=request,
            json={
                "lease_id": "lease-release-failure",
                "instance_id": "vbas-gpu0",
                "capability": "teacher_behavior",
                "capacity_pool": "offline",
                "service_url": "http://vbas-gpu0:8981",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CapacityLeaseHttpClient(
            http,
            control_service_url="http://control",
            renewal_policy=LeaseRenewalPolicy(
                max_attempts=1,
                base_delay_seconds=0,
                max_delay_seconds=0,
                safety_margin_seconds=1,
            ),
        )
        with pytest.raises(ValueError, match="分析根因"):
            async with client.acquire("teacher_behavior"):
                raise ValueError("分析根因")
