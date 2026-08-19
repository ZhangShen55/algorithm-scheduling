from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypeVar
from uuid import uuid4

from packages.platform_common.operator_registry import CapacityLease, WorkContext
from packages.platform_common.repository import NodeRecord, TaskTypeRecord

from ..domain.errors import CapacityUnavailableError


class DispatchRepository(Protocol):
    def defer_capability_nodes(self, capability: str) -> int: ...

    def resume_capability_nodes(self, capability: str) -> int: ...

    def claim_ready_node(self, capability: str, worker_id: str) -> NodeRecord | None: ...


class CapacityRegistry(Protocol):
    def has_available_capacity(self, capability: str) -> bool: ...


class CapacityLeaseClient(Protocol):
    async def acquire(self, capability: str) -> CapacityLease: ...

    async def bind_context(
        self,
        lease_id: str,
        work_context: WorkContext,
    ) -> CapacityLease: ...

    async def release(self, lease_id: str) -> None: ...

    async def run_with_renewal(
        self,
        lease: CapacityLease,
        operation: Awaitable[object],
        *,
        hard_timeout_seconds: float,
    ) -> object: ...


class LeaseDispatchRepository(Protocol):
    def defer_capability_nodes(self, capability: str) -> int: ...

    def resume_capability_nodes(self, capability: str) -> int: ...

    def aggregate_capability_task_types(self, capability: str) -> object: ...

    def claim_ready_node(self, capability: str, worker_id: str) -> NodeRecord | None: ...

    def get_task_type(self, course_task_type_id: int) -> TaskTypeRecord: ...


class LeaseScope(StrEnum):
    NODE = "NODE"
    WORK_ITEM = "WORK_ITEM"


WORK_ITEM_CAPABILITIES = frozenset({"ocr", "extract_keywords"})


@dataclass(frozen=True, slots=True)
class NodeReservation:
    node: NodeRecord
    lease: CapacityLease | None
    lease_scope: LeaseScope


ResultT = TypeVar("ResultT")


class LeaseAwareDispatcher:
    def __init__(
        self,
        repository: LeaseDispatchRepository,
        lease_client: CapacityLeaseClient,
    ) -> None:
        self._repository = repository
        self._lease_client = lease_client

    async def reserve_next(
        self,
        capability: str,
        worker_id: str,
    ) -> NodeReservation | None:
        if capability in WORK_ITEM_CAPABILITIES:
            await asyncio.to_thread(self._repository.resume_capability_nodes, capability)
            await asyncio.to_thread(
                self._repository.aggregate_capability_task_types,
                capability,
            )
            node = await asyncio.to_thread(
                self._repository.claim_ready_node,
                capability,
                worker_id,
            )
            if node is None:
                return None
            return NodeReservation(
                node=node,
                lease=None,
                lease_scope=LeaseScope.WORK_ITEM,
            )
        try:
            lease = await self._lease_client.acquire(capability)
        except CapacityUnavailableError:
            await asyncio.to_thread(self._repository.defer_capability_nodes, capability)
            await asyncio.to_thread(
                self._repository.aggregate_capability_task_types,
                capability,
            )
            return None

        try:
            await asyncio.to_thread(self._repository.resume_capability_nodes, capability)
            await asyncio.to_thread(
                self._repository.aggregate_capability_task_types,
                capability,
            )
            node = await asyncio.to_thread(
                self._repository.claim_ready_node,
                capability,
                worker_id,
            )
        except BaseException:
            await self._lease_client.release(lease.lease_id)
            raise
        if node is None:
            await self._lease_client.release(lease.lease_id)
            return None
        try:
            task_type = await asyncio.to_thread(
                self._repository.get_task_type,
                node.course_task_type_id,
            )
            lease = await self._lease_client.bind_context(
                lease.lease_id,
                WorkContext(
                    source_service="orchestrator-service",
                    work_type=node.node_code.lower(),
                    work_id=f"node-{node.id}",
                    task_id=task_type.task_id,
                    node_id=str(node.id),
                    trace_id=uuid4().hex,
                ),
            )
        except BaseException:
            await self._lease_client.release(lease.lease_id)
            raise
        return NodeReservation(
            node=node,
            lease=lease,
            lease_scope=LeaseScope.NODE,
        )

    async def release(self, reservation: NodeReservation) -> None:
        if reservation.lease is not None:
            await self._lease_client.release(reservation.lease.lease_id)

    async def run_with_renewal(
        self,
        reservation: NodeReservation,
        operation: Awaitable[ResultT],
        *,
        hard_timeout_seconds: float,
    ) -> ResultT:
        if reservation.lease is None:
            return await operation
        result = await self._lease_client.run_with_renewal(
            reservation.lease,
            operation,
            hard_timeout_seconds=hard_timeout_seconds,
        )
        return result  # type: ignore[return-value]


class NodeDispatcher:
    def __init__(
        self,
        repository: DispatchRepository,
        capacity_registry: CapacityRegistry,
    ) -> None:
        self._repository = repository
        self._capacity_registry = capacity_registry

    def claim_next(self, capability: str, worker_id: str) -> NodeRecord | None:
        if not self._capacity_registry.has_available_capacity(capability):
            self._repository.defer_capability_nodes(capability)
            return None
        self._repository.resume_capability_nodes(capability)
        return self._repository.claim_ready_node(capability, worker_id)
