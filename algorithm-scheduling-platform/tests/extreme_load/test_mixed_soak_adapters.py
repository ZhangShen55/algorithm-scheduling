from __future__ import annotations

import asyncio
import io
import wave
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import pytest

from scripts.extreme_load import mixed_soak_adapters
from scripts.extreme_load.catalog import (
    CampaignCatalog,
    CampaignPhase,
    CaseSpec,
    FixtureDescriptor,
    FixtureKind,
    FixtureManifest,
)
from scripts.extreme_load.mixed_soak_adapters import (
    MIXED_PROFILES,
    MixedLoadProfile,
    MixedSoakStageAdapter,
    RecoveryEvidence,
    RuntimeEvidenceStopMonitor,
    TrafficSnapshot,
    build_profile_http_requests,
    load_stable_profile,
    profile_http_concurrency,
)
from scripts.extreme_load.online_images import OnlineImageFixture
from scripts.extreme_load.plan import CampaignPlan, build_campaign_plan
from scripts.extreme_load.realtime_asr import AsrSessionResult
from scripts.run_extreme_load_campaign import _load_adapter_factories

_LONG_FIXTURES = ("long-teacher", "long-student", "long-slides")
_ALL_FIXTURES = (*_LONG_FIXTURES, "online-image", "realtime-audio")


def _fixture(fixture_id: str) -> FixtureDescriptor:
    kind = {
        "long-teacher": FixtureKind.LONG_COURSE,
        "long-student": FixtureKind.LONG_COURSE,
        "long-slides": FixtureKind.LONG_COURSE,
        "online-image": FixtureKind.ONLINE_IMAGE,
        "realtime-audio": FixtureKind.REALTIME_AUDIO,
    }[fixture_id]
    suffix = ".wav" if fixture_id == "realtime-audio" else ".bin"
    return FixtureDescriptor(
        fixture_id=fixture_id,
        kind=kind,
        path=f"http://192.168.29.12:5555/fixtures/{fixture_id}{suffix}",
        size_bytes=4096,
        duration_seconds=2700 if fixture_id in _LONG_FIXTURES else 30,
        sha256="a" * 64,
    )


def _case(kind: str, *, level: str = "daily", hours: int = 4) -> CaseSpec:
    phase = CampaignPhase.MIXED if kind == "mixed" else CampaignPhase.SOAK
    load: dict[str, object] = (
        {"kind": "mixed", "level": level}
        if kind == "mixed"
        else {"kind": "soak", "hours": hours, "stable_capacity_ratio": 0.75}
    )
    return CaseSpec(
        case_id=f"MIXED-{level.upper()}" if kind == "mixed" else f"SOAK-{hours}H",
        phase=phase,
        load=load,
        fixture_ids=_ALL_FIXTURES,
        expected="execution",
        timeout_seconds=32_400,
        guardrails=("evidence",),
        cleanup=("drain",),
        evidence_path=f"campaign/{phase.value}/{kind}-{level}-{hours}.json",
    )


def _plan(case: CaseSpec) -> CampaignPlan:
    return build_campaign_plan(
        release_tag="release-1",
        git_sha="b" * 40,
        seed=11,
        control_origin="http://192.168.29.11:18100",
        gateway_origin="http://192.168.29.11:18103",
        fixture_manifest=FixtureManifest(
            schema_version=1,
            fixtures=tuple(_fixture(fixture_id) for fixture_id in _ALL_FIXTURES),
        ),
        catalog=CampaignCatalog(schema_version=1, cases=(case,)),
    )


class NeverStopMonitor:
    async def wait(self, case: CaseSpec, finished: asyncio.Event) -> str | None:
        del case
        await finished.wait()
        return None


class ImmediateStopMonitor:
    async def wait(self, case: CaseSpec, finished: asyncio.Event) -> str | None:
        del case, finished
        await asyncio.sleep(0)
        return "测试护栏 STOP"


