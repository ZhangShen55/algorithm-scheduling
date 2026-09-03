import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from vision_orchestrator_service.app.infrastructure.cache import VisionStream
from vision_orchestrator_service.app.infrastructure.vbas import (
    ControlVbasOfflineCapacitySource,
    VbasAdapterError,
    VbasBatchClient,
    VbasBatchConfig,
    VbasFrame,
    VbasOfflineCapacityGate,
)

from packages.platform_common.operator_registry import CapacityLease, WorkContext


class MutableCapacitySource:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity

    async def total_capacity(self) -> int:
        return self.capacity


def _capacity_gate(capacity: int) -> VbasOfflineCapacityGate:
    return VbasOfflineCapacityGate(
        MutableCapacitySource(capacity),
        wait_timeout_seconds=1,
        retry_interval_seconds=0.001,
    )


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
                lease_ttl_seconds=120,
            ),
            capacity_gate=_capacity_gate(2),
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
    assert len({context.work_id for context in lease_contexts}) == 3
    assert all(
        context.work_id.startswith(f"course-001-t-{index:04d}-")
        for index, context in enumerate(lease_contexts)
    )
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
async def test_vbas_concurrency_is_shared_across_courses() -> None:
    active = 0
    peak = 0
    two_active = asyncio.Event()
    release = asyncio.Event()

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
            assert work_context is not None
            yield CapacityLease(
                f"lease-{work_context.work_id}",
                "vbas-gpu0",
                capability,
                "http://vbas-gpu0:9010",
                datetime.now(UTC) + timedelta(seconds=60),
            )

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        body = json.loads(request.content)
        active += 1
        peak = max(peak, active)
        if active == 2:
            two_active.set()
        try:
            await release.wait()
            return _success_response(body)
        finally:
            active -= 1

    def frames(course: str) -> list[VbasFrame]:
        return [
            VbasFrame(
                f"{course}-{index}",
                Path(f"/data/course/{course}/frames/{index}.jpg"),
                index,
                float(index),
            )
            for index in range(3)
        ]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = VbasBatchClient(
            http,
            LeaseClient(),
            config=VbasBatchConfig(batch_size=1),
            capacity_gate=_capacity_gate(2),
        )
        first = asyncio.create_task(
            client.analyze(
                task_id="course-a",
                stream=VisionStream.STUDENT,
                frames=frames("course-a"),
            )
        )
        second = asyncio.create_task(
            client.analyze(
                task_id="course-b",
                stream=VisionStream.STUDENT,
                frames=frames("course-b"),
            )
        )
        await asyncio.wait_for(two_active.wait(), timeout=1)

        assert peak == 2

        release.set()
        first_results, second_results = await asyncio.gather(first, second)

    assert peak == 2
    assert len(first_results) == 3
    assert len(second_results) == 3


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
            config=VbasBatchConfig(
                request_timeout_seconds=0.01,
                transient_max_attempts=1,
            ),
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
        with pytest.raises(VbasAdapterError, match="VBas 批次 HTTP 失败"):
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
        image_id = body["ImageList"][0]["ImageId"]
        if image_id == "teacher-0":
            await asyncio.wait_for(sibling_started.wait(), timeout=1)
            return httpx.Response(500)
        if image_id == "teacher-1":
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
                config=VbasBatchConfig(batch_size=1),
                capacity_gate=_capacity_gate(2),
            )
            with pytest.raises(VbasAdapterError, match="VBas 批次 HTTP 失败"):
                await client.analyze(
                    task_id="course-001",
                    stream=VisionStream.TEACHER,
                    frames=frames,
                )
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert len(set(acquired)) == 2
    assert set(released) == set(acquired)
    assert set(requests) == set(acquired)
    assert sibling_cancelled.is_set()
    assert not any("-t-0002-" in batch_id for batch_id in acquired)
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
                capacity_retry_delay_seconds=0.001,
            ),
            capacity_gate=_capacity_gate(2),
        )
        results = await client.analyze(
            task_id="course-001",
            stream=VisionStream.TEACHER,
            frames=frames,
        )

    assert [result["image_id"] for result in results] == ["teacher-0", "teacher-1"]
    assert peak == 1
    assert sorted(attempts.values()) == [1, 2]
    assert sorted(successes.values()) == [1, 1]
    assert len(successes) == 2


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


