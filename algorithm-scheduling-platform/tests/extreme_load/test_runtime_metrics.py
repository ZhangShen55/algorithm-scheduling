from __future__ import annotations

import asyncio
import json
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

import pytest

from scripts.extreme_load.catalog import (
    CampaignCatalog,
    CampaignPhase,
    CaseSpec,
    FixtureDescriptor,
    FixtureKind,
    FixtureManifest,
)
from scripts.extreme_load.guardrails import GiB, GuardrailLevel
from scripts.extreme_load.metrics import (
    ContainerMetrics,
    GatewayCounters,
    GpuMetrics,
    GpuProcessMetrics,
    InstanceCapacityMetrics,
    SamplingSchedule,
)
from scripts.extreme_load.plan import CampaignPlan, build_campaign_plan
from scripts.extreme_load.report import validate_public_payload
from scripts.extreme_load.runtime_metrics import RuntimeMetricsAdapter
from scripts.extreme_load.system_probes import (
    ControlPlaneMetrics,
    DirectorySizeMetrics,
    FilesystemMetrics,
    GatewayMetrics,
    LoadHostMetrics,
    TargetHostMetrics,
)

_T = TypeVar("_T")


class MutableProbe(Generic[_T]):
    def __init__(self, value: _T, *, delay_seconds: float = 0.0) -> None:
        self.value = value
        self.delay_seconds = delay_seconds
        self.calls = 0
        self.thread_ids: list[int] = []

    def collect(self) -> _T:
        self.calls += 1
        self.thread_ids.append(threading.get_ident())
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return self.value


@dataclass(slots=True)
class ProbeState:
    load_host: MutableProbe[LoadHostMetrics]
    target_host: MutableProbe[TargetHostMetrics]
    directory_sizes: MutableProbe[tuple[DirectorySizeMetrics, ...]]
    containers: MutableProbe[tuple[ContainerMetrics, ...]]
    gpus: MutableProbe[tuple[GpuMetrics, ...]]
    control: MutableProbe[ControlPlaneMetrics]
    gateway: MutableProbe[GatewayMetrics]


def _case(*, kind: str = "online_image") -> CaseSpec:
    return CaseSpec(
        case_id="ONLINE-RUNTIME-METRICS",
        phase=CampaignPhase.ONLINE,
        load={"kind": kind, "concurrency": 10},
        fixture_ids=("online-image",),
        expected="指标完整且护栏清除",
        timeout_seconds=30.0,
        guardrails=("storage", "oom", "gpu", "restart"),
        cleanup=("停止采样",),
        evidence_path="campaign/phase-3-online/runtime-metrics.json",
    )


def _plan(case: CaseSpec) -> CampaignPlan:
    fixture = FixtureDescriptor(
        fixture_id="online-image",
        kind=FixtureKind.ONLINE_IMAGE,
        path="/external/online-image.jpg",
        size_bytes=1024,
        sha256="a" * 64,
    )
    return build_campaign_plan(
        release_tag="release-20260823",
        git_sha="b" * 40,
        seed=20260823,
        control_origin="http://127.0.0.1:18100",
        gateway_origin="http://127.0.0.1:18103",
        fixture_manifest=FixtureManifest(schema_version=1, fixtures=(fixture,)),
        catalog=CampaignCatalog(schema_version=1, cases=(case,)),
    )


def _target(
    *,
    available_gib: int = 500,
    oom_events: int = 0,
) -> TargetHostMetrics:
    return TargetHostMetrics(
        filesystems=(
            FilesystemMetrics(
                requested_path="/data/course",
                mountpoint="/data",
                total_bytes=1000 * GiB,
                available_bytes=available_gib * GiB,
            ),
            FilesystemMetrics(
                requested_path="/data/result",
                mountpoint="/data",
                total_bytes=1000 * GiB,
                available_bytes=available_gib * GiB,
            ),
        ),
        directory_sizes=(),
        memory_total_bytes=64 * GiB,
        memory_available_bytes=48 * GiB,
        oom_events=oom_events,
    )


def _container(
    service: str,
    *,
    healthy: bool = True,
    restart_count: int = 0,
    container_id: str | None = None,
) -> ContainerMetrics:
    return ContainerMetrics(
        container_id=container_id or f"container-{service}",
        compose_project="algorithm-platform",
        compose_service=service,
        cpu_percent=4.0,
        memory_bytes=128 * 1024**2,
        restart_count=restart_count,
        healthy=healthy,
    )


