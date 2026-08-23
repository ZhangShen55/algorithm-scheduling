from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TypeVar


def _require_finite_non_negative(value: float, name: str) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} 必须是有限非负数")


def _require_non_negative_integer(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} 必须是非负整数")


@dataclass(frozen=True, slots=True)
class SamplingSchedule:
    regular_seconds: float = 5.0
    burst_seconds: float = 1.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.regular_seconds) or not 0 < self.regular_seconds <= 5:
            raise ValueError("常规采样间隔必须大于 0 且不超过 5 秒")
        if not math.isfinite(self.burst_seconds) or not 0.5 <= self.burst_seconds <= 1:
            raise ValueError("突发采样间隔必须位于 0.5–1 秒")


@dataclass(frozen=True, slots=True)
class HostMetrics:
    filesystem_total_bytes: int
    filesystem_free_bytes: int
    memory_total_bytes: int
    memory_available_bytes: int
    oom_events: int = 0
    cpu_percent: float = 0.0
    network_receive_bytes: int = 0
    network_transmit_bytes: int = 0
    open_socket_count: int = 0
    open_file_handle_count: int = 0

    def __post_init__(self) -> None:
        for name in (
            "filesystem_total_bytes",
            "filesystem_free_bytes",
            "memory_total_bytes",
            "memory_available_bytes",
            "oom_events",
            "network_receive_bytes",
            "network_transmit_bytes",
            "open_socket_count",
            "open_file_handle_count",
        ):
            _require_non_negative_integer(getattr(self, name), name)
        _require_finite_non_negative(self.cpu_percent, "host.cpu_percent")
        if self.cpu_percent > 100:
            raise ValueError("宿主机 CPU 利用率不能大于 100")
        if self.filesystem_free_bytes > self.filesystem_total_bytes:
            raise ValueError("文件系统剩余空间不能大于总空间")
        if self.memory_available_bytes > self.memory_total_bytes:
            raise ValueError("可用内存不能大于总内存")


@dataclass(frozen=True, slots=True)
class ContainerMetrics:
    container_id: str
    compose_project: str
    compose_service: str
    cpu_percent: float
    memory_bytes: int
    restart_count: int
    healthy: bool

    def __post_init__(self) -> None:
        if not self.container_id or not self.compose_project or not self.compose_service:
            raise ValueError("容器指标必须包含容器 ID 和 Compose 身份")
        _require_finite_non_negative(self.cpu_percent, "container.cpu_percent")
        _require_non_negative_integer(self.memory_bytes, "container.memory_bytes")
        _require_non_negative_integer(self.restart_count, "container.restart_count")
        if type(self.healthy) is not bool:
            raise ValueError("container.healthy 必须是布尔值")


@dataclass(frozen=True, slots=True)
class GpuProcessMetrics:
    pid: int
    name: str
    memory_bytes: int

    def __post_init__(self) -> None:
        if type(self.pid) is not int or self.pid <= 0:
            raise ValueError("GPU 进程 PID 必须是正整数")
        if not self.name:
            raise ValueError("GPU 进程名称不能为空")
        _require_non_negative_integer(self.memory_bytes, "gpu_process.memory_bytes")


@dataclass(frozen=True, slots=True)
class GpuMetrics:
    uuid: str
    utilization_percent: float
    memory_used_bytes: int
    processes: tuple[GpuProcessMetrics, ...] = ()

    def __post_init__(self) -> None:
        if not self.uuid:
            raise ValueError("GPU UUID 不能为空")
        _require_finite_non_negative(self.utilization_percent, "gpu.utilization_percent")
        if self.utilization_percent > 100:
            raise ValueError("GPU 利用率不能大于 100")
        _require_non_negative_integer(self.memory_used_bytes, "gpu.memory_used_bytes")


@dataclass(frozen=True, slots=True)
class InstanceCapacityMetrics:
    instance_id: str
    inflight: int
    active_leases: int
    declared_capacity: int

    def __post_init__(self) -> None:
        if not self.instance_id:
            raise ValueError("算子实例 ID 不能为空")
        _require_non_negative_integer(self.inflight, "instance.inflight")
        _require_non_negative_integer(self.active_leases, "instance.active_leases")
        if type(self.declared_capacity) is not int or self.declared_capacity <= 0:
            raise ValueError("instance.declared_capacity 必须是正整数")


