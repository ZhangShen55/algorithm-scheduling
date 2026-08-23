from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TypeVar

from .catalog import CaseSpec
from .guardrails import (
    GuardrailAssessment,
    GuardrailLevel,
    GuardrailObservation,
    GuardrailPolicy,
    StorageObservation,
    evaluate_guardrails,
)
from .metrics import (
    ContainerMetrics,
    GatewayCounters,
    GpuMetrics,
    SamplingSchedule,
    counter_delta,
)
from .plan import CampaignPlan
from .report import atomic_write_report, validate_public_payload
from .stage_runtime import StageCaseOutcome, StageCheckpoint
from .system_probes import (
    ControlPlaneMetrics,
    DirectorySizeMetrics,
    GatewayMetrics,
    LoadHostMetrics,
    TargetHostMetrics,
)

_SAFE_CRITICAL_ERROR = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/ -]{0,159}")
_SAFE_SERVICE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_BURST_CASE_KINDS = frozenset(
    {"online_image", "mixed_image", "s_stream", "image_boundary", "face_recognition"}
)
_DIRECTORY_CHECKPOINT_CASE_KINDS = frozenset({"long_course"})

_T_co = TypeVar("_T_co", covariant=True)


class CollectProbe(Protocol[_T_co]):
    def collect(self) -> _T_co: ...


EvidenceWriter = Callable[[Path, str], None]
CriticalGpuErrorProbe = Callable[[], Sequence[str]]
MaintenanceLockProbe = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class RuntimeMetricSample:
    campaign_id: str
    case_id: str
    phase: str
    sequence: int
    recorded_at: str
    monotonic_seconds: float
    burst: bool
    load_host: LoadHostMetrics
    target_host: TargetHostMetrics
    containers: tuple[ContainerMetrics, ...]
    gpus: tuple[GpuMetrics, ...]
    control: ControlPlaneMetrics
    gateway: GatewayMetrics
    guardrail: GuardrailAssessment
    evidence_path: Path | None = None

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "evidence_type": "extreme_load_runtime_metric_sample",
            "campaign_id": self.campaign_id,
            "case_id": self.case_id,
            "phase": self.phase,
            "sequence": self.sequence,
            "recorded_at": self.recorded_at,
            "monotonic_seconds": self.monotonic_seconds,
            "burst": self.burst,
            "load_host": asdict(self.load_host),
            "target_host": {
                "filesystems": [asdict(item) for item in self.target_host.filesystems],
                "directory_sizes": [
                    asdict(item) for item in self.target_host.directory_sizes
                ],
                "memory_total_bytes": self.target_host.memory_total_bytes,
                "memory_available_bytes": self.target_host.memory_available_bytes,
                "oom_events": self.target_host.oom_events,
            },
            "containers": [asdict(item) for item in self.containers],
            "gpus": [
                {
                    "uuid": gpu.uuid,
                    "utilization_percent": gpu.utilization_percent,
                    "memory_used_bytes": gpu.memory_used_bytes,
                    # 运行证据只需 UUID/PID/显存，不落盘进程命令或名称。
                    "processes": [
                        {"pid": process.pid, "memory_bytes": process.memory_bytes}
                        for process in gpu.processes
                    ],
                }
                for gpu in self.gpus
            ],
            "control": {
                "task_queue_depth": self.control.task_queue_depth,
                "outbox_pending": self.control.outbox_pending,
                "kafka_lag": self.control.kafka_lag,
                "instances": [asdict(item) for item in self.control.instances],
            },
            "gateway": {
                "counters": asdict(self.gateway.counters),
                "instance_requests": dict(self.gateway.instance_requests),
            },
            "guardrail": {
                "level": self.guardrail.level.value,
                "reasons": list(self.guardrail.reasons),
            },
        }


