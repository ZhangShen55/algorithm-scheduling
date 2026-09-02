from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from packages.platform_common.kafka import KafkaMessage
from packages.platform_common.repository import (
    NodeRecord,
    RepositoryStateConflictError,
    TaskTypeRecord,
)
from packages.platform_contracts.status import NodeStatus, Priority, TaskType
from packages.platform_contracts.vision import (
    VisualAnalysisCommand,
    VisualAnalysisEvent,
    VisualEventType,
)

from orchestrator_service.app.application.vision_events import (
    VisualCommandPublisher,
    VisualEventConsumerLoop,
    VisualEventProcessor,
    VisualNodeCoordinator,
)


def _node(status: NodeStatus = NodeStatus.PENDING) -> NodeRecord:
    return NodeRecord(
        id=31,
        course_task_type_id=7,
        node_code="TEACHER_BEHAVIOR_ANALYSIS",
        status=status,
        priority=Priority.URGENT,
        reason="等待教师行为视觉分析",
        required_capability=None,
        result=None,
        artifact_path=None,
        artifact_count=None,
        progress={},
        effective_params=None,
        updated_at=datetime.now(UTC),
        claimed_by="worker-visual",
        claim_token=UUID("00000000-0000-0000-0000-000000000031"),
        attempt=1,
    )


def _task(task_type: TaskType = TaskType.TEACHER_BEHAVIOR) -> TaskTypeRecord:
    payload: dict[str, object]
    if task_type is TaskType.TEACHER_BEHAVIOR:
        payload = {"teacher_video_path": "http://media/teacher.mp4"}
    else:
        payload = {
            "student_video_path": "http://media/student.mp4",
            "student_count": 38,
            "front_points": [{"X": 0, "Y": 0}],
        }
    return TaskTypeRecord(
        id=7,
        submission_id="submission-shared-001",
        task_id="course-001",
        task_type=task_type,
        status=NodeStatus.PENDING,
        priority=Priority.URGENT,
        reason="等待处理",
        request_payload=payload,
        effective_params=None,
        created=False,
        updated_at=datetime.now(UTC),
    )


class Repository:
    def __init__(
        self,
        *,
        node: NodeRecord | None = None,
        task: TaskTypeRecord | None = None,
        running: list[NodeRecord] | None = None,
    ) -> None:
        self.node = node
        self.task = task or _task()
        self.running = list(running or [])
        self.transitions: list[tuple[int, NodeStatus, str]] = []
        self.aggregated: list[int] = []
        self.progress: list[tuple[int, dict[str, object], str]] = []
        self.claims: list[str] = []

    def resume_visual_nodes(self) -> int:
        return 0

    def claim_ready_visual_node(self, worker_id: str) -> NodeRecord | None:
        self.claims.append(worker_id)
        selected, self.node = self.node, None
        return selected

    def list_running_visual_nodes(self) -> list[NodeRecord]:
        return self.running

    def get_task_type(self, course_task_type_id: int) -> TaskTypeRecord:
        assert course_task_type_id == self.task.id
        return self.task

    def get_node(self, node_id: int) -> NodeRecord:
        assert node_id == 31
        if self.running:
            return self.running[0]
        return _node(NodeStatus.COMPLETED)

    def transition_node(
        self,
        node_id: int,
        status: NodeStatus,
        reason: str,
    ) -> NodeRecord:
        self.transitions.append((node_id, status, reason))
        return replace(_node(), status=status, reason=reason)

    def update_node_progress(
        self,
        node_id: int,
        progress: dict[str, object],
        *,
        reason: str,
    ) -> NodeRecord:
        self.progress.append((node_id, progress, reason))
        return replace(_node(), status=NodeStatus.RUNNING, progress=progress)

    def aggregate_task_type_state(self, course_task_type_id: int) -> TaskTypeRecord:
        self.aggregated.append(course_task_type_id)
        self.task = replace(self.task, status=NodeStatus.COMPLETED)
        return self.task


class RecordingWorkspaceCleaner:
    def __init__(self) -> None:
        self.task_ids: list[str] = []

    def cleanup_if_terminal(self, task_id: str) -> bool:
        self.task_ids.append(task_id)
        return True


class ProgressTerminalRaceRepository(Repository):
    def __init__(self, terminal_status: NodeStatus = NodeStatus.COMPLETED) -> None:
        super().__init__(running=[_node(NodeStatus.RUNNING)])
        self._reads = 0
        self._terminal_status = terminal_status

    def get_node(self, node_id: int) -> NodeRecord:
        assert node_id == 31
        self._reads += 1
        if self._reads == 1:
            return _node(NodeStatus.RUNNING)
        return _node(self._terminal_status)

    def update_node_progress(
        self,
        node_id: int,
        progress: dict[str, object],
        *,
        reason: str,
    ) -> NodeRecord:
        raise RepositoryStateConflictError(
            f"只有处理中节点可以更新进度: {node_id}"
        )


