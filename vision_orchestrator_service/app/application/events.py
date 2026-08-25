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

from ..domain.adaptive_scan import AdaptiveScanError
from ..infrastructure.capacity import (
    CapacityLeaseClientError,
    CapacityUnavailableError,
)
from ..infrastructure.media import VideoFrameError
from ..infrastructure.vbas import VbasAdapterError

ProgressCallback = Callable[[int, str, str], Awaitable[None]]

_TERMINAL_ANALYSIS_ERRORS = (
    AdaptiveScanError,
    CapacityLeaseClientError,
    VbasAdapterError,
    VideoFrameError,
    FileNotFoundError,
    KeyError,
    TypeError,
    ValueError,
)


class _ProgressDeliveryError(RuntimeError):
    pass


class VisualAnalyzer(Protocol):
    async def analyze(
        self,
        command: VisualAnalysisCommand,
        progress: ProgressCallback,
    ) -> dict[str, Any] | NodeResultWrite: ...


class VisualResultRepository(Protocol):
    def transition_node(
        self,
        node_id: int,
        status: NodeStatus,
        reason: str,
    ) -> object: ...

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
        if getattr(existing, "status", None) is NodeStatus.FAILED:
            await self._aggregate(existing)
            return

        async def report(progress: int, stage: str, reason: str) -> None:
            try:
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
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # 进度存储或事件发布失败属于基础设施故障，不能伪装成单任务分析失败。
                raise _ProgressDeliveryError(
                    f"视觉进度基础设施处理失败: {exc}"
                ) from exc

        try:
            analyzed = await self._analyzer.analyze(command, report)
        except CapacityUnavailableError:
            raise
        except _TERMINAL_ANALYSIS_ERRORS as exc:
            failed = await asyncio.to_thread(
                self._repository.transition_node,
                command.node_id,
                NodeStatus.FAILED,
                f"视觉分析失败: {exc}",
            )
            await self._aggregate(failed, fallback=existing)
            return
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
        await self._aggregate(existing)
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

    async def _aggregate(self, node: object, *, fallback: object = None) -> None:
        aggregate = getattr(self._repository, "aggregate_task_type_state", None)
        if not callable(aggregate):
            return
        course_task_type_id = getattr(node, "course_task_type_id", None)
        if course_task_type_id is None:
            course_task_type_id = getattr(fallback, "course_task_type_id", None)
        if isinstance(course_task_type_id, int):
            await asyncio.to_thread(aggregate, course_task_type_id)