def test_vbas_capacity_snapshot_sums_only_schedulable_offline_pools() -> None:
    assert ControlVbasOfflineCapacitySource._parse_total_capacity(
        [
            {
                "instance_id": "vbas-gpu0",
                "operator_code": "vbas",
                "lifecycle": "ONLINE",
                "model_ready": True,
                "capacity_pools": {"offline": 1, "online": 24},
            },
            {
                "instance_id": "vbas-gpu1",
                "operator_code": "vbas",
                "lifecycle": "ONLINE",
                "model_ready": True,
                "capacity_pools": {"offline": 2, "online": 24},
            },
            {
                "instance_id": "vbas-draining",
                "operator_code": "vbas",
                "lifecycle": "DRAINING",
                "model_ready": True,
                "capacity_pools": {"offline": 8},
            },
            {
                "instance_id": "vbas-not-ready",
                "operator_code": "vbas",
                "lifecycle": "ONLINE",
                "model_ready": False,
                "capacity_pools": {"offline": 8},
            },
            {
                "instance_id": "vbas-paused",
                "operator_code": "vbas",
                "lifecycle": "ONLINE",
                "model_ready": True,
                "capacity_pools": {"offline": 0},
            },
            {
                "instance_id": "ocr-gpu0",
                "operator_code": "ocr",
                "lifecycle": "ONLINE",
                "model_ready": True,
                "capacity_pools": {"offline": 99},
            },
        ]
    ) == 3


