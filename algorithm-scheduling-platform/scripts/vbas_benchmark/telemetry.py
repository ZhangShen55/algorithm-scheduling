from __future__ import annotations

import json
import math
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from .models import BenchmarkGuardrails, JsonObject, VbasTarget

CommandRunner = Callable[[Sequence[str]], str]


@dataclass(frozen=True, slots=True)
class GpuSample:
    recorded_at: str
    monotonic_seconds: float
    phase: str
    gpu_index: int
    memory_used_mib: float
    memory_total_mib: float
    utilization_percent: float
    power_watts: float
    process_count: int
    process_memory_mib: float
    container_name: str
    container_restart_count: int
    container_running: bool


@dataclass(frozen=True, slots=True)
class MemoryAssessment:
    status: str
    reason: str
    p50_mib: float
    p95_mib: float
    peak_mib: float
    first_half_p50_mib: float
    second_half_p50_mib: float
    half_delta_mib: float
    slope_mib_per_minute: float

    def to_document(self) -> JsonObject:
        return asdict(self)


class NvidiaDockerTelemetry:
    def __init__(
        self,
        targets: Sequence[VbasTarget],
        *,
        guardrails: BenchmarkGuardrails,
        command_runner: CommandRunner | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not targets:
            raise ValueError("显存采集至少需要一个目标")
        self._targets = tuple(targets)
        self._guardrails = guardrails
        self._run = command_runner or _run_command
        self._clock = monotonic_clock
        self.samples: list[GpuSample] = []
        self.collector_error: str | None = None
        self._initial_restarts: dict[str, int] | None = None
        self._latest_guardrail_reason: str | None = None

    @property
    def latest_guardrail_reason(self) -> str | None:
        return self._latest_guardrail_reason

    def collect(self, phase: str) -> tuple[GpuSample, ...]:
        try:
            gpu_rows = _parse_gpu_rows(
                self._run(
                    [
                        "nvidia-smi",
                        "--query-gpu=index,memory.used,memory.total,utilization.gpu,power.draw",
                        "--format=csv,noheader,nounits",
                    ]
                )
            )
            process_memory = _parse_process_rows(
                self._run(
                    [
                        "nvidia-smi",
                        "--query-compute-apps=gpu_uuid,pid,used_memory",
                        "--format=csv,noheader,nounits",
                    ]
                )
            )
            gpu_uuid = _parse_gpu_uuid_rows(
                self._run(
                    [
                        "nvidia-smi",
                        "--query-gpu=index,uuid",
                        "--format=csv,noheader,nounits",
                    ]
                )
            )
            container_state = self._container_states()
        except Exception as exc:  # noqa: BLE001 - 采集失败必须失败关闭并保留类型
            self.collector_error = type(exc).__name__
            self._latest_guardrail_reason = f"GPU/容器采集器中断: {type(exc).__name__}"
            return ()

        if self._initial_restarts is None:
            self._initial_restarts = {
                name: state[0] for name, state in container_state.items()
            }
        recorded_at = _utc_now()
        monotonic_seconds = self._clock()
        collected: list[GpuSample] = []
        for target in self._targets:
            row = gpu_rows.get(target.gpu_index)
            if row is None:
                raise ValueError(f"缺少 GPU {target.gpu_index} 采样")
            uuid = gpu_uuid.get(target.gpu_index)
            restart_count, running = container_state[target.container_name]
            process_values = process_memory.get(uuid or "", ())
            sample = GpuSample(
                recorded_at=recorded_at,
                monotonic_seconds=monotonic_seconds,
                phase=phase,
                gpu_index=target.gpu_index,
                memory_used_mib=row[0],
                memory_total_mib=row[1],
                utilization_percent=row[2],
                power_watts=row[3],
                process_count=len(process_values),
                process_memory_mib=sum(process_values),
                container_name=target.container_name,
                container_restart_count=restart_count,
                container_running=running,
            )
            collected.append(sample)
            if not running:
                self._latest_guardrail_reason = f"容器已停止: {target.container_name}"
            elif restart_count > self._initial_restarts[target.container_name]:
                self._latest_guardrail_reason = f"容器发生重启: {target.container_name}"
            elif sample.memory_used_mib > self._guardrails.max_gpu_memory_mib:
                self._latest_guardrail_reason = (
                    f"GPU {target.gpu_index} 显存越过硬护栏: "
                    f"{sample.memory_used_mib:.0f} MiB"
                )
        self.samples.extend(collected)
        return tuple(collected)

    def _container_states(self) -> dict[str, tuple[int, bool]]:
        output = self._run(
            [
                "docker",
                "inspect",
                "--format",
                '{{json .Name}} {{json .RestartCount}} {{json .State.Running}}',
                *[target.container_name for target in self._targets],
            ]
        )
        states: dict[str, tuple[int, bool]] = {}
        for line in output.splitlines():
            if not line.strip():
                continue
            name_raw, restart_raw, running_raw = line.split(maxsplit=2)
            name = str(json.loads(name_raw)).lstrip("/")
            restart_count = json.loads(restart_raw)
            running = json.loads(running_raw)
            if isinstance(restart_count, bool) or not isinstance(restart_count, int):
                raise TypeError("Docker RestartCount 不是整数")
            if not isinstance(running, bool):
                raise TypeError("Docker Running 不是布尔值")
            states[name] = (restart_count, running)
        expected = {target.container_name for target in self._targets}
        if set(states) != expected:
            raise ValueError("Docker inspect 容器集合与基准目标不一致")
        return states

    def to_document(self) -> JsonObject:
        return {
            "schema_version": 1,
            "evidence_type": "vbas_gpu_timeseries",
            "collector_error": self.collector_error,
            "samples": [asdict(sample) for sample in self.samples],
            "memory_assessment": {
                target.instance_id: assess_memory_stability(
                    [
                        sample
                        for sample in self.samples
                        if sample.gpu_index == target.gpu_index
                        and sample.phase == "steady"
                    ],
                    guardrails=self._guardrails,
                ).to_document()
                for target in self._targets
            },
        }


def assess_memory_stability(
    samples: Sequence[GpuSample],
    *,
    guardrails: BenchmarkGuardrails,
) -> MemoryAssessment:
    values = [sample.memory_used_mib for sample in samples]
    if len(values) < 4:
        return MemoryAssessment(
            status="insufficient_evidence",
            reason="稳态显存采样少于 4 个",
            p50_mib=_quantile(values, 0.50),
            p95_mib=_quantile(values, 0.95),
            peak_mib=max(values, default=0.0),
            first_half_p50_mib=0.0,
            second_half_p50_mib=0.0,
            half_delta_mib=0.0,
            slope_mib_per_minute=0.0,
        )
    midpoint = len(values) // 2
    first_p50 = _quantile(values[:midpoint], 0.50)
    second_p50 = _quantile(values[midpoint:], 0.50)
    delta = second_p50 - first_p50
    slope = _linear_slope_mib_per_minute(samples)
    peak = max(values)
    if peak > guardrails.max_gpu_memory_mib:
        status, reason = "unstable", "稳态显存越过硬护栏"
    elif delta > guardrails.memory_half_delta_tolerance_mib:
        status, reason = "unstable", "稳态后半段显存分布持续高于前半段"
    elif slope > guardrails.memory_slope_tolerance_mib_per_minute:
        status, reason = "unstable", "稳态显存趋势仍持续向上"
    else:
        status, reason = "stable", "预热后形成稳定显存平台"
    return MemoryAssessment(
        status=status,
        reason=reason,
        p50_mib=_quantile(values, 0.50),
        p95_mib=_quantile(values, 0.95),
        peak_mib=peak,
        first_half_p50_mib=first_p50,
        second_half_p50_mib=second_p50,
        half_delta_mib=delta,
        slope_mib_per_minute=slope,
    )


def _parse_gpu_rows(output: str) -> dict[int, tuple[float, float, float, float]]:
    rows: dict[int, tuple[float, float, float, float]] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        fields = [item.strip() for item in line.split(",")]
        if len(fields) != 5:
            raise ValueError("nvidia-smi GPU 输出列数不正确")
        index = int(fields[0])
        values = tuple(_parse_number(item) for item in fields[1:])
        rows[index] = (values[0], values[1], values[2], values[3])
    return rows


def _parse_gpu_uuid_rows(output: str) -> dict[int, str]:
    rows: dict[int, str] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        raw_index, raw_uuid = line.split(",", maxsplit=1)
        rows[int(raw_index.strip())] = raw_uuid.strip()
    return rows


def _parse_process_rows(output: str) -> dict[str, tuple[float, ...]]:
    rows: dict[str, list[float]] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        fields = [item.strip() for item in line.split(",")]
        if len(fields) != 3:
            raise ValueError("nvidia-smi 进程输出列数不正确")
        rows.setdefault(fields[0], []).append(_parse_number(fields[2]))
    return {key: tuple(value) for key, value in rows.items()}


def _parse_number(value: str) -> float:
    if value in {"N/A", "[N/A]", ""}:
        return 0.0
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError("nvidia-smi 数值不是有限非负数")
    return parsed


def _linear_slope_mib_per_minute(samples: Sequence[GpuSample]) -> float:
    if len(samples) < 2:
        return 0.0
    origin = samples[0].monotonic_seconds
    xs = [sample.monotonic_seconds - origin for sample in samples]
    ys = [sample.memory_used_mib for sample in samples]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((value - mean_x) ** 2 for value in xs)
    if denominator == 0:
        return 0.0
    per_second = sum(
        (x_value - mean_x) * (y_value - mean_y)
        for x_value, y_value in zip(xs, ys, strict=True)
    ) / denominator
    return per_second * 60.0


def _quantile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _run_command(command: Sequence[str]) -> str:
    process = subprocess.run(
        list(command),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return process.stdout


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
