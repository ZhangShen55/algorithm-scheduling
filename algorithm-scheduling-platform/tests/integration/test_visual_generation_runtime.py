from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

import pytest
from aiokafka.admin import AIOKafkaAdminClient  # type: ignore[import-untyped]
from orchestrator_service.app.application.pipeline import pipeline_nodes
from orchestrator_service.app.application.vision_events import (
    VisualCommandPublisher,
    VisualNodeCoordinator,
)
from sqlalchemy import Engine, text
from vision_orchestrator_service.app.application.events import VisualCommandProcessor
from vision_orchestrator_service.app.infrastructure.runtime import VisualCommandConsumerLoop

from packages.platform_common.kafka import (
    AioKafkaConsumerAdapter,
    AioKafkaProducerAdapter,
    KafkaMessage,
    KafkaTopicManager,
)
from packages.platform_common.repository import CourseRepository, TaskTypeWrite
from packages.platform_contracts.status import NodeStatus, Priority, TaskType
from packages.platform_contracts.vision import VisualAnalysisCommand

pytestmark = pytest.mark.integration


def _bootstrap_servers() -> list[str]:
    value = os.getenv("PLATFORM_TEST_KAFKA_BOOTSTRAP", "127.0.0.1:9092")
    return [item.strip() for item in value.split(",") if item.strip()]


async def _poll_until(
    loop: VisualCommandConsumerLoop,
    expected: int,
    *,
    timeout_seconds: float = 10,
) -> int:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    handled = 0
    while handled < expected and asyncio.get_running_loop().time() < deadline:
        handled += await loop.run_once()
    assert handled == expected
    return handled


async def _poll_command(
    consumer: AioKafkaConsumerAdapter,
    *,
    timeout_seconds: float = 10,
) -> KafkaMessage:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        messages = await consumer.poll(timeout_seconds=0.25)
        if messages:
            return messages[0]
    raise AssertionError("在截止时间内未收到视觉命令")


def _running_visual(repository: CourseRepository, *, task_id: str):
    task = repository.create_task_types(
        task_id=task_id,
        writes=[
            TaskTypeWrite(
                task_type=TaskType.TEACHER_BEHAVIOR,
                priority=Priority.NORMAL,
                request_payload={"teacher_video_path": "http://media/teacher.mp4"},
                effective_params={"coarse_interval_seconds": 10},
            )
        ],
    )[0]
    repository.initialize_pipeline(
        task_id,
        TaskType.TEACHER_BEHAVIOR,
        pipeline_nodes(TaskType.TEACHER_BEHAVIOR, Priority.NORMAL),
    )
    claimed = repository.claim_ready_visual_node("visual-integration-worker")
    assert claimed is not None
    repository.transition_node(claimed.id, NodeStatus.RUNNING, "视觉集成测试运行中")
    return task, repository.get_node(claimed.id)


def _command(task, node, media_path: Path) -> VisualAnalysisCommand:
    assert node.claim_token is not None
    return VisualAnalysisCommand(
        command_id=uuid4(),
        task_id=task.task_id,
        task_type=task.task_type,
        node_id=node.id,
        submission_id=task.submission_id,
        local_video_path=str(media_path),
        priority=task.priority,
        dispatch_attempt=node.attempt,
        claim_token=node.claim_token,
        strategy=task.effective_params,
    )


@dataclass(frozen=True)
class _Downloaded:
    path: Path


class _Downloader:
    def __init__(self, path: Path) -> None:
        self._path = path

    async def download(self, *args, **kwargs) -> _Downloaded:
        del args, kwargs
        return _Downloaded(self._path)


class _Analyzer:
    def __init__(self) -> None:
        self.node_ids: list[int] = []

    async def analyze(self, command, progress):
        del progress
        self.node_ids.append(command.node_id)
        return {"intervals": []}