class FakeTrafficRunner:
    def __init__(
        self,
        *,
        categories: dict[str, int] | None = None,
        recovery: RecoveryEvidence | None = None,
        block_until_abort: bool = False,
        on_round: Callable[[MixedLoadProfile], None] | None = None,
    ) -> None:
        self._categories = categories or {"success": 1}
        self._recovery = recovery or RecoveryEvidence(
            recovered=True,
            queue_drained=True,
            attempts=1,
            control_ready=True,
            task_queue_depth=0,
            outbox_pending=0,
            active_leases=0,
            inflight=0,
            terminal_counts={"success": 1},
        )
        self._block_until_abort = block_until_abort
        self._on_round = on_round
        self.profiles: list[MixedLoadProfile] = []
        self.cancelled = False
        self.recovery_calls = 0

    async def run_round(
        self,
        case: CaseSpec,
        profile: MixedLoadProfile,
        abort_event: asyncio.Event,
    ) -> None:
        del case
        self.profiles.append(profile)
        if self._on_round is not None:
            self._on_round(profile)
        if self._block_until_abort:
            try:
                await abort_event.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    def snapshot(self) -> TrafficSnapshot:
        return TrafficSnapshot(
            round_count=len(self.profiles),
            categories=self._categories,
            latency_seconds=(0.01,) * sum(self._categories.values()),
            accepted_task_ids=("task-1",),
            correctness_failures=(),
        )

    async def recover(
        self,
        case: CaseSpec,
        *,
        timeout_seconds: float,
    ) -> RecoveryEvidence:
        del case
        assert timeout_seconds > 0
        self.recovery_calls += 1
        return self._recovery


async def _never_deadline(_deadline: float) -> None:
    await asyncio.Event().wait()


def test_mixed_profiles_match_the_approved_entry_load_contract() -> None:
    assert {name: profile.to_evidence() for name, profile in MIXED_PROFILES.items()} == {
        "daily": {
            "level": "daily",
            "long_courses": 3,
            "s_streams": 30,
            "s_stream_interval_seconds": 10,
            "online_images": 10,
            "asr_sessions": 10,
            "query_qps": 20,
        },
        "peak": {
            "level": "peak",
            "long_courses": 12,
            "s_streams": 100,
            "s_stream_interval_seconds": 5,
            "online_images": 100,
            "asr_sessions": 30,
            "query_qps": 100,
        },
        "extreme": {
            "level": "extreme",
            "long_courses": 36,
            "s_streams": 300,
            "s_stream_interval_seconds": 5,
            "online_images": 1000,
            "asr_sessions": 150,
            "query_qps": 1000,
        },
    }


def test_profile_request_builders_use_only_approved_northbound_origins() -> None:
    case = _case("mixed", level="extreme")
    plan = _plan(case)
    fixture = OnlineImageFixture("image-1", "/9j/2Q==")

    requests = build_profile_http_requests(
        plan,
        case,
        MIXED_PROFILES["extreme"],
        fixture,
        query_task_ids=("task-1", "task-2"),
        query_duration_seconds=10,
    )

    assert len(requests.offline) == 36
    assert len(requests.online_images) == 1000
    assert len(requests.s_stream_round) == 300
    assert len(requests.queries) == 10_000
    assert requests.maximum_concurrency == 1000
    for request in requests.all_requests:
        parsed = urlsplit(request.url)
        assert parsed.hostname == "192.168.29.11"
        assert parsed.port in {18100, 18103}
        if parsed.port == 18100:
            assert parsed.path.startswith("/api/course-jobs")
        else:
            assert parsed.path.startswith("/api/online/")


def test_extreme_profile_uses_one_explicit_global_http_concurrency_budget() -> None:
    budget = profile_http_concurrency(MIXED_PROFILES["extreme"])

    assert budget.online_images == 1000
    assert budget.s_streams == 300
    assert budget.queries == 748
    assert budget.total == 2048


