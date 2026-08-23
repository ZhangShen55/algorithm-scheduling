from __future__ import annotations

import pytest

from scripts.extreme_load.metrics import (
    ContainerMetrics,
    GatewayCounters,
    GpuMetrics,
    GpuProcessMetrics,
    HostMetrics,
    InstanceCapacityMetrics,
    MetricFrame,
    MetricProbes,
    SamplingSchedule,
    collect_frame,
    counter_delta,
    sample_regular_series,
    sample_series,
    sample_short_lease_series,
    summarize_metrics,
)


def _frame(
    *,
    sequence: int,
    counters: GatewayCounters,
    instance_requests: dict[str, int] | None = None,
) -> MetricFrame:
    return MetricFrame(
        recorded_at=f"2026-08-23T00:00:0{sequence}Z",
        monotonic_seconds=float(sequence),
        host=HostMetrics(
            filesystem_total_bytes=1_000,
            filesystem_free_bytes=500,
            memory_total_bytes=1_000,
            memory_available_bytes=700,
            oom_events=0,
            cpu_percent=20.0 + sequence,
            network_receive_bytes=100 + sequence * 10,
            network_transmit_bytes=200 + sequence * 20,
            open_socket_count=30 + sequence,
            open_file_handle_count=40 + sequence,
        ),
        containers=(
            ContainerMetrics(
                container_id="a" * 64,
                compose_project="algorithm-platform",
                compose_service="online-gateway-service",
                cpu_percent=10.0,
                memory_bytes=100,
                restart_count=sequence,
                healthy=True,
            ),
        ),
        gpus=(
            GpuMetrics(
                uuid="GPU-0",
                utilization_percent=20.0 + sequence,
                memory_used_bytes=200,
                processes=(GpuProcessMetrics(pid=100 + sequence, name="ocr", memory_bytes=50),),
            ),
        ),
        kafka_lag=sequence * 3,
        task_queue_depth=sequence * 2,
        instances=(
            InstanceCapacityMetrics(
                instance_id="ocr-gpu0",
                inflight=sequence,
                active_leases=sequence + 1,
                declared_capacity=256,
            ),
        ),
        gateway_counters=counters,
        gateway_instance_requests=instance_requests or {},
    )


def test_sampling_schedule_enforces_regular_and_burst_contract() -> None:
    assert SamplingSchedule(regular_seconds=5.0, burst_seconds=0.5).regular_seconds == 5
    assert SamplingSchedule(regular_seconds=1.0, burst_seconds=1.0).burst_seconds == 1

    with pytest.raises(ValueError, match="常规采样"):
        SamplingSchedule(regular_seconds=5.1, burst_seconds=0.5)
    with pytest.raises(ValueError, match="突发采样"):
        SamplingSchedule(regular_seconds=5.0, burst_seconds=0.49)
    with pytest.raises(ValueError, match="突发采样"):
        SamplingSchedule(regular_seconds=5.0, burst_seconds=1.01)


def test_collect_frame_calls_every_required_probe() -> None:
    calls: list[str] = []

    def mark(name: str, value: object):  # type: ignore[no-untyped-def]
        def probe():  # type: ignore[no-untyped-def]
            calls.append(name)
            return value

        return probe

    probes = MetricProbes(
        host=mark("host", _frame(sequence=0, counters=GatewayCounters()).host),
        containers=mark("containers", ()),
        gpus=mark("gpus", ()),
        kafka_lag=mark("kafka", 7),
        task_queue_depth=mark("queue", 9),
        instances=mark("instances", ()),
        gateway_counters=mark("gateway", GatewayCounters(requests_total=3)),
        gateway_instance_requests=mark("gateway_instances", {"ocr-gpu0": 3}),
    )

    frame = collect_frame(
        probes,
        wall_clock=lambda: "2026-08-23T00:00:00Z",
        monotonic_clock=lambda: 12.5,
    )

    assert calls == [
        "host",
        "containers",
        "gpus",
        "kafka",
        "queue",
        "instances",
        "gateway",
        "gateway_instances",
    ]
    assert frame.kafka_lag == 7
    assert frame.task_queue_depth == 9
    assert frame.gateway_counters.requests_total == 3
    assert frame.gateway_instance_requests == {"ocr-gpu0": 3}


