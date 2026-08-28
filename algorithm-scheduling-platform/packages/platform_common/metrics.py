from __future__ import annotations

import shutil
from pathlib import Path

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from packages.platform_common.operator_registry import OperatorCode


def _current_operator_code(value: str) -> str:
    try:
        return OperatorCode(value).value
    except ValueError as exc:
        raise ValueError(f"不支持的当前算子代码: {value}") from exc


class PlatformMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self._task_state = Gauge(
            "algorithm_task_state",
            "Current course task count by task type and integer status.",
            ("task_type", "status"),
            registry=self.registry,
        )
        self._node_state = Gauge(
            "algorithm_node_state",
            "Current node count by node code and integer status.",
            ("node_code", "status"),
            registry=self.registry,
        )
        self._outbox_pending = Gauge(
            "algorithm_outbox_pending",
            "Pending transactional Outbox event count.",
            registry=self.registry,
        )
        self._outbox_publish = Counter(
            "algorithm_outbox_publish",
            "Outbox publish attempts by outcome.",
            ("outcome",),
            registry=self.registry,
        )
        self._kafka_lag = Gauge(
            "algorithm_kafka_consumer_lag",
            "Kafka consumer lag by topic, group and partition.",
            ("topic", "consumer_group", "partition"),
            registry=self.registry,
        )
        self._operator_instances = Gauge(
            "algorithm_operator_instances",
            "Operator instance availability including lifecycle, readiness and GPU label.",
            ("operator_code", "lifecycle", "model_ready", "gpu"),
            registry=self.registry,
        )
        self._active_leases = Gauge(
            "algorithm_operator_active_leases",
            "Active capacity leases by operator and instance.",
            ("operator_code", "instance_id"),
            registry=self.registry,
        )
        self._capacity_lease_events = Counter(
            "algorithm_capacity_lease_events",
            "Capacity lease lifecycle events observed by a platform service.",
            ("capability", "outcome", "instance_id"),
            registry=self.registry,
        )
        self._operator_latency = Histogram(
            "algorithm_operator_request_latency_seconds",
            "Synchronous operator request latency.",
            ("operator_code", "capability", "instance_id"),
            registry=self.registry,
        )
        self._operator_errors = Counter(
            "algorithm_operator_request_errors",
            "Failed synchronous operator requests.",
            ("operator_code", "capability", "instance_id"),
            registry=self.registry,
        )
        self._postgres_transaction_events = Counter(
            "algorithm_postgres_transaction_events",
            "Retry lifecycle for short PostgreSQL transactions.",
            ("operation", "sqlstate", "outcome"),
            registry=self.registry,
        )
        self._disk_usage = Gauge(
            "algorithm_disk_usage_bytes",
            "Filesystem space by configured storage root.",
            ("path", "kind", "state"),
            registry=self.registry,
        )

    def set_task_state(self, task_type: str, status: int, count: int) -> None:
        self._task_state.labels(task_type=task_type, status=str(status)).set(count)

    def set_node_state(self, node_code: str, status: int, count: int) -> None:
        self._node_state.labels(node_code=node_code, status=str(status)).set(count)

    def set_outbox_pending(self, count: int) -> None:
        self._outbox_pending.set(count)

    def record_outbox_publish(self, outcome: str) -> None:
        self._outbox_publish.labels(outcome=outcome).inc()

    def set_kafka_lag(
        self,
        topic: str,
        consumer_group: str,
        partition: int,
        lag: int,
    ) -> None:
        self._kafka_lag.labels(
            topic=topic,
            consumer_group=consumer_group,
            partition=str(partition),
        ).set(lag)

    def set_operator_instance(
        self,
        *,
        operator_code: str,
        lifecycle: str,
        model_ready: bool,
        gpu_label: str,
        count: int,
    ) -> None:
        self._operator_instances.labels(
            operator_code=_current_operator_code(operator_code),
            lifecycle=lifecycle,
            model_ready=str(model_ready).lower(),
            gpu=gpu_label or "none",
        ).set(count)

    def set_active_leases(
        self,
        operator_code: str,
        instance_id: str,
        count: int,
    ) -> None:
        self._active_leases.labels(
            operator_code=_current_operator_code(operator_code),
            instance_id=instance_id,
        ).set(count)

    def record_capacity_lease_event(
        self,
        *,
        capability: str,
        outcome: str,
        instance_id: str | None = None,
    ) -> None:
        self._capacity_lease_events.labels(
            capability=capability,
            outcome=outcome,
            instance_id=instance_id or "none",
        ).inc()

    def observe_operator_request(
        self,
        *,
        operator_code: str,
        capability: str,
        instance_id: str,
        elapsed_seconds: float,
        success: bool,
    ) -> None:
        labels = {
            "operator_code": _current_operator_code(operator_code),
            "capability": capability,
            "instance_id": instance_id,
        }
        self._operator_latency.labels(**labels).observe(max(0.0, elapsed_seconds))
        if not success:
            self._operator_errors.labels(**labels).inc()

    def record_postgres_transaction_event(
        self,
        *,
        operation: str,
        sqlstate: str,
        outcome: str,
    ) -> None:
        self._postgres_transaction_events.labels(
            operation=operation,
            sqlstate=sqlstate,
            outcome=outcome,
        ).inc()

    def update_disk_usage(self, path: Path, *, kind: str) -> None:
        probe = path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        usage = shutil.disk_usage(probe)
        labels = {"path": str(path), "kind": kind}
        self._disk_usage.labels(**labels, state="total").set(usage.total)
        self._disk_usage.labels(**labels, state="used").set(usage.used)
        self._disk_usage.labels(**labels, state="free").set(usage.free)

    def render(self) -> bytes:
        return generate_latest(self.registry)