@pytest.mark.asyncio
async def test_mixed_asr_requires_finished_message_not_only_intermediate_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case("mixed", level="daily")
    plan = _plan(case)
    runner = mixed_soak_adapters._NorthboundTrafficRunner(plan)
    audio = mixed_soak_adapters.AudioStreamFixture(
        pcm=b"\x00\x00" * 1600,
        sample_rate_hz=16000,
        sample_width_bytes=2,
        channels=1,
        chunk_duration_seconds=0.1,
    )

    class FakeRealtimeAsrRunner:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def run_sessions(
            self,
            specs: object,
            _fixture: object,
        ) -> tuple[AsrSessionResult, ...]:
            assert isinstance(specs, tuple)
            return (
                AsrSessionResult(
                    session_id="session-1",
                    trace_id="trace-1",
                    category=mixed_soak_adapters.ResultCategory.SUCCESS,
                    sent_chunk_count=1,
                    message_digests=("intermediate-message-digest",),
                    finished_message_count=0,
                    sent_media_chunk_count=1,
                    planned_media_duration_seconds=0.1,
                    sent_media_duration_seconds=0.1,
                    send_elapsed_seconds=0.1,
                    realtime_factor=1.0,
                    max_positive_schedule_drift_seconds=0.01,
                ),
            )

    monkeypatch.setattr(
        mixed_soak_adapters,
        "RealtimeAsrRunner",
        FakeRealtimeAsrRunner,
    )

    await runner._run_asr(
        case,
        MIXED_PROFILES["daily"],
        mixed_soak_adapters._RoundAssets(
            OnlineImageFixture("image-1", "/9j/2Q=="),
            audio,
        ),
    )

    assert runner.snapshot().correctness_failures == (
        "1 个实时 ASR 成功会话缺少 finished=true 完整语句消息",
    )
    snapshot = runner.snapshot()
    assert snapshot.asr_session_count == 1
    assert snapshot.asr_sent_chunk_count == 1
    assert snapshot.asr_sent_media_chunk_count == 1
    assert snapshot.asr_sent_tail_silence_chunk_count == 0
    assert snapshot.asr_planned_media_duration_seconds == pytest.approx(0.1)
    assert snapshot.asr_sent_media_duration_seconds == pytest.approx(0.1)
    assert snapshot.asr_send_elapsed_seconds == pytest.approx(0.1)
    assert snapshot.asr_max_realtime_factor == pytest.approx(1.0)
    assert snapshot.asr_max_positive_schedule_drift_seconds == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_mixed_assets_use_the_asr_online_chunk_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case("mixed", level="daily")
    runner = mixed_soak_adapters._NorthboundTrafficRunner(_plan(case))

    async def fake_fixture(descriptor: object, **_kwargs: object) -> bytes:
        fixture_id = getattr(descriptor, "fixture_id", "")
        if fixture_id == "online-image":
            return b"image"
        target = io.BytesIO()
        with wave.open(target, "wb") as stream:
            stream.setframerate(16_000)
            stream.setsampwidth(2)
            stream.setnchannels(1)
            stream.writeframes(b"\x00\x00" * 7680)
        return target.getvalue()

    monkeypatch.setattr(mixed_soak_adapters, "_read_fixture_bytes", fake_fixture)

    assets = await runner._load_assets()

    assert assets.audio.chunk_duration_seconds == 0.48
    assert assets.audio.chunk_bytes == 15360


def test_factories_are_dynamically_loadable_by_the_existing_cli_contract() -> None:
    factories = _load_adapter_factories(
        (
            "mixed=scripts.extreme_load.mixed_soak_adapters:mixed_factory",
            "soak=scripts.extreme_load.mixed_soak_adapters:soak_factory",
        )
    )

    assert set(factories) == {"mixed", "soak"}
    assert all(callable(factory) for factory in factories.values())


