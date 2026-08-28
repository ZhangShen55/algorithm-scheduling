from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from orchestrator_service.app.application.dispatcher import (
    CapacityUnavailableError,
    LeaseAwareDispatcher,
)
from orchestrator_service.app.application.executor import NodeExecutor
from orchestrator_service.app.domain.ppt_work import PptSliceAsyncAccepted
from orchestrator_service.app.infrastructure.contract_stub import NodeExecutionContext
from packages.platform_common.operator_registry import CapacityLease, WorkContext
from packages.platform_common.repository import (
    NodeRecord,
    NodeResultWrite,
    TaskTypeRecord,
)
from packages.platform_contracts.status import NodeStatus, Priority, TaskType


def _node(*, status: NodeStatus = NodeStatus.PENDING) -> NodeRecord:
    return NodeRecord(
        id=11,
        course_task_type_id=7,
        node_code="ASR_TRANSCRIPTION",
        status=status,
        priority=Priority.URGENT,
        reason="等待离线语音转写",
        required_capability="asr_offline",
        result=None,
        artifact_path=None,
        artifact_count=None,
        progress={},
        effective_params=None,
        updated_at=datetime.now(UTC),
    )


def _task_type(*, status: NodeStatus = NodeStatus.PENDING) -> TaskTypeRecord:
    return TaskTypeRecord(
        id=7,
        submission_id="submission-001",
        task_id="course-001",
        task_type=TaskType.ASR,
        status=status,
        priority=Priority.URGENT,
        reason="任务已接收，等待处理",
        request_payload={"teacher_video_path": "http://media/teacher.mp4"},
        effective_params={"showSpk": True},
        created=False,
        updated_at=datetime.now(UTC),
    )


class RecordingRepository:
    def __init__(self, node: NodeRecord | None = None) -> None:
        self.node = node
        self.deferred: list[str] = []
        self.resumed: list[str] = []
        self.claimed: list[tuple[str, str]] = []
        self.transitions: list[tuple[int, NodeStatus, str]] = []
        self.completed: list[tuple[int, NodeResultWrite, str]] = []
        self.aggregated: list[int] = []
        self.capabilities_aggregated: list[str] = []
        self.progress_patches: list[tuple[int, dict[str, object], str]] = []

    def list_dispatch_capabilities(self) -> list[str]:
        return ["asr_offline"]

    def defer_capability_nodes(self, capability: str) -> int:
        self.deferred.append(capability)
        return 1

    def resume_capability_nodes(self, capability: str) -> int:
        self.resumed.append(capability)
        return 1

    def aggregate_capability_task_types(self, capability: str) -> list[TaskTypeRecord]:
        self.capabilities_aggregated.append(capability)
        return [_task_type()]

    def coordinate_capability_waiting(self, capability: str) -> list[int]:
        self.deferred.append(capability)
        return [7]

    def claim_ready_node(self, capability: str, worker_id: str) -> NodeRecord | None:
        self.claimed.append((capability, worker_id))
        selected, self.node = self.node, None
        return selected

    def get_task_type(self, course_task_type_id: int) -> TaskTypeRecord:
        assert course_task_type_id == 7
        return _task_type()

    def merge_node_progress(
        self,
        node_id: int,
        progress_patch: dict[str, object],
        *,
        reason: str,
    ) -> NodeRecord:
        self.progress_patches.append((node_id, progress_patch, reason))
        return replace(_node(), id=node_id, progress=dict(progress_patch), reason=reason)

    def transition_node(self, node_id: int, status: NodeStatus, reason: str) -> NodeRecord:
        self.transitions.append((node_id, status, reason))
        return replace(_node(), id=node_id, status=status, reason=reason)

    def complete_node(
        self,
        node_id: int,
        result: NodeResultWrite,
        *,
        reason: str,
    ) -> NodeRecord:
        self.completed.append((node_id, result, reason))
        return replace(_node(), id=node_id, status=NodeStatus.COMPLETED)

    def aggregate_task_type_state(self, course_task_type_id: int) -> TaskTypeRecord:
        self.aggregated.append(course_task_type_id)
        return _task_type(status=NodeStatus.COMPLETED)


