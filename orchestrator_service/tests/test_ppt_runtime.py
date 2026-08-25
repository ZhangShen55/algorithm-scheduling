from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from packages.platform_common.operator_registry import CapacityLease
from packages.platform_common.repository import NodeRecord, TaskTypeRecord
from packages.platform_contracts.status import NodeStatus, Priority, TaskType

from orchestrator_service.app.application.dispatcher import LeaseScope, NodeReservation
from orchestrator_service.app.domain.ppt_work import PptSliceAsyncAccepted
from orchestrator_service.app.infrastructure.ppt_runtime import PptRuntimeCoordinator
from orchestrator_service.app.infrastructure.ppt_slice import (
    PptSliceCallbackError,
    PptSliceManifestError,
    PptSliceTerminalCallback,
    PptTerminalHandleResult,
)


def _node(*, progress: dict[str, Any] | None = None) -> NodeRecord:
    return NodeRecord(
        id=11,
        course_task_type_id=7,
        node_code="PPT_SLICE",
        status=NodeStatus.RUNNING,
        priority=Priority.NORMAL,
        reason="PPT 切片处理中",
        required_capability="ppt_slice",
        result=None,
        artifact_path=None,
        artifact_count=None,
        progress=progress or {},
        effective_params=None,
        updated_at=datetime.now(UTC),
    )


def _lease() -> CapacityLease:
    return CapacityLease(
        lease_id="lease-ppt-001",
        instance_id="ppt-slice-cpu0",
        capability="ppt_slice",
        service_url="http://ppt-slice-cpu0:9001",
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )


def _task_type() -> TaskTypeRecord:
    return TaskTypeRecord(
        id=7,
        submission_id="submission-001",
        task_id="course-001",
        task_type=TaskType.PPT,
        status=NodeStatus.RUNNING,
        priority=Priority.NORMAL,
        reason="PPT 切片处理中",
        request_payload={"slides_video_path": "http://media/slides.mp4"},
        effective_params=None,
        created=False,
        updated_at=datetime.now(UTC),
    )


def _callback(status: NodeStatus = NodeStatus.COMPLETED) -> PptSliceTerminalCallback:
    return PptSliceTerminalCallback(
        task_id="course-001",
        operator_task_id="ppt-node-11",
        status=status,
        path="/data/result/course-001/ppt/slices",
        manifest_path="/data/result/course-001/ppt/manifest.json",
        count=2 if status is NodeStatus.COMPLETED else 0,
        reason="" if status is NodeStatus.COMPLETED else "PPT 视频解码失败",
        dynamic_segments=[],
    )


class Repository:
    def __init__(self, node: NodeRecord | None = None) -> None:
        self.node = node or _node()
        self.events: list[str] = []
        self.progress_writes: list[dict[str, Any]] = []
        self.fail_progress_write = False
        self.fail_running_nodes = False

    def get_node(self, node_id: int) -> NodeRecord:
        assert node_id == self.node.id
        return self.node

    def get_task_type(self, task_type_id: int) -> TaskTypeRecord:
        assert task_type_id == self.node.course_task_type_id
        return _task_type()

    def list_running_ppt_slice_nodes(self) -> list[NodeRecord]:
        if self.fail_running_nodes:
            raise RuntimeError("PostgreSQL 查询失败")
        return [self.node] if self.node.status is NodeStatus.RUNNING else []

    def update_node_progress(
        self,
        node_id: int,
        progress: dict[str, Any],
        *,
        reason: str,
    ) -> NodeRecord:
        assert node_id == self.node.id
        assert self.node.status is NodeStatus.RUNNING
        if self.fail_progress_write:
            raise RuntimeError("PostgreSQL 写入失败")
        self.events.append(f"progress:{progress.get('lease_status')}")
        self.progress_writes.append(dict(progress))
        self.node = replace(self.node, progress=dict(progress), reason=reason)
        return self.node

    def merge_node_progress(
        self,
        node_id: int,
        progress_patch: dict[str, Any],
        *,
        reason: str,
    ) -> NodeRecord:
        progress = {
            **(self.node.progress if isinstance(self.node.progress, dict) else {}),
            **progress_patch,
        }
        return self.update_node_progress(node_id, progress, reason=reason)

    def aggregate_task_type_state(self, course_task_type_id: int) -> object:
        assert course_task_type_id == 7
        self.events.append("aggregate")
        return object()