@dataclass(frozen=True, slots=True)
class GatewayCounters:
    requests_total: int = 0
    lease_acquired_total: int = 0
    lease_rejected_total: int = 0
    lease_released_total: int = 0

    def __post_init__(self) -> None:
        for name in (
            "requests_total",
            "lease_acquired_total",
            "lease_rejected_total",
            "lease_released_total",
        ):
            _require_non_negative_integer(getattr(self, name), f"gateway.{name}")


@dataclass(frozen=True, slots=True)
class MetricFrame:
    recorded_at: str
    monotonic_seconds: float
    host: HostMetrics
    containers: tuple[ContainerMetrics, ...]
    gpus: tuple[GpuMetrics, ...]
    kafka_lag: int
    task_queue_depth: int
    instances: tuple[InstanceCapacityMetrics, ...]
    gateway_counters: GatewayCounters
    gateway_instance_requests: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.recorded_at:
            raise ValueError("指标采样时间不能为空")
        _require_finite_non_negative(self.monotonic_seconds, "monotonic_seconds")
        _require_non_negative_integer(self.kafka_lag, "kafka_lag")
        _require_non_negative_integer(self.task_queue_depth, "task_queue_depth")
        instance_ids = [item.instance_id for item in self.instances]
        if len(instance_ids) != len(set(instance_ids)):
            raise ValueError("同一帧不能包含重复算子实例")
        for instance_id, count in self.gateway_instance_requests.items():
            if not instance_id:
                raise ValueError("Gateway 实例级请求指标的实例 ID 不能为空")
            _require_non_negative_integer(count, "gateway_instance_requests")


@dataclass(frozen=True, slots=True)
class MetricProbes:
    host: Callable[[], HostMetrics]
    containers: Callable[[], tuple[ContainerMetrics, ...]]
    gpus: Callable[[], tuple[GpuMetrics, ...]]
    kafka_lag: Callable[[], int]
    task_queue_depth: Callable[[], int]
    instances: Callable[[], tuple[InstanceCapacityMetrics, ...]]
    gateway_counters: Callable[[], GatewayCounters]
    gateway_instance_requests: Callable[[], Mapping[str, int]] = lambda: {}