class RecordingLeaseClient:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.acquired: list[str] = []
        self.released: list[str] = []
        self.bound: list[tuple[str, WorkContext]] = []
        self.renewed_operations: list[str] = []

    async def acquire(self, capability: str) -> CapacityLease:
        self.acquired.append(capability)
        if self.unavailable:
            raise CapacityUnavailableError(f"暂无可用算子容量: {capability}")
        return CapacityLease(
            lease_id="lease-001",
            instance_id="stub-001",
            capability=capability,
            service_url="http://stub.local",
            expires_at=datetime.now(UTC) + timedelta(seconds=60),
        )

    async def bind_context(
        self,
        lease_id: str,
        work_context: WorkContext,
    ) -> CapacityLease:
        self.bound.append((lease_id, work_context))
        return CapacityLease(
            lease_id=lease_id,
            instance_id="stub-001",
            capability="asr_offline",
            service_url="http://stub.local",
            expires_at=datetime.now(UTC) + timedelta(seconds=60),
            work_context=work_context,
        )

    async def run_with_renewal(
        self,
        lease: CapacityLease,
        operation: Awaitable[Any],
        *,
        hard_timeout_seconds: float,
    ) -> Any:
        assert hard_timeout_seconds == 7_200
        self.renewed_operations.append(lease.lease_id)
        return await operation

    async def release(self, lease_id: str) -> None:
        self.released.append(lease_id)


class RecordingAdapter:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, NodeExecutionContext]] = []

    async def execute(
        self,
        service_url: str,
        context: NodeExecutionContext,
    ) -> NodeResultWrite:
        self.calls.append((service_url, context))
        if self.error is not None:
            raise self.error
        return NodeResultWrite(
            result={"stub": True, "node_code": context.node_code},
            effective_params=context.effective_params,
        )


class RecordingWorkspaceCleaner:
    def __init__(self) -> None:
        self.task_ids: list[str] = []

    def cleanup_if_terminal(self, task_id: str) -> bool:
        self.task_ids.append(task_id)
        return True


class FailingWorkspaceCleaner:
    def cleanup_if_terminal(self, task_id: str) -> bool:
        raise RuntimeError(f"cleanup failed: {task_id}")


