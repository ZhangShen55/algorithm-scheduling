from orchestrator_service.app.application.dispatcher import NodeDispatcher

from packages.platform_common.repository import NodeRecord


class FakeDispatchRepository:
    def __init__(self) -> None:
        self.deferred: list[str] = []
        self.resumed: list[str] = []
        self.claimed: list[tuple[str, str]] = []

    def defer_capability_nodes(self, capability: str) -> int:
        self.deferred.append(capability)
        return 2

    def resume_capability_nodes(self, capability: str) -> int:
        self.resumed.append(capability)
        return 1

    def claim_ready_node(self, capability: str, worker_id: str) -> NodeRecord | None:
        self.claimed.append((capability, worker_id))
        return None


class FakeCapacityRegistry:
    def __init__(self, available: bool) -> None:
        self.available = available

    def has_available_capacity(self, capability: str) -> bool:
        return self.available


def test_dispatcher_marks_nodes_waiting_when_operator_is_unavailable() -> None:
    repository = FakeDispatchRepository()
    dispatcher = NodeDispatcher(repository, FakeCapacityRegistry(available=False))

    claimed = dispatcher.claim_next("ocr", "worker-a")

    assert claimed is None
    assert repository.deferred == ["ocr"]
    assert repository.claimed == []


def test_dispatcher_resumes_waiting_nodes_before_claiming_capacity() -> None:
    repository = FakeDispatchRepository()
    dispatcher = NodeDispatcher(repository, FakeCapacityRegistry(available=True))

    dispatcher.claim_next("ocr", "worker-a")

    assert repository.resumed == ["ocr"]
    assert repository.claimed == [("ocr", "worker-a")]
