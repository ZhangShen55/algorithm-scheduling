from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI
from packages.platform_common.kafka import KafkaMessage
from packages.platform_contracts.status import NodeStatus, TaskType
from packages.platform_contracts.vision import (
    VisualAnalysisCommand,
    VisualAnalysisEvent,
    VisualEventType,
)

from vision_orchestrator_service.app.application.events import VisualCommandProcessor
from vision_orchestrator_service.app.core.config import VisionSettings
from vision_orchestrator_service.app.infrastructure.capacity import (
    CapacityUnavailableError,
)
from vision_orchestrator_service.app.infrastructure.media import VideoFrameError
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


def test_runtime_passes_worker_retry_delay_and_shutdown_event_to_vbas(tmp_path) -> None:
    settings = VisionSettings(
        worker={"poll_interval_seconds": 0.125},
        storage={
            "course_root": tmp_path / "course",
            "result_root": tmp_path / "result",
        },
    )
    runtime = VisionOrchestratorRuntime(settings)
    resources = VisionResources(
        engine=object(),
        repository=object(),
        http_client=object(),
        producer=object(),
        consumer=object(),
        topic_manager=object(),
    )

    analyzer = runtime._build_analyzer(resources)

    assert analyzer._vbas._config.capacity_retry_delay_seconds == 0.125
    assert analyzer._vbas._shutdown_event is runtime.stop_event


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
async def test_consumer_limits_concurrent_course_processing(tmp_path) -> None:
    values = [
        replace(
            _command(
                tmp_path,
                TaskType.TEACHER_BEHAVIOR,
                strategy={"coarse_interval_seconds": 10},
            ),
            command_id=UUID(f"00000000-0000-0000-0000-{index:012d}"),
            node_id=100 + index,
        ).to_bytes()
        for index in range(1, 5)
    ]
    active = 0
    peak = 0
    two_active = asyncio.Event()
    release = asyncio.Event()

    class Processor:
        async def handle(self, value: bytes) -> None:
            nonlocal active, peak
            del value
            active += 1
            peak = max(peak, active)
            if active == 2:
                two_active.set()
            try:
                await release.wait()
            finally:
                active -= 1

    consumer = Consumer([_message(value, index) for index, value in enumerate(values)])
    loop = VisualCommandConsumerLoop(
        consumer,
        Processor(),
        poll_timeout_seconds=0.1,
        concurrency=2,
    )
    task = asyncio.create_task(loop.run_once())
    await asyncio.wait_for(two_active.wait(), timeout=1)

    assert peak == 2
    assert consumer.committed == []

    release.set()
    assert await asyncio.wait_for(task, timeout=1) == 4
    assert consumer.committed == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_consumer_does_not_commit_past_unfinished_partition_offset(
    tmp_path,
) -> None:
    first = _command(
        tmp_path,
        TaskType.TEACHER_BEHAVIOR,
        strategy={"coarse_interval_seconds": 10},
    ).to_bytes()
    second = replace(
        VisualAnalysisCommand.from_bytes(first),
        command_id=UUID("00000000-0000-0000-0000-000000000222"),
        node_id=222,
    ).to_bytes()
    release_first = asyncio.Event()
    second_finished = asyncio.Event()

    class Processor:
        async def handle(self, value: bytes) -> None:
            command = VisualAnalysisCommand.from_bytes(value)
            if command.node_id == 222:
                second_finished.set()
                return
            await release_first.wait()

    consumer = Consumer([_message(first, 10), _message(second, 11)])
    loop = VisualCommandConsumerLoop(
        consumer,
        Processor(),
        poll_timeout_seconds=0.1,
        concurrency=2,
    )
    task = asyncio.create_task(loop.run_once())
    await asyncio.wait_for(second_finished.wait(), timeout=1)
    await asyncio.sleep(0)

    assert consumer.committed == []

    release_first.set()
    assert await asyncio.wait_for(task, timeout=1) == 2
    assert consumer.committed == [10, 11]


@pytest.mark.asyncio
async def test_consumer_retries_capacity_unavailable_without_losing_message(
    tmp_path,
) -> None:
    value = _command(
        tmp_path,
        TaskType.TEACHER_BEHAVIOR,
        strategy={"coarse_interval_seconds": 10},
    ).to_bytes()

    class Processor:
        def __init__(self) -> None:
            self.calls = 0

        async def handle(self, value: bytes) -> None:
            del value
            self.calls += 1
            if self.calls < 3:
                raise CapacityUnavailableError("capacity unavailable")

    consumer = Consumer([_message(value)])
    processor = Processor()
    loop = VisualCommandConsumerLoop(
        consumer,
        processor,
        poll_timeout_seconds=0.1,
        retry_delay_seconds=0.001,
    )

    assert await loop.run_once(stop_event=asyncio.Event()) == 1
    assert processor.calls == 3
    assert consumer.committed == [0]


