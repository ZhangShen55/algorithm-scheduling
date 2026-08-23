from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from scripts.extreme_load.catalog import (
    CampaignCatalog,
    CampaignPhase,
    CaseSpec,
    FixtureDescriptor,
    FixtureKind,
    FixtureManifest,
)
from scripts.extreme_load.plan import (
    CampaignPlan,
    build_campaign_plan,
    execution_path,
    load_campaign_plan,
    load_fixture_manifest,
    publish_campaign_plan,
    read_case_evidence,
    read_case_executions,
)
from scripts.extreme_load.report import atomic_write_report


def _case() -> CaseSpec:
    return CaseSpec(
        case_id="PLAN-CASE",
        phase=CampaignPhase.BASELINE,
        load={"kind": "phase_gate"},
        fixture_ids=("external-fixture-manifest",),
        expected="plan evidence",
        timeout_seconds=30,
        guardrails=("evidence",),
        cleanup=("drain",),
        evidence_path="campaign/phase-0-baseline/plan-case.json",
    )


def _manifest(tmp_path: Path) -> FixtureManifest:
    return FixtureManifest(
        schema_version=1,
        fixtures=(
            FixtureDescriptor(
                fixture_id="online-image",
                kind=FixtureKind.ONLINE_IMAGE,
                path=str(tmp_path / "external-image.png"),
                size_bytes=1,
                sha256="a" * 64,
            ),
        ),
    )


def _plan(tmp_path: Path) -> CampaignPlan:
    return build_campaign_plan(
        release_tag="release-1",
        git_sha="a" * 40,
        seed=7,
        control_origin="http://192.168.29.11:18100",
        gateway_origin="http://192.168.29.11:18103",
        fixture_manifest=_manifest(tmp_path),
        catalog=CampaignCatalog(schema_version=1, cases=(_case(),)),
    )


def _evidence_document(plan: CampaignPlan, case: CaseSpec) -> dict[str, object]:
    return {
        "schema_version": 1,
        "evidence_type": "extreme_load_campaign_case",
        "campaign_id": plan.campaign_id,
        "release_tag": plan.release_tag,
        "git_sha": plan.git_sha,
        "case_id": case.case_id,
        "phase": case.phase.value,
        "status": "passed",
    }


def test_campaign_plan_publication_is_atomic_0600_and_non_overwriting(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    target = tmp_path / "release" / "campaign-plan.json"

    publish_campaign_plan(target, plan)

    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.stat().st_nlink == 1
    assert load_campaign_plan(target) == plan
    with pytest.raises(FileExistsError):
        publish_campaign_plan(target, plan)


def test_fixture_manifest_reader_requires_0600_owner_and_single_link(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fixture-manifest.json"
    path.write_text(json.dumps(_manifest(tmp_path).model_dump(mode="json")))
    path.chmod(0o600)

    assert load_fixture_manifest(path) == _manifest(tmp_path)
    path.chmod(0o644)
    with pytest.raises(PermissionError, match="0600"):
        load_fixture_manifest(path)
    path.chmod(0o600)
    alias = tmp_path / "fixture-manifest.alias.json"
    os.link(path, alias)
    with pytest.raises(PermissionError, match="单硬链接"):
        load_fixture_manifest(path)


def test_case_evidence_requires_exact_path_identity_and_secure_file(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    case = plan.catalog.cases[0]
    release_root = tmp_path / "release"
    path = execution_path(release_root, plan, case.case_id)
    atomic_write_report(
        path,
        json.dumps(_evidence_document(plan, case), ensure_ascii=False),
    )

    assert read_case_evidence(release_root, plan, case)["status"] == "passed"
    assert read_case_executions(release_root, plan)[0].case_id == case.case_id
    path.chmod(0o644)
    with pytest.raises(PermissionError, match="0600"):
        read_case_executions(release_root, plan)

    mismatched_root = tmp_path / "mismatched"
    mismatched_path = execution_path(mismatched_root, plan, case.case_id)
    document = _evidence_document(plan, case)
    document["release_tag"] = "other-release"
    atomic_write_report(mismatched_path, json.dumps(document))
    with pytest.raises(ValueError, match="release_tag"):
        read_case_evidence(mismatched_root, plan, case)
