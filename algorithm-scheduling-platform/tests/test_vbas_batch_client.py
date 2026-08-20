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


def _success_response(body: dict[str, object]) -> httpx.Response:
    images = body["ImageList"]
    assert isinstance(images, list)
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
                for image in images
            ],
        },
    )


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


@pytest.mark.asyncio
async def test_vbas_operator_429_is_recoverable_capacity_wait_and_releases_lease() -> None:
    acquired = 0
    released: list[str] = []
    requests = 0

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
            nonlocal acquired
            del ttl_seconds, renew_interval_seconds
            assert capability == "teacher_behavior"
            assert work_context is not None
            acquired += 1
            lease_id = f"lease-overloaded-{acquired}"
            try:
                yield CapacityLease(
                    lease_id,
                    "vbas-gpu0",
                    capability,
                    "http://vbas-gpu0:9010",
                    datetime.now(UTC) + timedelta(seconds=60),
                )
            finally:
                released.append(lease_id)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(429)
        body = json.loads(request.content)
        return _success_response(body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = VbasBatchClient(
            http,
            LeaseClient(),
            config=VbasBatchConfig(capacity_retry_delay_seconds=0.001),
        )
        results = await client.analyze(
            task_id="course-001",
            stream=VisionStream.TEACHER,
            frames=[
                VbasFrame(
                    "teacher-0",
                    Path("/data/course/course-001/frames/teacher-0.jpg"),
                    0,
                    0.0,
                )
            ],
        )

    assert [result["image_id"] for result in results] == ["teacher-0"]
    assert requests == 2
    assert released == ["lease-overloaded-1", "lease-overloaded-2"]


@pytest.mark.asyncio
async def test_vbas_operator_503_is_not_misclassified_as_capacity_wait() -> None:
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
            del ttl_seconds, work_context, renew_interval_seconds
            yield CapacityLease(
                "lease-upstream-failed",
                "vbas-gpu0",
                capability,
                "http://vbas-gpu0:9010",
                datetime.now(UTC) + timedelta(seconds=60),
            )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(503))
    ) as http:
        client = VbasBatchClient(http, LeaseClient())
        with pytest.raises(VbasAdapterError, match="VBas 批次调用失败"):
            await client.analyze(
                task_id="course-001",
                stream=VisionStream.STUDENT,
                frames=[
                    VbasFrame(
                        "student-0",
                        Path("/data/course/course-001/frames/student-0.jpg"),
                        0,
                        0.0,
                    )
                ],
            )


@pytest.mark.asyncio
async def test_vbas_fatal_error_cancels_and_reaps_sibling_batches_before_returning() -> None:
    acquired: list[str] = []
    released: list[str] = []
    requests: list[str] = []
    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()
    block_sibling = asyncio.Event()
    loop_errors: list[dict[str, object]] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda event_loop, context: loop_errors.append(context))

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
            assert capability == "teacher_behavior"
            assert work_context is not None
            batch_id = work_context.work_id
            acquired.append(batch_id)
            try:
                yield CapacityLease(
                    f"lease-{batch_id}",
                    "vbas-gpu0",
                    capability,
                    "http://vbas-gpu0:9010",
                    datetime.now(UTC) + timedelta(seconds=60),
                )
            finally:
                released.append(batch_id)

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        batch_id = body["batch_id"]
        requests.append(batch_id)
        if batch_id.endswith("0000"):
            await asyncio.wait_for(sibling_started.wait(), timeout=1)
            return httpx.Response(500)
        if batch_id.endswith("0001"):
            sibling_started.set()
            try:
                await block_sibling.wait()
            except asyncio.CancelledError:
                sibling_cancelled.set()
                raise
            raise AssertionError("blocked sibling must be cancelled")
        raise AssertionError("third batch must not start")

    frames = [
        VbasFrame(
            f"teacher-{index}",
            Path(f"/data/course/course-001/frames/teacher-{index}.jpg"),
            index,
            float(index),
        )
        for index in range(3)
    ]
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = VbasBatchClient(
                http,
                LeaseClient(),
                config=VbasBatchConfig(batch_size=1, max_concurrency=2),
            )
            with pytest.raises(VbasAdapterError, match="VBas 批次调用失败"):
                await client.analyze(
                    task_id="course-001",
                    stream=VisionStream.TEACHER,
                    frames=frames,
                )
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    expected_batches = {"course-001-t-0000", "course-001-t-0001"}
    assert set(acquired) == expected_batches
    assert set(released) == expected_batches
    assert set(requests) == expected_batches
    assert sibling_cancelled.is_set()
    assert "course-001-t-0002" not in acquired
    assert loop_errors == []


@pytest.mark.asyncio
async def test_vbas_capacity_retry_preserves_successful_sibling_batch() -> None:
    active = 0
    peak = 0
    attempts: dict[str, int] = {}
    successes: dict[str, int] = {}
    overload_seen = asyncio.Event()

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
            assert capability == "teacher_behavior"
            assert work_context is not None
            yield CapacityLease(
                f"lease-{work_context.work_id}-{attempts.get(work_context.work_id, 0)}",
                "vbas-gpu0",
                capability,
                "http://vbas-gpu0:9010",
                datetime.now(UTC) + timedelta(seconds=60),
            )

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        body = json.loads(request.content)
        batch_id = body["batch_id"]
        attempts[batch_id] = attempts.get(batch_id, 0) + 1
        if active >= 1:
            overload_seen.set()
            return httpx.Response(429)
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.wait_for(overload_seen.wait(), timeout=1)
            successes[batch_id] = successes.get(batch_id, 0) + 1
            return _success_response(body)
        finally:
            active -= 1

    frames = [
        VbasFrame(
            f"teacher-{index}",
            Path(f"/data/course/course-001/frames/teacher-{index}.jpg"),
            index,
            float(index),
        )
        for index in range(2)
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = VbasBatchClient(
            http,
            LeaseClient(),
            config=VbasBatchConfig(
                batch_size=1,
                max_concurrency=2,
                capacity_retry_delay_seconds=0.001,
            ),
        )
        results = await client.analyze(
            task_id="course-001",
            stream=VisionStream.TEACHER,
            frames=frames,
        )

    assert [result["image_id"] for result in results] == ["teacher-0", "teacher-1"]
    assert peak == 1
    assert sorted(attempts.values()) == [1, 2]
    assert successes == {
        "course-001-t-0000": 1,
        "course-001-t-0001": 1,
    }


@pytest.mark.asyncio
async def test_vbas_shutdown_interrupts_capacity_retry_wait_immediately() -> None:
    attempted = asyncio.Event()
    shutdown = asyncio.Event()
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
            del ttl_seconds, work_context, renew_interval_seconds
            try:
                yield CapacityLease(
                    "lease-shutdown",
                    "vbas-gpu0",
                    capability,
                    "http://vbas-gpu0:9010",
                    datetime.now(UTC) + timedelta(seconds=60),
                )
            finally:
                released.append("lease-shutdown")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        attempted.set()
        return httpx.Response(429)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = VbasBatchClient(
            http,
            LeaseClient(),
            config=VbasBatchConfig(capacity_retry_delay_seconds=60),
            shutdown_event=shutdown,
        )
        task = asyncio.create_task(
            client.analyze(
                task_id="course-001",
                stream=VisionStream.TEACHER,
                frames=[
                    VbasFrame(
                        "teacher-0",
                        Path("/data/course/course-001/frames/teacher-0.jpg"),
                        0,
                        0.0,
                    )
                ],
            )
        )
        await asyncio.wait_for(attempted.wait(), timeout=1)
        shutdown.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.1)

    assert released == ["lease-shutdown"]