@pytest.mark.asyncio
async def test_consumer_shutdown_stops_capacity_retry_without_commit(tmp_path) -> None:
    value = _command(
        tmp_path,
        TaskType.STUDENT_BEHAVIOR,
        strategy={"coarse_interval_seconds": 10},
    ).to_bytes()

    class Processor:
        async def handle(self, value: bytes) -> None:
            del value
            raise CapacityUnavailableError("capacity unavailable")

    consumer = Consumer([_message(value)])
    loop = VisualCommandConsumerLoop(
        consumer,
        Processor(),
        poll_timeout_seconds=0.1,
        retry_delay_seconds=0.01,
    )
    stop_event = asyncio.Event()
    task = asyncio.create_task(loop.run_once(stop_event=stop_event))
    await asyncio.sleep(0)
    stop_event.set()

    assert await task == 0
    assert consumer.committed == []


@pytest.mark.asyncio
async def test_consumer_loop_stays_alive_while_waiting_for_capacity(tmp_path) -> None:
    value = _command(
        tmp_path,
        TaskType.TEACHER_BEHAVIOR,
        strategy={"coarse_interval_seconds": 10},
    ).to_bytes()

    class Processor:
        def __init__(self) -> None:
            self.called = asyncio.Event()

        async def handle(self, value: bytes) -> None:
            del value
            self.called.set()
            raise CapacityUnavailableError("capacity unavailable")

    consumer = Consumer([_message(value)])
    processor = Processor()
    loop = VisualCommandConsumerLoop(
        consumer,
        processor,
        poll_timeout_seconds=0.1,
        retry_delay_seconds=0.01,
    )
    stop_event = asyncio.Event()
    task = asyncio.create_task(loop.run(stop_event))
    await asyncio.wait_for(processor.called.wait(), timeout=1)

    assert task.done() is False
    assert consumer.committed == []

    stop_event.set()
    await asyncio.wait_for(task, timeout=1)
    assert consumer.committed == []


@pytest.mark.parametrize(
    "analysis_error",
    (
        VideoFrameError("ffmpeg 无法解码课程视频"),
        ValueError("视觉视频文件不存在"),
        TypeError("视觉扫描策略字段类型不合法"),
        KeyError("VBas 单帧结果缺少字段"),
        FileNotFoundError("视觉证据图片不存在"),
    ),
)
@pytest.mark.asyncio
async def test_processor_persists_expected_analysis_failure_and_loop_continues(
    tmp_path,
    analysis_error: Exception,
) -> None:
    failed_command = _command(
        tmp_path,
        TaskType.TEACHER_BEHAVIOR,
        strategy={"coarse_interval_seconds": 10},
    )
    completed_command = replace(
        failed_command,
        command_id=UUID("00000000-0000-0000-0000-000000000202"),
        node_id=32,
    )

    class Analyzer:
        async def analyze(self, command, progress):
            del progress
            if command.node_id == failed_command.node_id:
                raise analysis_error
            return {"analyzed": True}

    class Repository:
        def __init__(self) -> None:
            self.nodes = {
                failed_command.node_id: SimpleNamespace(
                    id=failed_command.node_id,
                    status=NodeStatus.RUNNING,
                    course_task_type_id=7,
                ),
                completed_command.node_id: SimpleNamespace(
                    id=completed_command.node_id,
                    status=NodeStatus.RUNNING,
                    course_task_type_id=8,
                ),
            }
            self.transitions: list[tuple[int, NodeStatus, str]] = []
            self.aggregated: list[int] = []

        def get_node(self, node_id: int) -> object:
            return self.nodes[node_id]

        def transition_node(
            self,
            node_id: int,
            status: NodeStatus,
            reason: str,
        ) -> object:
            self.transitions.append((node_id, status, reason))
            node = self.nodes[node_id]
            node.status = status
            return node

        def complete_node(self, node_id: int, result, *, reason: str) -> object:
            del result, reason
            node = self.nodes[node_id]
            node.status = NodeStatus.COMPLETED
            return node

        def update_node_progress(self, node_id, progress, *, reason):
            raise AssertionError((node_id, progress, reason))

        def aggregate_task_type_state(self, course_task_type_id: int) -> object:
            self.aggregated.append(course_task_type_id)
            return object()

    class Producer:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> object:
            del topic, key
            self.sent.append(value)
            return object()

    class WaitingConsumer(Consumer):
        def __init__(self, messages: list[KafkaMessage]) -> None:
            super().__init__(messages)
            self.all_committed = asyncio.Event()

        async def poll(self, *, timeout_seconds: float) -> list[KafkaMessage]:
            await asyncio.sleep(min(timeout_seconds, 0.001))
            return await super().poll(timeout_seconds=timeout_seconds)

        async def commit(self, message: KafkaMessage) -> None:
            await super().commit(message)
            if len(self.committed) == 2:
                self.all_committed.set()

    repository = Repository()
    producer = Producer()
    processor = VisualCommandProcessor(
        Analyzer(),
        repository,
        producer,
        event_topic="visual.events",
    )
    consumer = WaitingConsumer(
        [
            _message(failed_command.to_bytes()),
            _message(completed_command.to_bytes(), 1),
        ]
    )
    loop = VisualCommandConsumerLoop(consumer, processor, poll_timeout_seconds=0.01)
    stop_event = asyncio.Event()
    task = asyncio.create_task(loop.run(stop_event))
    await asyncio.wait_for(consumer.all_committed.wait(), timeout=1)

    assert task.done() is False
    assert consumer.committed == [0, 1]
    assert repository.nodes[failed_command.node_id].status is NodeStatus.FAILED
    assert repository.nodes[completed_command.node_id].status is NodeStatus.COMPLETED
    assert repository.transitions == [
        (
            failed_command.node_id,
            NodeStatus.FAILED,
            f"视觉分析失败: {analysis_error}",
        )
    ]
    assert repository.aggregated == [7, 8]
    event = VisualAnalysisEvent.from_bytes(producer.sent[0])
    assert event.node_id == completed_command.node_id
    assert event.event_type is VisualEventType.COMPLETED

    stop_event.set()
    await asyncio.wait_for(task, timeout=1)


