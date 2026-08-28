from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from orchestrator_service.app.application.recovery import StaleNodeRecovery
from packages.platform_common.repository import NodeRecord
from packages.platform_contracts.status import NodeStatus, Priority


def _node(
    node_id: int,
    *,
    node_code: str = "ASR_TRANSCRIPTION",
    lease_id: str | None = None,
    claimed_by: str | None = "orchestrator-old",
) -> NodeRecord:
    progress = {"lease_id": lease_id} if lease_id is not None else {}
    return NodeRecord(
        id=node_id,
        course_task_type_id=node_id + 100,
        node_code=node_code,
        status=NodeStatus.RUNNING,
        priority=Priority.NORMAL,
        reason="节点执行中",
        required_capability="asr_offline",
        result=None,
        artifact_path=None,
        artifact_count=None,
        progress=progress,
        effective_params=None,
        updated_at=datetime.now(UTC) - timedelta(minutes=10),
        claimed_at=datetime.now(UTC) - timedelta(minutes=10),
        started_at=datetime.now(UTC) - timedelta(minutes=10),
        claimed_by=claimed_by,
    )


class RecoveryRepository:
    def __init__(self, nodes: list[NodeRecord]) -> None:
        self.nodes = nodes
        self.recovered: list[int] = []
        self.aggregated: list[int] = []

    def list_stale_claimed_nodes(self, claimed_before: datetime) -> list[NodeRecord]:
        assert claimed_before < datetime.now(UTC)
        return self.nodes

    def recover_stale_claimed_node(
        self,
        node_id: int,
        *,
        claimed_before: datetime,
        reason: str,
    ) -> bool:
        assert claimed_before < datetime.now(UTC)
        assert "租约不存在" in reason
        self.recovered.append(node_id)
        return True

    def aggregate_task_type_state(self, course_task_type_id: int) -> object:
        self.aggregated.append(course_task_type_id)
        return object()


class LeaseClient:
    def __init__(self, active: set[str]) -> None:
        self.active = active
        self.queries: list[str] = []

    async def is_active(self, lease_id: str) -> bool:
        self.queries.append(lease_id)
        return lease_id in self.active


@pytest.mark.asyncio
async def test_stale_node_with_active_lease_is_not_recovered() -> None:
    repository = RecoveryRepository([_node(1, lease_id="lease-active")])
    recovery = StaleNodeRecovery(
        repository,
        LeaseClient({"lease-active"}),
        timeout_seconds=60,
    )

    assert await recovery.recover_once() == 0
    assert repository.recovered == []
    assert repository.aggregated == []


@pytest.mark.asyncio
async def test_stale_node_without_active_lease_returns_to_waiting() -> None:
    repository = RecoveryRepository(
        [_node(1, lease_id="lease-expired"), _node(2)]
    )
    recovery = StaleNodeRecovery(
        repository,
        LeaseClient(set()),
        timeout_seconds=60,
    )

    assert await recovery.recover_once() == 2
    assert repository.recovered == [1, 2]
    assert repository.aggregated == [101, 102]


@pytest.mark.asyncio
async def test_current_worker_node_is_not_recovered_without_outer_lease() -> None:
    repository = RecoveryRepository(
        [_node(1, node_code="PPT_OCR", lease_id=None, claimed_by="worker-current")]
    )
    recovery = StaleNodeRecovery(
        repository,
        LeaseClient(set()),
        timeout_seconds=60,
        current_worker_id="worker-current",
    )

    assert await recovery.recover_once() == 0
    assert repository.recovered == []
    assert repository.aggregated == []


def test_ppt_slice_is_excluded_by_repository_contract_fixture() -> None:
    ppt_node = _node(3, node_code="PPT_SLICE", lease_id="lease-ppt")
    assert replace(ppt_node).node_code == "PPT_SLICE"
