import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from packages.platform_common.repository import (
    NodeResultWrite,
    RepositoryStateConflictError,
)
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


class _TerminalNodeRace(RuntimeError):
    """分析期间节点已由其他事务终结，当前命令无需继续执行。"""


_TERMINAL_NODE_STATUSES = frozenset(
    {
        NodeStatus.COMPLETED,
        NodeStatus.FAILED,
        NodeStatus.CANCELLED,
    }
)


def _is_terminal_status(value: object) -> bool:
    try:
        return NodeStatus(value) in _TERMINAL_NODE_STATUSES
    except (TypeError, ValueError):
        return False


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
        if getattr(existing, "status", None) in {
            NodeStatus.FAILED,
            NodeStatus.CANCELLED,
        }:
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
            except RepositoryStateConflictError as exc:
                if callable(get_node):
                    try:
                        current = await asyncio.to_thread(
                            get_node,
                            command.node_id,
                        )
                    except Exception as read_exc:
                        raise _ProgressDeliveryError(
                            f"视觉进度状态确认失败: {read_exc}"
                        ) from read_exc
                    if _is_terminal_status(getattr(current, "status", None)):
                        raise _TerminalNodeRace from exc
                # 只有确认节点已经进入终态时才幂等，其他冲突必须可见。
                raise _ProgressDeliveryError(
                    f"视觉进度基础设施处理失败: {exc}"
                ) from exc
            except Exception as exc:
                # 进度存储或事件发布失败属于基础设施故障，不能伪装成单任务分析失败。
                raise _ProgressDeliveryError(
                    f"视觉进度基础设施处理失败: {exc}"
                ) from exc

        try:
            analyzed = await self._analyzer.analyze(command, report)
        except _TerminalNodeRace:
            # 其他事务已经决定节点终态，迟到的分析结果不能覆盖该决定。
            return
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
        try:
            await asyncio.to_thread(
                self._repository.complete_node,
                command.node_id,
                result,
                reason="视觉分析完成",
            )
        except RepositoryStateConflictError:
            if callable(get_node):
                current = await asyncio.to_thread(get_node, command.node_id)
                if _is_terminal_status(getattr(current, "status", None)):
                    # 结果已由并发事务落库，当前处理器不重复写入或发布事件。
                    return
            raise
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