def _gpu(*, uuid: str = "GPU-0", pid: int = 42, name: str = "python") -> GpuMetrics:
    return GpuMetrics(
        uuid=uuid,
        utilization_percent=25.0,
        memory_used_bytes=2 * GiB,
        processes=(GpuProcessMetrics(pid=pid, name=name, memory_bytes=GiB),),
    )


def _state(*, delay_seconds: float = 0.0) -> ProbeState:
    instance = InstanceCapacityMetrics(
        instance_id="vbas-gpu0",
        inflight=1,
        active_leases=1,
        declared_capacity=10,
    )
    return ProbeState(
        load_host=MutableProbe(
            LoadHostMetrics(
                cpu_percent=12.0,
                memory_total_bytes=32 * GiB,
                memory_available_bytes=24 * GiB,
                network_receive_bytes=100,
                network_transmit_bytes=200,
                open_socket_count=10,
                open_file_handle_count=20,
            ),
            delay_seconds=delay_seconds,
        ),
        target_host=MutableProbe(_target(), delay_seconds=delay_seconds),
        directory_sizes=MutableProbe(
            (
                DirectorySizeMetrics(requested_path="/data/course", size_bytes=100),
                DirectorySizeMetrics(requested_path="/data/result", size_bytes=200),
            ),
            delay_seconds=delay_seconds,
        ),
        containers=MutableProbe((_container("vbas-gpu0"),), delay_seconds=delay_seconds),
        gpus=MutableProbe((_gpu(),), delay_seconds=delay_seconds),
        control=MutableProbe(
            ControlPlaneMetrics(
                task_queue_depth=2,
                outbox_pending=1,
                kafka_lag=3,
                instances=(instance,),
            ),
            delay_seconds=delay_seconds,
        ),
        gateway=MutableProbe(
            GatewayMetrics(
                counters=GatewayCounters(
                    requests_total=10,
                    lease_acquired_total=8,
                    lease_rejected_total=2,
                    lease_released_total=7,
                ),
                instance_requests={"vbas-gpu0": 8},
            ),
            delay_seconds=delay_seconds,
        ),
    )


def _adapter(
    tmp_path: Path,
    case: CaseSpec,
    state: ProbeState,
    **overrides: object,
) -> RuntimeMetricsAdapter:
    options: dict[str, object] = {
        "schedule": SamplingSchedule(regular_seconds=0.02, burst_seconds=0.5),
        "database_services": (),
        "critical_container_services": (),
        "directory_size_probe": state.directory_sizes.collect,
    }
    options.update(overrides)
    return RuntimeMetricsAdapter(
        _plan(case),
        (tmp_path / "release").resolve(),
        load_host_probe=state.load_host,
        target_host_probe=state.target_host,
        docker_probe=state.containers,
        gpu_probe=state.gpus,
        control_probe=state.control,
        gateway_probe=state.gateway,
        **options,
    )


@pytest.mark.parametrize(
    ("regular", "burst"),
    ((5.01, 1.0), (5.0, 0.49), (5.0, 1.01)),
)
def test_sampling_schedule_rejects_intervals_outside_contract(
    regular: float,
    burst: float,
) -> None:
    with pytest.raises(ValueError):
        SamplingSchedule(regular_seconds=regular, burst_seconds=burst)


@pytest.mark.asyncio
async def test_regular_sampler_takes_immediate_and_repeated_immutable_samples(
    tmp_path: Path,
) -> None:
    case = _case()
    state = _state()
    adapter = _adapter(tmp_path, case, state)

    assessment = await adapter.start(case)
    with pytest.raises(RuntimeError, match="已启动"):
        await adapter.start(case)
    await asyncio.sleep(0.07)
    summary = await adapter.stop()

    assert assessment.level is GuardrailLevel.CLEAR
    assert summary is not None
    assert summary.sample_count >= 4
    assert not adapter.is_running
    assert await adapter.stop() is None
    samples = adapter.samples(case.case_id)
    assert samples[0].sequence == 1
    assert all(sample.evidence_path is not None for sample in samples)
    paths = [sample.evidence_path for sample in samples]
    assert len(paths) == len(set(paths))
    for path in paths:
        assert path is not None
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.parent == (tmp_path / "release" / "campaign" / "runtime-metrics" / case.case_id)


