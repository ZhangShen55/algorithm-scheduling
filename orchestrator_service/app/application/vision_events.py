from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from packages.platform_common.repository import (
    NodeRecord,
    RepositoryStateConflictError,
    TaskTypeRecord,
)
from packages.platform_contracts.status import NodeStatus, TaskType
from packages.platform_contracts.vision import (
    VisualAnalysisCommand,
    VisualAnalysisEvent,
    VisualEventType,
)

JsonObject = dict[str, Any]


class AsyncKafkaProducer(Protocol):
    async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> object: ...


class VisualCommandPublisher:
    def __init__(self, producer: AsyncKafkaProducer, *, topic: str) -> None:
        self._producer = producer
        self._topic = topic

    async def publish(self, command: VisualAnalysisCommand) -> None:
        key = f"{command.task_id}:{command.task_type.value}".encode()
        await self._producer.send_and_wait(
            self._topic,
            command.to_bytes(),
            key,
        )


class VisualDispatchRepository(Protocol):
    def resume_visual_nodes(self) -> int: ...

    def claim_ready_visual_node(self, worker_id: str) -> NodeRecord | None: ...

    def list_running_visual_nodes(self) -> list[NodeRecord]: ...

    def get_task_type(self, course_task_type_id: int) -> TaskTypeRecord: ...

    def transition_node(
        self,
        node_id: int,
        status: NodeStatus,
        reason: str,
    ) -> NodeRecord: ...

    def aggregate_task_type_state(self, course_task_type_id: int) -> TaskTypeRecord: ...


class VisualMediaDownloader(Protocol):
    async def download(
        self,
        task_id: str,
        source_url: str,
        media_role: str,
        *,
        download_group_id: str | None = None,
    ) -> object: ...


class VisualNodeCoordinator:
    """Prepare shared media and publish one idempotent command per visual node."""

    def __init__(
        self,
        repository: VisualDispatchRepository,
        media_downloader: VisualMediaDownloader,
        publisher: VisualCommandPublisher,
        *,
        worker_id: str,
    ) -> None:
        self._repository = repository
        self._media_downloader = media_downloader
        self._publisher = publisher
        self._worker_id = worker_id

    async def run_once(self) -> int:
        await asyncio.to_thread(self._repository.resume_visual_nodes)
        node = await asyncio.to_thread(
            self._repository.claim_ready_visual_node,
            self._worker_id,
        )
        if node is None:
            return 0
        await asyncio.to_thread(
            self._repository.transition_node,
            node.id,
            NodeStatus.RUNNING,
            "视觉节点已领取，正在准备本地视频",
        )
        try:
            command = await self._command(node)
        except asyncio.CancelledError:
            await self._transition_for_retry(
                node,
                NodeStatus.WAITING_OPERATOR,
                "视觉命令准备被中断，等待重试",
            )
            raise
        except Exception:
            await self._transition_for_retry(
                node,
                NodeStatus.FAILED,
                "视觉命令准备失败，任务已终止",
            )
            return 1
        try:
            await self._publisher.publish(command)
        except asyncio.CancelledError:
            await self._transition_for_retry(
                node,
                NodeStatus.WAITING_OPERATOR,
                "视觉命令发布失败，等待 Kafka 恢复",
            )
            raise
        except Exception:
            await self._transition_for_retry(
                node,
                NodeStatus.WAITING_OPERATOR,
                "视觉命令发布失败，等待 Kafka 恢复",
            )
            raise
        await asyncio.to_thread(
            self._repository.aggregate_task_type_state,
            node.course_task_type_id,
        )
        return 1

    async def recover(self) -> int:
        recovered = 0
        nodes = await asyncio.to_thread(self._repository.list_running_visual_nodes)
        for node in nodes:
            try:
                command = await self._command(node)
            except asyncio.CancelledError:
                await self._transition_for_retry(
                    node,
                    NodeStatus.WAITING_OPERATOR,
                    "视觉运行中节点恢复被中断，等待重试",
                )
                raise
            except Exception:
                await self._transition_for_retry(
                    node,
                    NodeStatus.FAILED,
                    "视觉运行中节点媒体准备失败，任务已终止",
                )
                recovered += 1
                continue
            try:
                await self._publisher.publish(command)
            except BaseException:
                await self._transition_for_retry(
                    node,
                    NodeStatus.WAITING_OPERATOR,
                    "视觉运行中节点恢复发布失败，等待重试",
                )
                raise
            recovered += 1
        return recovered

    async def _transition_for_retry(
        self,
        node: NodeRecord,
        status: NodeStatus,
        reason: str,
    ) -> None:
        await asyncio.to_thread(
            self._repository.transition_node,
            node.id,
            status,
            reason,
        )
        await asyncio.to_thread(
            self._repository.aggregate_task_type_state,
            node.course_task_type_id,
        )

    async def _command(self, node: NodeRecord) -> VisualAnalysisCommand:
        task = await asyncio.to_thread(
            self._repository.get_task_type,
            node.course_task_type_id,
        )
        source_field, media_role = _visual_source(task.task_type)
        source_url = task.request_payload.get(source_field)
        if not isinstance(source_url, str) or not source_url:
            raise RuntimeError(f"视觉任务缺少 {source_field}")
        if not task.submission_id:
            raise RuntimeError("视觉任务缺少 submission_id")
        downloaded = await self._media_downloader.download(
            task.task_id,
            source_url,
            media_role,
            download_group_id=task.submission_id,
        )
        local_path = getattr(downloaded, "path", None)
        if not isinstance(local_path, Path) or not local_path.is_absolute():
            raise RuntimeError("视觉任务媒体下载器未返回绝对本地路径")
        student_count: int | None = None
        front_points: list[JsonObject] | None = None
        back_point: list[JsonObject] | None = None
        if task.task_type is TaskType.STUDENT_BEHAVIOR:
            count = task.request_payload.get("student_count")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise RuntimeError("学生行为任务缺少有效 student_count")
            student_count = count
            front_points = _optional_regions(
                task.request_payload.get("front_points"),
                "front_points",
            )
            back_point = _optional_regions(
                task.request_payload.get("back_point"),
                "back_point",
            )
        strategy = task.effective_params if isinstance(task.effective_params, dict) else {}
        return VisualAnalysisCommand(
            command_id=uuid5(
                NAMESPACE_URL,
                f"algorithm-visual:{task.submission_id}:{node.id}",
            ),
            task_id=task.task_id,
            task_type=task.task_type,
            node_id=node.id,
            submission_id=task.submission_id,
            local_video_path=str(local_path),
            priority=task.priority,
            strategy=strategy,
            student_count=student_count,
            front_points=front_points,
            back_point=back_point,
        )


