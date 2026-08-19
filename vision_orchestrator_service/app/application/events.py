import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from packages.platform_common.repository import NodeResultWrite
from packages.platform_contracts.status import NodeStatus
from packages.platform_contracts.vision import (
    VisualAnalysisCommand,
    VisualAnalysisEvent,
    VisualEventType,
)

ProgressCallback = Callable[[int, str, str], Awaitable[None]]


class VisualAnalyzer(Protocol):
    async def analyze(
        self,
        command: VisualAnalysisCommand,
        progress: ProgressCallback,
    ) -> dict[str, Any] | NodeResultWrite: ...


class VisualResultRepository(Protocol):
    def update_node_progress(
        self,
        node_id: int,
        progress: dict[str, Any],
        *,
        reason: str,
    ) -> object: ...

    def complete_node(
        self,
        node_id: int,
        result: NodeResultWrite,
        *,
        reason: str,
    ) -> object: ...

    def get_node(self, node_id: int) -> object: ...

    def aggregate_task_type_state(self, course_task_type_id: int) -> object: ...


class AsyncKafkaProducer(Protocol):
    async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> object: ...


class VisualCommandProcessor:
    def __init__(
        self,
        analyzer: VisualAnalyzer,
        repository: VisualResultRepository,
        producer: AsyncKafkaProducer,
        *,
        event_topic: str,
    ) -> None:
        self._analyzer = analyzer
        self._repository = repository
        self._producer = producer
        self._event_topic = event_topic

    async def handle(self, value: bytes) -> None:
        command = VisualAnalysisCommand.from_bytes(value)
        get_node = getattr(self._repository, "get_node", None)
        existing = (
            await asyncio.to_thread(get_node, command.node_id)
            if callable(get_node)
            else None
        )
        if getattr(existing, "status", None) is NodeStatus.COMPLETED:
            await self._publish(
                VisualAnalysisEvent.create(
                    command,
                    event_type=VisualEventType.COMPLETED,
                    progress=100,
                    stage="完成",
                    reason="视觉分析已完成，重复发布终态事件",
                )
            )
            return

        async def report(progress: int, stage: str, reason: str) -> None:
            await asyncio.to_thread(
                self._repository.update_node_progress,
                command.node_id,
                {"percent": progress, "stage": stage},
                reason=reason,
            )
            await self._publish(
                VisualAnalysisEvent.create(
                    command,
                    event_type=VisualEventType.PROGRESS,
                    progress=progress,
                    stage=stage,
                    reason=reason,
                )
            )

        analyzed = await self._analyzer.analyze(command, report)
        if isinstance(analyzed, NodeResultWrite):
            result = NodeResultWrite(
                result=analyzed.result,
                artifact_path=analyzed.artifact_path,
                artifact_count=analyzed.artifact_count,
                progress={"percent": 100, "stage": "完成"},
                effective_params=analyzed.effective_params,
            )
        else:
            result = NodeResultWrite(
                result=analyzed,
                progress={"percent": 100, "stage": "完成"},
            )
        await asyncio.to_thread(
            self._repository.complete_node,
            command.node_id,
            result,
            reason="视觉分析完成",
        )
        aggregate = getattr(self._repository, "aggregate_task_type_state", None)
        if callable(aggregate):
            course_task_type_id = getattr(existing, "course_task_type_id", None)
            if course_task_type_id is None and callable(get_node):
                completed = await asyncio.to_thread(get_node, command.node_id)
                course_task_type_id = getattr(completed, "course_task_type_id", None)
            if isinstance(course_task_type_id, int):
                await asyncio.to_thread(aggregate, course_task_type_id)
        await self._publish(
            VisualAnalysisEvent.create(
                command,
                event_type=VisualEventType.COMPLETED,
                progress=100,
                stage="完成",
                reason="视觉分析完成",
            )
        )

    async def _publish(self, event: VisualAnalysisEvent) -> None:
        key = f"{event.task_id}:{event.task_type.value}".encode()
        await self._producer.send_and_wait(
            self._event_topic,
            event.to_bytes(),
            key,
        )
