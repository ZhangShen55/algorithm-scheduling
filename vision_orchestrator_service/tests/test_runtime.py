from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from packages.platform_common.kafka import KafkaMessage
from packages.platform_contracts.status import NodeStatus, TaskType
from packages.platform_contracts.vision import VisualEventType
from vision_orchestrator_service.app.application.events import VisualCommandProcessor
from vision_orchestrator_service.app.core.config import VisionSettings
from vision_orchestrator_service.app.infrastructure.runtime import (
    VisionOrchestratorRuntime,
    VisionResources,
    VisualCommandConsumerLoop,
)

from .test_visual_analyzer import _command


class Consumer:
    def __init__(self, messages: list[KafkaMessage]) -> None:
        self.messages = messages
        self.committed: list[int] = []

    async def poll(self, *, timeout_seconds: float) -> list[KafkaMessage]:
        assert timeout_seconds > 0
        messages, self.messages = self.messages, []
        return messages

    async def commit(self, message: KafkaMessage) -> None:
        self.committed.append(message.offset)


def _message(value: bytes, offset: int = 0) -> KafkaMessage:
    return KafkaMessage("visual", 0, offset, None, value, None)


@pytest.mark.asyncio
async def test_consumer_commits_only_after_success_and_skips_invalid_envelope(
    tmp_path,
) -> None:
    valid = _command(
        tmp_path,
        task_type=TaskType.TEACHER_BEHAVIOR,
        strategy={"coarse_interval_seconds": 10},
    ).to_bytes()

    class Processor:
        def __init__(self) -> None:
            self.values: list[bytes] = []

        async def handle(self, value: bytes) -> None:
            self.values.append(value)

    consumer = Consumer([_message(b"not-json"), _message(valid, 1)])
    processor = Processor()
    loop = VisualCommandConsumerLoop(consumer, processor, poll_timeout_seconds=0.1)

    assert await loop.run_once() == 1
    assert consumer.committed == [0, 1]
    assert processor.values == [valid]


@pytest.mark.asyncio
async def test_consumer_does_not_commit_retryable_processing_failure(tmp_path) -> None:
    value = _command(
        tmp_path,
        TaskType.TEACHER_BEHAVIOR,
        strategy={"coarse_interval_seconds": 10},
    ).to_bytes()

    class Processor:
        async def handle(self, value: bytes) -> None:
            del value
            raise RuntimeError("temporary")

    consumer = Consumer([_message(value)])
    loop = VisualCommandConsumerLoop(consumer, Processor(), poll_timeout_seconds=0.1)

    with pytest.raises(RuntimeError, match="temporary"):
        await loop.run_once()
    assert consumer.committed == []


@pytest.mark.asyncio
async def test_completed_node_republishes_terminal_event_without_reanalysis(tmp_path) -> None:
    command = _command(
        tmp_path,
        TaskType.TEACHER_BEHAVIOR,
        strategy={"coarse_interval_seconds": 10},
    )

    class Analyzer:
        async def analyze(self, command, progress):
            raise AssertionError("completed node must not be analyzed again")

    class Repository:
        def get_node(self, node_id: int) -> object:
            return SimpleNamespace(
                id=node_id,
                status=NodeStatus.COMPLETED,
                course_task_type_id=7,
                updated_at=datetime.now(UTC),
            )

    class Producer:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> object:
            del topic, key
            self.sent.append(value)
            return object()

    producer = Producer()
    processor = VisualCommandProcessor(
        Analyzer(),
        Repository(),
        producer,
        event_topic="visual.events",
    )

    await processor.handle(command.to_bytes())

    from packages.platform_contracts.vision import VisualAnalysisEvent

    event = VisualAnalysisEvent.from_bytes(producer.sent[0])
    assert event.event_type is VisualEventType.COMPLETED
    assert "重复发布" in event.reason


@pytest.mark.asyncio
async def test_runtime_lifespan_starts_consumer_and_reports_real_readiness(
    tmp_path,
) -> None:
    class Engine:
        def __init__(self) -> None:
            self.disposed = False

        def dispose(self) -> None:
            self.disposed = True

    class Repository:
        def count_courses(self) -> int:
            return 0

    class Http:
        def __init__(self) -> None:
            self.closed = False

        async def get(self, url: str) -> object:
            assert url.endswith("/health")
            return SimpleNamespace(raise_for_status=lambda: None)

        async def aclose(self) -> None:
            self.closed = True

    class Producer:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False

        async def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            self.stopped = True

    class RuntimeConsumer:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False

        async def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            self.stopped = True

        async def poll(self, *, timeout_seconds: float) -> list[object]:
            await asyncio.sleep(min(timeout_seconds, 0.001))
            return []

        async def commit(self, message: object) -> None:
            del message

        async def lag(self) -> dict[str, int]:
            return {}

    class Topics:
        def __init__(self) -> None:
            self.validated = False

        async def validate_topics(self) -> None:
            self.validated = True

    class Analyzer:
        async def analyze(self, command, progress):
            raise AssertionError("test does not publish a command")

    engine = Engine()
    http = Http()
    producer = Producer()
    consumer = RuntimeConsumer()
    topics = Topics()
    resources = VisionResources(
        engine=engine,
        repository=Repository(),
        http_client=http,
        producer=producer,
        consumer=consumer,
        topic_manager=topics,
    )
    settings = VisionSettings(
        worker={"shutdown_timeout_seconds": 1},
        kafka={"poll_timeout_seconds": 0.01},
        storage={
            "course_root": tmp_path / "course",
            "result_root": tmp_path / "result",
        },
    )
    runtime = VisionOrchestratorRuntime(
        settings,
        resource_factory=lambda selected: resources,
        analyzer_factory=lambda selected: Analyzer(),
    )
    app = FastAPI()
    runtime.attach(app)

    async with runtime.lifespan(app):
        readiness = await runtime.readiness()
        assert readiness["status"] == "ready"
        assert readiness["checks"]["visual_command_consumer"]["ready"] is True
        assert topics.validated is True
        assert producer.started is True
        assert consumer.started is True

    assert consumer.stopped is True
    assert producer.stopped is True
    assert http.closed is True
    assert engine.disposed is True
