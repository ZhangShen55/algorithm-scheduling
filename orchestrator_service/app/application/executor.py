from __future__ import annotations

import asyncio
from typing import Protocol

from packages.platform_common.repository import (
    NodeRecord,
    NodeResultWrite,
    TaskTypeRecord,
)
from packages.platform_contracts.status import NodeStatus

from ..infrastructure.contract_stub import NodeExecutionContext
from .dispatcher import LeaseAwareDispatcher, NodeReservation


class ExecutionRepository(Protocol):
    def list_dispatch_capabilities(self) -> list[str]: ...

    def get_task_type(self, course_task_type_id: int) -> TaskTypeRecord: ...

    def transition_node(
        self,
        node_id: int,
        status: NodeStatus,
        reason: str,
    ) -> NodeRecord: ...

    def complete_node(
        self,
        node_id: int,
        result: NodeResultWrite,
        *,
        reason: str,
    ) -> NodeRecord: ...

    def aggregate_task_type_state(self, course_task_type_id: int) -> TaskTypeRecord: ...


class NodeExecutionAdapter(Protocol):
    async def execute(
        self,
        service_url: str,
        context: NodeExecutionContext,
    ) -> NodeResultWrite: ...


class NodeExecutor:
    def __init__(
        self,
        repository: ExecutionRepository,
        dispatcher: LeaseAwareDispatcher,
        adapter: NodeExecutionAdapter,
        *,
        worker_id: str,
        concurrency: int,
    ) -> None:
        if concurrency <= 0:
            raise ValueError("节点执行并发数必须大于 0")
        self._repository = repository
        self._dispatcher = dispatcher
        self._adapter = adapter
        self._worker_id = worker_id
        self._semaphore = asyncio.Semaphore(concurrency)

    async def run_once(self) -> int:
        capabilities = await asyncio.to_thread(
            self._repository.list_dispatch_capabilities
        )
        if not capabilities:
            return 0
        executed = await asyncio.gather(
            *(self._run_capability(capability) for capability in capabilities)
        )
        return sum(executed)

    async def _run_capability(self, capability: str) -> int:
        async with self._semaphore:
            reservation = await self._dispatcher.reserve_next(
                capability,
                self._worker_id,
            )
            if reservation is None:
                return 0
            await self._execute_reservation(reservation)
            return 1

    async def _execute_reservation(self, reservation: NodeReservation) -> None:
        node = reservation.node
        try:
            task_type = await asyncio.to_thread(
                self._repository.get_task_type,
                node.course_task_type_id,
            )
            await asyncio.to_thread(
                self._repository.transition_node,
                node.id,
                NodeStatus.RUNNING,
                f"正在执行节点: {node.node_code}",
            )
            context = NodeExecutionContext(
                task_id=task_type.task_id,
                task_type=task_type.task_type.value,
                node_code=node.node_code,
                request_payload=task_type.request_payload,
                effective_params=task_type.effective_params,
            )
            try:
                result = await self._adapter.execute(
                    reservation.lease.service_url,
                    context,
                )
            except Exception as exc:
                await asyncio.to_thread(
                    self._repository.transition_node,
                    node.id,
                    NodeStatus.FAILED,
                    f"节点执行失败: {exc}",
                )
            else:
                await asyncio.to_thread(
                    self._repository.complete_node,
                    node.id,
                    result,
                    reason=f"节点执行完成: {node.node_code}",
                )
        finally:
            try:
                await asyncio.to_thread(
                    self._repository.aggregate_task_type_state,
                    node.course_task_type_id,
                )
            finally:
                await self._dispatcher.release(reservation)