@dataclass(frozen=True, slots=True)
class RuntimeMetricSummary:
    sample_count: int
    started_at: str
    finished_at: str
    gateway_delta: GatewayCounters
    gateway_instance_request_delta: Mapping[str, int]
    peak_inflight: Mapping[str, int]
    peak_active_leases: Mapping[str, int]
    peak_declared_capacity: Mapping[str, int]
    max_kafka_lag: int
    max_task_queue_depth: int
    max_outbox_pending: int
    container_restart_delta: Mapping[str, int]
    peak_gpu_utilization: Mapping[str, float]
    peak_gpu_memory_bytes: Mapping[str, int]
    gpu_process_pids: Mapping[str, tuple[int, ...]]
    minimum_target_filesystem_available_bytes: Mapping[str, int]
    target_directory_bytes_before: Mapping[str, int]
    target_directory_bytes_after: Mapping[str, int]
    target_directory_bytes_delta: Mapping[str, int]
    minimum_target_memory_available_bytes: int
    target_oom_delta: int
    peak_load_host_cpu_percent: float
    minimum_load_host_memory_available_bytes: int
    load_host_network_receive_delta_bytes: int
    load_host_network_transmit_delta_bytes: int
    peak_load_host_open_sockets: int
    peak_load_host_open_file_handles: int
    latest_guardrail: GuardrailAssessment

    def to_dict(self) -> dict[str, object]:
        return {
            "sample_count": self.sample_count,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "gateway_delta": asdict(self.gateway_delta),
            "gateway_instance_request_delta": dict(self.gateway_instance_request_delta),
            "peak_inflight": dict(self.peak_inflight),
            "peak_active_leases": dict(self.peak_active_leases),
            "peak_declared_capacity": dict(self.peak_declared_capacity),
            "max_kafka_lag": self.max_kafka_lag,
            "max_task_queue_depth": self.max_task_queue_depth,
            "max_outbox_pending": self.max_outbox_pending,
            "container_restart_delta": dict(self.container_restart_delta),
            "peak_gpu_utilization": dict(self.peak_gpu_utilization),
            "peak_gpu_memory_bytes": dict(self.peak_gpu_memory_bytes),
            "gpu_process_pids": {
                gpu_uuid: list(pids) for gpu_uuid, pids in self.gpu_process_pids.items()
            },
            "minimum_target_filesystem_available_bytes": dict(
                self.minimum_target_filesystem_available_bytes
            ),
            "target_directory_bytes_before": dict(self.target_directory_bytes_before),
            "target_directory_bytes_after": dict(self.target_directory_bytes_after),
            "target_directory_bytes_delta": dict(self.target_directory_bytes_delta),
            "minimum_target_memory_available_bytes": self.minimum_target_memory_available_bytes,
            "target_oom_delta": self.target_oom_delta,
            "peak_load_host_cpu_percent": self.peak_load_host_cpu_percent,
            "minimum_load_host_memory_available_bytes": (
                self.minimum_load_host_memory_available_bytes
            ),
            "load_host_network_receive_delta_bytes": (self.load_host_network_receive_delta_bytes),
            "load_host_network_transmit_delta_bytes": (self.load_host_network_transmit_delta_bytes),
            "peak_load_host_open_sockets": self.peak_load_host_open_sockets,
            "peak_load_host_open_file_handles": self.peak_load_host_open_file_handles,
            "latest_guardrail": {
                "level": self.latest_guardrail.level.value,
                "reasons": list(self.latest_guardrail.reasons),
            },
        }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def uses_burst_sampling(case: CaseSpec) -> bool:
    kind = case.load.get("kind")
    return isinstance(kind, str) and kind in _BURST_CASE_KINDS


def uses_directory_checkpoint_sampling(case: CaseSpec) -> bool:
    kind = case.load.get("kind")
    return isinstance(kind, str) and kind in _DIRECTORY_CHECKPOINT_CASE_KINDS


def _merge_stop_reasons(
    assessment: GuardrailAssessment,
    reasons: Sequence[str],
) -> GuardrailAssessment:
    if not reasons:
        return assessment
    unique = tuple(dict.fromkeys((*assessment.reasons, *reasons)))
    return GuardrailAssessment(GuardrailLevel.STOP, unique)


