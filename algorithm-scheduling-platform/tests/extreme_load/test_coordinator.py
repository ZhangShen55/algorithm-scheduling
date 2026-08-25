from __future__ import annotations

import json
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Never

import pytest

from scripts.extreme_load.catalog import (
    CampaignPhase,
    CaseSpec,
    FixtureDescriptor,
    FixtureKind,
    FixtureManifest,
)
from scripts.extreme_load.coordinator import (
    CampaignCoordinator,
    CoordinatorBlockedError,
    _face_instance_observation,
    _negative_drain_observation,
)
from scripts.extreme_load.guardrails import GuardrailAssessment, GuardrailLevel
from scripts.extreme_load.plan import (
    CampaignPlan,
    build_campaign_plan,
    execution_path,
    load_campaign_plan,
    publish_campaign_plan,
)
from scripts.extreme_load.stage_runtime import StageCaseOutcome
from scripts.run_extreme_load_campaign import main

_GIT_SHA = "a" * 40


def _fixture_manifest(tmp_path: Path) -> FixtureManifest:
    definitions = (
        ("short-teacher", FixtureKind.SHORT_TEACHER_VIDEO, 30.0),
        ("short-student", FixtureKind.SHORT_STUDENT_VIDEO, 30.0),
        ("short-slides", FixtureKind.SHORT_SLIDES_VIDEO, 30.0),
        ("long-teacher", FixtureKind.LONG_COURSE, 2700.0),
        ("long-student", FixtureKind.LONG_COURSE, 2700.0),
        ("long-slides", FixtureKind.LONG_COURSE, 2700.0),
        ("online-image", FixtureKind.ONLINE_IMAGE, None),
        ("realtime-audio", FixtureKind.REALTIME_AUDIO, 30.0),
        ("person-photo", FixtureKind.PERSON_PHOTO, None),
    )
    fixtures = tuple(
        FixtureDescriptor(
            fixture_id=fixture_id,
            kind=kind,
            path=str(tmp_path / fixture_id),
            size_bytes=128,
            duration_seconds=duration,
            sha256=f"{index + 1:x}" * 64,
        )
        for index, (fixture_id, kind, duration) in enumerate(definitions)
    )
    return FixtureManifest(schema_version=1, fixtures=fixtures)


def _plan(tmp_path: Path) -> CampaignPlan:
    return build_campaign_plan(
        release_tag="release-20260823",
        git_sha=_GIT_SHA,
        seed=260823,
        control_origin="http://127.0.0.1:18100",
        gateway_origin="http://127.0.0.1:18103",
        fixture_manifest=_fixture_manifest(tmp_path),
    )