class TerminalHandler:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        self.manifest_ready = False
        self.terminal_writes = 0
        self.reconcile_calls = 0
        self.reconcile_error: Exception | None = None

    def handle_callback(
        self,
        *,
        node_id: int,
        callback: PptSliceTerminalCallback,
    ) -> PptTerminalHandleResult:
        if self.repository.node.status in {NodeStatus.COMPLETED, NodeStatus.FAILED}:
            return PptTerminalHandleResult(
                completed=self.repository.node.status is NodeStatus.COMPLETED,
                duplicate=True,
            )
        self.terminal_writes += 1
        self.repository.events.append("db_terminal")
        self.repository.node = replace(
            self.repository.node,
            status=NodeStatus(callback.status),
            reason=callback.reason,
        )
        return PptTerminalHandleResult(
            completed=callback.status == NodeStatus.COMPLETED,
            duplicate=False,
        )

    def reconcile(self, *, node_id: int) -> PptTerminalHandleResult:
        self.reconcile_calls += 1
        if self.reconcile_error is not None:
            raise self.reconcile_error
        if not self.manifest_ready:
            return PptTerminalHandleResult(completed=False, duplicate=False)
        return self.handle_callback(node_id=node_id, callback=_callback())


class LeaseClient:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.renewals = 0
        self.releases = 0
        self.fail_renewal = False
        self.expired = False

    async def renew(
        self,
        lease_id: str,
        *,
        ttl_seconds: int | None = None,
    ) -> CapacityLease:
        assert lease_id == "lease-ppt-001"
        assert ttl_seconds == 1
        self.renewals += 1
        self.events.append("renew")
        if self.expired:
            request = httpx.Request("POST", "http://control/lease/renew")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("expired", request=request, response=response)
        if self.fail_renewal:
            raise httpx.ConnectError("control-service 不可用")
        return _lease()

    async def release(self, lease_id: str) -> None:
        assert lease_id == "lease-ppt-001"
        self.releases += 1
        self.events.append("release")


def _coordinator(
    repository: Repository,
    terminal: TerminalHandler,
    leases: LeaseClient,
) -> PptRuntimeCoordinator:
    return PptRuntimeCoordinator(
        repository=repository,
        terminal_handler=terminal,  # type: ignore[arg-type]
        lease_client=leases,
        lease_ttl_seconds=1,
        lease_renew_interval_seconds=0.02,
        reconcile_interval_seconds=0.02,
    )


async def _adopt(coordinator: PptRuntimeCoordinator, repository: Repository) -> None:
    await coordinator.adopt(
        NodeReservation(
            node=repository.node,
            lease=_lease(),
            lease_scope=LeaseScope.NODE,
        ),
        PptSliceAsyncAccepted(
            task_id="course-001",
            operator_task_id="ppt-node-11",
            reason="PPT 切片任务已由算子受理",
            progress={"source_video_path": "/data/course/course-001/slides.mp4"},
        ),
    )


@pytest.mark.asyncio
async def test_adopt_keeps_node_running_and_persists_runtime_identity() -> None:
    repository = Repository()
    leases = LeaseClient()
    coordinator = _coordinator(repository, TerminalHandler(repository), leases)

    await _adopt(coordinator, repository)

    assert repository.node.status is NodeStatus.RUNNING
    assert repository.node.progress == {
        "source_video_path": "/data/course/course-001/slides.mp4",
        "task_id": "course-001",
        "operator_task_id": "ppt-node-11",
        "lease_id": "lease-ppt-001",
        "instance_id": "ppt-slice-cpu0",
        "service_url": "http://ppt-slice-cpu0:9001",
        "lease_status": "ACTIVE",
    }
    assert leases.releases == 0
    await coordinator.shutdown()
    assert leases.releases == 0