def _mapping_delta(before: Mapping[str, int], after: Mapping[str, int]) -> dict[str, int]:
    missing = sorted(set(before) - set(after))
    if missing:
        raise ValueError("Gateway 实例累计指标在末帧缺失: " + ", ".join(missing))
    result: dict[str, int] = {}
    for key, current in after.items():
        previous = before.get(key, 0)
        if current < previous:
            raise ValueError(f"Gateway 实例累计指标发生回退: {key}")
        result[key] = current - previous
    return result


def _container_restart_delta(
    first: Sequence[ContainerMetrics],
    last: Sequence[ContainerMetrics],
) -> dict[str, int]:
    first_by_id = {item.container_id: item for item in first}
    result: dict[str, int] = {}
    for current in last:
        previous = first_by_id.get(current.container_id)
        baseline = previous.restart_count if previous is not None else 0
        if current.restart_count < baseline:
            raise ValueError(f"容器重启计数发生回退: {current.compose_service}")
        result[current.compose_service] = max(
            result.get(current.compose_service, 0),
            current.restart_count - baseline,
        )
    return result


def summarize_runtime_metrics(samples: Sequence[RuntimeMetricSample]) -> RuntimeMetricSummary:
    if not samples:
        raise ValueError("运行时指标时序不能为空")
    ordered = sorted(samples, key=lambda item: item.monotonic_seconds)
    if len({item.monotonic_seconds for item in ordered}) != len(ordered):
        raise ValueError("运行时指标时序不能包含重复单调时间")
    first = ordered[0]
    last = ordered[-1]
    if last.target_host.oom_events < first.target_host.oom_events:
        raise ValueError("宿主机 OOM 累计指标发生回退")
    if last.load_host.network_receive_bytes < first.load_host.network_receive_bytes:
        raise ValueError("负载机网络接收累计指标发生回退")
    if last.load_host.network_transmit_bytes < first.load_host.network_transmit_bytes:
        raise ValueError("负载机网络发送累计指标发生回退")

    peak_inflight: dict[str, int] = {}
    peak_active_leases: dict[str, int] = {}
    peak_declared_capacity: dict[str, int] = {}
    peak_gpu_utilization: dict[str, float] = {}
    peak_gpu_memory: dict[str, int] = {}
    gpu_pids: dict[str, set[int]] = {}
    filesystem_minimums: dict[str, int] = {}
    for sample in ordered:
        for instance in sample.control.instances:
            peak_inflight[instance.instance_id] = max(
                peak_inflight.get(instance.instance_id, 0), instance.inflight
            )
            peak_active_leases[instance.instance_id] = max(
                peak_active_leases.get(instance.instance_id, 0), instance.active_leases
            )
            peak_declared_capacity[instance.instance_id] = max(
                peak_declared_capacity.get(instance.instance_id, 0),
                instance.declared_capacity,
            )
        for gpu in sample.gpus:
            peak_gpu_utilization[gpu.uuid] = max(
                peak_gpu_utilization.get(gpu.uuid, 0.0), gpu.utilization_percent
            )
            peak_gpu_memory[gpu.uuid] = max(peak_gpu_memory.get(gpu.uuid, 0), gpu.memory_used_bytes)
            gpu_pids.setdefault(gpu.uuid, set()).update(process.pid for process in gpu.processes)
        for filesystem in sample.target_host.filesystems:
            filesystem_minimums[filesystem.requested_path] = min(
                filesystem_minimums.get(filesystem.requested_path, filesystem.available_bytes),
                filesystem.available_bytes,
            )

    directory_samples = tuple(item for item in ordered if item.target_host.directory_sizes)
    directory_before = (
        {
            item.requested_path: item.size_bytes
            for item in directory_samples[0].target_host.directory_sizes
        }
        if directory_samples
        else {}
    )
    directory_after = (
        {
            item.requested_path: item.size_bytes
            for item in directory_samples[-1].target_host.directory_sizes
        }
        if len(directory_samples) >= 2
        else {}
    )
    if directory_after and set(directory_before) != set(directory_after):
        raise ValueError("目标机目录字节指标在前后检查点不一致")

    return RuntimeMetricSummary(
        sample_count=len(ordered),
        started_at=first.recorded_at,
        finished_at=last.recorded_at,
        gateway_delta=counter_delta(first.gateway.counters, last.gateway.counters),
        gateway_instance_request_delta=_mapping_delta(
            first.gateway.instance_requests,
            last.gateway.instance_requests,
        ),
        peak_inflight=peak_inflight,
        peak_active_leases=peak_active_leases,
        peak_declared_capacity=peak_declared_capacity,
        max_kafka_lag=max(item.control.kafka_lag for item in ordered),
        max_task_queue_depth=max(item.control.task_queue_depth for item in ordered),
        max_outbox_pending=max(item.control.outbox_pending for item in ordered),
        container_restart_delta=_container_restart_delta(first.containers, last.containers),
        peak_gpu_utilization=peak_gpu_utilization,
        peak_gpu_memory_bytes=peak_gpu_memory,
        gpu_process_pids={
            gpu_uuid: tuple(sorted(pids)) for gpu_uuid, pids in sorted(gpu_pids.items())
        },
        minimum_target_filesystem_available_bytes=filesystem_minimums,
        target_directory_bytes_before=directory_before,
        target_directory_bytes_after=directory_after,
        target_directory_bytes_delta={
            path: directory_after[path] - size for path, size in directory_before.items()
        }
        if directory_after
        else {},
        minimum_target_memory_available_bytes=min(
            item.target_host.memory_available_bytes for item in ordered
        ),
        target_oom_delta=last.target_host.oom_events - first.target_host.oom_events,
        peak_load_host_cpu_percent=max(item.load_host.cpu_percent for item in ordered),
        minimum_load_host_memory_available_bytes=min(
            item.load_host.memory_available_bytes for item in ordered
        ),
        load_host_network_receive_delta_bytes=(
            last.load_host.network_receive_bytes - first.load_host.network_receive_bytes
        ),
        load_host_network_transmit_delta_bytes=(
            last.load_host.network_transmit_bytes - first.load_host.network_transmit_bytes
        ),
        peak_load_host_open_sockets=max(item.load_host.open_socket_count for item in ordered),
        peak_load_host_open_file_handles=max(
            item.load_host.open_file_handle_count for item in ordered
        ),
        latest_guardrail=last.guardrail,
    )