@dataclass(frozen=True, slots=True)
class MetricSummary:
    sample_count: int
    started_at: str
    finished_at: str
    gateway_delta: GatewayCounters
    peak_inflight: Mapping[str, int]
    peak_active_leases: Mapping[str, int]
    max_kafka_lag: int
    max_task_queue_depth: int
    container_restart_delta: Mapping[str, int]
    peak_gpu_utilization: Mapping[str, float]
    peak_gpu_memory_bytes: Mapping[str, int]
    gpu_process_names: Mapping[str, tuple[str, ...]]
    peak_container_cpu_percent: Mapping[str, float]
    peak_container_memory_bytes: Mapping[str, int]
    minimum_filesystem_free_bytes: int
    minimum_host_memory_available_bytes: int
    gateway_instance_request_delta: Mapping[str, int]
    peak_host_cpu_percent: float = 0.0
    host_network_receive_delta_bytes: int = 0
    host_network_transmit_delta_bytes: int = 0
    peak_host_open_sockets: int = 0
    peak_host_open_file_handles: int = 0
    recovery_seconds: float | None = None

    def __post_init__(self) -> None:
        if type(self.sample_count) is not int or self.sample_count <= 0:
            raise ValueError("指标摘要至少需要一条采样")
        _require_finite_non_negative(self.peak_host_cpu_percent, "peak_host_cpu_percent")
        if self.peak_host_cpu_percent > 100:
            raise ValueError("宿主机 CPU 利用率峰值不能大于 100")
        for name in (
            "host_network_receive_delta_bytes",
            "host_network_transmit_delta_bytes",
            "peak_host_open_sockets",
            "peak_host_open_file_handles",
        ):
            _require_non_negative_integer(getattr(self, name), name)
        if self.recovery_seconds is not None:
            _require_finite_non_negative(self.recovery_seconds, "recovery_seconds")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def collect_frame(
    probes: MetricProbes,
    *,
    wall_clock: Callable[[], str] = _utc_now,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> MetricFrame:
    """按固定顺序读取各来源，便于用例证明没有遗漏关键观测面。"""

    return MetricFrame(
        recorded_at=wall_clock(),
        monotonic_seconds=monotonic_clock(),
        host=probes.host(),
        containers=probes.containers(),
        gpus=probes.gpus(),
        kafka_lag=probes.kafka_lag(),
        task_queue_depth=probes.task_queue_depth(),
        instances=probes.instances(),
        gateway_counters=probes.gateway_counters(),
        gateway_instance_requests=probes.gateway_instance_requests(),
    )


_T = TypeVar("_T")


def sample_series(
    source: Callable[[], _T],
    *,
    duration_seconds: float,
    interval_seconds: float,
    monotonic_clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[_T, ...]:
    if not math.isfinite(duration_seconds) or duration_seconds < 0:
        raise ValueError("采样持续时间必须是有限非负数")
    if not math.isfinite(interval_seconds) or interval_seconds <= 0:
        raise ValueError("采样间隔必须是有限正数")
    started = monotonic_clock()
    deadline = started + duration_seconds
    next_sample = started
    frames: list[_T] = []
    while True:
        frames.append(source())
        now = monotonic_clock()
        if now >= deadline:
            return tuple(frames)
        next_sample += interval_seconds
        if next_sample <= now:
            missed_intervals = math.floor((now - next_sample) / interval_seconds) + 1
            next_sample += missed_intervals * interval_seconds
        delay = min(next_sample, deadline) - now
        if delay > 0:
            sleep(delay)


def sample_regular_series(
    source: Callable[[], _T],
    *,
    duration_seconds: float,
    schedule: SamplingSchedule | None = None,
    monotonic_clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[_T, ...]:
    active_schedule = schedule or SamplingSchedule()
    return sample_series(
        source,
        duration_seconds=duration_seconds,
        interval_seconds=active_schedule.regular_seconds,
        monotonic_clock=monotonic_clock,
        sleep=sleep,
    )


def sample_short_lease_series(
    source: Callable[[], _T],
    *,
    duration_seconds: float,
    schedule: SamplingSchedule | None = None,
    monotonic_clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[_T, ...]:
    active_schedule = schedule or SamplingSchedule()
    return sample_series(
        source,
        duration_seconds=duration_seconds,
        interval_seconds=active_schedule.burst_seconds,
        monotonic_clock=monotonic_clock,
        sleep=sleep,
    )


def counter_delta(before: GatewayCounters, after: GatewayCounters) -> GatewayCounters:
    values: dict[str, int] = {}
    for name in (
        "requests_total",
        "lease_acquired_total",
        "lease_rejected_total",
        "lease_released_total",
    ):
        old = getattr(before, name)
        new = getattr(after, name)
        if new < old:
            raise ValueError(f"累计指标发生回退: {name}")
        values[name] = new - old
    return GatewayCounters(**values)


def _container_restart_delta(
    first: Sequence[ContainerMetrics],
    last: Sequence[ContainerMetrics],
) -> dict[str, int]:
    first_by_id = {item.container_id: item for item in first}
    result: dict[str, int] = {}
    for current in last:
        previous = first_by_id.get(current.container_id)
        if previous is None:
            result[current.compose_service] = max(
                result.get(current.compose_service, 0), current.restart_count
            )
            continue
        if current.restart_count < previous.restart_count:
            raise ValueError(f"容器重启计数发生回退: {current.compose_service}")
        result[current.compose_service] = max(
            result.get(current.compose_service, 0),
            current.restart_count - previous.restart_count,
        )
    return result


def _mapping_counter_delta(
    before: Mapping[str, int],
    after: Mapping[str, int],
) -> dict[str, int]:
    missing = sorted(set(before) - set(after))
    if missing:
        raise ValueError("Gateway 实例累计指标在末帧缺失: " + ", ".join(missing))
    result: dict[str, int] = {}
    for key, new in after.items():
        _require_non_negative_integer(new, f"gateway_instance_requests.{key}")
        old = before.get(key, 0)
        _require_non_negative_integer(old, f"gateway_instance_requests.{key}")
        if new < old:
            raise ValueError(f"Gateway 实例累计指标发生回退: {key}")
        result[key] = new - old
    return result


def _validate_cumulative_series(frames: Sequence[MetricFrame]) -> None:
    for before, after in zip(frames, frames[1:], strict=False):
        counter_delta(before.gateway_counters, after.gateway_counters)
        _mapping_counter_delta(
            before.gateway_instance_requests,
            after.gateway_instance_requests,
        )
        if after.host.network_receive_bytes < before.host.network_receive_bytes:
            raise ValueError("宿主机网络接收累计指标发生回退")
        if after.host.network_transmit_bytes < before.host.network_transmit_bytes:
            raise ValueError("宿主机网络发送累计指标发生回退")


def summarize_metrics(
    frames: Sequence[MetricFrame],
    *,
    recovery_seconds: float | None = None,
) -> MetricSummary:
    if not frames:
        raise ValueError("指标时序不能为空")
    ordered = sorted(frames, key=lambda item: item.monotonic_seconds)
    if len({item.monotonic_seconds for item in ordered}) != len(ordered):
        raise ValueError("指标时序不能包含重复单调时间")
    _validate_cumulative_series(ordered)

    peak_inflight: dict[str, int] = {}
    peak_active_leases: dict[str, int] = {}
    peak_gpu: dict[str, float] = {}
    peak_gpu_memory: dict[str, int] = {}
    gpu_process_names: dict[str, set[str]] = {}
    peak_container_cpu: dict[str, float] = {}
    peak_container_memory: dict[str, int] = {}
    for frame in ordered:
        for instance in frame.instances:
            peak_inflight[instance.instance_id] = max(
                peak_inflight.get(instance.instance_id, 0), instance.inflight
            )
            peak_active_leases[instance.instance_id] = max(
                peak_active_leases.get(instance.instance_id, 0), instance.active_leases
            )
        for gpu in frame.gpus:
            peak_gpu[gpu.uuid] = max(peak_gpu.get(gpu.uuid, 0.0), gpu.utilization_percent)
            peak_gpu_memory[gpu.uuid] = max(peak_gpu_memory.get(gpu.uuid, 0), gpu.memory_used_bytes)
            gpu_process_names.setdefault(gpu.uuid, set()).update(
                process.name for process in gpu.processes
            )
        for container in frame.containers:
            peak_container_cpu[container.compose_service] = max(
                peak_container_cpu.get(container.compose_service, 0.0),
                container.cpu_percent,
            )
            peak_container_memory[container.compose_service] = max(
                peak_container_memory.get(container.compose_service, 0),
                container.memory_bytes,
            )

    return MetricSummary(
        sample_count=len(ordered),
        started_at=ordered[0].recorded_at,
        finished_at=ordered[-1].recorded_at,
        gateway_delta=counter_delta(
            ordered[0].gateway_counters,
            ordered[-1].gateway_counters,
        ),
        peak_inflight=peak_inflight,
        peak_active_leases=peak_active_leases,
        max_kafka_lag=max(frame.kafka_lag for frame in ordered),
        max_task_queue_depth=max(frame.task_queue_depth for frame in ordered),
        container_restart_delta=_container_restart_delta(
            ordered[0].containers,
            ordered[-1].containers,
        ),
        peak_gpu_utilization=peak_gpu,
        peak_gpu_memory_bytes=peak_gpu_memory,
        gpu_process_names={
            gpu_uuid: tuple(sorted(names)) for gpu_uuid, names in sorted(gpu_process_names.items())
        },
        peak_container_cpu_percent=peak_container_cpu,
        peak_container_memory_bytes=peak_container_memory,
        minimum_filesystem_free_bytes=min(frame.host.filesystem_free_bytes for frame in ordered),
        minimum_host_memory_available_bytes=min(
            frame.host.memory_available_bytes for frame in ordered
        ),
        gateway_instance_request_delta=_mapping_counter_delta(
            ordered[0].gateway_instance_requests,
            ordered[-1].gateway_instance_requests,
        ),
        peak_host_cpu_percent=max(frame.host.cpu_percent for frame in ordered),
        host_network_receive_delta_bytes=(
            ordered[-1].host.network_receive_bytes - ordered[0].host.network_receive_bytes
        ),
        host_network_transmit_delta_bytes=(
            ordered[-1].host.network_transmit_bytes - ordered[0].host.network_transmit_bytes
        ),
        peak_host_open_sockets=max(frame.host.open_socket_count for frame in ordered),
        peak_host_open_file_handles=max(frame.host.open_file_handle_count for frame in ordered),
        recovery_seconds=recovery_seconds,
    )