def _write_evidence(
    release_root: Path,
    plan: CampaignPlan,
    case_id: str,
    status: str = "passed",
    *,
    campaign_id: str | None = None,
) -> Path:
    path = execution_path(release_root, plan, case_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "evidence_type": "extreme_load_campaign_case",
                "campaign_id": campaign_id or plan.campaign_id,
                "release_tag": plan.release_tag,
                "git_sha": plan.git_sha,
                "case_id": case_id,
                "phase": next(
                    case.phase.value for case in plan.catalog.cases if case.case_id == case_id
                ),
                "status": status,
                "reason": "测试用例证据",
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _pass_required_before(
    release_root: Path,
    plan: CampaignPlan,
    phase: CampaignPhase,
) -> None:
    for case in plan.catalog.cases:
        if case.required and case.phase.sequence < phase.sequence:
            _write_evidence(release_root, plan, case.case_id)


def _pass_all_required(release_root: Path, plan: CampaignPlan) -> None:
    for case in plan.catalog.cases:
        if case.required:
            _write_evidence(release_root, plan, case.case_id)


def _clear_guardrail() -> GuardrailAssessment:
    return GuardrailAssessment(GuardrailLevel.CLEAR, ())


class FakeMetricsAdapter:
    def __init__(
        self,
        assessments: list[GuardrailAssessment] | None = None,
        *,
        outcome: StageCaseOutcome | None = None,
    ) -> None:
        self.assessments = list(assessments or [])
        self.outcome = outcome
        self.calls: list[tuple[str, str]] = []

    async def assess(self, case: CaseSpec, checkpoint: str) -> GuardrailAssessment:
        case_id = case.case_id
        self.calls.append((case_id, checkpoint))
        return self.assessments.pop(0) if self.assessments else _clear_guardrail()

    async def execute(self, case: CaseSpec) -> StageCaseOutcome:
        self.calls.append((case.case_id, "execute"))
        if self.outcome is not None:
            return self.outcome
        prefix = f"campaign/runtime-metrics/{case.case_id}"
        return StageCaseOutcome(
            "passed",
            "指标探针通过",
            {
                "runtime_metrics": {
                    "sample_count": 2,
                    "gateway_delta": {"requests_total": 3},
                    "gateway_instance_request_delta": {"vbas-gpu0": 2},
                    "peak_inflight": {"vbas-gpu0": 2},
                    "peak_active_leases": {"vbas-gpu0": 2},
                    "peak_declared_capacity": {"vbas-gpu0": 10},
                    "peak_gpu_utilization": {"GPU-0": 25.0},
                    "peak_gpu_memory_bytes": {"GPU-0": 1024},
                    "final_task_queue_depth": 0,
                    "final_outbox_pending": 0,
                    "final_kafka_lag": 0,
                    "final_active_leases": {},
                    "minimum_target_filesystem_available_bytes": {"/data": 4096},
                    "latest_guardrail": {"level": "CLEAR", "reasons": []},
                    "target_directory_bytes_before": {
                        "/data/course": 100,
                        "/data/result": 200,
                    },
                    "target_directory_bytes_after": {
                        "/data/course": 130,
                        "/data/result": 240,
                    },
                    "target_directory_bytes_delta": {
                        "/data/course": 30,
                        "/data/result": 40,
                    },
                },
                "sample_evidence": [f"{prefix}/00000001.json", f"{prefix}/00000002.json"],
            },
        )


def _runtime_outcome(
    *,
    requests: int,
    acquired: int,
    rejected: int,
    released: int,
    instance_requests: dict[str, int] | None = None,
    case_id: str = "IMG-BOUNDARY-INVALID-B64",
    latest_guardrail: dict[str, object] | None = None,
    final_task_queue_depth: int = 0,
    final_outbox_pending: int = 0,
    final_kafka_lag: int = 0,
    final_active_leases: dict[str, int] | None = None,
) -> StageCaseOutcome:
    prefix = f"campaign/runtime-metrics/{case_id}"
    return StageCaseOutcome(
        "passed",
        "指标探针通过",
        {
            "runtime_metrics": {
                "sample_count": 2,
                "gateway_delta": {
                    "requests_total": requests,
                    "lease_acquired_total": acquired,
                    "lease_rejected_total": rejected,
                    "lease_released_total": released,
                },
                "gateway_instance_request_delta": instance_requests or {},
                "peak_inflight": {},
                "peak_active_leases": {},
                "peak_declared_capacity": {},
                "peak_gpu_utilization": {},
                "peak_gpu_memory_bytes": {},
                "final_task_queue_depth": final_task_queue_depth,
                "final_outbox_pending": final_outbox_pending,
                "final_kafka_lag": final_kafka_lag,
                "final_active_leases": final_active_leases or {},
                "minimum_target_filesystem_available_bytes": {"/": 4096},
                "latest_guardrail": latest_guardrail
                or {"level": "CLEAR", "reasons": []},
                "target_directory_bytes_before": {},
                "target_directory_bytes_after": {},
                "target_directory_bytes_delta": {},
            },
            "sample_evidence": [f"{prefix}/00000001.json", f"{prefix}/00000002.json"],
        },
    )


def _face_business_evidence(
    *,
    status: str = "passed",
    recognized: int = 499,
    deleted_absence: int = 1,
    invalid: int = 0,
) -> dict[str, object]:
    return {
        "extra": {
            "person_fact_consistency": {
                "status": status,
                "reason": "北向人物事实证据",
                "expected_retained_number_count": 499,
                "recognized_retained_number_count": recognized,
                "expected_deleted_absence_count": 1,
                "validated_deleted_absence_count": deleted_absence,
                "invalid_response_count": invalid,
            }
        }
    }


@pytest.mark.parametrize(
    (
        "final_task_queue_depth",
        "final_outbox_pending",
        "final_kafka_lag",
        "final_active_leases",
        "expected_status",
    ),
    (
        (0, 0, 0, {}, "passed"),
        (1, 0, 0, {}, "blocked"),
        (0, 1, 0, {}, "blocked"),
        (0, 0, 1, {}, "blocked"),
        (0, 0, 0, {"ppt-slice-cpu0": 1}, "blocked"),
    ),
)
def test_negative_submission_requires_final_queue_and_lease_drain(
    tmp_path: Path,
    final_task_queue_depth: int,
    final_outbox_pending: int,
    final_kafka_lag: int,
    final_active_leases: dict[str, int],
    expected_status: str,
) -> None:
    plan = _plan(tmp_path)
    case = next(
        item for item in plan.catalog.cases if item.case_id == "OFF-NEGATIVE-1PCT"
    )
    metrics = _runtime_outcome(
        requests=0,
        acquired=0,
        rejected=0,
        released=0,
        case_id=case.case_id,
        final_task_queue_depth=final_task_queue_depth,
        final_outbox_pending=final_outbox_pending,
        final_kafka_lag=final_kafka_lag,
        final_active_leases=final_active_leases,
    )

    observation = _negative_drain_observation(case, metrics)

    assert observation is not None
    assert observation["status"] == expected_status
    assert observation["final_task_queue_depth"] == final_task_queue_depth
    assert observation["final_outbox_pending"] == final_outbox_pending
    assert observation["final_kafka_lag"] == final_kafka_lag
    assert observation["final_active_leases"] == final_active_leases


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("final_task_queue_depth", "expected_status"),
    ((0, "passed"), (1, "blocked")),
)
async def test_negative_submission_normative_result_enforces_final_drain(
    tmp_path: Path,
    final_task_queue_depth: int,
    expected_status: str,
) -> None:
    plan = _plan(tmp_path)
    release_root = tmp_path / "release"
    _pass_required_before(release_root, plan, CampaignPhase.OFFLINE)
    metrics = FakeMetricsAdapter(
        outcome=_runtime_outcome(
            requests=0,
            acquired=0,
            rejected=0,
            released=0,
            case_id="OFF-NEGATIVE-1PCT",
            final_task_queue_depth=final_task_queue_depth,
        )
    )

    class FakeExecutor:
        def __init__(self, observed_plan: CampaignPlan) -> None:
            self.observed_plan = observed_plan

        async def execute(self, case_id: str) -> Path:
            return _write_evidence(release_root, self.observed_plan, case_id)

    result = await CampaignCoordinator(
        plan,
        release_root,
        executor_factory=lambda observed_plan, _: FakeExecutor(observed_plan),
        adapter_factories={"metrics": _factory(metrics)},
    ).execute_case("OFF-NEGATIVE-1PCT", allow_live_execution=True)

    assert result.status == expected_status
    document = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert document["business_status"] == "passed"
    assert document["negative_drain_observation"]["status"] == expected_status


