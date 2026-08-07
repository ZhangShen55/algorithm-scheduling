import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest

from packages.platform_common.repository import NodeResultWrite
from packages.platform_contracts.status import Priority, TaskType
from packages.platform_contracts.vision import (
    VisualAnalysisCommand,
    VisualAnalysisEvent,
    VisualEventType,
)
from services.orchestrator_service.vision_events import VisualCommandPublisher
from services.vision_orchestrator_service.capacity import CapacityLeaseHttpClient
from services.vision_orchestrator_service.events import VisualCommandProcessor


class RecordingProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes, bytes]] = []

    async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> object:
        self.sent.append((topic, value, key))
        return object()


def teacher_command() -> VisualAnalysisCommand:
    return VisualAnalysisCommand(
        command_id=UUID("00000000-0000-0000-0000-000000000101"),
        task_id="course-vision-001",
        task_type=TaskType.TEACHER_BEHAVIOR,
        node_id=31,
        submission_id="submission-001",
        local_video_path="/data/course/course-vision-001/teacher.mp4",
        priority=Priority.URGENT,
        strategy={"coarse_interval_seconds": 30},
    )


@pytest.mark.asyncio
async def test_orchestrator_publishes_course_level_visual_command() -> None:
    producer = RecordingProducer()
    publisher = VisualCommandPublisher(
        producer,
        topic="algorithm.visual.commands",
    )

    await publisher.publish(teacher_command())

    assert len(producer.sent) == 1
    topic, value, key = producer.sent[0]
    payload = json.loads(value)
    assert topic == "algorithm.visual.commands"
    assert key == b"course-vision-001:TEACHER_BEHAVIOR"
    assert payload["event_type"] == "VISUAL_ANALYSIS_REQUESTED"
    assert payload["payload"]["local_video_path"].startswith("/data/course/")
    assert not any(
        field in payload["payload"]
        for field in ("image", "image_base64", "frame_bytes", "video_bytes")
    )


@pytest.mark.asyncio
async def test_vision_service_consumes_command_and_publishes_progress_and_completion() -> None:
    producer = RecordingProducer()

    class ResultRepository:
        progress_updates: list[tuple[int, dict[str, object], str]] = []
        completed: NodeResultWrite | None = None

        def update_node_progress(
            self,
            node_id: int,
            progress: dict[str, object],
            *,
            reason: str,
        ) -> object:
            self.progress_updates.append((node_id, progress, reason))
            return object()

        def complete_node(
            self,
            node_id: int,
            result: NodeResultWrite,
            *,
            reason: str,
        ) -> object:
            assert node_id == 31
            assert reason == "视觉分析完成"
            self.completed = result
            return object()

    repository = ResultRepository()

    class Analyzer:
        async def analyze(
            self,
            command: VisualAnalysisCommand,
            progress: Any,
        ) -> dict[str, object]:
            assert command.task_type is TaskType.TEACHER_BEHAVIOR
            await progress(20, "粗粒度扫描", "教师视频粗粒度扫描中")
            await progress(80, "边界细化", "正在细化教师行为区间")
            return {"writing_intervals": []}

    processor = VisualCommandProcessor(
        Analyzer(),
        repository,
        producer,
        event_topic="algorithm.visual.events",
    )

    await processor.handle(teacher_command().to_bytes())

    events = [VisualAnalysisEvent.from_bytes(value) for _, value, _ in producer.sent]
    assert [event.event_type for event in events] == [
        VisualEventType.PROGRESS,
        VisualEventType.PROGRESS,
        VisualEventType.COMPLETED,
    ]
    assert [event.progress for event in events] == [20, 80, 100]
    assert all(event.task_id == "course-vision-001" for event in events)
    assert all(event.node_id == 31 for event in events)
    assert producer.sent[-1][2] == b"course-vision-001:TEACHER_BEHAVIOR"
    assert repository.progress_updates == [
        (
            31,
            {"percent": 20, "stage": "粗粒度扫描"},
            "教师视频粗粒度扫描中",
        ),
        (
            31,
            {"percent": 80, "stage": "边界细化"},
            "正在细化教师行为区间",
        ),
    ]
    assert repository.completed is not None
    assert repository.completed.result == {"writing_intervals": []}


def test_visual_command_requires_selected_stream_local_path() -> None:
    payload = json.loads(teacher_command().to_bytes())
    payload["payload"]["local_video_path"] = "http://media/teacher.mp4"

    with pytest.raises(ValueError, match="local_video_path 必须是绝对本地路径"):
        VisualAnalysisCommand.from_bytes(json.dumps(payload).encode())


@pytest.mark.asyncio
async def test_vision_vbas_calls_use_control_service_capacity_lease() -> None:
    captured: list[tuple[str, dict[str, object]]] = []
    expires_at = datetime.now(UTC) + timedelta(seconds=60)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append((request.url.path, body))
        if request.url.path.endswith("/lease"):
            return httpx.Response(
                200,
                json={
                    "lease_id": "lease-vbas-001",
                    "instance_id": "vbas-gpu0",
                    "capability": "teacher_behavior",
                    "service_url": "http://vbas-gpu0:9010",
                    "expires_at": expires_at.isoformat(),
                },
            )
        return httpx.Response(
            200,
            json={"lease_id": "lease-vbas-001", "status": "RELEASED"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        lease_client = CapacityLeaseHttpClient(
            http,
            control_service_url="http://control-service:8000",
        )
        async with lease_client.acquire("teacher_behavior", ttl_seconds=90) as lease:
            assert lease.instance_id == "vbas-gpu0"
            assert lease.service_url == "http://vbas-gpu0:9010"

    assert captured == [
        (
            "/internal/operator-instances/lease",
            {"capability": "teacher_behavior", "ttl_seconds": 90},
        ),
        (
            "/internal/operator-instances/release",
            {"lease_id": "lease-vbas-001"},
        ),
    ]
