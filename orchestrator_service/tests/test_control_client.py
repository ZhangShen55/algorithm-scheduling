from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from orchestrator_service.app.infrastructure.control_client import (
    ControlLeaseClient,
    LeaseRenewalError,
    LeaseUnavailableError,
)
from packages.platform_common.operator_registry import WorkContext


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


@pytest.mark.asyncio
async def test_control_client_sends_and_parses_work_context() -> None:
    requests: list[tuple[str, dict[str, object]]] = []
    context = WorkContext(
        source_service="orchestrator-service",
        work_type="ppt_ocr_item",
        work_id="ppt-001",
        task_id="course-001",
        node_id="11",
        item_id="ppt-001",
        trace_id="trace-001",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append((request.url.path, payload))
        return httpx.Response(
            200,
            json={
                "lease_id": "lease-ctx",
                "instance_id": "ocr-gpu0",
                "capability": "ocr",
                "service_url": "http://ocr-gpu0:8866",
                "acquired_at": "2026-08-19T12:00:00Z",
                "expires_at": "2026-08-19T12:01:00Z",
                "work_context": context.as_dict(),
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://control-service:18100",
    ) as http_client:
        client = ControlLeaseClient(http_client, default_ttl_seconds=60)
        acquired = await client.acquire("ocr", work_context=context)
        bound = await client.bind_context("lease-ctx", context)

    assert acquired.work_context == bound.work_context == context
    assert requests == [
        (
            "/internal/operator-instances/lease",
            {
                "capability": "ocr",
                "ttl_seconds": 60,
                "work_context": context.as_dict(),
            },
        ),
        (
            "/internal/operator-instances/lease/context",
            {"lease_id": "lease-ctx", "work_context": context.as_dict()},
        ),
    ]


@pytest.mark.asyncio
async def test_control_client_renews_long_http_operation_without_reacquiring() -> None:
    renewals = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal renewals
        if request.url.path.endswith("/lease/renew"):
            renewals += 1
        return httpx.Response(
            200,
            json={
                "lease_id": "lease-long",
                "instance_id": "asr-gpu0",
                "capability": "asr_offline",
                "service_url": "http://asr-gpu0:8083",
                "acquired_at": "2026-08-19T12:00:00Z",
                "expires_at": "2026-08-19T12:01:00Z",
                "work_context": None,
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://control-service:18100",
    ) as http_client:
        client = ControlLeaseClient(http_client, default_ttl_seconds=1)
        lease = await client.acquire("asr_offline")

        async def operation() -> str:
            await asyncio.sleep(0.05)
            return "ok"

        result = await client.run_with_renewal(
            lease,
            operation(),
            renew_interval_seconds=0.01,
            hard_timeout_seconds=1,
        )

    assert result == "ok"
    assert renewals >= 1


@pytest.mark.asyncio
async def test_control_client_cancels_operation_when_renewal_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/lease/renew"):
            return httpx.Response(503, json={"detail": "redis unavailable"})
        return httpx.Response(
            200,
            json={
                "lease_id": "lease-fail",
                "instance_id": "ocr-gpu0",
                "capability": "ocr",
                "service_url": "http://ocr-gpu0:8866",
                "expires_at": "2026-08-19T12:01:00Z",
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://control-service:18100",
    ) as http_client:
        client = ControlLeaseClient(http_client, default_ttl_seconds=1)
        lease = await client.acquire("ocr")

        async def operation() -> None:
            await asyncio.sleep(1)

        with pytest.raises(LeaseRenewalError):
            await client.run_with_renewal(
                lease,
                operation(),
                renew_interval_seconds=0.01,
                hard_timeout_seconds=1,
            )


@pytest.mark.asyncio
async def test_control_client_propagates_cancellation_to_operator_call() -> None:
    operation_cancelled = asyncio.Event()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
        base_url="http://control-service:18100",
    ) as http_client:
        client = ControlLeaseClient(http_client, default_ttl_seconds=30)

        async def operation() -> None:
            try:
                await asyncio.Future()
            finally:
                operation_cancelled.set()

        lease = ControlLeaseClient._parse_lease(
            {
                "lease_id": "lease-cancel",
                "instance_id": "ocr-gpu0",
                "capability": "ocr",
                "service_url": "http://ocr-gpu0:8866",
                "expires_at": "2026-08-19T12:01:00Z",
            }
        )
        task = asyncio.create_task(
            client.run_with_renewal(
                lease,
                operation(),
                renew_interval_seconds=1,
                hard_timeout_seconds=30,
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert operation_cancelled.is_set()


@pytest.mark.asyncio
async def test_control_client_hard_timeout_cancels_operator_call() -> None:
    operation_cancelled = asyncio.Event()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
        base_url="http://control-service:18100",
    ) as http_client:
        client = ControlLeaseClient(http_client, default_ttl_seconds=30)

        async def operation() -> None:
            try:
                await asyncio.Future()
            finally:
                operation_cancelled.set()

        lease = ControlLeaseClient._parse_lease(
            {
                "lease_id": "lease-timeout",
                "instance_id": "asr-gpu0",
                "capability": "asr_offline",
                "service_url": "http://asr-gpu0:8083",
                "expires_at": "2026-08-19T12:01:00Z",
            }
        )
        with pytest.raises(TimeoutError, match="算子 HTTP 调用超过硬超时"):
            await client.run_with_renewal(
                lease,
                operation(),
                renew_interval_seconds=1,
                hard_timeout_seconds=0.01,
            )

    assert operation_cancelled.is_set()


@pytest.mark.asyncio
async def test_control_client_treats_missing_release_as_idempotent_success() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(404, json={"detail": "租约已不存在"})
        ),
        base_url="http://control-service:18100",
    ) as http_client:
        client = ControlLeaseClient(http_client, default_ttl_seconds=30)
        await client.release("lease-missing")
