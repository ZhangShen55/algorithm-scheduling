from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any, Protocol

import httpx
from packages.platform_common.operator_registry import CapacityLease
from packages.platform_common.repository import NodeRecord, TaskTypeRecord
from packages.platform_contracts.status import NodeStatus

from ..application.dispatcher import NodeReservation
from ..domain.ppt_work import PptSliceAsyncAccepted
from .ppt_slice import (
    PptCapacityLeaseKeeper,
    PptSliceCallbackError,
    PptSliceTerminalCallback,
    PptSliceTerminalHandler,
    PptTerminalHandleResult,
)


class PptRuntimeRepository(Protocol):
    def get_node(self, node_id: int) -> NodeRecord: ...

    def get_task_type(self, task_type_id: int) -> TaskTypeRecord: ...

    def list_running_ppt_slice_nodes(self) -> list[NodeRecord]: ...

    def update_node_progress(
        self,
        node_id: int,
        progress: dict[str, Any],
        *,
        reason: str,
    ) -> NodeRecord: ...

    def merge_node_progress(
        self,
        node_id: int,
        progress_patch: dict[str, Any],
        *,
        reason: str,
    ) -> NodeRecord: ...

    def aggregate_task_type_state(self, course_task_type_id: int) -> TaskTypeRecord: ...


class PptRuntimeLeaseClient(Protocol):
    async def renew(
        self,
        lease_id: str,
        *,
        ttl_seconds: int | None = None,
    ) -> CapacityLease: ...

    async def release(self, lease_id: str) -> None: ...