@pytest.mark.asyncio
async def test_mixed_adapter_records_expected_overload_and_requires_drain() -> None:
    case = _case("mixed", level="extreme")
    runner = FakeTrafficRunner(categories={"success": 900, "overload": 100})
    adapter = MixedSoakStageAdapter(
        _plan(case),
        Path("/tmp/release"),
        stage_kind="mixed",
        traffic_runner=runner,
        stop_monitor=NeverStopMonitor(),
    )

    outcome = await adapter.execute(case)

    assert outcome.status == "passed"
    assert outcome.recovery_succeeded is True
    assert outcome.evidence["classification"] == "expected_overload"
    assert outcome.evidence["stable_capacity"] is False
    assert outcome.evidence["categories"] == {"overload": 100, "success": 900}
    assert outcome.evidence["realtime_asr"] == {
        "session_count": 0,
        "sent_chunk_count": 0,
        "sent_media_chunk_count": 0,
        "sent_tail_silence_chunk_count": 0,
        "planned_media_duration_seconds": 0.0,
        "sent_media_duration_seconds": 0.0,
        "send_elapsed_seconds": 0.0,
        "max_realtime_factor": 0.0,
        "max_positive_schedule_drift_seconds": 0.0,
    }
    recovery = cast(Mapping[str, object], outcome.evidence["recovery"])
    assert recovery["queue_drained"] is True
    assert runner.profiles == [MIXED_PROFILES["extreme"]]


@pytest.mark.asyncio
async def test_guardrail_stop_cancels_new_traffic_preserves_partial_counts_and_recovers() -> None:
    case = _case("mixed")
    runner = FakeTrafficRunner(
        categories={"success": 7},
        block_until_abort=True,
    )
    adapter = MixedSoakStageAdapter(
        _plan(case),
        Path("/tmp/release"),
        stage_kind="mixed",
        traffic_runner=runner,
        stop_monitor=ImmediateStopMonitor(),
    )

    outcome = await adapter.execute(case)

    assert outcome.status == "blocked"
    assert outcome.recovery_succeeded is True
    assert outcome.evidence["classification"] == "guardrail_stop"
    assert outcome.evidence["stop_reason"] == "测试护栏 STOP"
    assert outcome.evidence["categories"] == {"guardrail_abort": 1, "success": 7}
    assert runner.cancelled is True
    assert runner.recovery_calls == 1


@pytest.mark.asyncio
async def test_task_cancellation_is_converted_to_stop_recovery_and_public_evidence() -> None:
    case = _case("mixed")
    runner = FakeTrafficRunner(block_until_abort=True)
    adapter = MixedSoakStageAdapter(
        _plan(case),
        Path("/tmp/release"),
        stage_kind="mixed",
        traffic_runner=runner,
        stop_monitor=NeverStopMonitor(),
    )
    execution = asyncio.create_task(adapter.execute(case))
    await asyncio.sleep(0)

    execution.cancel()
    outcome = await execution

    assert outcome.status == "blocked"
    assert outcome.recovery_succeeded is True
    assert outcome.evidence["stop_reason"] == "阶段执行收到取消请求"
    assert runner.cancelled is True
    assert runner.recovery_calls == 1


@pytest.mark.asyncio
async def test_adapter_cannot_pass_when_recovery_or_drain_is_unproven() -> None:
    case = _case("mixed", level="daily")
    runner = FakeTrafficRunner(
        recovery=RecoveryEvidence(
            recovered=False,
            queue_drained=False,
            attempts=3,
            control_ready=False,
            task_queue_depth=2,
            outbox_pending=1,
            active_leases=1,
            inflight=1,
            terminal_counts={"running": 1},
        )
    )
    adapter = MixedSoakStageAdapter(
        _plan(case),
        Path("/tmp/release"),
        stage_kind="mixed",
        traffic_runner=runner,
        stop_monitor=NeverStopMonitor(),
    )

    outcome = await adapter.execute(case)

    assert outcome.status == "failed"
    assert outcome.recovery_succeeded is False
    assert outcome.evidence["classification"] == "nonconforming"