class VisualEventRepository(Protocol):
    def get_node(self, node_id: int) -> NodeRecord: ...

    def get_task_type(self, course_task_type_id: int) -> TaskTypeRecord: ...

    def update_node_progress(
        self,
        node_id: int,
        progress: JsonObject,
        *,
        reason: str,
    ) -> NodeRecord: ...

    def aggregate_task_type_state(self, course_task_type_id: int) -> TaskTypeRecord: ...


class VisualEventProcessor:
    """Idempotently acknowledge state already persisted by the vision service."""

    def __init__(self, repository: VisualEventRepository) -> None:
        self._repository = repository

    async def handle(self, value: bytes) -> None:
        event = VisualAnalysisEvent.from_bytes(value)
        node = await asyncio.to_thread(self._repository.get_node, event.node_id)
        task = await asyncio.to_thread(
            self._repository.get_task_type,
            node.course_task_type_id,
        )
        if task.task_id != event.task_id or task.task_type is not event.task_type:
            raise ValueError("视觉事件与任务事实不一致")
        if event.event_type is VisualEventType.PROGRESS:
            if node.status is NodeStatus.COMPLETED:
                return
            if node.status is not NodeStatus.RUNNING:
                raise RuntimeError("视觉进度事件对应节点不在处理中")
            try:
                await asyncio.to_thread(
                    self._repository.update_node_progress,
                    node.id,
                    {"percent": event.progress, "stage": event.stage},
                    reason=event.reason,
                )
            except RepositoryStateConflictError:
                # Vision Orchestrator persists the terminal result before publishing
                # its completion event. A previously published progress event can
                # therefore be consumed after the node has already completed. Re-read
                # the node under that race and acknowledge only the completed state;
                # all other repository validation failures remain fatal.
                current = await asyncio.to_thread(
                    self._repository.get_node,
                    node.id,
                )
                if current.status is NodeStatus.COMPLETED:
                    return
                raise
            return
        if node.status is not NodeStatus.COMPLETED:
            raise RuntimeError("视觉终态事件早于结果持久化")
        if task.status is NodeStatus.COMPLETED:
            return
        if task.status in {NodeStatus.FAILED, NodeStatus.CANCELLED}:
            raise RuntimeError("视觉终态事件与任务类型终态不一致")
        await asyncio.to_thread(
            self._repository.aggregate_task_type_state,
            node.course_task_type_id,
        )


class VisualEventConsumer(Protocol):
    async def poll(self, *, timeout_seconds: float) -> list[Any]: ...

    async def commit(self, message: Any) -> None: ...


class VisualEventConsumerLoop:
    def __init__(
        self,
        consumer: VisualEventConsumer,
        processor: VisualEventProcessor,
        *,
        poll_timeout_seconds: float,
    ) -> None:
        self._consumer = consumer
        self._processor = processor
        self._poll_timeout_seconds = poll_timeout_seconds

    async def run_once(self) -> int:
        messages = await self._consumer.poll(timeout_seconds=self._poll_timeout_seconds)
        handled = 0
        for message in messages:
            try:
                VisualAnalysisEvent.from_bytes(message.value)
            except ValueError:
                await self._consumer.commit(message)
                continue
            await self._processor.handle(message.value)
            await self._consumer.commit(message)
            handled += 1
        return handled

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self.run_once()


def _visual_source(task_type: TaskType) -> tuple[str, str]:
    if task_type is TaskType.TEACHER_BEHAVIOR:
        return "teacher_video_path", "teacher"
    if task_type is TaskType.STUDENT_BEHAVIOR:
        return "student_video_path", "student"
    raise RuntimeError(f"不支持的视觉任务类型: {task_type.value}")


def _optional_regions(value: object, field_name: str) -> list[JsonObject] | None:
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise RuntimeError(f"{field_name} 必须是对象列表")
    return value