@pytest.mark.asyncio
async def test_burst_sampler_uses_half_second_interval(tmp_path: Path) -> None:
    case = _case()
    adapter = _adapter(tmp_path, case, _state())

    await adapter.start(case, burst=True)
    await asyncio.sleep(0.55)
    summary = await adapter.stop()

    assert summary is not None
    assert summary.sample_count >= 3
    samples = adapter.samples(case.case_id)
    assert all(sample.burst for sample in samples)
    assert samples[1].monotonic_seconds - samples[0].monotonic_seconds >= 0.45


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "delay", "expected_burst"),
    (("online_image", 0.55, True), ("query", 0.06, False)),
)
async def test_assess_before_and_after_cover_case_with_continuous_sampling(
    tmp_path: Path,
    kind: str,
    delay: float,
    expected_burst: bool,
) -> None:
    case = _case(kind=kind)
    adapter = _adapter(tmp_path, case, _state())

    before = await adapter.assess(case, "before")
    assert before.level is GuardrailLevel.CLEAR
    assert adapter.is_running
    await asyncio.sleep(delay)
    after = await adapter.assess(case, "after")

    assert after.level is GuardrailLevel.CLEAR
    assert not adapter.is_running
    samples = adapter.samples(case.case_id)
    assert len(samples) >= 3
    assert all(sample.burst is expected_burst for sample in samples)


@pytest.mark.asyncio
async def test_blocking_probes_run_off_event_loop_and_in_parallel(tmp_path: Path) -> None:
    case = _case()
    state = _state(delay_seconds=0.12)
    adapter = _adapter(tmp_path, case, state)
    ticks = 0
    running = True

    async def ticker() -> None:
        nonlocal ticks
        while running:
            ticks += 1
            await asyncio.sleep(0.01)

    ticker_task = asyncio.create_task(ticker())
    started = time.monotonic()
    await adapter.start(case)
    elapsed = time.monotonic() - started
    running = False
    await ticker_task
    await adapter.stop()

    assert ticks >= 5
    assert elapsed < 0.35
    main_thread = threading.get_ident()
    probes = (
        state.load_host,
        state.target_host,
        state.containers,
        state.gpus,
        state.control,
        state.gateway,
    )
    assert all(probe.thread_ids and main_thread not in probe.thread_ids for probe in probes)


@pytest.mark.asyncio
async def test_existing_sequence_is_never_overwritten(tmp_path: Path) -> None:
    case = _case()
    first = _adapter(tmp_path, case, _state())
    assert (await first.assess(case, "before")).level is GuardrailLevel.CLEAR
    path = first.samples(case.case_id)[0].evidence_path
    assert path is not None
    original = path.read_bytes()

    second = _adapter(tmp_path, case, _state())
    assessment = await second.assess(case, "before")
    await first.assess(case, "after")

    assert assessment.level is GuardrailLevel.STOP
    assert "证据无法原子写入" in assessment.reasons
    assert path.read_bytes() == original
    assert second.samples(case.case_id)[0].evidence_path is None


@pytest.mark.asyncio
async def test_public_sample_separates_hosts_and_omits_process_names(tmp_path: Path) -> None:
    case = _case()
    state = _state()
    state.gpus.value = (_gpu(name="password=do-not-persist"),)
    adapter = _adapter(tmp_path, case, state)

    await adapter.assess(case, "before")
    path = adapter.samples(case.case_id)[0].evidence_path
    await adapter.assess(case, "after")
    assert path is not None
    raw = path.read_text(encoding="utf-8")
    document = json.loads(raw)

    validate_public_payload(document)
    assert "do-not-persist" not in raw
    assert document["load_host"]["memory_total_bytes"] == 32 * GiB
    assert document["target_host"]["memory_total_bytes"] == 64 * GiB
    assert document["target_host"]["directory_sizes"] == []
    assert document["gpus"][0]["processes"] == [{"memory_bytes": GiB, "pid": 42}]