@pytest.mark.asyncio
async def test_adopt_starts_renewal_before_initial_progress_write() -> None:
    repository = Repository()
    repository.fail_progress_write = True
    leases = LeaseClient()
    coordinator = _coordinator(repository, TerminalHandler(repository), leases)

    with pytest.raises(RuntimeError, match="PostgreSQL 写入失败"):
        await _adopt(coordinator, repository)

    await asyncio.sleep(0.05)
    assert leases.renewals >= 1
    assert leases.releases == 0
    await coordinator.shutdown()
    assert leases.releases == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status", (NodeStatus.COMPLETED, NodeStatus.FAILED))
async def test_callback_persists_terminal_before_releasing_lease(
    status: NodeStatus,
) -> None:
    repository = Repository()
    leases = LeaseClient()
    leases.events = repository.events
    terminal = TerminalHandler(repository)
    coordinator = _coordinator(repository, terminal, leases)
    await _adopt(coordinator, repository)

    result = await coordinator.handle_callback(node_id=11, callback=_callback(status))

    assert repository.node.status is status
    assert result.completed is (status is NodeStatus.COMPLETED)
    assert repository.events.index("db_terminal") < repository.events.index("aggregate")
    assert repository.events.index("aggregate") < repository.events.index("release")
    assert leases.releases == 1


@pytest.mark.asyncio
async def test_duplicate_callback_is_idempotent_after_terminal_persistence() -> None:
    repository = Repository()
    leases = LeaseClient()
    terminal = TerminalHandler(repository)
    coordinator = _coordinator(repository, terminal, leases)
    await _adopt(coordinator, repository)

    first = await coordinator.handle_callback(node_id=11, callback=_callback())
    duplicate = await coordinator.handle_callback(node_id=11, callback=_callback())

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert terminal.terminal_writes == 1


@pytest.mark.asyncio
async def test_manifest_reconcile_recovers_lost_callback_and_releases() -> None:
    repository = Repository()
    leases = LeaseClient()
    terminal = TerminalHandler(repository)
    coordinator = _coordinator(repository, terminal, leases)
    await _adopt(coordinator, repository)
    terminal.manifest_ready = True

    reconciled = await coordinator.reconcile_once()

    assert reconciled == 1
    assert repository.node.status is NodeStatus.COMPLETED
    assert leases.releases == 1


@pytest.mark.asyncio
async def test_reconcile_recovers_missing_identity_from_persistent_task_facts() -> None:
    repository = Repository(
        _node(
            progress={
                "lease_id": "lease-ppt-001",
                "service_url": "http://ppt-slice-cpu0:9001",
            }
        )
    )
    leases = LeaseClient()
    terminal = TerminalHandler(repository)
    terminal.manifest_ready = True
    coordinator = _coordinator(repository, terminal, leases)

    reconciled = await coordinator.reconcile_once()

    assert reconciled == 1
    assert terminal.reconcile_calls == 1
    assert repository.node.status is NodeStatus.COMPLETED
    assert repository.progress_writes[0] == {
        "lease_id": "lease-ppt-001",
        "service_url": "http://ppt-slice-cpu0:9001",
        "task_id": "course-001",
        "operator_task_id": "ppt-node-11",
    }