class RuntimeMetricsAdapter:
    def __init__(
        self,
        plan: CampaignPlan,
        release_root: Path,
        *,
        load_host_probe: CollectProbe[LoadHostMetrics],
        target_host_probe: CollectProbe[TargetHostMetrics],
        directory_size_probe: Callable[[], tuple[DirectorySizeMetrics, ...]] | None = None,
        docker_probe: CollectProbe[tuple[ContainerMetrics, ...]],
        gpu_probe: CollectProbe[tuple[GpuMetrics, ...]],
        control_probe: CollectProbe[ControlPlaneMetrics],
        gateway_probe: CollectProbe[GatewayMetrics],
        schedule: SamplingSchedule | None = None,
        guardrail_policy: GuardrailPolicy | None = None,
        database_services: Sequence[str] = (),
        critical_container_services: Sequence[str] = (),
        restart_loop_threshold: int = 3,
        restart_loop_window_seconds: float = 60.0,
        expected_gpu_by_pid: Mapping[int, str] | None = None,
        critical_gpu_error_probe: CriticalGpuErrorProbe | None = None,
        maintenance_lock_probe: MaintenanceLockProbe | None = None,
        evidence_writer: EvidenceWriter = atomic_write_report,
        wall_clock: Callable[[], str] = _utc_now,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not release_root.is_absolute():
            raise ValueError("release_root 必须是绝对路径")
        if type(restart_loop_threshold) is not int or restart_loop_threshold <= 0:
            raise ValueError("容器连续重启阈值必须是正整数")
        if (
            isinstance(restart_loop_window_seconds, bool)
            or not isinstance(restart_loop_window_seconds, (int, float))
            or not math.isfinite(restart_loop_window_seconds)
            or restart_loop_window_seconds <= 0
        ):
            raise ValueError("容器连续重启窗口必须是有限正数")
        databases = tuple(database_services)
        critical = tuple(critical_container_services)
        if any(_SAFE_SERVICE.fullmatch(item) is None for item in (*databases, *critical)):
            raise ValueError("关键容器服务名不安全")
        if len(databases) != len(set(databases)) or len(critical) != len(set(critical)):
            raise ValueError("关键容器服务名不能重复")
        gpu_assignment = dict(expected_gpu_by_pid or {})
        if any(
            type(pid) is not int or pid <= 0 or not uuid for pid, uuid in gpu_assignment.items()
        ):
            raise ValueError("预期 GPU 归属必须使用正整数 PID 和非空 UUID")

        self.plan = plan
        self.release_root = release_root.resolve()
        self.load_host_probe = load_host_probe
        self.target_host_probe = target_host_probe
        self.directory_size_probe = directory_size_probe
        self.docker_probe = docker_probe
        self.gpu_probe = gpu_probe
        self.control_probe = control_probe
        self.gateway_probe = gateway_probe
        self.schedule = schedule or SamplingSchedule()
        self.guardrail_policy = guardrail_policy or GuardrailPolicy()
        self.database_services = databases
        self.critical_container_services = critical
        self.restart_loop_threshold = restart_loop_threshold
        self.restart_loop_window_seconds = float(restart_loop_window_seconds)
        self.expected_gpu_by_pid = gpu_assignment
        self.critical_gpu_error_probe = critical_gpu_error_probe
        self.maintenance_lock_probe = maintenance_lock_probe
        self.evidence_writer = evidence_writer
        self.wall_clock = wall_clock
        self.monotonic_clock = monotonic_clock

        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._sampling_task: asyncio.Task[None] | None = None
        self._active_case: CaseSpec | None = None
        self._active_burst = False
        self._samples: dict[str, list[RuntimeMetricSample]] = {}
        self._window_start: dict[str, int] = {}
        self._next_sequence: dict[str, int] = {}
        self._oom_baseline: dict[str, int] = {}
        self._restart_history: dict[str, deque[tuple[float, int, str]]] = {}
        self._latest_assessment = GuardrailAssessment(GuardrailLevel.CLEAR, ())
        self._latched_stop_reasons: tuple[str, ...] = ()

    @property
    def is_running(self) -> bool:
        return self._sampling_task is not None and not self._sampling_task.done()

    @property
    def latest_assessment(self) -> GuardrailAssessment:
        return self._latest_assessment

    def samples(self, case_id: str) -> tuple[RuntimeMetricSample, ...]:
        return tuple(self._samples.get(case_id, ()))

    def summary(self, case_id: str) -> RuntimeMetricSummary:
        samples = self._samples.get(case_id, ())
        start = self._window_start.get(case_id, 0)
        return summarize_runtime_metrics(samples[start:])

    def _prepare_window(self, case: CaseSpec) -> None:
        if self._sampling_task is not None:
            raise RuntimeError("重置指标窗口前必须先停止现有采样")
        self._window_start[case.case_id] = len(self._samples.get(case.case_id, ()))
        self._oom_baseline.pop(case.case_id, None)
        self._restart_history.clear()

    async def _collect_surfaces(
        self,
        *,
        include_directory_sizes: bool,
    ) -> tuple[
        LoadHostMetrics,
        TargetHostMetrics,
        tuple[ContainerMetrics, ...],
        tuple[GpuMetrics, ...],
        ControlPlaneMetrics,
        GatewayMetrics,
        tuple[str, ...],
        bool,
    ]:
        critical_errors = self.critical_gpu_error_probe or (lambda: ())
        maintenance_lock = self.maintenance_lock_probe or (lambda: True)
        async with asyncio.TaskGroup() as group:
            load_host_task = group.create_task(asyncio.to_thread(self.load_host_probe.collect))
            target_host_task = group.create_task(asyncio.to_thread(self.target_host_probe.collect))
            containers_task = group.create_task(asyncio.to_thread(self.docker_probe.collect))
            gpus_task = group.create_task(asyncio.to_thread(self.gpu_probe.collect))
            control_task = group.create_task(asyncio.to_thread(self.control_probe.collect))
            gateway_task = group.create_task(asyncio.to_thread(self.gateway_probe.collect))
            critical_errors_task = group.create_task(asyncio.to_thread(critical_errors))
            maintenance_lock_task = group.create_task(asyncio.to_thread(maintenance_lock))
        load_host = load_host_task.result()
        target_host = target_host_task.result()
        if include_directory_sizes:
            if self.directory_size_probe is None:
                raise RuntimeError("长课目录字节检查点缺少固定路径探针")
            directory_sizes = await asyncio.to_thread(self.directory_size_probe)
            target_host = replace(target_host, directory_sizes=tuple(directory_sizes))
        containers = containers_task.result()
        gpus = gpus_task.result()
        control = control_task.result()
        gateway = gateway_task.result()
        errors: tuple[str, ...] = tuple(critical_errors_task.result())
        lock_owned = maintenance_lock_task.result()
        if any(
            not isinstance(item, str) or _SAFE_CRITICAL_ERROR.fullmatch(item) is None
            for item in errors
        ):
            raise ValueError("GPU 严重错误探针返回了非脱敏标识")
        if type(lock_owned) is not bool:
            raise ValueError("维护锁探针必须返回布尔值")
        return (
            load_host,
            target_host,
            containers,
            gpus,
            control,
            gateway,
            errors,
            lock_owned,
        )

    def _restart_loop_containers(
        self,
        containers: Sequence[ContainerMetrics],
        monotonic_seconds: float,
    ) -> tuple[str, ...]:
        loops: set[str] = set()
        observed_ids = {item.container_id for item in containers}
        for container_id in tuple(self._restart_history):
            if container_id not in observed_ids:
                del self._restart_history[container_id]
        for container in containers:
            history = self._restart_history.setdefault(container.container_id, deque())
            history.append((monotonic_seconds, container.restart_count, container.compose_service))
            cutoff = monotonic_seconds - self.restart_loop_window_seconds
            while len(history) > 1 and history[0][0] < cutoff:
                history.popleft()
            if container.restart_count < history[0][1]:
                loops.add(container.compose_service)
            elif container.restart_count - history[0][1] >= self.restart_loop_threshold:
                loops.add(container.compose_service)
        return tuple(sorted(loops))

    def _gpu_assignment_valid(self, gpus: Sequence[GpuMetrics]) -> bool:
        observed: dict[int, str] = {}
        for gpu in gpus:
            for process in gpu.processes:
                previous = observed.setdefault(process.pid, gpu.uuid)
                if previous != gpu.uuid:
                    return False
        return all(
            pid not in observed or observed[pid] == expected_uuid
            for pid, expected_uuid in self.expected_gpu_by_pid.items()
        )

    def _assessment(
        self,
        *,
        case_id: str,
        target_host: TargetHostMetrics,
        containers: tuple[ContainerMetrics, ...],
        gpus: tuple[GpuMetrics, ...],
        critical_errors: tuple[str, ...],
        lock_owned: bool,
        monotonic_seconds: float,
    ) -> GuardrailAssessment:
        baseline = self._oom_baseline.setdefault(case_id, target_host.oom_events)
        by_service: dict[str, list[ContainerMetrics]] = {}
        for container in containers:
            by_service.setdefault(container.compose_service, []).append(container)
        database_health = {
            service: bool(by_service.get(service))
            and all(container.healthy for container in by_service[service])
            for service in self.database_services
        }
        observation = GuardrailObservation(
            storage=tuple(
                StorageObservation(
                    name=f"target:{filesystem.requested_path}",
                    total_bytes=filesystem.total_bytes,
                    free_bytes=filesystem.available_bytes,
                )
                for filesystem in target_host.filesystems
            ),
            gpu_critical_errors=critical_errors,
            host_oom=target_host.oom_events > baseline,
            restart_loop_containers=self._restart_loop_containers(containers, monotonic_seconds),
            database_health=database_health,
            maintenance_lock_owned=lock_owned,
            gpu_assignment_valid=self._gpu_assignment_valid(gpus),
        )
        assessment = evaluate_guardrails(observation, self.guardrail_policy)
        unhealthy_critical = tuple(
            f"关键容器不健康或缺失: {service}"
            for service in self.critical_container_services
            if not by_service.get(service)
            or not all(container.healthy for container in by_service[service])
        )
        assessment = _merge_stop_reasons(assessment, unhealthy_critical)
        return _merge_stop_reasons(assessment, self._latched_stop_reasons)

    def _evidence_path(self, case: CaseSpec, sequence: int) -> Path:
        return (
            self.release_root
            / "campaign"
            / "runtime-metrics"
            / case.case_id
            / f"{sequence:08d}.json"
        )

    def _latch_stop(self, reason: str) -> GuardrailAssessment:
        self._latched_stop_reasons = tuple(dict.fromkeys((*self._latched_stop_reasons, reason)))
        self._latest_assessment = _merge_stop_reasons(
            self._latest_assessment,
            self._latched_stop_reasons,
        )
        return self._latest_assessment

    async def _sample_once(
        self,
        case: CaseSpec,
        *,
        burst: bool,
        include_directory_sizes: bool = False,
    ) -> RuntimeMetricSample | None:
        async with self._lock:
            try:
                (
                    load_host,
                    target_host,
                    containers,
                    gpus,
                    control,
                    gateway,
                    critical_errors,
                    lock_owned,
                ) = await self._collect_surfaces(
                    include_directory_sizes=include_directory_sizes
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._latch_stop(f"运行时指标采集失败: {type(error).__name__}")
                return None

            monotonic_seconds = self.monotonic_clock()
            sequence = self._next_sequence.get(case.case_id, 1)
            self._next_sequence[case.case_id] = sequence + 1
            assessment = self._assessment(
                case_id=case.case_id,
                target_host=target_host,
                containers=containers,
                gpus=gpus,
                critical_errors=critical_errors,
                lock_owned=lock_owned,
                monotonic_seconds=monotonic_seconds,
            )
            sample = RuntimeMetricSample(
                campaign_id=self.plan.campaign_id,
                case_id=case.case_id,
                phase=case.phase.value,
                sequence=sequence,
                recorded_at=self.wall_clock(),
                monotonic_seconds=monotonic_seconds,
                burst=burst,
                load_host=load_host,
                target_host=target_host,
                containers=containers,
                gpus=gpus,
                control=control,
                gateway=gateway,
                guardrail=assessment,
            )
            path = self._evidence_path(case, sequence)
            try:
                document = sample.to_document()
                validate_public_payload(document)
                content = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                await asyncio.to_thread(
                    self.evidence_writer,
                    path,
                    content,
                )
            except Exception:
                assessment = self._latch_stop("证据无法原子写入")
                sample = replace(sample, guardrail=assessment)
            else:
                sample = replace(sample, evidence_path=path)
                self._latest_assessment = assessment
            self._samples.setdefault(case.case_id, []).append(sample)
            return sample

    async def _sampling_loop(self, case: CaseSpec, *, burst: bool) -> None:
        interval = self.schedule.burst_seconds if burst else self.schedule.regular_seconds
        next_deadline = self.monotonic_clock() + interval
        try:
            while not self._stop_event.is_set():
                delay = max(0.0, next_deadline - self.monotonic_clock())
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                except TimeoutError:
                    pass
                if self._stop_event.is_set():
                    return
                sample = await self._sample_once(case, burst=burst)
                if sample is None or sample.guardrail.level is GuardrailLevel.STOP:
                    return
                now = self.monotonic_clock()
                next_deadline += interval
                if next_deadline <= now:
                    missed = math.floor((now - next_deadline) / interval) + 1
                    next_deadline += missed * interval
        except asyncio.CancelledError:
            raise

    async def start(self, case: CaseSpec, *, burst: bool = False) -> GuardrailAssessment:
        if type(burst) is not bool:
            raise ValueError("burst 必须是布尔值")
        if self._sampling_task is not None:
            raise RuntimeError("运行时指标采样已启动")
        self._prepare_window(case)
        self._stop_event = asyncio.Event()
        self._active_case = case
        self._active_burst = burst
        sample = await self._sample_once(
            case,
            burst=burst,
            include_directory_sizes=uses_directory_checkpoint_sampling(case),
        )
        assessment = sample.guardrail if sample is not None else self._latest_assessment
        if assessment.level is not GuardrailLevel.CLEAR:
            self._active_case = None
            return assessment
        self._sampling_task = asyncio.create_task(
            self._sampling_loop(case, burst=burst),
            name=f"runtime-metrics-{case.case_id}",
        )
        return assessment

    async def stop(self) -> RuntimeMetricSummary | None:
        task = self._sampling_task
        case = self._active_case
        if task is None or case is None:
            return None
        self._stop_event.set()
        await task
        self._sampling_task = None
        self._active_case = None
        await self._sample_once(
            case,
            burst=self._active_burst,
            include_directory_sizes=uses_directory_checkpoint_sampling(case),
        )
        return self.summary(case.case_id)

    async def assess(
        self,
        case: CaseSpec,
        checkpoint: StageCheckpoint,
    ) -> GuardrailAssessment:
        if checkpoint == "before":
            return await self.start(case, burst=uses_burst_sampling(case))
        if self._active_case is not None:
            if self._active_case.case_id != case.case_id:
                raise RuntimeError("不能使用其他用例的采样任务完成后置评估")
            await self.stop()
            samples = self.samples(case.case_id)
            sample = samples[-1] if samples else None
        else:
            sample = await self._sample_once(
                case,
                burst=uses_burst_sampling(case),
                include_directory_sizes=uses_directory_checkpoint_sampling(case),
            )
        return sample.guardrail if sample is not None else self._latest_assessment

    async def execute(self, case: CaseSpec) -> StageCaseOutcome:
        if self._active_case is not None:
            if self._active_case.case_id != case.case_id:
                raise RuntimeError("不能执行其他用例的指标摘要")
            await self.stop()
        elif len(self._samples.get(case.case_id, ())) <= self._window_start.get(
            case.case_id, 0
        ):
            await self._sample_once(
                case,
                burst=False,
                include_directory_sizes=uses_directory_checkpoint_sampling(case),
            )
        assessment = self._latest_assessment
        summary = self.summary(case.case_id)
        samples = self.samples(case.case_id)[self._window_start.get(case.case_id, 0) :]
        directory_checkpoint_count = sum(
            bool(sample.target_host.directory_sizes) for sample in samples
        )
        if uses_directory_checkpoint_sampling(case) and directory_checkpoint_count != 2:
            assessment = self._latch_stop("长课目录字节 before/after 检查点不完整")
        summary = replace(summary, latest_guardrail=assessment)
        evidence = {
            "runtime_metrics": summary.to_dict(),
            "sample_evidence": [
                str(sample.evidence_path.relative_to(self.release_root))
                for sample in samples
                if sample.evidence_path is not None
            ],
        }
        validate_public_payload(evidence)
        if assessment.level is GuardrailLevel.CLEAR:
            return StageCaseOutcome("passed", "运行时指标采集与护栏评估通过", evidence)
        return StageCaseOutcome(
            "blocked",
            f"运行时指标护栏为 {assessment.level.value}: " + "；".join(assessment.reasons),
            evidence,
        )