class PptRuntimeCoordinator:
    """Own accepted PPT leases until a durable terminal state exists."""

    def __init__(
        self,
        *,
        repository: PptRuntimeRepository,
        terminal_handler: PptSliceTerminalHandler,
        lease_client: PptRuntimeLeaseClient,
        lease_ttl_seconds: int,
        lease_renew_interval_seconds: float,
        reconcile_interval_seconds: float,
    ) -> None:
        if reconcile_interval_seconds <= 0:
            raise ValueError("PPT 终态对账周期必须大于 0")
        self._repository = repository
        self._terminal_handler = terminal_handler
        self._lease_client = lease_client
        self._lease_ttl_seconds = lease_ttl_seconds
        self._lease_renew_interval_seconds = lease_renew_interval_seconds
        self._reconcile_interval_seconds = reconcile_interval_seconds
        self._keepers: dict[int, PptCapacityLeaseKeeper] = {}
        self._terminal_nodes: set[int] = set()
        self._lock = asyncio.Lock()

    async def adopt(
        self,
        reservation: NodeReservation,
        accepted: PptSliceAsyncAccepted,
    ) -> None:
        lease = reservation.lease
        if lease is None:
            raise RuntimeError("PPT 异步任务缺少节点级容量租约")
        node = reservation.node
        progress = dict(accepted.progress)
        progress.update(
            {
                "task_id": accepted.task_id,
                "operator_task_id": accepted.operator_task_id,
                "lease_id": lease.lease_id,
                "instance_id": lease.instance_id,
                "service_url": lease.service_url,
                "lease_status": "ACTIVE",
            }
        )
        keeper = self._new_keeper(node.id, lease.lease_id)
        async with self._lock:
            if node.id in self._terminal_nodes:
                release_immediately = True
            else:
                self._keepers[node.id] = keeper
                release_immediately = False
        if release_immediately:
            await self._release_without_failure(lease.lease_id)
            return

        # The operator already accepted the background task. Take over renewal before
        # the first database write so a transient PostgreSQL failure cannot expose the
        # instance capacity while the accepted task is still running.
        await keeper.start()
        try:
            await asyncio.to_thread(
                self._repository.update_node_progress,
                node.id,
                progress,
                reason=accepted.reason,
            )
        except Exception:
            current = await asyncio.to_thread(self._repository.get_node, node.id)
            if current.status in {
                NodeStatus.COMPLETED,
                NodeStatus.FAILED,
                NodeStatus.CANCELLED,
            }:
                await self._after_terminal_persistence(node.id)
            raise

        current = await asyncio.to_thread(self._repository.get_node, node.id)
        if current.status is not NodeStatus.RUNNING:
            await self._after_terminal_persistence(node.id)

    async def handle_callback(
        self,
        *,
        node_id: int,
        callback: PptSliceTerminalCallback,
    ) -> PptTerminalHandleResult:
        result = await asyncio.to_thread(
            self._terminal_handler.handle_callback,
            node_id=node_id,
            callback=callback,
        )
        await self._after_terminal_persistence(node_id)
        return result

    async def recover(self) -> int:
        return await self.reconcile_once()

    async def reconcile_once(self) -> int:
        nodes = await asyncio.to_thread(
            self._repository.list_running_ppt_slice_nodes
        )
        reconciled = 0
        for node in nodes:
            progress = node.progress if isinstance(node.progress, dict) else {}
            task_type = await asyncio.to_thread(
                self._repository.get_task_type,
                node.course_task_type_id,
            )
            expected_task_id = task_type.task_id
            expected_operator_task_id = f"ppt-node-{node.id}"
            persisted_task_id = progress.get("task_id")
            persisted_operator_task_id = progress.get("operator_task_id")
            if (
                isinstance(persisted_task_id, str)
                and persisted_task_id != expected_task_id
            ):
                raise PptSliceCallbackError("PPT 持久化 task_id 与任务事实不一致")
            if (
                isinstance(persisted_operator_task_id, str)
                and persisted_operator_task_id != expected_operator_task_id
            ):
                raise PptSliceCallbackError(
                    "PPT 持久化 operator_task_id 与节点事实不一致"
                )
            if not isinstance(persisted_task_id, str) or not isinstance(
                persisted_operator_task_id, str
            ):
                # 算子身份可由持久任务事实确定性恢复，覆盖受理后、身份落库前的重启窗口。
                progress_patch = {
                    "task_id": expected_task_id,
                    "operator_task_id": expected_operator_task_id,
                }
                node = await asyncio.to_thread(
                    self._repository.merge_node_progress,
                    node.id,
                    progress_patch,
                    reason="PPT 异步任务身份已由持久事实恢复，等待终态对账",
                )
            result = await asyncio.to_thread(
                self._terminal_handler.reconcile,
                node_id=node.id,
            )
            current = await asyncio.to_thread(self._repository.get_node, node.id)
            if current.status in {
                NodeStatus.COMPLETED,
                NodeStatus.FAILED,
                NodeStatus.CANCELLED,
            }:
                await self._after_terminal_persistence(node.id)
                reconciled += 1
                continue
            if result.completed:
                await self._after_terminal_persistence(node.id)
                reconciled += 1
                continue
            await self._restore_keeper(current)
        return reconciled

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self.reconcile_once()
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._reconcile_interval_seconds,
                )
            except TimeoutError:
                continue

    async def shutdown(self) -> None:
        async with self._lock:
            keepers = list(self._keepers.values())
            self._keepers.clear()
        for keeper in keepers:
            with suppress(Exception):
                await keeper.stop_renewal()

    def _new_keeper(self, node_id: int, lease_id: str) -> PptCapacityLeaseKeeper:
        async def renewal_failed(exc: Exception) -> None:
            await self._record_lease_status(
                node_id,
                "RENEW_FAILED",
                "PPT 容量租约续租失败，等待终态对账",
            )

        return PptCapacityLeaseKeeper(
            client=self._lease_client,
            lease_id=lease_id,
            ttl_seconds=self._lease_ttl_seconds,
            renew_interval_seconds=self._lease_renew_interval_seconds,
            on_renewal_failure=renewal_failed,
        )

    async def _restore_keeper(self, node: NodeRecord) -> None:
        async with self._lock:
            if node.id in self._keepers or node.id in self._terminal_nodes:
                return
        progress = node.progress if isinstance(node.progress, dict) else {}
        lease_id = progress.get("lease_id")
        lease_status = progress.get("lease_status")
        if not isinstance(lease_id, str) or not lease_id:
            return
        if lease_status in {"EXPIRED", "RENEW_FAILED", "TERMINAL_PERSISTED"}:
            return
        try:
            await self._lease_client.renew(
                lease_id,
                ttl_seconds=self._lease_ttl_seconds,
            )
        except httpx.HTTPStatusError as exc:
            status = "EXPIRED" if exc.response.status_code == 404 else "RENEW_FAILED"
            reason = (
                "PPT 容量租约已过期，等待终态对账"
                if status == "EXPIRED"
                else "PPT 容量租约续租失败，等待终态对账"
            )
            await self._record_lease_status(node.id, status, reason)
            return
        except Exception:  # noqa: BLE001 - transport clients expose multiple error types
            await self._record_lease_status(
                node.id,
                "RENEW_FAILED",
                "PPT 容量租约续租失败，等待终态对账",
            )
            return

        keeper = self._new_keeper(node.id, lease_id)
        async with self._lock:
            if node.id in self._keepers or node.id in self._terminal_nodes:
                return
            self._keepers[node.id] = keeper
        await self._record_lease_status(
            node.id,
            "ACTIVE",
            "PPT 容量租约已恢复续租，等待终态对账",
        )
        await keeper.start()

    async def _record_lease_status(
        self,
        node_id: int,
        lease_status: str,
        reason: str,
    ) -> None:
        try:
            node = await asyncio.to_thread(self._repository.get_node, node_id)
            if node.status is not NodeStatus.RUNNING:
                return
            progress = dict(node.progress) if isinstance(node.progress, dict) else {}
            progress["lease_status"] = lease_status
            await asyncio.to_thread(
                self._repository.update_node_progress,
                node_id,
                progress,
                reason=reason,
            )
        except (ValueError, PptSliceCallbackError):
            return

    async def _after_terminal_persistence(self, node_id: int) -> None:
        node = await asyncio.to_thread(self._repository.get_node, node_id)
        await asyncio.to_thread(
            self._repository.aggregate_task_type_state,
            node.course_task_type_id,
        )
        async with self._lock:
            self._terminal_nodes.add(node_id)
            keeper = self._keepers.pop(node_id, None)
        if keeper is not None:
            try:
                await keeper.release_after_terminal_persistence()
            except Exception:  # noqa: BLE001 - terminal persistence already committed
                if not keeper.released:
                    return
            return
        progress = node.progress if isinstance(node.progress, dict) else {}
        lease_id = progress.get("lease_id")
        if isinstance(lease_id, str) and lease_id:
            await self._release_without_failure(lease_id)

    async def _release_without_failure(self, lease_id: str) -> None:
        with suppress(Exception):
            await self._lease_client.release(lease_id)