@pytest.mark.asyncio
async def test_reconcile_missing_identity_does_not_swallow_recovery_write_failure() -> None:
    repository = Repository()
    repository.fail_progress_write = True
    coordinator = _coordinator(
        repository,
        TerminalHandler(repository),
        LeaseClient(),
    )

    with pytest.raises(RuntimeError, match="PostgreSQL 写入失败"):
        await coordinator.reconcile_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("progress", "message"),
    (
        (
            {"task_id": "wrong-course", "operator_task_id": "ppt-node-11"},
            "task_id 与任务事实不一致",
        ),
        (
            {"task_id": "course-001", "operator_task_id": "ppt-node-999"},
            "operator_task_id 与节点事实不一致",
        ),
        (
            {"task_id": "wrong-course"},
            "task_id 与任务事实不一致",
        ),
    ),
)
async def test_reconcile_rejects_persisted_identity_mismatch(
    progress: dict[str, Any],
    message: str,
) -> None:
    repository = Repository(_node(progress=progress))
    terminal = TerminalHandler(repository)
    coordinator = _coordinator(repository, terminal, LeaseClient())

    with pytest.raises(PptSliceCallbackError, match=message):
        await coordinator.reconcile_once()

    assert terminal.reconcile_calls == 0
    assert repository.progress_writes == []


@pytest.mark.asyncio
async def test_reconcile_does_not_swallow_manifest_errors() -> None:
    repository = Repository(
        _node(
            progress={
                "task_id": "course-001",
                "operator_task_id": "ppt-node-11",
            }
        )
    )
    terminal = TerminalHandler(repository)
    terminal.reconcile_error = PptSliceManifestError("manifest 损坏")
    coordinator = _coordinator(repository, terminal, LeaseClient())

    with pytest.raises(PptSliceManifestError, match="manifest 损坏"):
        await coordinator.reconcile_once()


@pytest.mark.asyncio
async def test_reconcile_does_not_swallow_database_errors() -> None:
    repository = Repository()
    repository.fail_running_nodes = True
    coordinator = _coordinator(
        repository,
        TerminalHandler(repository),
        LeaseClient(),
    )

    with pytest.raises(RuntimeError, match="PostgreSQL 查询失败"):
        await coordinator.reconcile_once()


@pytest.mark.asyncio
async def test_renewal_failure_keeps_node_running_with_deterministic_status() -> None:
    repository = Repository()
    leases = LeaseClient()
    leases.fail_renewal = True
    coordinator = _coordinator(repository, TerminalHandler(repository), leases)
    await _adopt(coordinator, repository)

    await asyncio.sleep(0.05)

    assert repository.node.status is NodeStatus.RUNNING
    assert repository.node.progress["lease_status"] == "RENEW_FAILED"
    assert repository.node.reason == "PPT 容量租约续租失败，等待终态对账"
    assert leases.releases == 0
    await coordinator.shutdown()


@pytest.mark.asyncio
async def test_restart_restores_original_lease_without_resubmitting_work() -> None:
    repository = Repository(
        _node(
            progress={
                "task_id": "course-001",
                "operator_task_id": "ppt-node-11",
                "lease_id": "lease-ppt-001",
                "instance_id": "ppt-slice-cpu0",
                "service_url": "http://ppt-slice-cpu0:9001",
                "lease_status": "ACTIVE",
            }
        )
    )
    leases = LeaseClient()
    coordinator = _coordinator(repository, TerminalHandler(repository), leases)

    reconciled = await coordinator.recover()

    assert reconciled == 0
    assert leases.renewals == 1
    assert repository.node.progress["lease_status"] == "ACTIVE"
    await coordinator.shutdown()
    assert leases.releases == 0


@pytest.mark.asyncio
async def test_restart_marks_expired_lease_without_acquiring_or_resubmitting() -> None:
    repository = Repository(
        _node(
            progress={
                "task_id": "course-001",
                "operator_task_id": "ppt-node-11",
                "lease_id": "lease-ppt-001",
                "instance_id": "ppt-slice-cpu0",
                "service_url": "http://ppt-slice-cpu0:9001",
                "lease_status": "ACTIVE",
            }
        )
    )
    leases = LeaseClient()
    leases.expired = True
    coordinator = _coordinator(repository, TerminalHandler(repository), leases)

    await coordinator.recover()

    assert repository.node.status is NodeStatus.RUNNING
    assert repository.node.progress["lease_status"] == "EXPIRED"
    assert repository.node.reason == "PPT 容量租约已过期，等待终态对账"
    assert leases.releases == 0
