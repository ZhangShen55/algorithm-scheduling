from packages.platform_common.operator_operations import (
    build_operator_capacity_snapshot,
)
from packages.platform_common.operator_registry import (
    OperatorCode,
    OperatorInstance,
    OperatorLifecycle,
)


class OperationsRegistry:
    def __init__(self, *, reported_inflight: int, active_lease_count: int) -> None:
        self._instance = OperatorInstance(
            instance_id="vbas-gpu0",
            operator_code=OperatorCode.VBAS,
            capabilities=["teacher_behavior"],
            service_url="http://vbas-gpu0:8981",
            declared_capacity=2,
            lifecycle=OperatorLifecycle.ONLINE,
            inflight=reported_inflight,
        )
        self._active_lease_count = active_lease_count

    def list_instances(self) -> list[OperatorInstance]:
        return [self._instance]

    def active_lease_count(self, instance_id: str) -> int:
        assert instance_id == self._instance.instance_id
        return self._active_lease_count


def test_capacity_snapshot_marks_reported_inflight_lease_mismatch() -> None:
    snapshots = build_operator_capacity_snapshot(
        OperationsRegistry(reported_inflight=2, active_lease_count=0)
    )

    assert len(snapshots) == 1
    assert snapshots[0].reported_inflight == 2
    assert snapshots[0].active_lease_count == 0
    assert snapshots[0].capacity_mismatch is True
    assert snapshots[0].lifecycle is OperatorLifecycle.ONLINE


def test_capacity_snapshot_does_not_flag_matching_capacity() -> None:
    snapshot = build_operator_capacity_snapshot(
        OperationsRegistry(reported_inflight=1, active_lease_count=1)
    )[0]

    assert snapshot.capacity_mismatch is False