class ProgressFailureRepository(ProgressTerminalRaceRepository):
    def get_node(self, node_id: int) -> NodeRecord:
        assert node_id == 31
        return _node(NodeStatus.RUNNING)


class Downloader:
    def __init__(self, tmp_path: Path) -> None:
        self.path = (tmp_path / "teacher.mp4").resolve()
        self.path.write_bytes(b"video")
        self.calls: list[tuple[str, str, str, str | None]] = []

    async def download(
        self,
        task_id: str,
        source_url: str,
        media_role: str,
        *,
        download_group_id: str | None = None,
    ) -> object:
        self.calls.append((task_id, source_url, media_role, download_group_id))
        return SimpleNamespace(path=self.path)


class FailingOnceDownloader(Downloader):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.failures_remaining = 1

    async def download(
        self,
        task_id: str,
        source_url: str,
        media_role: str,
        *,
        download_group_id: str | None = None,
    ) -> object:
        self.calls.append((task_id, source_url, media_role, download_group_id))
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("媒体不可用")
        return SimpleNamespace(path=self.path)


class FailingDownloader(Downloader):
    async def download(
        self,
        task_id: str,
        source_url: str,
        media_role: str,
        *,
        download_group_id: str | None = None,
    ) -> object:
        self.calls.append((task_id, source_url, media_role, download_group_id))
        raise RuntimeError("媒体不可用")


class Producer:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.sent: list[tuple[str, bytes, bytes]] = []

    async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> object:
        self.sent.append((topic, value, key))
        if self.error is not None:
            raise self.error
        return object()


@pytest.mark.asyncio
async def test_visual_node_claim_prepares_shared_submission_and_publishes(tmp_path: Path) -> None:
    repository = Repository(node=_node())
    downloader = Downloader(tmp_path)
    producer = Producer()
    coordinator = VisualNodeCoordinator(
        repository,
        downloader,
        VisualCommandPublisher(producer, topic="algorithm.visual.commands"),
        worker_id="worker-visual",
    )

    assert await coordinator.run_once() == 1

    assert repository.claims == ["worker-visual"]
    assert downloader.calls == [
        (
            "course-001",
            "http://media/teacher.mp4",
            "teacher",
            "submission-shared-001",
        )
    ]
    assert repository.transitions[0][1] is NodeStatus.RUNNING
    command = VisualAnalysisCommand.from_bytes(producer.sent[0][1])
    assert command.local_video_path == str(downloader.path)
    assert command.submission_id == "submission-shared-001"
    assert command.dispatch_attempt == 1
    assert command.claim_token == UUID("00000000-0000-0000-0000-000000000031")
    assert producer.sent[0][2] == b"course-001:TEACHER_BEHAVIOR"


@pytest.mark.asyncio
async def test_visual_media_failure_is_terminal_and_next_node_can_run(tmp_path: Path) -> None:
    repository = Repository(node=_node())
    downloader = FailingOnceDownloader(tmp_path)
    producer = Producer()
    workspace_cleaner = RecordingWorkspaceCleaner()
    coordinator = VisualNodeCoordinator(
        repository,
        downloader,
        VisualCommandPublisher(producer, topic="visual"),
        worker_id="worker-visual",
        workspace_cleaner=workspace_cleaner,
    )

    assert await coordinator.run_once() == 1
    assert [status for _, status, _ in repository.transitions] == [
        NodeStatus.RUNNING,
        NodeStatus.FAILED,
    ]
    assert repository.aggregated == [7]
    assert producer.sent == []
    assert workspace_cleaner.task_ids == ["course-001"]

    repository.node = _node()
    assert await coordinator.run_once() == 1
    assert [status for _, status, _ in repository.transitions] == [
        NodeStatus.RUNNING,
        NodeStatus.FAILED,
        NodeStatus.RUNNING,
    ]
    assert len(producer.sent) == 1


@pytest.mark.asyncio
async def test_visual_recovery_republishes_same_stable_command_without_reclaim(
    tmp_path: Path,
) -> None:
    repository = Repository(running=[_node(NodeStatus.RUNNING)])
    downloader = Downloader(tmp_path)
    producer = Producer()
    coordinator = VisualNodeCoordinator(
        repository,
        downloader,
        VisualCommandPublisher(producer, topic="visual"),
        worker_id="worker-visual",
    )

    assert await coordinator.recover() == 1
    first = VisualAnalysisCommand.from_bytes(producer.sent[0][1])
    assert await coordinator.recover() == 1
    second = VisualAnalysisCommand.from_bytes(producer.sent[1][1])

    assert first.command_id == second.command_id
    assert first.command_id.version == 5
    assert repository.claims == []
    assert repository.transitions == []


