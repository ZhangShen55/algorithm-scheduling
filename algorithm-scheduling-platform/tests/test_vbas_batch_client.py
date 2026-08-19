import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from vision_orchestrator_service.app.infrastructure.cache import VisionStream
from vision_orchestrator_service.app.infrastructure.vbas import (
    VbasAdapterError,
    VbasBatchClient,
    VbasBatchConfig,
    VbasFrame,
)

from packages.platform_common.operator_registry import CapacityLease, WorkContext


@pytest.mark.asyncio
async def test_vbas_batches_use_capacity_lease_and_configured_concurrency() -> None:
    requests: list[tuple[str, dict[str, object]]] = []
    lease_capabilities: list[str] = []
    lease_contexts: list[WorkContext] = []
    active = 0
    peak = 0

    class LeaseClient:
        @asynccontextmanager
        async def acquire(
            self,
            capability: str,
            *,
            ttl_seconds: int = 60,
            work_context: WorkContext | None = None,
            renew_interval_seconds: float | None = None,
        ):
            nonlocal active, peak
            del renew_interval_seconds
            lease_capabilities.append(capability)
            assert work_context is not None
            lease_contexts.append(work_context)
            assert ttl_seconds == 120
            active += 1
            peak = max(peak, active)
            try:
                yield CapacityLease(
                    lease_id=f"lease-{len(lease_capabilities)}",
                    instance_id="vbas-gpu0",
                    capability=capability,
                    service_url="http://vbas-gpu0:9010",
                    expires_at=datetime.now(UTC) + timedelta(seconds=120),
                )
            finally:
                active -= 1

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        await asyncio.sleep(0.01)
        return httpx.Response(
            200,
            json={
                "StatusObject": {"StatusString": "success", "StatusCode": 0},
                "DataList": [
                    {
                        "StatusObject": {
                            "StatusString": "success",
                            "StatusCode": 0,
                            "ImageId": image["ImageId"],
                        },
                        "ResultList": [],
                    }
                    for image in body["ImageList"]
                ],
            },
        )

    frames = [
        VbasFrame(
            image_id=f"teacher-{index}",
            path=Path(f"/data/course/course-001/frames/teacher-{index}.jpg"),
            frame_index=index,
            timestamp_seconds=float(index * 10),
        )
        for index in range(5)
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = VbasBatchClient(
            http,
            LeaseClient(),
            config=VbasBatchConfig(
                batch_size=2,
                max_concurrency=2,
                lease_ttl_seconds=120,
            ),
        )
        results = await client.analyze(
            task_id="course-001",
            stream=VisionStream.TEACHER,
            frames=frames,
            trace_id="trace-001",
        )

    assert peak == 2
    assert lease_capabilities == [
        "teacher_behavior",
        "teacher_behavior",
        "teacher_behavior",
    ]
    assert [context.work_id for context in lease_contexts] == [
        "course-001-t-0000",
        "course-001-t-0001",
        "course-001-t-0002",
    ]
    assert all(
        context.source_service == "vision-orchestrator-service"
        and context.work_type == "vbas_teacher_batch"
        and context.task_id == "course-001"
        and context.item_id == context.work_id
        and context.trace_id == "trace-001"
        for context in lease_contexts
    )
    assert [path for path, _ in requests] == [
        "/ImageDetect/teacher/v1.0.0",
        "/ImageDetect/teacher/v1.0.0",
        "/ImageDetect/teacher/v1.0.0",
    ]
    assert sorted(len(body["ImageList"]) for _, body in requests) == [1, 2, 2]
    assert [result["image_id"] for result in results] == [
        "teacher-0",
        "teacher-1",
        "teacher-2",
        "teacher-3",
        "teacher-4",
    ]
    assert all(
        image["StoragePath"].startswith("/data/course/")
        for _, body in requests
        for image in body["ImageList"]
    )


@pytest.mark.asyncio
async def test_student_vbas_request_preserves_roi_points() -> None:
    captured: dict[str, object] = {}

    class LeaseClient:
        @asynccontextmanager
        async def acquire(
            self,
            capability: str,
            *,
            ttl_seconds: int = 60,
            work_context: WorkContext | None = None,
            renew_interval_seconds: float | None = None,
        ):
            del ttl_seconds, renew_interval_seconds
            assert capability == "student_behavior"
            assert work_context is not None
            assert work_context.work_type == "vbas_student_batch"
            yield CapacityLease(
                "lease-student",
                "vbas-gpu1",
                capability,
                "http://vbas-gpu1:9011",
                datetime.now(UTC) + timedelta(seconds=60),
            )

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "StatusObject": {"StatusString": "success", "StatusCode": 0},
                "DataList": [
                    {
                        "StatusObject": {
                            "StatusString": "success",
                            "StatusCode": 0,
                            "ImageId": "student-0",
                        },
                        "ResultList": [],
                    }
                ],
            },
        )

    points = [{"X": 0, "Y": 0}, {"X": 100, "Y": 0}, {"X": 100, "Y": 100}]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await VbasBatchClient(http, LeaseClient()).analyze(
            task_id="course-001",
            stream=VisionStream.STUDENT,
            frames=[
                VbasFrame(
                    "student-0",
                    Path("/data/course/course-001/frames/student-0.jpg"),
                    0,
                    0.0,
                    points,
                )
            ],
        )

    assert captured["path"] == "/ImageDetect/student/v1.0.0"
    assert captured["body"]["ImageList"][0]["Points"] == points


@pytest.mark.asyncio
async def test_vbas_batch_hard_timeout_releases_its_single_lease() -> None:
    released: list[str] = []

    class LeaseClient:
        @asynccontextmanager
        async def acquire(
            self,
            capability: str,
            *,
            ttl_seconds: int = 60,
            work_context: WorkContext | None = None,
            renew_interval_seconds: float | None = None,
        ):
            del ttl_seconds, renew_interval_seconds
            assert capability == "student_behavior"
            assert work_context is not None
            try:
                yield CapacityLease(
                    "lease-timeout",
                    "vbas-gpu0",
                    capability,
                    "http://vbas-gpu0:9010",
                    datetime.now(UTC) + timedelta(seconds=60),
                )
            finally:
                released.append("lease-timeout")

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={})

    frame = VbasFrame(
        "student-0",
        Path("/data/course/course-001/frames/student-0.jpg"),
        0,
        0.0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = VbasBatchClient(
            http,
            LeaseClient(),
            config=VbasBatchConfig(request_timeout_seconds=0.01),
        )
        with pytest.raises(VbasAdapterError, match="VBas 批次调用失败"):
            await client.analyze(
                task_id="course-001",
                stream=VisionStream.STUDENT,
                frames=[frame],
            )

    assert released == ["lease-timeout"]
