from __future__ import annotations

from scripts.vbas_benchmark.models import BenchmarkGuardrails, VbasTarget
from scripts.vbas_benchmark.telemetry import (
    GpuSample,
    NvidiaDockerTelemetry,
    assess_memory_stability,
)


def _sample(index: int, memory: float) -> GpuSample:
    return GpuSample(
        recorded_at=f"2026-09-07T00:00:{index:02d}Z",
        monotonic_seconds=float(index * 60),
        phase="steady",
        gpu_index=0,
        memory_used_mib=memory,
        memory_total_mib=24_000,
        utilization_percent=90,
        power_watts=180,
        process_count=1,
        process_memory_mib=memory,
        container_name="vbas0",
        container_restart_count=0,
        container_running=True,
    )


def test_memory_assessment_accepts_warmed_stable_platform() -> None:
    result = assess_memory_stability(
        [_sample(index, value) for index, value in enumerate((4500, 4520, 4510, 4525, 4515, 4520))],
        guardrails=BenchmarkGuardrails(),
    )

    assert result.status == "stable"
    assert result.peak_mib == 4525


def test_memory_assessment_rejects_continuing_growth() -> None:
    result = assess_memory_stability(
        [_sample(index, value) for index, value in enumerate((4500, 4600, 4700, 5000, 5300, 5600))],
        guardrails=BenchmarkGuardrails(
            memory_half_delta_tolerance_mib=128,
            memory_slope_tolerance_mib_per_minute=64,
        ),
    )

    assert result.status == "unstable"
    assert result.half_delta_mib > 128


def test_memory_assessment_accepts_mid_run_cache_step_then_recent_plateau() -> None:
    samples = [
        GpuSample(
            recorded_at="2026-09-07T00:00:00Z",
            monotonic_seconds=float(index),
            phase="steady",
            gpu_index=0,
            memory_used_mib=4500 if index < 310 else 5080,
            memory_total_mib=24_000,
            utilization_percent=90,
            power_watts=180,
            process_count=1,
            process_memory_mib=5080,
            container_name="vbas0",
            container_restart_count=0,
            container_running=True,
        )
        for index in range(601)
    ]

    result = assess_memory_stability(
        samples,
        guardrails=BenchmarkGuardrails(
            memory_half_delta_tolerance_mib=256,
            memory_slope_tolerance_mib_per_minute=64,
        ),
    )

    assert result.status == "stable"
    assert result.peak_mib == 5080
    assert result.half_delta_mib == 0
    assert result.slope_mib_per_minute < 64


def test_collector_failure_latches_guardrail_without_losing_evidence() -> None:
    def broken(_command: object) -> str:
        raise OSError("nvidia-smi unavailable")

    collector = NvidiaDockerTelemetry(
        (VbasTarget("vbas-gpu0", "http://vbas0:8981", 0, "vbas0"),),
        guardrails=BenchmarkGuardrails(),
        command_runner=broken,
    )

    assert collector.collect("M_ready") == ()
    assert collector.collector_error == "OSError"
    assert collector.latest_guardrail_reason == "GPU/容器采集器中断: OSError"


def test_collector_stops_on_memory_guardrail_and_container_restart() -> None:
    outputs = iter(
        (
            "0, 23001, 24576, 80, 200\n",
            "GPU-0, 42, 22000\n",
            "0, GPU-0\n",
            '"/vbas0" 0 true\n',
        )
    )
    collector = NvidiaDockerTelemetry(
        (VbasTarget("vbas-gpu0", "http://vbas0:8981", 0, "vbas0"),),
        guardrails=BenchmarkGuardrails(max_gpu_memory_mib=22_000),
        command_runner=lambda _command: next(outputs),
    )

    samples = collector.collect("steady")

    assert len(samples) == 1
    assert collector.latest_guardrail_reason == "GPU 0 显存越过硬护栏: 23001 MiB"

    outputs = iter(
        (
            "0, 5000, 24576, 80, 200\n",
            "GPU-0, 42, 4500\n",
            "0, GPU-0\n",
            '"/vbas0" 1 true\n',
        )
    )
    collector._run = lambda _command: next(outputs)  # type: ignore[method-assign]
    collector.collect("steady")
    assert collector.latest_guardrail_reason == "容器发生重启: vbas0"
