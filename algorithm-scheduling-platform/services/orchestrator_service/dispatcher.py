from typing import Protocol

from packages.platform_common.repository import NodeRecord


class DispatchRepository(Protocol):
    def defer_capability_nodes(self, capability: str) -> int: ...

    def resume_capability_nodes(self, capability: str) -> int: ...

    def claim_ready_node(self, capability: str, worker_id: str) -> NodeRecord | None: ...


class CapacityRegistry(Protocol):
    def has_available_capacity(self, capability: str) -> bool: ...


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