def test_face_recognition_accepts_uneven_complete_three_instance_participation(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    case = next(
        item for item in plan.catalog.cases if item.case_id == "FACE-RECOGNIZE-500"
    )
    business = _face_business_evidence()
    uneven = _runtime_outcome(
        requests=500,
        acquired=500,
        rejected=0,
        released=500,
        instance_requests={
            "facerec-gpu0": 220,
            "facerec-gpu1": 170,
            "facerec-gpu2": 110,
            "ocr-gpu0": 0,
            "vbas-gpu1": 0,
        },
    )

    observation = _face_instance_observation(case, business, uneven)

    assert observation is not None
    assert observation["status"] == "passed"
    assert observation["person_fact_consistency"]["status"] == "passed"
    routing = observation["recognition_routing_participation"]
    assert routing["status"] == "passed"
    assert routing["observed_instance_request_count"] == {
        "facerec-gpu0": 220,
        "facerec-gpu1": 170,
        "facerec-gpu2": 110,
    }
    assert "计数不关联人物与实例" in str(routing["reason"])
    assert "每个" not in str(observation)
    assert "各完成一遍" not in str(observation)


@pytest.mark.parametrize(
    ("business", "instance_requests", "expected_fact_status", "expected_routing_status"),
    (
        (
            _face_business_evidence(),
            {"facerec-gpu0": 300, "facerec-gpu1": 200},
            "passed",
            "failed",
        ),
        (
            _face_business_evidence(),
            {
                "facerec-gpu0": 200,
                "facerec-gpu1": 150,
                "facerec-gpu2": 100,
                "facerec-gpu3": 50,
            },
            "passed",
            "failed",
        ),
        (
            _face_business_evidence(),
            {"facerec-gpu0": 200, "facerec-gpu1": 150, "facerec-gpu2": 100},
            "passed",
            "failed",
        ),
        (
            _face_business_evidence(),
            {"facerec-gpu0": 250, "facerec-gpu1": 250, "facerec-gpu2": 0},
            "passed",
            "failed",
        ),
        (
            _face_business_evidence(status="failed", recognized=498, invalid=1),
            {"facerec-gpu0": 220, "facerec-gpu1": 170, "facerec-gpu2": 110},
            "failed",
            "passed",
        ),
        (
            _face_business_evidence(status="passed", deleted_absence=0),
            {"facerec-gpu0": 220, "facerec-gpu1": 170, "facerec-gpu2": 110},
            "failed",
            "passed",
        ),
    ),
)
def test_face_recognition_fails_closed_for_incomplete_independent_evidence(
    tmp_path: Path,
    business: dict[str, object],
    instance_requests: dict[str, int],
    expected_fact_status: str,
    expected_routing_status: str,
) -> None:
    plan = _plan(tmp_path)
    case = next(
        item for item in plan.catalog.cases if item.case_id == "FACE-RECOGNIZE-500"
    )
    metrics = _runtime_outcome(
        requests=500,
        acquired=500,
        rejected=0,
        released=500,
        instance_requests=instance_requests,
    )

    observation = _face_instance_observation(case, business, metrics)

    assert observation is not None
    assert observation["status"] == "failed"
    assert observation["person_fact_consistency"]["status"] == expected_fact_status
    assert (
        observation["recognition_routing_participation"]["status"]
        == expected_routing_status
    )


class FakeStageAdapter:
    def __init__(self, outcome: StageCaseOutcome) -> None:
        self.outcome = outcome
        self.calls: list[str] = []

    async def execute(self, case: CaseSpec) -> StageCaseOutcome:
        self.calls.append(case.case_id)
        return self.outcome


def _factory(instance: object) -> Callable[[CampaignPlan, Path], object]:
    def build(plan: CampaignPlan, release_root: Path) -> object:
        del plan, release_root
        return instance

    return build


def test_status_preserves_catalog_phase_order_and_first_phase_readiness(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    coordinator = CampaignCoordinator(
        plan,
        tmp_path / "release",
        adapter_factories={"metrics": _factory(FakeMetricsAdapter())},
    )

    status = coordinator.status()

    assert tuple(item.case_id for item in status.cases) == tuple(
        case.case_id for case in plan.catalog.cases
    )
    assert tuple(item.phase.sequence for item in status.cases) == tuple(
        sorted(item.phase.sequence for item in status.cases)
    )
    assert status.active_phase is CampaignPhase.BASELINE
    assert "BASE-OFFLINE-PPT" in status.ready_case_ids
    assert coordinator.readiness("BASE-MEDIA-DOWNLOAD-1").state == "blocked"


def test_later_phase_is_blocked_until_earlier_required_gate_passes(tmp_path: Path) -> None:
    coordinator = CampaignCoordinator(_plan(tmp_path), tmp_path / "release")

    readiness = coordinator.readiness("OFF-UNIQUE-PPT-100")

    assert readiness.state == "blocked"
    assert "PHASE-0-COMPLETE" in readiness.reason


def test_failed_prerequisite_blocks_dependent_phase_gate(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    release_root = tmp_path / "release"
    baseline_cases = tuple(
        case
        for case in plan.catalog.cases
        if case.phase is CampaignPhase.BASELINE and case.load["kind"] != "phase_gate"
    )
    for case in baseline_cases:
        _write_evidence(
            release_root,
            plan,
            case.case_id,
            "failed" if case.case_id == "BASE-OFFLINE-PPT" else "passed",
        )

    readiness = CampaignCoordinator(plan, release_root).readiness("PHASE-0-COMPLETE")

    assert readiness.state == "blocked"
    assert "BASE-OFFLINE-PPT=failed" in readiness.reason


def test_optional_blocked_evidence_does_not_satisfy_case_or_fail_required_completion(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    release_root = tmp_path / "release"
    _pass_all_required(release_root, plan)
    _write_evidence(release_root, plan, "SOAK-8H-OPTIONAL", "blocked")

    validation = CampaignCoordinator(plan, release_root).validate()

    assert validation.passed is True
    assert validation.optional_pending == ("SOAK-8H-OPTIONAL",)
    assert validation.catalog_verdict["passed"] is False


@pytest.mark.parametrize(
    ("case_id", "phase", "reason_fragment"),
    (
        ("BASE-MEDIA-DOWNLOAD-1", CampaignPhase.BASELINE, "SSH/媒体下载"),
        ("FACE-PHOTO-RESIDUE", CampaignPhase.ONLINE, "残留检查"),
        ("MIXED-DAILY", CampaignPhase.MIXED, "混合负载"),
        ("RECOVERY-OPERATOR-OCR", CampaignPhase.RECOVERY, "故障注入"),
        ("RECOVERY-GPU-0", CampaignPhase.RECOVERY, "故障注入"),
        ("RECOVERY-PLATFORM-CONTROL", CampaignPhase.RECOVERY, "故障注入"),
        ("RECOVERY-KAFKA", CampaignPhase.RECOVERY, "故障注入"),
        ("SOAK-4H", CampaignPhase.SOAK, "长稳负载"),
    ),
)
def test_stage_coordinated_cases_remain_explicit_integration_blockers(
    tmp_path: Path,
    case_id: str,
    phase: CampaignPhase,
    reason_fragment: str,
) -> None:
    plan = _plan(tmp_path)
    release_root = tmp_path / "release"
    _pass_required_before(release_root, plan, phase)
    selected = next(case for case in plan.catalog.cases if case.case_id == case_id)
    for prerequisite in selected.prerequisites:
        prerequisite_case = next(
            case for case in plan.catalog.cases if case.case_id == prerequisite
        )
        if prerequisite_case.phase is phase:
            _write_evidence(release_root, plan, prerequisite)

    readiness = CampaignCoordinator(plan, release_root).readiness(case_id)

    assert readiness.state == "blocked"
    assert reason_fragment in readiness.reason


@pytest.mark.asyncio
async def test_explicit_stage_factories_publish_atomic_case_evidence(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    release_root = tmp_path / "release"
    metrics = FakeMetricsAdapter()
    media = FakeStageAdapter(
        StageCaseOutcome("passed", "媒体下载基线通过", {"concurrency": 1})
    )
    coordinator = CampaignCoordinator(
        plan,
        release_root,
        adapter_factories={
            "metrics": _factory(metrics),
            "media_download": _factory(media),
        },
    )

    assert coordinator.readiness("BASE-MEDIA-DOWNLOAD-1").state == "ready"
    result = await coordinator.execute_case(
        "BASE-MEDIA-DOWNLOAD-1",
        allow_live_execution=True,
    )

    document = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert result.status == "passed"
    assert media.calls == ["BASE-MEDIA-DOWNLOAD-1"]
    assert metrics.calls == [
        ("BASE-MEDIA-DOWNLOAD-1", "before"),
        ("BASE-MEDIA-DOWNLOAD-1", "after"),
    ]
    assert document["adapter"] == "media_download"
    assert document["adapter_evidence"] == {"concurrency": 1}
    assert stat.S_IMODE(result.evidence_path.stat().st_mode) == 0o600
    assert coordinator.readiness("BASE-MEDIA-DOWNLOAD-3").state == "ready"


@pytest.mark.asyncio
async def test_stage_adapter_exception_still_stops_metrics_after_sampling(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    metrics = FakeMetricsAdapter()

    class RaisingAdapter:
        async def execute(self, case: CaseSpec) -> StageCaseOutcome:
            del case
            raise RuntimeError("stage failed")

    result = await CampaignCoordinator(
        plan,
        tmp_path / "release",
        adapter_factories={
            "metrics": _factory(metrics),
            "media_download": _factory(RaisingAdapter()),
        },
    ).execute_case("BASE-MEDIA-DOWNLOAD-1", allow_live_execution=True)

    assert result.status == "failed"
    assert metrics.calls == [
        ("BASE-MEDIA-DOWNLOAD-1", "before"),
        ("BASE-MEDIA-DOWNLOAD-1", "after"),
    ]


@pytest.mark.asyncio
async def test_metrics_adapter_is_required_even_when_case_adapter_is_registered(
    tmp_path: Path,
) -> None:
    media = FakeStageAdapter(StageCaseOutcome("passed", "不应执行", {}))
    coordinator = CampaignCoordinator(
        _plan(tmp_path),
        tmp_path / "release",
        adapter_factories={"media_download": _factory(media)},
    )

    readiness = coordinator.readiness("BASE-MEDIA-DOWNLOAD-1")

    assert readiness.state == "blocked"
    assert "实时主机指标" in readiness.reason
    with pytest.raises(CoordinatorBlockedError, match="实时主机指标"):
        await coordinator.execute_case(
            "BASE-MEDIA-DOWNLOAD-1",
            allow_live_execution=True,
        )
    assert media.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("level", (GuardrailLevel.WARNING, GuardrailLevel.STOP))
async def test_warning_or_stop_guardrail_blocks_stage_escalation(
    tmp_path: Path,
    level: GuardrailLevel,
) -> None:
    plan = _plan(tmp_path)
    release_root = tmp_path / "release"
    assessments = (
        [GuardrailAssessment(level, (f"{level.value} threshold",))]
        if level is GuardrailLevel.STOP
        else [_clear_guardrail(), GuardrailAssessment(level, ("disk warning",))]
    )
    metrics = FakeMetricsAdapter(assessments)
    media = FakeStageAdapter(StageCaseOutcome("passed", "档位执行完成", {"concurrency": 1}))
    coordinator = CampaignCoordinator(
        plan,
        release_root,
        adapter_factories={
            "metrics": _factory(metrics),
            "media_download": _factory(media),
        },
    )

    result = await coordinator.execute_case(
        "BASE-MEDIA-DOWNLOAD-1",
        allow_live_execution=True,
    )

    assert result.status == "blocked"
    assert coordinator.readiness("BASE-MEDIA-DOWNLOAD-3").state == "blocked"
    assert "禁止继续升级" in json.loads(
        result.evidence_path.read_text(encoding="utf-8")
    )["reason"]
    if level is GuardrailLevel.STOP:
        assert media.calls == []
    else:
        assert media.calls == ["BASE-MEDIA-DOWNLOAD-1"]


@pytest.mark.asyncio
async def test_recovery_failure_stops_all_following_fault_cases(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    release_root = tmp_path / "release"
    _pass_required_before(release_root, plan, CampaignPhase.RECOVERY)
    metrics = FakeMetricsAdapter()
    fault = FakeStageAdapter(
        StageCaseOutcome(
            "passed",
            "故障动作结束但恢复失败",
            {"scenario": "first"},
            recovery_succeeded=False,
        )
    )
    coordinator = CampaignCoordinator(
        plan,
        release_root,
        adapter_factories={"metrics": _factory(metrics), "fault": _factory(fault)},
    )

    assert coordinator.readiness("RECOVERY-OPERATOR-ASR-OFFLINE").state == "ready"
    assert coordinator.readiness("RECOVERY-OPERATOR-ASR-ONLINE").state == "blocked"
    result = await coordinator.execute_case(
        "RECOVERY-OPERATOR-ASR-OFFLINE",
        allow_live_execution=True,
    )

    assert result.status == "failed"
    later = coordinator.readiness("RECOVERY-OPERATOR-ASR-ONLINE")
    assert later.state == "blocked"
    assert "禁止继续升级或注入故障" in later.reason
    assert fault.calls == ["RECOVERY-OPERATOR-ASR-OFFLINE"]


@pytest.mark.asyncio
async def test_four_hour_soak_is_required_and_eight_hour_soak_remains_optional(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    release_root = tmp_path / "release"
    _pass_required_before(release_root, plan, CampaignPhase.SOAK)
    metrics = FakeMetricsAdapter()
    soak = FakeStageAdapter(
        StageCaseOutcome("passed", "四小时长稳完成", {"hours": 4})
    )
    coordinator = CampaignCoordinator(
        plan,
        release_root,
        adapter_factories={"metrics": _factory(metrics), "soak": _factory(soak)},
    )

    assert coordinator.readiness("SOAK-8H-OPTIONAL").state == "blocked"
    four_hour = await coordinator.execute_case("SOAK-4H", allow_live_execution=True)
    assert four_hour.status == "passed"
    assert coordinator.readiness("SOAK-8H-OPTIONAL").state == "ready"

    gate = await coordinator.execute_case("PHASE-6-COMPLETE")
    validation = coordinator.validate()
    assert gate.status == "passed"
    assert validation.passed is True
    assert validation.optional_pending == ("SOAK-8H-OPTIONAL",)


@pytest.mark.asyncio
async def test_default_execute_does_not_instantiate_live_executor(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    called = False

    def forbidden_factory(plan: CampaignPlan, release_root: Path) -> Never:
        del plan, release_root
        nonlocal called
        called = True
        raise AssertionError("live executor must not be instantiated")

    coordinator = CampaignCoordinator(
        plan,
        tmp_path / "release",
        executor_factory=forbidden_factory,
        adapter_factories={"metrics": _factory(FakeMetricsAdapter())},
    )

    with pytest.raises(CoordinatorBlockedError, match="allow-live-execution"):
        await coordinator.execute_case("BASE-OFFLINE-PPT")
    assert called is False


@pytest.mark.asyncio
async def test_live_executor_case_requires_metrics_adapter_for_readiness(
    tmp_path: Path,
) -> None:
    coordinator = CampaignCoordinator(_plan(tmp_path), tmp_path / "release")

    readiness = coordinator.readiness("BASE-OFFLINE-PPT")

    assert readiness.state == "blocked"
    assert readiness.requires_live_execution is True
    assert "实时主机指标" in readiness.reason
    with pytest.raises(CoordinatorBlockedError, match="实时主机指标"):
        await coordinator.execute_case("BASE-OFFLINE-PPT", allow_live_execution=True)


@pytest.mark.asyncio
async def test_explicit_live_opt_in_runs_supported_executor_case(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    release_root = tmp_path / "release"
    calls: list[str] = []
    metrics = FakeMetricsAdapter()

    class FakeExecutor:
        def __init__(self, candidate: CampaignPlan) -> None:
            self.candidate = candidate

        async def execute(self, case_id: str) -> Path:
            calls.append(case_id)
            return _write_evidence(release_root, self.candidate, case_id)

    def factory(candidate: CampaignPlan, candidate_root: Path) -> FakeExecutor:
        assert candidate.campaign_id == plan.campaign_id
        assert candidate is not plan
        assert candidate_root == release_root
        return FakeExecutor(candidate)

    result = await CampaignCoordinator(
        plan,
        release_root,
        executor_factory=factory,
        adapter_factories={"metrics": _factory(metrics)},
    ).execute_case("BASE-OFFLINE-PPT", allow_live_execution=True)

    assert result.status == "passed"
    assert calls == ["BASE-OFFLINE-PPT"]
    assert metrics.calls == [
        ("BASE-OFFLINE-PPT", "before"),
        ("BASE-OFFLINE-PPT", "after"),
        ("BASE-OFFLINE-PPT", "execute"),
    ]
    document = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert document["business_status"] == "passed"
    assert document["runtime_observability"]["status"] == "passed"
    assert document["runtime_observability"]["runtime_metrics"]["peak_inflight"] == {
        "vbas-gpu0": 2
    }
    assert document["runtime_observability"]["sample_evidence"] == [
        "campaign/runtime-metrics/BASE-OFFLINE-PPT/00000001.json",
        "campaign/runtime-metrics/BASE-OFFLINE-PPT/00000002.json",
    ]
    business_path = release_root / document["business_evidence_path"]
    assert json.loads(business_path.read_text(encoding="utf-8"))["status"] == "passed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gateway_counts", "expected_status"),
    (
        ((0, 0, 0, 0), "passed"),
        ((1, 1, 0, 1), "failed"),
    ),
)
async def test_rejected_image_boundary_requires_observed_zero_lease_delta(
    tmp_path: Path,
    gateway_counts: tuple[int, int, int, int],
    expected_status: str,
) -> None:
    plan = _plan(tmp_path)
    release_root = tmp_path / "release"
    _pass_required_before(release_root, plan, CampaignPhase.ONLINE)
    requests, acquired, rejected, released = gateway_counts
    metrics = FakeMetricsAdapter(
        outcome=_runtime_outcome(
            requests=requests,
            acquired=acquired,
            rejected=rejected,
            released=released,
            instance_requests=(
                {"ocr-gpu0": requests} if requests else {}
            ),
        )
    )

    class FakeExecutor:
        def __init__(self, observed_plan: CampaignPlan) -> None:
            self.observed_plan = observed_plan

        async def execute(self, case_id: str) -> Path:
            return _write_evidence(release_root, self.observed_plan, case_id)

    result = await CampaignCoordinator(
        plan,
        release_root,
        executor_factory=lambda observed_plan, _root: FakeExecutor(observed_plan),
        adapter_factories={"metrics": _factory(metrics)},
    ).execute_case("IMG-BOUNDARY-INVALID-B64", allow_live_execution=True)

    assert result.status == expected_status
    evidence = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert evidence["lease_boundary_observation"]["status"] == expected_status
    assert evidence["lease_boundary_observation"]["expected_lease_acquisitions"] == 0
    assert evidence["lease_boundary_observation"]["observed_lease_acquisitions"] == acquired


@pytest.mark.asyncio
async def test_live_case_pre_guardrail_blocks_before_business_executor(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    release_root = tmp_path / "release"
    metrics = FakeMetricsAdapter([GuardrailAssessment(GuardrailLevel.WARNING, ("disk warning",))])

    def forbidden_factory(plan: CampaignPlan, release_root: Path) -> Never:
        del plan, release_root
        raise AssertionError("前置护栏非 CLEAR 时不得执行业务负载")

    result = await CampaignCoordinator(
        plan,
        release_root,
        executor_factory=forbidden_factory,
        adapter_factories={"metrics": _factory(metrics)},
    ).execute_case("BASE-OFFLINE-PPT", allow_live_execution=True)

    assert result.status == "blocked"
    document = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert document["status"] == "blocked"
    assert document["guardrail_before"]["level"] == "WARNING"
    assert metrics.calls == [
        ("BASE-OFFLINE-PPT", "before"),
        ("BASE-OFFLINE-PPT", "execute"),
    ]


@pytest.mark.asyncio
async def test_live_case_post_guardrail_cannot_leave_normative_evidence_passed(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    release_root = tmp_path / "release"
    metrics = FakeMetricsAdapter(
        [_clear_guardrail(), GuardrailAssessment(GuardrailLevel.STOP, ("host OOM",))]
    )

    class FakeExecutor:
        def __init__(self, observed_plan: CampaignPlan) -> None:
            self.observed_plan = observed_plan

        async def execute(self, case_id: str) -> Path:
            return _write_evidence(release_root, self.observed_plan, case_id)

    def factory(observed_plan: CampaignPlan, candidate_root: Path) -> FakeExecutor:
        assert candidate_root == release_root
        return FakeExecutor(observed_plan)

    result = await CampaignCoordinator(
        plan,
        release_root,
        executor_factory=factory,
        adapter_factories={"metrics": _factory(metrics)},
    ).execute_case("BASE-OFFLINE-PPT", allow_live_execution=True)

    assert result.status == "blocked"
    normative = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert normative["status"] == "blocked"
    assert normative["business_status"] == "passed"
    assert normative["guardrail_after"] == {"level": "STOP", "reasons": ["host OOM"]}
    business = json.loads(
        (release_root / normative["business_evidence_path"]).read_text(encoding="utf-8")
    )
    assert business["status"] == "passed"


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ("WARNING", "STOP"))
async def test_live_case_rejects_passed_runtime_summary_with_non_clear_guardrail(
    tmp_path: Path,
    level: str,
) -> None:
    plan = _plan(tmp_path)
    release_root = tmp_path / "release"
    metrics = FakeMetricsAdapter(
        outcome=_runtime_outcome(
            requests=0,
            acquired=0,
            rejected=0,
            released=0,
            case_id="BASE-OFFLINE-PPT",
            latest_guardrail={"level": level, "reasons": [f"sample {level}"]},
        )
    )

    class FakeExecutor:
        def __init__(self, observed_plan: CampaignPlan) -> None:
            self.observed_plan = observed_plan

        async def execute(self, case_id: str) -> Path:
            return _write_evidence(release_root, self.observed_plan, case_id)

    result = await CampaignCoordinator(
        plan,
        release_root,
        executor_factory=lambda observed_plan, _: FakeExecutor(observed_plan),
        adapter_factories={"metrics": _factory(metrics)},
    ).execute_case("BASE-OFFLINE-PPT", allow_live_execution=True)

    assert result.status == "failed"
    document = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert document["status"] == "failed"
    assert document["business_status"] == "passed"
    assert document["runtime_observability"] == {
        "error_type": "ValueError",
        "reason": "运行时指标汇总失败: ValueError",
        "status": "failed",
    }


@pytest.mark.asyncio
async def test_live_case_missing_runtime_summary_fails_closed(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    release_root = tmp_path / "release"
    metrics = FakeMetricsAdapter(
        outcome=StageCaseOutcome("passed", "伪造通过但缺少 summary", {"sample_evidence": []})
    )

    class FakeExecutor:
        def __init__(self, observed_plan: CampaignPlan) -> None:
            self.observed_plan = observed_plan

        async def execute(self, case_id: str) -> Path:
            return _write_evidence(release_root, self.observed_plan, case_id)

    result = await CampaignCoordinator(
        plan,
        release_root,
        executor_factory=lambda observed_plan, _: FakeExecutor(observed_plan),
        adapter_factories={"metrics": _factory(metrics)},
    ).execute_case("BASE-OFFLINE-PPT", allow_live_execution=True)

    assert result.status == "failed"
    document = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert document["business_status"] == "passed"
    assert document["runtime_observability"] == {
        "error_type": "ValueError",
        "reason": "运行时指标汇总失败: ValueError",
        "status": "failed",
    }


@pytest.mark.asyncio
async def test_live_opt_in_never_enables_fault_or_remote_probe_kinds(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    called = False

    def forbidden_factory(plan: CampaignPlan, release_root: Path) -> Never:
        del plan, release_root
        nonlocal called
        called = True
        raise AssertionError("stage adapter must not be inferred")

    coordinator = CampaignCoordinator(
        plan,
        tmp_path / "release",
        executor_factory=forbidden_factory,
    )

    with pytest.raises(CoordinatorBlockedError, match="SSH/媒体下载"):
        await coordinator.execute_case(
            "BASE-MEDIA-DOWNLOAD-1",
            allow_live_execution=True,
        )
    assert called is False


def test_evidence_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    release_root = tmp_path / "release"
    _write_evidence(
        release_root,
        plan,
        "BASE-OFFLINE-PPT",
        campaign_id="another-campaign",
    )

    with pytest.raises(ValueError, match="身份不属于当前 Campaign"):
        CampaignCoordinator(plan, release_root).status()


def test_cli_create_validate_and_default_execute_are_local_and_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path = tmp_path / "fixtures.json"
    manifest_path.write_text(
        _fixture_manifest(tmp_path).model_dump_json(indent=2),
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)
    plan_path = tmp_path / "campaign-plan.json"
    release_root = tmp_path / "release"

    create_code = main(
        [
            "create-plan",
            "--release-tag",
            "release-20260823",
            "--git-sha",
            _GIT_SHA,
            "--seed",
            "260823",
            "--fixture-manifest",
            str(manifest_path),
            "--output",
            str(plan_path),
        ]
    )
    create_output = json.loads(capsys.readouterr().out)
    assert create_code == 0
    assert create_output["status"] == "created"
    assert load_campaign_plan(plan_path).git_sha == _GIT_SHA

    validate_code = main(
        ["validate", "--plan", str(plan_path), "--release-root", str(release_root)]
    )
    validate_output = json.loads(capsys.readouterr().out)
    assert validate_code == 1
    assert validate_output["status"] == "failed"

    execute_code = main(
        [
            "execute-case",
            "--plan",
            str(plan_path),
            "--release-root",
            str(release_root),
            "--case-id",
            "BASE-OFFLINE-PPT",
        ]
    )
    execute_output = json.loads(capsys.readouterr().out)
    assert execute_code == 3
    assert execute_output["status"] == "blocked"
    assert "实时主机指标" in execute_output["reason"]


def test_cli_loads_only_explicit_named_stage_adapter_factories(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    plan_path = tmp_path / "campaign-plan.json"
    publish_campaign_plan(plan_path, plan)
    release_root = tmp_path / "release"
    metrics = FakeMetricsAdapter()
    media = FakeStageAdapter(
        StageCaseOutcome("passed", "CLI 媒体基线完成", {"concurrency": 1})
    )
    module = ModuleType("coordinator_test_adapters")
    module.__dict__["metrics_factory"] = _factory(metrics)
    module.__dict__["media_factory"] = _factory(media)
    monkeypatch.setitem(sys.modules, module.__name__, module)

    exit_code = main(
        [
            "execute-case",
            "--plan",
            str(plan_path),
            "--release-root",
            str(release_root),
            "--case-id",
            "BASE-MEDIA-DOWNLOAD-1",
            "--adapter-factory",
            "metrics=coordinator_test_adapters:metrics_factory",
            "--adapter-factory",
            "media_download=coordinator_test_adapters:media_factory",
            "--allow-live-execution",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["status"] == "passed"
    assert media.calls == ["BASE-MEDIA-DOWNLOAD-1"]


def test_cli_execute_sequence_records_each_terminal_result(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    plan_path = tmp_path / "campaign-plan.json"
    publish_campaign_plan(plan_path, plan)
    release_root = tmp_path / "release"
    metrics = FakeMetricsAdapter()
    media = FakeStageAdapter(
        StageCaseOutcome("passed", "CLI 媒体基线完成", {"concurrency": 1})
    )
    module = ModuleType("coordinator_sequence_test_adapters")
    module.__dict__["metrics_factory"] = _factory(metrics)
    module.__dict__["media_factory"] = _factory(media)
    monkeypatch.setitem(sys.modules, module.__name__, module)

    exit_code = main(
        [
            "execute-sequence",
            "--plan",
            str(plan_path),
            "--release-root",
            str(release_root),
            "--case-id",
            "BASE-MEDIA-DOWNLOAD-1",
            "--case-id",
            "BASE-MEDIA-DOWNLOAD-3",
            "--adapter-factory",
            "metrics=coordinator_sequence_test_adapters:metrics_factory",
            "--adapter-factory",
            "media_download=coordinator_sequence_test_adapters:media_factory",
            "--allow-live-execution",
        ]
    )

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert exit_code == 0
    assert [(event["event"], event.get("case_id")) for event in events] == [
        ("sequence_started", None),
        ("case_started", "BASE-MEDIA-DOWNLOAD-1"),
        ("case_ended", "BASE-MEDIA-DOWNLOAD-1"),
        ("case_started", "BASE-MEDIA-DOWNLOAD-3"),
        ("case_ended", "BASE-MEDIA-DOWNLOAD-3"),
        ("sequence_ended", None),
    ]
    assert events[0]["git_sha"] == _GIT_SHA
    assert events[0]["case_ids"] == [
        "BASE-MEDIA-DOWNLOAD-1",
        "BASE-MEDIA-DOWNLOAD-3",
    ]
    assert events[-1]["status"] == "passed"
    assert events[-1]["exit_code"] == 0
    assert media.calls == ["BASE-MEDIA-DOWNLOAD-1", "BASE-MEDIA-DOWNLOAD-3"]


def test_wrapper_is_local_non_mutating_and_uses_project_venv() -> None:
    project_root = Path(__file__).resolve().parents[2]
    wrapper = project_root / "deploy/scripts/run-extreme-load-campaign"
    content = wrapper.read_text(encoding="utf-8")

    assert content.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert '"$PROJECT_ROOT/.venv/bin/python"' in content
    assert '"$PROJECT_ROOT/scripts/run_extreme_load_campaign.py" "$@"' in content
    assert not {"ssh", "docker", "prune", " down "} & set(content.lower().split())
    assert wrapper.stat().st_mode & stat.S_IXUSR


def test_sequence_launcher_is_non_overwriting_and_detached() -> None:
    project_root = Path(__file__).resolve().parents[2]
    launcher = project_root / "deploy/scripts/start-extreme-load-campaign-sequence"
    content = launcher.read_text(encoding="utf-8")

    assert content.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert 'nohup "$RUNNER" "$@" </dev/null' in content
    assert '[[ -e "$PID_FILE" || -L "$PID_FILE"' in content
    assert '-e "$LOG_FILE" || -L "$LOG_FILE"' in content
    assert 'chmod 0600 "$PID_TEMP" "$LOG_FILE"' in content
    assert launcher.stat().st_mode & stat.S_IXUSR


def test_status_reports_unimplemented_live_integration_boundaries(tmp_path: Path) -> None:
    blockers = (
        CampaignCoordinator(_plan(tmp_path), tmp_path / "release").status().integration_blockers
    )

    assert any("实时主机" in item for item in blockers)
    assert any("SSH/媒体下载" in item for item in blockers)
    assert any("故障注入" in item for item in blockers)
    assert any("混合负载" in item for item in blockers)
    assert any("长稳负载" in item for item in blockers)
