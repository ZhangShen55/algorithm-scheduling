import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from packages.platform_common.repository import NodeResultWrite
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
    ) -> dict[str, Any]: ...


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

        result = await self._analyzer.analyze(command, report)
        await asyncio.to_thread(
            self._repository.complete_node,
            command.node_id,
            NodeResultWrite(
                result=result,
                progress={"percent": 100, "stage": "完成"},
            ),
            reason="视觉分析完成",
        )
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