@pytest.mark.asyncio
async def test_evidence_write_failure_latches_stop(tmp_path: Path) -> None:
    case = _case()

    def fail_write(path: Path, content: str) -> None:
        del path, content
        raise OSError("disk unavailable")

    adapter = _adapter(tmp_path, case, _state(), evidence_writer=fail_write)

    before = await adapter.assess(case, "before")
    after = await adapter.assess(case, "after")

    assert before.level is GuardrailLevel.STOP
    assert after.level is GuardrailLevel.STOP
    assert "证据无法原子写入" in after.reasons
    assert not tuple((tmp_path / "release").rglob("*.json"))


@pytest.mark.parametrize(
    ("available_gib", "expected"),
    (
        (150, GuardrailLevel.CLEAR),
        (149, GuardrailLevel.WARNING),
        (100, GuardrailLevel.WARNING),
        (99, GuardrailLevel.STOP),
    ),
)
@pytest.mark.asyncio
async def test_storage_guardrail_uses_exact_absolute_and_ratio_boundaries(
    tmp_path: Path,
    available_gib: int,
    expected: GuardrailLevel,
) -> None:
    case = _case()
    state = _state()
    state.target_host.value = _target(available_gib=available_gib)
    adapter = _adapter(tmp_path, case, state)

    assessment = await adapter.assess(case, "before")
    if assessment.level is GuardrailLevel.CLEAR:
        await adapter.assess(case, "after")

    assert assessment.level is expected


@pytest.mark.asyncio
async def test_oom_delta_after_baseline_causes_stop(tmp_path: Path) -> None:
    case = _case()
    state = _state()
    state.target_host.value = _target(oom_events=7)
    adapter = _adapter(tmp_path, case, state)
    assert (await adapter.assess(case, "before")).level is GuardrailLevel.CLEAR

    state.target_host.value = _target(oom_events=8)
    assessment = await adapter.assess(case, "after")

    assert assessment.level is GuardrailLevel.STOP
    assert "宿主机发生 OOM" in assessment.reasons


@pytest.mark.asyncio
async def test_unhealthy_critical_container_and_database_cause_stop(tmp_path: Path) -> None:
    case = _case()
    state = _state()
    state.containers.value = (
        _container("control-service", healthy=False),
        _container("postgres", healthy=False),
    )
    adapter = _adapter(
        tmp_path,
        case,
        state,
        critical_container_services=("control-service",),
        database_services=("postgres",),
    )

    assessment = await adapter.assess(case, "before")

    assert assessment.level is GuardrailLevel.STOP
    assert "关键容器不健康或缺失: control-service" in assessment.reasons
    assert "关键数据库不健康: postgres" in assessment.reasons


@pytest.mark.asyncio
async def test_restart_growth_within_window_causes_stop(tmp_path: Path) -> None:
    case = _case()
    state = _state()
    state.containers.value = (_container("vbas-gpu0", restart_count=0),)
    adapter = _adapter(
        tmp_path,
        case,
        state,
        restart_loop_threshold=3,
        restart_loop_window_seconds=10.0,
    )
    assert (await adapter.assess(case, "before")).level is GuardrailLevel.CLEAR

    state.containers.value = (_container("vbas-gpu0", restart_count=3),)
    assessment = await adapter.assess(case, "after")

    assert assessment.level is GuardrailLevel.STOP
    assert "容器连续重启: vbas-gpu0" in assessment.reasons


@pytest.mark.asyncio
async def test_cross_gpu_assignment_and_critical_gpu_error_cause_stop(tmp_path: Path) -> None:
    case = _case()
    state = _state()
    state.gpus.value = (_gpu(uuid="GPU-actual", pid=42),)
    adapter = _adapter(
        tmp_path,
        case,
        state,
        expected_gpu_by_pid={42: "GPU-expected"},
        critical_gpu_error_probe=lambda: ("GPU-actual:XID-79", "GPU-actual:ECC-DBE"),
    )

    assessment = await adapter.assess(case, "before")

    assert assessment.level is GuardrailLevel.STOP
    assert "发现 GPU 跨卡归属" in assessment.reasons
    assert "GPU 严重错误: GPU-actual:XID-79" in assessment.reasons
    assert "GPU 严重错误: GPU-actual:ECC-DBE" in assessment.reasons