@pytest.mark.asyncio
async def test_visual_new_claim_generation_changes_command_identity(tmp_path: Path) -> None:
    first_node = _node(NodeStatus.RUNNING)
    second_node = replace(
        first_node,
        attempt=2,
        claim_token=UUID("00000000-0000-0000-0000-000000000032"),
    )
    repository = Repository(running=[first_node])
    producer = Producer()
    coordinator = VisualNodeCoordinator(
        repository,
        Downloader(tmp_path),
        VisualCommandPublisher(producer, topic="visual"),
        worker_id="worker-visual",
    )

    assert await coordinator.recover() == 1
    repository.running = [second_node]
    assert await coordinator.recover() == 1
    first = VisualAnalysisCommand.from_bytes(producer.sent[0][1])
    second = VisualAnalysisCommand.from_bytes(producer.sent[1][1])

    assert first.command_id != second.command_id
    assert (first.dispatch_attempt, second.dispatch_attempt) == (1, 2)
    assert first.claim_token != second.claim_token


@pytest.mark.asyncio
async def test_visual_recovery_rejects_missing_claim_identity(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = Repository(
        running=[
            replace(
                _node(NodeStatus.RUNNING),
                attempt=0,
                claim_token=None,
            )
        ]
    )
    producer = Producer()
    coordinator = VisualNodeCoordinator(
        repository,
        Downloader(tmp_path),
        VisualCommandPublisher(producer, topic="visual"),
        worker_id="worker-visual",
    )

    assert await coordinator.recover() == 0
    assert producer.sent == []
    assert repository.transitions == []
    assert "缺少领取身份" in caplog.text


@pytest.mark.asyncio
async def test_visual_recovery_terminalizes_unrecoverable_media(tmp_path: Path) -> None:
    repository = Repository(running=[_node(NodeStatus.RUNNING)])
    coordinator = VisualNodeCoordinator(
        repository,
        FailingDownloader(tmp_path),
        VisualCommandPublisher(Producer(), topic="visual"),
        worker_id="worker-visual",
    )

    assert await coordinator.recover() == 1
    assert [status for _, status, _ in repository.transitions] == [NodeStatus.FAILED]
    assert repository.aggregated == [7]


@pytest.mark.asyncio
async def test_visual_publish_failure_moves_running_node_to_retry_wait(tmp_path: Path) -> None:
    repository = Repository(node=_node())
    coordinator = VisualNodeCoordinator(
        repository,
        Downloader(tmp_path),
        VisualCommandPublisher(Producer(error=RuntimeError("kafka unavailable")), topic="visual"),
        worker_id="worker-visual",
    )

    with pytest.raises(RuntimeError, match="kafka unavailable"):
        await coordinator.run_once()

    assert [status for _, status, _ in repository.transitions] == [
        NodeStatus.RUNNING,
        NodeStatus.WAITING_OPERATOR,
    ]


def _event(event_type: VisualEventType, *, progress: int = 100) -> bytes:
    command = VisualAnalysisCommand(
        command_id=UUID("00000000-0000-0000-0000-000000000101"),
        task_id="course-001",
        task_type=TaskType.TEACHER_BEHAVIOR,
        node_id=31,
        submission_id="submission-shared-001",
        local_video_path="/data/course/course-001/teacher.mp4",
        priority=Priority.URGENT,
        dispatch_attempt=1,
        claim_token=UUID("00000000-0000-0000-0000-000000000031"),
    )
    return VisualAnalysisEvent.create(
        command,
        event_type=event_type,
        progress=progress,
        stage="完成" if event_type is VisualEventType.COMPLETED else "粗粒度扫描",
        reason="视觉分析完成" if event_type is VisualEventType.COMPLETED else "扫描中",
    ).to_bytes()


@pytest.mark.asyncio
async def test_visual_events_are_idempotent_for_progress_and_completion() -> None:
    running_repository = Repository(running=[_node(NodeStatus.RUNNING)])
    progress_processor = VisualEventProcessor(running_repository)
    value = _event(VisualEventType.PROGRESS, progress=30)
    await progress_processor.handle(value)
    await progress_processor.handle(value)
    assert running_repository.progress == [
        (31, {"percent": 30, "stage": "粗粒度扫描"}, "扫描中"),
        (31, {"percent": 30, "stage": "粗粒度扫描"}, "扫描中"),
    ]

    completed_repository = Repository()
    workspace_cleaner = RecordingWorkspaceCleaner()
    completed_processor = VisualEventProcessor(
        completed_repository,
        workspace_cleaner=workspace_cleaner,
    )
    terminal = _event(VisualEventType.COMPLETED)
    await completed_processor.handle(terminal)
    await completed_processor.handle(terminal)
    assert completed_repository.aggregated == [7]
    assert workspace_cleaner.task_ids == ["course-001", "course-001"]


@pytest.mark.asyncio
async def test_progress_repository_error_remains_fatal_while_node_is_running() -> None:
    processor = VisualEventProcessor(ProgressFailureRepository())

    with pytest.raises(
        RepositoryStateConflictError,
        match="只有处理中节点可以更新进度: 31",
    ):
        await processor.handle(_event(VisualEventType.PROGRESS, progress=95))


@pytest.mark.parametrize(
    "terminal_status",
    (NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.CANCELLED),
)
@pytest.mark.asyncio
async def test_late_progress_is_committed_after_terminal_race(
    terminal_status: NodeStatus,
) -> None:
    repository = ProgressTerminalRaceRepository(terminal_status)
    consumer = Consumer(
        [
            KafkaMessage(
                "visual",
                0,
                10,
                None,
                _event(VisualEventType.PROGRESS, progress=95),
                None,
            ),
        ]
    )
    loop = VisualEventConsumerLoop(
        consumer,
        VisualEventProcessor(repository),
        poll_timeout_seconds=0.1,
    )

    assert await loop.run_once() == 1
    assert consumer.committed == [10]
    assert repository.progress == []


@pytest.mark.parametrize(
    "terminal_status",
    (NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.CANCELLED),
)
@pytest.mark.asyncio
async def test_late_progress_is_committed_when_node_is_already_terminal(
    terminal_status: NodeStatus,
) -> None:
    repository = Repository(running=[_node(terminal_status)])
    consumer = Consumer(
        [
            KafkaMessage(
                "visual",
                0,
                10,
                None,
                _event(VisualEventType.PROGRESS, progress=95),
                None,
            ),
        ]
    )
    loop = VisualEventConsumerLoop(
        consumer,
        VisualEventProcessor(repository),
        poll_timeout_seconds=0.1,
    )

    assert await loop.run_once() == 1
    assert consumer.committed == [10]
    assert repository.progress == []


@pytest.mark.asyncio
async def test_late_progress_is_committed_and_consumer_continues_to_terminal() -> None:
    repository = ProgressTerminalRaceRepository()
    consumer = Consumer(
        [
            KafkaMessage(
                "visual",
                0,
                10,
                None,
                _event(VisualEventType.PROGRESS, progress=95),
                None,
            ),
            KafkaMessage(
                "visual",
                0,
                11,
                None,
                _event(VisualEventType.COMPLETED),
                None,
            ),
        ]
    )
    loop = VisualEventConsumerLoop(
        consumer,
        VisualEventProcessor(repository),
        poll_timeout_seconds=0.1,
    )

    assert await loop.run_once() == 2
    assert consumer.committed == [10, 11]
    assert repository.progress == []
    assert repository.aggregated == [7]

    consumer.messages.append(
        KafkaMessage(
            "visual",
            0,
            12,
            None,
            _event(VisualEventType.COMPLETED),
            None,
        )
    )
    assert await loop.run_once() == 1
    assert consumer.committed == [10, 11, 12]
    assert repository.aggregated == [7]


@pytest.mark.asyncio
async def test_mismatched_visual_event_remains_uncommitted_and_fatal() -> None:
    payload = json.loads(_event(VisualEventType.COMPLETED))
    payload["payload"]["task_id"] = "another-course"
    consumer = Consumer(
        [
            KafkaMessage(
                "visual",
                0,
                20,
                None,
                json.dumps(payload).encode(),
                None,
            )
        ]
    )
    loop = VisualEventConsumerLoop(
        consumer,
        VisualEventProcessor(Repository()),
        poll_timeout_seconds=0.1,
    )

    with pytest.raises(ValueError, match="视觉事件与任务事实不一致"):
        await loop.run_once()

    assert consumer.committed == []


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


@pytest.mark.asyncio
async def test_visual_event_consumer_skips_invalid_and_commits_after_processing() -> None:
    consumer = Consumer(
        [
            KafkaMessage("visual", 0, 0, None, b"invalid", None),
            KafkaMessage(
                "visual",
                0,
                1,
                None,
                _event(VisualEventType.COMPLETED),
                None,
            ),
        ]
    )
    loop = VisualEventConsumerLoop(
        consumer,
        VisualEventProcessor(Repository()),
        poll_timeout_seconds=0.1,
    )

    assert await loop.run_once() == 1
    assert consumer.committed == [0, 1]
