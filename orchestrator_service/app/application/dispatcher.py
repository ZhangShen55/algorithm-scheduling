from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from packages.platform_common.operator_registry import CapacityLease
from packages.platform_common.repository import NodeRecord


class DispatchRepository(Protocol):
    def defer_capability_nodes(self, capability: str) -> int: ...

    def resume_capability_nodes(self, capability: str) -> int: ...

    def claim_ready_node(self, capability: str, worker_id: str) -> NodeRecord | None: ...


class CapacityRegistry(Protocol):
    def has_available_capacity(self, capability: str) -> bool: ...


class CapacityUnavailableError(RuntimeError):
    pass


class CapacityLeaseClient(Protocol):
    async def acquire(self, capability: str) -> CapacityLease: ...

    async def release(self, lease_id: str) -> None: ...


class LeaseDispatchRepository(Protocol):
    def defer_capability_nodes(self, capability: str) -> int: ...

    def resume_capability_nodes(self, capability: str) -> int: ...

    def aggregate_capability_task_types(self, capability: str) -> object: ...

    def claim_ready_node(self, capability: str, worker_id: str) -> NodeRecord | None: ...


@dataclass(frozen=True, slots=True)
class NodeReservation:
    node: NodeRecord
    lease: CapacityLease


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
        return NodeReservation(node=node, lease=lease)

    async def release(self, reservation: NodeReservation) -> None:
        await self._lease_client.release(reservation.lease.lease_id)


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
