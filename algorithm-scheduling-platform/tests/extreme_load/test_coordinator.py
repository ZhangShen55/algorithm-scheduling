from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Never

import pytest

from scripts.extreme_load.catalog import (
    CampaignPhase,
    FixtureDescriptor,
    FixtureKind,
    FixtureManifest,
)
from scripts.extreme_load.coordinator import (
    CampaignCoordinator,
    CoordinatorBlockedError,
)
from scripts.extreme_load.plan import (
    CampaignPlan,
    build_campaign_plan,
    execution_path,
    load_campaign_plan,
)
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


def test_status_preserves_catalog_phase_order_and_first_phase_readiness(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    coordinator = CampaignCoordinator(plan, tmp_path / "release")

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

    readiness = CampaignCoordinator(plan, release_root).readiness(case_id)

    assert readiness.state == "blocked"
    assert reason_fragment in readiness.reason


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
    )

    with pytest.raises(CoordinatorBlockedError, match="allow-live-execution"):
        await coordinator.execute_case("BASE-OFFLINE-PPT")
    assert called is False


@pytest.mark.asyncio
async def test_explicit_live_opt_in_runs_supported_executor_case(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    release_root = tmp_path / "release"
    calls: list[str] = []

    class FakeExecutor:
        async def execute(self, case_id: str) -> Path:
            calls.append(case_id)
            return _write_evidence(release_root, plan, case_id)

    def factory(candidate: CampaignPlan, candidate_root: Path) -> FakeExecutor:
        assert candidate is plan
        assert candidate_root == release_root
        return FakeExecutor()

    result = await CampaignCoordinator(
        plan,
        release_root,
        executor_factory=factory,
    ).execute_case("BASE-OFFLINE-PPT", allow_live_execution=True)

    assert result.status == "passed"
    assert calls == ["BASE-OFFLINE-PPT"]


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
    assert "allow-live-execution" in execute_output["reason"]


def test_wrapper_is_local_non_mutating_and_uses_project_venv() -> None:
    project_root = Path(__file__).resolve().parents[2]
    wrapper = project_root / "deploy/scripts/run-extreme-load-campaign"
    content = wrapper.read_text(encoding="utf-8")

    assert content.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert '"$PROJECT_ROOT/.venv/bin/python"' in content
    assert '"$PROJECT_ROOT/scripts/run_extreme_load_campaign.py" "$@"' in content
    assert not {"ssh", "docker", "prune", " down "} & set(content.lower().split())
    assert wrapper.stat().st_mode & stat.S_IXUSR


def test_status_reports_unimplemented_live_integration_boundaries(tmp_path: Path) -> None:
    blockers = (
        CampaignCoordinator(_plan(tmp_path), tmp_path / "release").status().integration_blockers
    )

    assert any("实时主机" in item for item in blockers)
    assert any("SSH/媒体下载" in item for item in blockers)
    assert any("故障注入" in item for item in blockers)
    assert any("混合负载与长稳负载" in item for item in blockers)
