from __future__ import annotations

import asyncio
import logging
from collections import Counter
from typing import Protocol

from packages.platform_common.repository import (
    NodeRecord,
    NodeResultWrite,
    TaskTypeRecord,
)
from packages.platform_contracts.status import NodeStatus

from ..domain.errors import CapacityUnavailableError, LeaseRenewalError
from ..domain.ppt_work import PptSliceAsyncAccepted
from ..infrastructure.contract_stub import NodeExecutionContext
from .dispatcher import LeaseAwareDispatcher, NodeReservation
from .lifecycle import WorkspaceCleaner

logger = logging.getLogger(__name__)


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
        service_url: str | None,
        context: NodeExecutionContext,
    ) -> NodeResultWrite | PptSliceAsyncAccepted: ...


class AsyncNodeCoordinator(Protocol):
    async def adopt(
        self,
        reservation: NodeReservation,
        accepted: PptSliceAsyncAccepted,
    ) -> None: ...


class NodeExecutor:
    def __init__(
        self,
        repository: ExecutionRepository,
        dispatcher: LeaseAwareDispatcher,
        adapter: NodeExecutionAdapter,
        *,
        worker_id: str,
        concurrency: int,
        operator_hard_timeout_seconds: float = 7_200.0,
        async_node_coordinator: AsyncNodeCoordinator | None = None,
        workspace_cleaner: WorkspaceCleaner | None = None,
    ) -> None:
        if concurrency <= 0:
            raise ValueError("节点执行并发数必须大于 0")
        self._repository = repository
        self._dispatcher = dispatcher
        self._adapter = adapter
        self._worker_id = worker_id
        self._concurrency = concurrency
        self._semaphore = asyncio.Semaphore(concurrency)
        self._capability_cursor = 0
        if operator_hard_timeout_seconds <= 0:
            raise ValueError("算子 HTTP 硬超时必须大于 0")
        self._operator_hard_timeout_seconds = operator_hard_timeout_seconds
        self._async_node_coordinator = async_node_coordinator
        self._workspace_cleaner = workspace_cleaner

    async def run_once(self) -> int:
        running: set[asyncio.Task[None]] = set()
        completed = 0
        pending_error: BaseException | None = None
        try:
            while True:
                available_slots = self._concurrency - len(running)
                if available_slots > 0 and pending_error is None:
                    reservations, reservation_errors = await self._reserve_slots(
                        available_slots
                    )
                    if reservation_errors:
                        pending_error = reservation_errors[0]
                        logger.warning(
                            "部分能力批次领取失败，已领取槽位继续执行后再上报",
                            extra={
                                "failed_capability_batches": len(reservation_errors)
                            },
                        )
                    running.update(
                        asyncio.create_task(self._run_reservation(reservation))
                        for reservation in reservations
                    )

                if not running:
                    if pending_error is not None:
                        raise pending_error
                    return completed

                done, waiting = await asyncio.wait(
                    running,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                running = set(waiting)
                execution_errors: list[BaseException] = []
                for task in done:
                    try:
                        task.result()
                    except BaseException as exc:  # noqa: BLE001 - 槽位异常需延迟上报
                        execution_errors.append(exc)
                    else:
                        completed += 1
                if execution_errors:
                    pending_error = pending_error or execution_errors[0]
                    logger.error(
                        "部分节点槽位发生基础设施异常，其他在途槽位继续收敛",
                        extra={"failed_slots": len(execution_errors)},
                    )
                if not running:
                    if pending_error is not None:
                        raise pending_error
                    return completed
        except asyncio.CancelledError:
            for task in running:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)
            raise

    async def _reserve_slots(
        self,
        limit: int,
    ) -> tuple[list[NodeReservation], list[BaseException]]:
        capabilities = await asyncio.to_thread(
            self._repository.list_dispatch_capabilities
        )
        if not capabilities or limit <= 0:
            return [], []
        start = self._capability_cursor % len(capabilities)
        scheduled = tuple(
            capabilities[(start + index) % len(capabilities)]
            for index in range(limit)
        )
        self._capability_cursor = (start + limit) % len(capabilities)
        slot_plan = Counter(scheduled)
        batches = await asyncio.gather(
            *(
                self._dispatcher.reserve_many(
                    capability,
                    self._worker_id,
                    limit=slot_count,
                )
                for capability, slot_count in slot_plan.items()
            ),
            return_exceptions=True,
        )
        reservations = [
            reservation
            for batch in batches
            if isinstance(batch, list)
            for reservation in batch
        ]
        errors = [batch for batch in batches if isinstance(batch, BaseException)]
        return reservations, errors

    async def _run_reservation(self, reservation: NodeReservation) -> None:
        async with self._semaphore:
            await self._execute_reservation(reservation)

    async def _execute_reservation(self, reservation: NodeReservation) -> None:
        node = reservation.node
        reservation_transferred = False
        task_id: str | None = None
        try:
            task_type = await asyncio.to_thread(
                self._repository.get_task_type,
                node.course_task_type_id,
            )
            task_id = task_type.task_id
            await asyncio.to_thread(
                self._repository.transition_node,
                node.id,
                NodeStatus.RUNNING,
                f"正在执行节点: {node.node_code}",
            )
            context = NodeExecutionContext(
                node_id=node.id,
                course_task_type_id=node.course_task_type_id,
                task_id=task_type.task_id,
                submission_id=task_type.submission_id,
                task_type=task_type.task_type.value,
                node_code=node.node_code,
                request_payload=task_type.request_payload,
                effective_params=task_type.effective_params,
            )
            try:
                service_url = (
                    reservation.lease.service_url
                    if reservation.lease is not None
                    else None
                )
                result = await self._dispatcher.run_with_renewal(
                    reservation,
                    self._adapter.execute(
                        service_url,
                        context,
                    ),
                    hard_timeout_seconds=self._operator_hard_timeout_seconds,
                )
            except CapacityUnavailableError as exc:
                await asyncio.to_thread(
                    self._repository.transition_node,
                    node.id,
                    NodeStatus.WAITING_OPERATOR,
                    str(exc),
                )
            except LeaseRenewalError as exc:
                await asyncio.to_thread(
                    self._repository.transition_node,
                    node.id,
                    NodeStatus.WAITING_OPERATOR,
                    f"算子容量租约续租未确认，等待安全重排: {type(exc).__name__}",
                )
            except Exception as exc:
                error_detail = str(exc).strip() or type(exc).__name__
                logger.exception(
                    "节点执行失败",
                    extra={
                        "task_id": task_id,
                        "node_id": str(node.id),
                        "node_code": node.node_code,
                        "exception_type": type(exc).__name__,
                        "reason": error_detail,
                    },
                )
                await asyncio.to_thread(
                    self._repository.transition_node,
                    node.id,
                    NodeStatus.FAILED,
                    f"节点执行失败: {error_detail}",
                )
            else:
                if isinstance(result, PptSliceAsyncAccepted):
                    reservation_transferred = True
                    if self._async_node_coordinator is None:
                        raise RuntimeError("PPT 异步节点协调器尚未装配")
                    await self._async_node_coordinator.adopt(reservation, result)
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
                if self._workspace_cleaner is not None and task_id is not None:
                    try:
                        await asyncio.to_thread(
                            self._workspace_cleaner.cleanup_if_terminal,
                            task_id,
                        )
                    except Exception as exc:  # noqa: BLE001 - 业务终态已经持久化
                        logger.warning(
                            "课程临时工作区清理失败",
                            extra={"task_id": task_id, "reason": str(exc)},
                        )
            finally:
                if not reservation_transferred:
                    await self._dispatcher.release(reservation)