@pytest.mark.asyncio
async def test_before_after_summary_contains_control_and_gateway_deltas(tmp_path: Path) -> None:
    case = _case(kind="long_course")
    state = _state()
    adapter = _adapter(tmp_path, case, state)
    assert (await adapter.assess(case, "before")).level is GuardrailLevel.CLEAR

    state.control.value = ControlPlaneMetrics(
        task_queue_depth=9,
        outbox_pending=5,
        kafka_lag=11,
        instances=(
            InstanceCapacityMetrics(
                instance_id="vbas-gpu0",
                inflight=8,
                active_leases=7,
                declared_capacity=10,
            ),
        ),
    )
    state.gateway.value = GatewayMetrics(
        counters=GatewayCounters(
            requests_total=16,
            lease_acquired_total=12,
            lease_rejected_total=3,
            lease_released_total=11,
        ),
        instance_requests={"vbas-gpu0": 13},
    )
    state.directory_sizes.value = (
        DirectorySizeMetrics(requested_path="/data/course", size_bytes=160),
        DirectorySizeMetrics(requested_path="/data/result", size_bytes=275),
    )
    assert (await adapter.assess(case, "after")).level is GuardrailLevel.CLEAR
    summary = adapter.summary(case.case_id)
    outcome = await adapter.execute(case)

    assert summary.sample_count >= 2
    assert summary.gateway_delta == GatewayCounters(
        requests_total=6,
        lease_acquired_total=4,
        lease_rejected_total=1,
        lease_released_total=4,
    )
    assert summary.gateway_instance_request_delta == {"vbas-gpu0": 5}
    assert summary.peak_inflight == {"vbas-gpu0": 8}
    assert summary.peak_active_leases == {"vbas-gpu0": 7}
    assert summary.peak_declared_capacity == {"vbas-gpu0": 10}
    assert summary.max_task_queue_depth == 9
    assert summary.max_outbox_pending == 5
    assert summary.max_kafka_lag == 11
    assert summary.target_directory_bytes_before == {
        "/data/course": 100,
        "/data/result": 200,
    }
    assert summary.target_directory_bytes_after == {
        "/data/course": 160,
        "/data/result": 275,
    }
    assert summary.target_directory_bytes_delta == {
        "/data/course": 60,
        "/data/result": 75,
    }
    assert outcome.status == "passed"
    assert outcome.evidence["runtime_metrics"]["gateway_delta"]["requests_total"] == 6
    assert outcome.evidence["runtime_metrics"]["target_directory_bytes_delta"] == {
        "/data/course": 60,
        "/data/result": 75,
    }


@pytest.mark.asyncio
async def test_burst_sampling_never_recursively_measures_data_directories(
    tmp_path: Path,
) -> None:
    case = _case(kind="online_image")
    state = _state()
    adapter = _adapter(tmp_path, case, state)

    assert (await adapter.assess(case, "before")).level is GuardrailLevel.CLEAR
    await asyncio.sleep(0.03)
    assert (await adapter.assess(case, "after")).level is GuardrailLevel.CLEAR

    assert state.directory_sizes.calls == 0
    summary = adapter.summary(case.case_id)
    assert summary.target_directory_bytes_before == {}
    assert summary.target_directory_bytes_after == {}
    assert summary.target_directory_bytes_delta == {}


@pytest.mark.asyncio
async def test_long_course_measures_directories_only_at_before_and_after(
    tmp_path: Path,
) -> None:
    case = _case(kind="long_course")
    state = _state()
    adapter = _adapter(tmp_path, case, state)

    assert (await adapter.assess(case, "before")).level is GuardrailLevel.CLEAR
    await asyncio.sleep(0.07)
    state.directory_sizes.value = (
        DirectorySizeMetrics(requested_path="/data/course", size_bytes=180),
        DirectorySizeMetrics(requested_path="/data/result", size_bytes=260),
    )
    assert (await adapter.assess(case, "after")).level is GuardrailLevel.CLEAR

    assert len(adapter.samples(case.case_id)) >= 3
    assert state.directory_sizes.calls == 2
    assert [
        bool(sample.target_host.directory_sizes) for sample in adapter.samples(case.case_id)
    ].count(True) == 2


@pytest.mark.asyncio
async def test_long_course_missing_directory_checkpoint_probe_stops_before_load(
    tmp_path: Path,
) -> None:
    case = _case(kind="long_course")
    adapter = _adapter(tmp_path, case, _state(), directory_size_probe=None)

    assessment = await adapter.assess(case, "before")

    assert assessment.level is GuardrailLevel.STOP
    assert "运行时指标采集失败: RuntimeError" in assessment.reasons