@pytest.mark.asyncio
@pytest.mark.parametrize("hours", (4, 8))
async def test_soak_uses_real_duration_semantics_with_an_injected_test_clock(hours: int) -> None:
    case = _case("soak", hours=hours)
    current = 100.0

    def advance(_profile: MixedLoadProfile) -> None:
        nonlocal current
        current += 3600

    runner = FakeTrafficRunner(on_round=advance)
    stable = MIXED_PROFILES["peak"]
    adapter = MixedSoakStageAdapter(
        _plan(case),
        Path("/tmp/release"),
        stage_kind="soak",
        traffic_runner=runner,
        stop_monitor=NeverStopMonitor(),
        clock=lambda: current,
        wait_until=_never_deadline,
        stable_profile_loader=lambda _plan, _root: stable,
    )

    outcome = await adapter.execute(case)

    assert outcome.status == "passed"
    assert outcome.evidence["configured_duration_seconds"] == hours * 3600
    observed_duration = outcome.evidence["observed_duration_seconds"]
    assert isinstance(observed_duration, (int, float))
    assert observed_duration >= hours * 3600
    assert len(runner.profiles) == hours
    assert all(profile == stable.scaled(0.75, level="soak-75pct") for profile in runner.profiles)


@pytest.mark.asyncio
async def test_soak_blocks_without_truthful_prior_stable_capacity_evidence() -> None:
    case = _case("soak")
    runner = FakeTrafficRunner()
    adapter = MixedSoakStageAdapter(
        _plan(case),
        Path("/tmp/release"),
        stage_kind="soak",
        traffic_runner=runner,
        stop_monitor=NeverStopMonitor(),
        stable_profile_loader=lambda _plan, _root: None,
    )

    outcome = await adapter.execute(case)

    assert outcome.status == "blocked"
    assert outcome.evidence == {"configuration_state": "stable_capacity_unproven"}
    assert runner.profiles == []


def test_stable_capacity_loader_rejects_overload_and_selects_highest_truthful_level(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = tuple(_case("mixed", level=level) for level in ("daily", "peak", "extreme"))
    plan = _plan(cases[0]).model_copy(
        update={"catalog": CampaignCatalog(schema_version=1, cases=cases)}
    )
    documents = {
        "daily": {"stable": True, "recovered": True, "guardrail": "CLEAR"},
        "peak": {"stable": True, "recovered": True, "guardrail": "CLEAR"},
        "extreme": {"stable": False, "recovered": True, "guardrail": "CLEAR"},
    }

    def fake_read(
        _root: Path,
        _plan: CampaignPlan,
        case: CaseSpec,
    ) -> dict[str, object]:
        level = str(case.load["level"])
        state = documents[level]
        return {
            "status": "passed",
            "recovery_succeeded": state["recovered"],
            "guardrail_before": {"level": state["guardrail"], "reasons": []},
            "guardrail_after": {"level": state["guardrail"], "reasons": []},
            "adapter_evidence": {
                "stable_capacity": state["stable"],
                "profile": MIXED_PROFILES[level].to_evidence(),
            },
        }

    monkeypatch.setattr(mixed_soak_adapters, "read_case_evidence", fake_read)

    assert load_stable_profile(plan, tmp_path) == MIXED_PROFILES["peak"]


@pytest.mark.asyncio
async def test_runtime_evidence_monitor_stops_on_stale_sampling(tmp_path: Path) -> None:
    case = _case("mixed")
    current = 0.0

    def clock() -> float:
        nonlocal current
        current += 1.0
        return current

    monitor = RuntimeEvidenceStopMonitor(
        tmp_path,
        campaign_id=_plan(case).campaign_id,
        clock=clock,
        poll_seconds=0.001,
    )

    reason = await monitor.wait(case, asyncio.Event())

    assert reason == "运行时指标证据流超时，停止产生新负载"