@pytest.mark.asyncio
async def test_vbas_capacity_snapshot_refreshes_from_control() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/ops/operator-instances/snapshot"
        return httpx.Response(
            200,
            request=request,
            json=[
                {
                    "instance_id": "vbas-gpu0",
                    "operator_code": "vbas",
                    "lifecycle": "ONLINE",
                    "model_ready": True,
                    "capacity_pools": {"offline": calls},
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = ControlVbasOfflineCapacitySource(
            http,
            control_service_url="http://control",
            refresh_seconds=0.001,
        )
        assert await source.total_capacity() == 1
        assert await source.total_capacity() == 1
        await asyncio.sleep(0.002)
        assert await source.total_capacity() == 2

    assert calls == 2


@pytest.mark.asyncio
async def test_vbas_capacity_snapshot_failure_throttles_stale_fallback() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                request=request,
                json=[
                    {
                        "instance_id": "vbas-gpu0",
                        "operator_code": "vbas",
                        "lifecycle": "ONLINE",
                        "model_ready": True,
                        "capacity_pools": {"offline": 1},
                    }
                ],
            )
        raise httpx.ReadError("快照连接中断", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = ControlVbasOfflineCapacitySource(
            http,
            control_service_url="http://control",
            refresh_seconds=0.01,
        )
        assert await source.total_capacity() == 1
        await asyncio.sleep(0.02)
        assert await source.total_capacity() == 1
        assert await source.total_capacity() == 1

    assert calls == 2


@pytest.mark.asyncio
async def test_vbas_capacity_gate_refreshes_capacity_and_queues_excess_work() -> None:
    source = MutableCapacitySource(1)
    gate = VbasOfflineCapacityGate(
        source,
        wait_timeout_seconds=1,
        retry_interval_seconds=0.001,
    )
    active = 0
    peak = 0
    first_started = asyncio.Event()
    release = asyncio.Event()

    async def work() -> None:
        nonlocal active, peak
        async with gate.admit():
            active += 1
            peak = max(peak, active)
            first_started.set()
            await release.wait()
            active -= 1

    first = asyncio.create_task(work())
    second = asyncio.create_task(work())
    await asyncio.wait_for(first_started.wait(), timeout=1)
    await asyncio.sleep(0.01)
    assert active == 1

    source.capacity = 2
    await asyncio.sleep(0.01)
    assert active == 2

    release.set()
    await asyncio.gather(first, second)
    assert peak == 2


@pytest.mark.asyncio
async def test_vbas_batch_identity_distinguishes_scan_rounds_and_is_stable() -> None:
    work_ids: list[str] = []

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
            assert work_context is not None
            work_ids.append(work_context.work_id)
            yield CapacityLease(
                f"lease-{len(work_ids)}",
                "vbas-gpu0",
                capability,
                "http://vbas-gpu0:9010",
                datetime.now(UTC) + timedelta(seconds=60),
            )

    def handler(request: httpx.Request) -> httpx.Response:
        return _success_response(json.loads(request.content))

    first_frame = VbasFrame(
        "teacher-full-000000000000",
        Path("/data/course/course-001/frames/0.jpg"),
        0,
        0.0,
    )
    refined_frame = VbasFrame(
        "teacher-full-000000005000",
        Path("/data/course/course-001/frames/5.jpg"),
        5000,
        5.0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = VbasBatchClient(http, LeaseClient())
        for frames in ([first_frame], [refined_frame], [first_frame]):
            await client.analyze(
                task_id="course-001",
                stream=VisionStream.TEACHER,
                frames=frames,
            )

    assert work_ids[0] != work_ids[1]
    assert work_ids[0] == work_ids[2]
    assert all(work_id.startswith("course-001-t-0000-") for work_id in work_ids)


@pytest.mark.asyncio
async def test_vbas_transient_failure_releases_lease_and_retries_same_batch() -> None:
    acquired: list[str] = []
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
            del ttl_seconds, renew_interval_seconds
            assert work_context is not None
            acquired.append(work_context.work_id)
            lease_id = f"lease-{len(acquired)}"
            try:
                yield CapacityLease(
                    lease_id,
                    f"vbas-gpu{len(acquired) - 1}",
                    capability,
                    "http://vbas:9010",
                    datetime.now(UTC) + timedelta(seconds=60),
                )
            finally:
                released.append(lease_id)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            raise httpx.ReadError("连接被重置", request=request)
        return _success_response(json.loads(request.content))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        results = await VbasBatchClient(
            http,
            LeaseClient(),
            config=VbasBatchConfig(
                transient_max_attempts=2,
                transient_retry_base_delay_seconds=0,
                transient_retry_max_delay_seconds=0,
            ),
        ).analyze(
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

    assert [item["image_id"] for item in results] == ["student-0"]
    assert requests == 2
    assert len(acquired) == 2
    assert acquired[0] == acquired[1]
    assert released == ["lease-1", "lease-2"]


@pytest.mark.asyncio
async def test_vbas_empty_timeout_reason_includes_type_instance_and_attempt() -> None:
    class LeaseClient:
        @asynccontextmanager
        async def acquire(self, capability: str, **kwargs):
            del kwargs
            yield CapacityLease(
                "lease-timeout",
                "vbas-gpu2",
                capability,
                "http://vbas:9010",
                datetime.now(UTC) + timedelta(seconds=60),
            )

    def handler(request: httpx.Request) -> httpx.Response:
        raise TimeoutError

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = VbasBatchClient(
            http,
            LeaseClient(),
            config=VbasBatchConfig(
                transient_max_attempts=1,
                transient_retry_base_delay_seconds=0,
                transient_retry_max_delay_seconds=0,
            ),
        )
        with pytest.raises(VbasAdapterError) as captured:
            await client.analyze(
                task_id="course-timeout",
                stream=VisionStream.STUDENT,
                frames=[
                    VbasFrame(
                        "student-0",
                        Path("/data/course/course-timeout/frames/student-0.jpg"),
                        0,
                        0.0,
                    )
                ],
            )

    reason = str(captured.value)
    assert "TimeoutError" in reason
    assert "vbas-gpu2" in reason
    assert "course-timeout-s-0000-" in reason
    assert "attempt=1/1" in reason