def test_short_lease_evidence_uses_peak_samples_and_counter_deltas() -> None:
    first = _frame(
        sequence=0,
        counters=GatewayCounters(
            requests_total=100,
            lease_acquired_total=80,
            lease_rejected_total=20,
            lease_released_total=80,
        ),
        instance_requests={"ocr-gpu0": 60, "ocr-gpu1": 20},
    )
    second = _frame(
        sequence=2,
        counters=GatewayCounters(
            requests_total=180,
            lease_acquired_total=130,
            lease_rejected_total=50,
            lease_released_total=129,
        ),
        instance_requests={"ocr-gpu0": 90, "ocr-gpu1": 40},
    )

    summary = summarize_metrics((first, second))

    assert summary.gateway_delta.requests_total == 80
    assert summary.gateway_delta.lease_acquired_total == 50
    assert summary.gateway_delta.lease_rejected_total == 30
    assert summary.gateway_delta.lease_released_total == 49
    assert summary.peak_active_leases == {"ocr-gpu0": 3}
    assert summary.peak_inflight == {"ocr-gpu0": 2}
    assert summary.max_kafka_lag == 6
    assert summary.max_task_queue_depth == 4
    assert summary.container_restart_delta == {"online-gateway-service": 2}
    assert summary.gateway_instance_request_delta == {"ocr-gpu0": 30, "ocr-gpu1": 20}
    assert summary.peak_container_cpu_percent == {"online-gateway-service": 10.0}
    assert summary.peak_container_memory_bytes == {"online-gateway-service": 100}
    assert summary.peak_gpu_memory_bytes == {"GPU-0": 200}
    assert summary.gpu_process_names == {"GPU-0": ("ocr",)}
    assert summary.minimum_host_memory_available_bytes == 700
    assert summary.peak_host_cpu_percent == 22.0
    assert summary.host_network_receive_delta_bytes == 20
    assert summary.host_network_transmit_delta_bytes == 40
    assert summary.peak_host_open_sockets == 32
    assert summary.peak_host_open_file_handles == 42


def test_counter_reset_is_not_silently_reported_as_a_negative_delta() -> None:
    with pytest.raises(ValueError, match="累计指标发生回退"):
        counter_delta(
            GatewayCounters(requests_total=10),
            GatewayCounters(requests_total=9),
        )


def test_sample_series_uses_bounded_schedule_without_extra_sleep() -> None:
    ticks = iter((0.0, 0.0, 0.5, 1.0, 1.5))
    sleeps: list[float] = []
    sequences = iter(range(10))

    frames = sample_series(
        lambda: _frame(sequence=next(sequences), counters=GatewayCounters()),
        duration_seconds=1.0,
        interval_seconds=0.5,
        monotonic_clock=lambda: next(ticks),
        sleep=lambda seconds: sleeps.append(seconds),
    )

    assert len(frames) == 3
    assert sleeps == [0.5, 0.5]


def test_regular_and_short_lease_samplers_use_the_configured_cadence() -> None:
    schedule = SamplingSchedule(regular_seconds=5.0, burst_seconds=0.5)
    regular_ticks = iter((0.0, 0.0, 5.0))
    burst_ticks = iter((0.0, 0.0, 0.5))
    regular_sleeps: list[float] = []
    burst_sleeps: list[float] = []

    regular = sample_regular_series(
        lambda: "regular",
        duration_seconds=5.0,
        schedule=schedule,
        monotonic_clock=lambda: next(regular_ticks),
        sleep=regular_sleeps.append,
    )
    burst = sample_short_lease_series(
        lambda: "burst",
        duration_seconds=0.5,
        schedule=schedule,
        monotonic_clock=lambda: next(burst_ticks),
        sleep=burst_sleeps.append,
    )

    assert regular == ("regular", "regular")
    assert burst == ("burst", "burst")
    assert regular_sleeps == [5.0]
    assert burst_sleeps == [0.5]


def test_counter_reset_inside_phase_fails_even_if_last_value_recovers() -> None:
    first = _frame(
        sequence=0,
        counters=GatewayCounters(requests_total=100),
        instance_requests={"ocr-gpu0": 100},
    )
    reset = _frame(
        sequence=1,
        counters=GatewayCounters(requests_total=1),
        instance_requests={"ocr-gpu0": 1},
    )
    recovered = _frame(
        sequence=2,
        counters=GatewayCounters(requests_total=110),
        instance_requests={"ocr-gpu0": 110},
    )

    with pytest.raises(ValueError, match="累计指标发生回退"):
        summarize_metrics((first, reset, recovered))
