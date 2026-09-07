from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from vision_orchestrator_service.app.infrastructure.cache import VisionStream
from vision_orchestrator_service.app.infrastructure.capacity import (
    CapacityLeaseHttpClient,
    WorkContext,
)
from vision_orchestrator_service.app.infrastructure.vbas import VbasBatchClient, VbasFrame

from packages.platform_common.lease_resilience import LeaseRenewalPolicy
from packages.platform_common.operator_registry import CapacityLease


@pytest.mark.asyncio
async def test_vbas_stage_observer_records_lease_and_http_order() -> None:
    events: list[tuple[str, dict[str, object]]] = []

    class LeaseClient:
        @asynccontextmanager
        async def acquire(self, capability, **kwargs):
            del kwargs
            yield CapacityLease(
                "lease-1",
                "vbas-gpu1",
                capability,
                "http://vbas-gpu1:8981",
                datetime.now(UTC) + timedelta(seconds=30),
            )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "StatusObject": {"StatusCode": 0},
                "DataList": [
                    {
                        "StatusObject": {
                            "StatusCode": 0,
                            "ImageId": image["ImageId"],
                        },
                        "ResultList": [],
                    }
                    for image in body["ImageList"]
                ],
            },
        )

    frame = VbasFrame("frame-1", Path("/data/course/c/frame.jpg"), 1, 1.0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = VbasBatchClient(
            http,
            LeaseClient(),
            stage_observer=lambda event, detail: events.append((event, detail)),
        )
        result = await client.analyze(
            task_id="course-1",
            stream=VisionStream.TEACHER,
            frames=[frame],
        )

    assert len(result) == 1
    assert [event for event, _ in events] == [
        "lease_requested",
        "lease_acquired",
        "vbas_started",
        "vbas_finished",
    ]
    assert all(detail["batch_id"] == events[0][1]["batch_id"] for _, detail in events)
    assert events[1][1]["instance_id"] == "vbas-gpu1"


@pytest.mark.asyncio
async def test_capacity_observer_records_authority_retry_acquire_and_release() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    lease_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal lease_attempts
        if request.url.path.endswith("/release"):
            return httpx.Response(200, request=request, json={"released": True})
        lease_attempts += 1
        if lease_attempts == 1:
            return httpx.Response(
                503,
                request=request,
                json={"detail": "暂无可用算子容量: student_behavior"},
            )
        return httpx.Response(
            200,
            request=request,
            json={
                "lease_id": "lease-observed",
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
            acquire_retry_interval_seconds=0,
            stage_observer=lambda event, detail: events.append((event, detail)),
        )
        async with client.acquire(
            "student_behavior",
            work_context=WorkContext(
                source_service="benchmark",
                work_type="dispatch_replay",
                work_id="batch-001",
            ),
        ):
            pass

    assert [event for event, _ in events] == [
        "lease_attempt",
        "lease_retry",
        "lease_attempt",
        "lease_authority_acquired",
        "lease_released",
    ]
    assert events[1][1]["outcome"] == "capacity_unavailable"
    assert events[3][1]["instance_id"] == "vbas-gpu2"
    assert all(detail["batch_id"] == "batch-001" for _, detail in events)


@pytest.mark.asyncio
async def test_capacity_observer_marks_unconfirmed_release() -> None:
    events: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/release"):
            return httpx.Response(503, request=request, json={"detail": "恢复中"})
        return httpx.Response(
            200,
            request=request,
            json={
                "lease_id": "lease-release-unconfirmed",
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
            stage_observer=lambda event, detail: events.append((event, detail)),
        )
        async with client.acquire(
            "teacher_behavior",
            work_context=WorkContext(
                source_service="benchmark",
                work_type="dispatch_replay",
                work_id="batch-002",
            ),
        ):
            pass

    assert [event for event, _ in events][-1] == "lease_release_unconfirmed"
    assert events[-1][1]["outcome"] == "unconfirmed"
    assert events[-1][1]["instance_id"] == "vbas-gpu0"
