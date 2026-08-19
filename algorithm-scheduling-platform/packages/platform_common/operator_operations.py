from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from packages.platform_common.operator_registry import (
    OperatorCode,
    OperatorInstance,
    OperatorLifecycle,
)


class OperatorOperationsRegistry(Protocol):
    def list_instances(self) -> list[OperatorInstance]: ...

    def active_lease_count(self, instance_id: str) -> int: ...


@dataclass(frozen=True, slots=True)
class OperatorCapacitySnapshot:
    instance_id: str
    operator_code: OperatorCode
    lifecycle: OperatorLifecycle
    model_ready: bool
    declared_capacity: int
    reported_inflight: int
    active_lease_count: int
    schedulable_used: int
    attribution_difference: int
    capacity_mismatch: bool


def build_operator_capacity_snapshot(
    registry: OperatorOperationsRegistry,
) -> list[OperatorCapacitySnapshot]:
    snapshots: list[OperatorCapacitySnapshot] = []
    for instance in registry.list_instances():
        active_lease_count = registry.active_lease_count(instance.instance_id)
        snapshots.append(
            OperatorCapacitySnapshot(
                instance_id=instance.instance_id,
                operator_code=instance.operator_code,
                lifecycle=instance.lifecycle,
                model_ready=instance.model_ready,
                declared_capacity=instance.declared_capacity,
                reported_inflight=instance.inflight,
                active_lease_count=active_lease_count,
                schedulable_used=active_lease_count,
                attribution_difference=instance.inflight - active_lease_count,
                capacity_mismatch=instance.inflight != active_lease_count,
            )
        )
    return snapshots