@pytest.mark.asyncio
async def test_executor_uses_all_concurrency_slots_for_one_capability() -> None:
    class QueueRepository(RecordingRepository):
        def __init__(self) -> None:
            super().__init__()
            self.nodes = [replace(_node(), id=node_id) for node_id in range(11, 15)]

        def claim_ready_node(
            self,
            capability: str,
            worker_id: str,
        ) -> NodeRecord | None:
            self.claimed.append((capability, worker_id))
            return self.nodes.pop(0) if self.nodes else None

        def transition_node(
            self,
            node_id: int,
            status: NodeStatus,
            reason: str,
        ) -> NodeRecord:
            self.transitions.append((node_id, status, reason))
            return replace(_node(), id=node_id, status=status, reason=reason)

        def complete_node(
            self,
            node_id: int,
            result: NodeResultWrite,
            *,
            reason: str,
        ) -> NodeRecord:
            self.completed.append((node_id, result, reason))
            return replace(_node(), id=node_id, status=NodeStatus.COMPLETED)

    class ConcurrentAdapter(RecordingAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.peak = 0

        async def execute(
            self,
            service_url: str,
            context: NodeExecutionContext,
        ) -> NodeResultWrite:
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(0.02)
                return await super().execute(service_url, context)
            finally:
                self.active -= 1

    repository = QueueRepository()
    adapter = ConcurrentAdapter()
    executor = NodeExecutor(
        repository,
        LeaseAwareDispatcher(repository, RecordingLeaseClient()),
        adapter,
        worker_id="worker-a",
        concurrency=4,
    )

    executed = await executor.run_once()

    assert executed == 4
    assert adapter.peak == 4
    assert len(repository.completed) == 4


@pytest.mark.asyncio
async def test_executor_rotates_concurrency_slots_across_capabilities() -> None:
    class MultiCapabilityRepository(RecordingRepository):
        def list_dispatch_capabilities(self) -> list[str]:
            return ["asr_offline", "ppt_slice", "ocr"]

    repository = MultiCapabilityRepository()
    executor = NodeExecutor(
        repository,
        LeaseAwareDispatcher(repository, RecordingLeaseClient()),
        RecordingAdapter(),
        worker_id="worker-a",
        concurrency=2,
    )

    assert await executor.run_once() == 0
    first_claims = {capability for capability, _ in repository.claimed}
    repository.claimed.clear()
    assert await executor.run_once() == 0
    second_claims = {capability for capability, _ in repository.claimed}

    assert first_claims == {"asr_offline", "ppt_slice"}
    assert second_claims == {"asr_offline", "ocr"}


@pytest.mark.asyncio
async def test_dispatcher_defers_nodes_without_claiming_when_capacity_is_unavailable() -> None:
    repository = RecordingRepository(node=_node())
    leases = RecordingLeaseClient(unavailable=True)
    dispatcher = LeaseAwareDispatcher(repository, leases)

    reservation = await dispatcher.reserve_next("asr_offline", "worker-a")

    assert reservation is None
    assert repository.deferred == ["asr_offline"]
    assert repository.aggregated == [7]
    assert repository.claimed == []
    assert leases.released == []


@pytest.mark.asyncio
async def test_dispatcher_releases_lease_when_no_node_is_ready() -> None:
    repository = RecordingRepository(node=None)
    leases = RecordingLeaseClient()
    dispatcher = LeaseAwareDispatcher(repository, leases)

    reservation = await dispatcher.reserve_next("asr_offline", "worker-a")

    assert reservation is None
    assert repository.resumed == []
    assert repository.claimed == [("asr_offline", "worker-a")]
    assert leases.released == ["lease-001"]
    assert leases.bound == []


@pytest.mark.asyncio
async def test_dispatcher_coordinates_capacity_waiting_once_per_capability_batch() -> None:
    repository = RecordingRepository(node=_node())
    leases = RecordingLeaseClient(unavailable=True)
    dispatcher = LeaseAwareDispatcher(repository, leases)

    reservations = await dispatcher.reserve_many(
        "asr_offline",
        "worker-a",
        limit=16,
    )

    assert reservations == []
    assert leases.acquired == ["asr_offline"] * 16
    assert repository.deferred == ["asr_offline"]
    assert repository.aggregated == [7]
    assert repository.resumed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", ("asr_offline", "ppt_slice"))
async def test_dispatcher_batch_does_not_repeat_global_resume_or_aggregate(
    capability: str,
) -> None:
    class QueueRepository(RecordingRepository):
        def __init__(self) -> None:
            super().__init__()
            self.nodes = [replace(_node(), id=node_id) for node_id in range(1, 17)]

        def claim_ready_node(
            self,
            capability: str,
            worker_id: str,
        ) -> NodeRecord | None:
            self.claimed.append((capability, worker_id))
            return self.nodes.pop() if self.nodes else None

    repository = QueueRepository()
    dispatcher = LeaseAwareDispatcher(repository, RecordingLeaseClient())

    reservations = await dispatcher.reserve_many(capability, "worker-a", limit=16)

    assert len(reservations) == 16
    assert repository.resumed == []
    assert repository.capabilities_aggregated == []
    assert repository.deferred == []


@pytest.mark.asyncio
async def test_ocr_batch_claims_each_outer_node_without_operator_lease() -> None:
    class OcrQueueRepository(RecordingRepository):
        def __init__(self) -> None:
            super().__init__()
            self.nodes = [replace(_node(), id=node_id) for node_id in range(1, 17)]

        def claim_ready_node(
            self,
            capability: str,
            worker_id: str,
        ) -> NodeRecord | None:
            self.claimed.append((capability, worker_id))
            return self.nodes.pop() if self.nodes else None

    repository = OcrQueueRepository()
    leases = RecordingLeaseClient()

    reservations = await LeaseAwareDispatcher(repository, leases).reserve_many(
        "ocr",
        "worker-a",
        limit=16,
    )

    assert len(reservations) == 16
    assert all(item.lease is None for item in reservations)
    assert leases.acquired == []
    assert repository.resumed == []
    assert repository.capabilities_aggregated == []


@pytest.mark.asyncio
async def test_dispatcher_releases_lease_when_node_claim_raises() -> None:
    class FailingClaimRepository(RecordingRepository):
        def claim_ready_node(self, capability: str, worker_id: str) -> NodeRecord | None:
            del capability, worker_id
            raise RuntimeError("节点领取事务失败")

    repository = FailingClaimRepository(node=_node())
    leases = RecordingLeaseClient()
    dispatcher = LeaseAwareDispatcher(repository, leases)

    with pytest.raises(RuntimeError, match="节点领取事务失败"):
        await dispatcher.reserve_next("asr_offline", "worker-a")

    assert leases.released == ["lease-001"]
    assert leases.bound == []


@pytest.mark.asyncio
async def test_dispatcher_releases_lease_when_context_binding_fails() -> None:
    class FailingBindLeaseClient(RecordingLeaseClient):
        async def bind_context(
            self,
            lease_id: str,
            work_context: WorkContext,
        ) -> CapacityLease:
            self.bound.append((lease_id, work_context))
            raise RuntimeError("租约上下文绑定失败")

    repository = RecordingRepository(node=_node())
    leases = FailingBindLeaseClient()
    dispatcher = LeaseAwareDispatcher(repository, leases)

    with pytest.raises(RuntimeError, match="租约上下文绑定失败"):
        await dispatcher.reserve_next("asr_offline", "worker-a")

    assert leases.bound[0][1].task_id == "course-001"
    assert leases.released == ["lease-001"]


@pytest.mark.asyncio
async def test_executor_persists_stub_result_aggregates_and_releases_lease() -> None:
    repository = RecordingRepository(node=_node())
    leases = RecordingLeaseClient()
    adapter = RecordingAdapter()
    workspace_cleaner = RecordingWorkspaceCleaner()
    executor = NodeExecutor(
        repository,
        LeaseAwareDispatcher(repository, leases),
        adapter,
        worker_id="worker-a",
        concurrency=2,
        workspace_cleaner=workspace_cleaner,
    )

    executed = await executor.run_once()

    assert executed == 1
    assert repository.transitions == [(11, NodeStatus.RUNNING, "正在执行节点: ASR_TRANSCRIPTION")]
    assert adapter.calls[0][0] == "http://stub.local"
    assert adapter.calls[0][1].request_payload == {
        "teacher_video_path": "http://media/teacher.mp4"
    }
    assert repository.completed[0][1].result == {
        "stub": True,
        "node_code": "ASR_TRANSCRIPTION",
    }
    assert repository.aggregated == [7]
    assert leases.bound[0][1].task_id == "course-001"
    assert leases.bound[0][1].node_id == "11"
    assert leases.bound[0][1].work_type == "asr_transcription"
    assert leases.renewed_operations == ["lease-001"]
    assert leases.released == ["lease-001", "lease-001"]
    assert workspace_cleaner.task_ids == ["course-001"]


@pytest.mark.asyncio
async def test_workspace_cleanup_failure_does_not_reverse_completed_node() -> None:
    repository = RecordingRepository(node=_node())
    leases = RecordingLeaseClient()
    executor = NodeExecutor(
        repository,
        LeaseAwareDispatcher(repository, leases),
        RecordingAdapter(),
        worker_id="worker-a",
        concurrency=1,
        workspace_cleaner=FailingWorkspaceCleaner(),
    )

    assert await executor.run_once() == 1
    assert len(repository.completed) == 1
    assert repository.aggregated == [7]
    assert leases.released == ["lease-001"]


@pytest.mark.asyncio
async def test_executor_marks_failed_node_then_aggregates_and_releases() -> None:
    repository = RecordingRepository(node=_node())
    leases = RecordingLeaseClient()
    adapter = RecordingAdapter(error=RuntimeError("Stub 业务处理失败"))
    executor = NodeExecutor(
        repository,
        LeaseAwareDispatcher(repository, leases),
        adapter,
        worker_id="worker-a",
        concurrency=1,
    )

    executed = await executor.run_once()

    assert executed == 1
    assert repository.transitions[-1] == (
        11,
        NodeStatus.FAILED,
        "节点执行失败: Stub 业务处理失败",
    )
    assert repository.completed == []
    assert repository.aggregated == [7]
    assert leases.released == ["lease-001"]


@pytest.mark.asyncio
async def test_executor_uses_exception_type_when_error_message_is_empty() -> None:
    repository = RecordingRepository(node=_node())
    leases = RecordingLeaseClient()
    executor = NodeExecutor(
        repository,
        LeaseAwareDispatcher(repository, leases),
        RecordingAdapter(error=RuntimeError()),
        worker_id="worker-a",
        concurrency=1,
    )

    assert await executor.run_once() == 1

    assert repository.transitions[-1] == (
        11,
        NodeStatus.FAILED,
        "节点执行失败: RuntimeError",
    )
    assert repository.aggregated == [7]
    assert leases.released == ["lease-001"]


@pytest.mark.asyncio
async def test_ppt_ocr_coordination_node_has_no_outer_operator_lease() -> None:
    class WorkItemRepository(RecordingRepository):
        def list_dispatch_capabilities(self) -> list[str]:
            return ["ocr"]

    node = _node()
    node = NodeRecord(
        id=node.id,
        course_task_type_id=node.course_task_type_id,
        node_code="PPT_OCR",
        status=node.status,
        priority=node.priority,
        reason=node.reason,
        required_capability="ocr",
        result=node.result,
        artifact_path=node.artifact_path,
        artifact_count=node.artifact_count,
        progress=node.progress,
        effective_params=node.effective_params,
        updated_at=node.updated_at,
    )
    repository = WorkItemRepository(node=node)
    leases = RecordingLeaseClient()
    adapter = RecordingAdapter()
    executor = NodeExecutor(
        repository,
        LeaseAwareDispatcher(repository, leases),
        adapter,
        worker_id="worker-a",
        concurrency=1,
    )

    executed = await executor.run_once()

    assert executed == 1
    assert leases.acquired == []
    assert leases.bound == []
    assert leases.released == []
    assert len(adapter.calls) == 1
    assert adapter.calls[0][0] is None
    assert repository.completed[0][1].result == {
        "stub": True,
        "node_code": "PPT_OCR",
    }


@pytest.mark.asyncio
async def test_work_item_capacity_shortage_returns_node_to_waiting() -> None:
    class WorkItemRepository(RecordingRepository):
        def list_dispatch_capabilities(self) -> list[str]:
            return ["ocr"]

    node = _node()
    node = NodeRecord(
        id=node.id,
        course_task_type_id=node.course_task_type_id,
        node_code="PPT_OCR",
        status=node.status,
        priority=node.priority,
        reason=node.reason,
        required_capability="ocr",
        result=node.result,
        artifact_path=node.artifact_path,
        artifact_count=node.artifact_count,
        progress=node.progress,
        effective_params=node.effective_params,
        updated_at=node.updated_at,
    )
    repository = WorkItemRepository(node=node)
    leases = RecordingLeaseClient()
    adapter = RecordingAdapter(
        error=CapacityUnavailableError("等待算子能力可用: ocr")
    )
    executor = NodeExecutor(
        repository,
        LeaseAwareDispatcher(repository, leases),
        adapter,
        worker_id="worker-a",
        concurrency=1,
    )

    await executor.run_once()

    assert repository.transitions[-1] == (
        node.id,
        NodeStatus.WAITING_OPERATOR,
        "等待算子能力可用: ocr",
    )
    assert repository.completed == []
    assert leases.acquired == []


@pytest.mark.asyncio
async def test_ppt_slice_acceptance_transfers_lease_without_completing_node() -> None:
    class PptRepository(RecordingRepository):
        def list_dispatch_capabilities(self) -> list[str]:
            return ["ppt_slice"]

    class AcceptedAdapter:
        async def execute(
            self,
            service_url: str | None,
            context: NodeExecutionContext,
        ) -> PptSliceAsyncAccepted:
            assert service_url == "http://stub.local"
            return PptSliceAsyncAccepted(
                task_id=context.task_id,
                operator_task_id="ppt-node-11",
                reason="PPT 切片任务已由算子受理",
                progress={"source_video_path": "/data/course/course-001/slides.mp4"},
            )

    class Coordinator:
        def __init__(self) -> None:
            self.adoptions: list[tuple[Any, PptSliceAsyncAccepted]] = []

        async def adopt(
            self,
            reservation: Any,
            accepted: PptSliceAsyncAccepted,
        ) -> None:
            self.adoptions.append((reservation, accepted))

    base = _node()
    node = NodeRecord(
        id=base.id,
        course_task_type_id=base.course_task_type_id,
        node_code="PPT_SLICE",
        status=base.status,
        priority=base.priority,
        reason=base.reason,
        required_capability="ppt_slice",
        result=None,
        artifact_path=None,
        artifact_count=None,
        progress={},
        effective_params=None,
        updated_at=base.updated_at,
    )
    repository = PptRepository(node=node)
    leases = RecordingLeaseClient()
    coordinator = Coordinator()
    executor = NodeExecutor(
        repository,
        LeaseAwareDispatcher(repository, leases),
        AcceptedAdapter(),
        worker_id="worker-a",
        concurrency=1,
        async_node_coordinator=coordinator,
    )

    executed = await executor.run_once()

    assert executed == 1
    assert repository.completed == []
    assert repository.aggregated == [7]
    assert len(coordinator.adoptions) == 1
    assert coordinator.adoptions[0][0].lease.lease_id == "lease-001"
    assert leases.released == []


@pytest.mark.asyncio
async def test_ppt_slice_accepted_lease_is_not_released_when_adoption_fails() -> None:
    class PptRepository(RecordingRepository):
        def list_dispatch_capabilities(self) -> list[str]:
            return ["ppt_slice"]

    class AcceptedAdapter:
        async def execute(
            self,
            service_url: str | None,
            context: NodeExecutionContext,
        ) -> PptSliceAsyncAccepted:
            return PptSliceAsyncAccepted(
                task_id=context.task_id,
                operator_task_id="ppt-node-11",
                reason="PPT 切片任务已由算子受理",
                progress={},
            )

    class FailingCoordinator:
        async def adopt(self, reservation: Any, accepted: Any) -> None:
            raise RuntimeError("PPT 租约转交持久化失败")

    base = _node()
    node = NodeRecord(
        id=base.id,
        course_task_type_id=base.course_task_type_id,
        node_code="PPT_SLICE",
        status=base.status,
        priority=base.priority,
        reason=base.reason,
        required_capability="ppt_slice",
        result=None,
        artifact_path=None,
        artifact_count=None,
        progress={},
        effective_params=None,
        updated_at=base.updated_at,
    )
    repository = PptRepository(node=node)
    leases = RecordingLeaseClient()
    executor = NodeExecutor(
        repository,
        LeaseAwareDispatcher(repository, leases),
        AcceptedAdapter(),
        worker_id="worker-a",
        concurrency=1,
        async_node_coordinator=FailingCoordinator(),
    )

    with pytest.raises(RuntimeError, match="PPT 租约转交持久化失败"):
        await executor.run_once()

    assert repository.completed == []
    assert leases.released == []