@pytest.mark.parametrize("failure_source", ("repository", "producer"))
@pytest.mark.asyncio
async def test_processor_does_not_turn_progress_infrastructure_failure_into_task_failure(
    tmp_path,
    failure_source: str,
) -> None:
    command = _command(
        tmp_path,
        TaskType.TEACHER_BEHAVIOR,
        strategy={"coarse_interval_seconds": 10},
    )

    class Analyzer:
        async def analyze(self, command, progress):
            del command
            await progress(5, "视频校验", "正在校验本地视觉视频")
            return {"analyzed": True}

    class Repository:
        def __init__(self) -> None:
            self.node = SimpleNamespace(
                id=command.node_id,
                status=NodeStatus.RUNNING,
                course_task_type_id=7,
            )
            self.transitions: list[tuple[int, NodeStatus, str]] = []

        def get_node(self, node_id: int) -> object:
            assert node_id == command.node_id
            return self.node

        def update_node_progress(self, node_id, progress, *, reason):
            del node_id, progress, reason
            if failure_source == "repository":
                raise ValueError("进度事实写入失败")
            return self.node

        def transition_node(self, node_id, status, reason):
            self.transitions.append((node_id, status, reason))
            return self.node

    class Producer:
        async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> object:
            del topic, value, key
            if failure_source == "producer":
                raise ValueError("视觉进度事件发布失败")
            return object()

    repository = Repository()
    processor = VisualCommandProcessor(
        Analyzer(),
        repository,
        Producer(),
        event_topic="visual.events",
    )

    with pytest.raises(RuntimeError, match="视觉进度基础设施处理失败"):
        await processor.handle(command.to_bytes())

    assert repository.node.status is NodeStatus.RUNNING
    assert repository.transitions == []


@pytest.mark.asyncio
async def test_processor_preserves_capacity_unavailable_for_consumer_retry(
    tmp_path,
) -> None:
    command = _command(
        tmp_path,
        TaskType.TEACHER_BEHAVIOR,
        strategy={"coarse_interval_seconds": 10},
    )

    class Analyzer:
        async def analyze(self, command, progress):
            del command, progress
            raise CapacityUnavailableError("capacity unavailable")

    class Repository:
        def get_node(self, node_id: int) -> object:
            return SimpleNamespace(
                id=node_id,
                status=NodeStatus.RUNNING,
                course_task_type_id=7,
            )

        def transition_node(self, node_id, status, reason):
            raise AssertionError((node_id, status, reason))

    processor = VisualCommandProcessor(
        Analyzer(),
        Repository(),
        object(),
        event_topic="visual.events",
    )

    with pytest.raises(CapacityUnavailableError, match="capacity unavailable"):
        await processor.handle(command.to_bytes())


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
