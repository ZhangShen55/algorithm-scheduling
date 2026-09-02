import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from packages.platform_common.repository import (
    NodeResultWrite,
    VisualCommandDisposition,
    VisualCommandResult,
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


class _VisualCommandSuperseded(RuntimeError):
    """分析期间命令已陈旧或节点已终结，当前执行不得继续写入。"""


class VisualAnalyzer(Protocol):
    async def analyze(
        self,
        command: VisualAnalysisCommand,
        progress: ProgressCallback,
    ) -> dict[str, Any] | NodeResultWrite: ...


class VisualResultRepository(Protocol):
    def inspect_visual_command(
        self,
        node_id: int,
        *,
        task_id: str,
        submission_id: str,
        dispatch_attempt: int,
        claim_token: object,
    ) -> VisualCommandResult: ...

    def update_visual_progress_if_current(
        self,
        node_id: int,
        progress: dict[str, Any],
        *,
        reason: str,
        task_id: str,
        submission_id: str,
        dispatch_attempt: int,
        claim_token: object,
    ) -> VisualCommandResult: ...

    def complete_visual_node_if_current(
        self,
        node_id: int,
        result: NodeResultWrite,
        *,
        reason: str,
        task_id: str,
        submission_id: str,
        dispatch_attempt: int,
        claim_token: object,
    ) -> VisualCommandResult: ...

    def fail_visual_node_if_current(
        self,
        node_id: int,
        *,
        reason: str,
        task_id: str,
        submission_id: str,
        dispatch_attempt: int,
        claim_token: object,
    ) -> VisualCommandResult: ...


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
        identity = {
            "task_id": command.task_id,
            "submission_id": command.submission_id,
            "dispatch_attempt": command.dispatch_attempt,
            "claim_token": command.claim_token,
        }
        admission = await asyncio.to_thread(
            self._repository.inspect_visual_command,
            command.node_id,
            **identity,
        )
        if admission.disposition is VisualCommandDisposition.STALE:
            return
        if admission.disposition is VisualCommandDisposition.TERMINAL:
            if admission.status is not NodeStatus.COMPLETED:
                return
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
        if admission.disposition is not VisualCommandDisposition.CURRENT:
            raise RuntimeError(
                f"未知视觉命令准入结果: {admission.disposition}"
            )

        async def report(progress: int, stage: str, reason: str) -> None:
            try:
                updated = await asyncio.to_thread(
                    self._repository.update_visual_progress_if_current,
                    command.node_id,
                    {"percent": progress, "stage": stage},
                    reason=reason,
                    **identity,
                )
                if updated.disposition in {
                    VisualCommandDisposition.STALE,
                    VisualCommandDisposition.TERMINAL,
                }:
                    raise _VisualCommandSuperseded
                if updated.disposition is not VisualCommandDisposition.APPLIED:
                    raise _ProgressDeliveryError(
                        f"未知视觉进度写入结果: {updated.disposition}"
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
            except _VisualCommandSuperseded:
                raise
            except Exception as exc:
                # 进度存储或事件发布失败属于基础设施故障，不能伪装成单任务分析失败。
                raise _ProgressDeliveryError(
                    f"视觉进度基础设施处理失败: {exc}"
                ) from exc

        try:
            analyzed = await self._analyzer.analyze(command, report)
        except _VisualCommandSuperseded:
            return
        except CapacityUnavailableError:
            raise
        except _TERMINAL_ANALYSIS_ERRORS as exc:
            failed = await asyncio.to_thread(
                self._repository.fail_visual_node_if_current,
                command.node_id,
                reason=f"视觉分析失败: {exc}",
                **identity,
            )
            if failed.disposition not in {
                VisualCommandDisposition.APPLIED,
                VisualCommandDisposition.STALE,
                VisualCommandDisposition.TERMINAL,
            }:
                raise RuntimeError(
                    f"未知视觉失败写入结果: {failed.disposition}"
                )
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
        completed = await asyncio.to_thread(
            self._repository.complete_visual_node_if_current,
            command.node_id,
            result,
            reason="视觉分析完成",
            **identity,
        )
        if completed.disposition in {
            VisualCommandDisposition.STALE,
            VisualCommandDisposition.TERMINAL,
        }:
            return
        if completed.disposition is not VisualCommandDisposition.APPLIED:
            raise RuntimeError(
                f"未知视觉完成写入结果: {completed.disposition}"
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
