from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol

from packages.platform_common.repository import NodeRecord

_VISUAL_NODE_CODES = frozenset(
    {"TEACHER_BEHAVIOR_ANALYSIS", "STUDENT_BEHAVIOR_ANALYSIS"}
)


class RecoveryRepository(Protocol):
    def list_stale_claimed_nodes(self, claimed_before: datetime) -> list[NodeRecord]: ...

    def recover_stale_claimed_node(
        self,
        node_id: int,
        *,
        claimed_before: datetime,
        reason: str,
    ) -> bool: ...

    def aggregate_task_type_state(self, course_task_type_id: int) -> object: ...


class LeaseStatusClient(Protocol):
    async def is_active(self, lease_id: str) -> bool: ...


class StaleNodeRecovery:
    def __init__(
        self,
        repository: RecoveryRepository,
        lease_client: LeaseStatusClient,
        *,
        timeout_seconds: float,
        current_worker_id: str | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("普通节点恢复超时必须大于 0")
        self._repository = repository
        self._lease_client = lease_client
        self._timeout_seconds = timeout_seconds
        self._current_worker_id = current_worker_id

    async def recover_once(self) -> int:
        claimed_before = datetime.now(UTC) - timedelta(
            seconds=self._timeout_seconds
        )
        nodes = await asyncio.to_thread(
            self._repository.list_stale_claimed_nodes,
            claimed_before,
        )
        recovered = 0
        for node in nodes:
            if node.node_code in _VISUAL_NODE_CODES:
                continue
            # OCR 外层节点不占算子租约，仅凭超时不能判定当前进程已失效。
            if (
                self._current_worker_id is not None
                and node.claimed_by == self._current_worker_id
            ):
                continue
            lease_id = node.progress.get("lease_id")
            if (
                isinstance(lease_id, str)
                and lease_id
                and await self._lease_client.is_active(lease_id)
            ):
                continue
            changed = await asyncio.to_thread(
                self._repository.recover_stale_claimed_node,
                node.id,
                claimed_before=claimed_before,
                reason="原执行器已失效且容量租约不存在，节点等待安全重排",
            )
            if not changed:
                continue
            await asyncio.to_thread(
                self._repository.aggregate_task_type_state,
                node.course_task_type_id,
            )
            recovered += 1
        return recovered