@pytest.mark.asyncio
async def test_real_postgres_kafka_visual_generation_recovery_and_redelivery(
    milestone1_postgres,
    tmp_path: Path,
) -> None:
    engine: Engine = milestone1_postgres.engine
    with engine.begin() as connection:
        for table in (
            "outbox_events",
            "node_results",
            "task_node_dependencies",
            "task_nodes",
            "course_task_types",
            "course_jobs",
        ):
            connection.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
    repository = CourseRepository(engine)
    media_path = (tmp_path / "teacher.mp4").resolve()
    media_path.write_bytes(b"fixture")
    suffix = uuid4().hex
    command_topic = f"algorithm.test.visual.commands.{suffix}"
    recovery_topic = f"algorithm.test.visual.recovery.{suffix}"
    event_topic = f"algorithm.test.visual.events.{suffix}"
    group_id = f"algorithm-test-visual-{suffix}"
    bootstrap = _bootstrap_servers()
    manager = KafkaTopicManager(
        bootstrap_servers=bootstrap,
        client_id=f"visual-admin-{suffix[:8]}",
        topics=(command_topic, recovery_topic, event_topic),
    )
    producer = AioKafkaProducerAdapter(
        bootstrap_servers=bootstrap,
        client_id=f"visual-producer-{suffix[:8]}",
    )
    consumer = AioKafkaConsumerAdapter(
        topics=[command_topic],
        bootstrap_servers=bootstrap,
        group_id=group_id,
        client_id=f"visual-consumer-{suffix[:8]}",
        max_poll_records=10,
    )
    producer_started = False
    consumer_started = False
    try:
        await manager.ensure_topics()
        await producer.start()
        producer_started = True
        await consumer.start()
        consumer_started = True

        task, first_generation = _running_visual(
            repository,
            task_id=f"visual-generation-{suffix}",
        )
        stale_command = _command(task, first_generation, media_path)
        replacement_token = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE task_nodes SET attempt = attempt + 1, claim_token = :claim_token "
                    "WHERE id = :node_id"
                ),
                {"node_id": first_generation.id, "claim_token": replacement_token},
            )
        current_generation = repository.get_node(first_generation.id)
        current_command = replace(
            _command(task, current_generation, media_path),
            command_id=uuid4(),
        )
        analyzer = _Analyzer()
        processor = VisualCommandProcessor(
            analyzer,
            repository,
            producer,
            event_topic=event_topic,
        )
        loop = VisualCommandConsumerLoop(
            consumer,
            processor,
            poll_timeout_seconds=0.1,
            concurrency=2,
        )
        await producer.send_and_wait(command_topic, stale_command.to_bytes(), b"stale")
        await producer.send_and_wait(command_topic, current_command.to_bytes(), b"current")
        await _poll_until(loop, 2)
        assert analyzer.node_ids == [current_generation.id]
        assert repository.get_node(current_generation.id).status is NodeStatus.COMPLETED

        terminal_payload = current_command.to_bytes()
        await producer.send_and_wait(command_topic, terminal_payload, b"terminal-redelivery")
        first_delivery = await _poll_command(consumer)
        assert first_delivery.value == terminal_payload
        await consumer.stop()
        consumer_started = False

        resumed = AioKafkaConsumerAdapter(
            topics=[command_topic],
            bootstrap_servers=bootstrap,
            group_id=group_id,
            client_id=f"visual-resumed-{suffix[:8]}",
            max_poll_records=10,
        )
        await resumed.start()
        try:
            resumed_loop = VisualCommandConsumerLoop(
                resumed,
                processor,
                poll_timeout_seconds=0.1,
            )
            await _poll_until(resumed_loop, 1)
        finally:
            await resumed.stop()
        assert analyzer.node_ids == [current_generation.id]

        recovery_task, recovery_node = _running_visual(
            repository,
            task_id=f"visual-recovery-{suffix}",
        )
        coordinator = VisualNodeCoordinator(
            repository,
            _Downloader(media_path),
            VisualCommandPublisher(producer, topic=recovery_topic),
            worker_id="visual-recovery-worker",
        )
        assert await coordinator.recover() == 1
        assert await coordinator.recover() == 1
        observer = AioKafkaConsumerAdapter(
            topics=[recovery_topic],
            bootstrap_servers=bootstrap,
            group_id=f"algorithm-test-visual-recovery-{suffix}",
            client_id=f"visual-observer-{suffix[:8]}",
            max_poll_records=1,
        )
        await observer.start()
        try:
            first_message = await _poll_command(observer)
            first = VisualAnalysisCommand.from_bytes(first_message.value)
            await observer.commit(first_message)
            second = VisualAnalysisCommand.from_bytes((await _poll_command(observer)).value)
        finally:
            await observer.stop()
        assert first.command_id == second.command_id
        assert first.dispatch_attempt == second.dispatch_attempt == recovery_node.attempt
        assert first.claim_token == second.claim_token == recovery_node.claim_token
        assert first.submission_id == recovery_task.submission_id
    finally:
        if consumer_started:
            await consumer.stop()
        if producer_started:
            await producer.stop()
        admin = AIOKafkaAdminClient(
            bootstrap_servers=bootstrap,
            client_id=f"visual-cleanup-{suffix[:8]}",
        )
        await admin.start()
        try:
            await admin.delete_topics([command_topic, recovery_topic, event_topic])
        finally:
            await admin.close()
