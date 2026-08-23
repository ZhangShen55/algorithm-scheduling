from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from scripts.extreme_load.catalog import (
    CampaignCatalog,
    CampaignPhase,
    CaseExecution,
    FixtureDescriptor,
    FixtureKind,
    FixtureManifest,
    default_catalog,
    default_load_profile,
    external_fixture_manifest_template,
    validate_case_executions,
)
from scripts.extreme_load.core import ReproducibleIdentity


def test_default_catalog_covers_all_seven_phases_and_has_complete_cases() -> None:
    catalog = default_catalog()

    assert catalog.schema_version == 2
    assert len(catalog.cases) > 140
    assert {case.phase for case in catalog.cases} == set(CampaignPhase)
    assert len({case.case_id for case in catalog.cases}) == len(catalog.cases)
    for case in catalog.cases:
        assert case.timeout_seconds > 0
        assert case.expected
        assert case.guardrails
        assert case.cleanup
        assert case.evidence_path.startswith(f"campaign/phase-{case.phase.sequence}-")

    case_ids = {case.case_id for case in catalog.cases}
    assert {
        "OFF-UNIQUE-ALL-1000",
        "OFF-LONG-COURSE-36",
        "QUERY-HERD-1000",
        "IMG-VBAS-1000",
        "SSTREAM-1000-5S",
        "ASR-WS-150",
        "FACE-MANAGE-5000",
        "MIXED-EXTREME",
        "RECOVERY-GPU-2",
        "SOAK-4H",
        "SOAK-8H-OPTIONAL",
    }.issubset(case_ids)
    optional_soak = next(
        case for case in catalog.cases if case.case_id == "SOAK-8H-OPTIONAL"
    )
    assert optional_soak.required is False


def test_catalog_rejects_duplicate_ids_unknown_prerequisites_and_cycles() -> None:
    baseline = default_catalog().cases[0]
    duplicate = baseline.model_copy(update={"evidence_path": "campaign/duplicate.json"})
    with pytest.raises(ValidationError, match="用例 ID 重复"):
        CampaignCatalog(schema_version=1, cases=[baseline, duplicate])

    unknown = baseline.model_copy(
        update={"case_id": "UNKNOWN-001", "prerequisites": ("MISSING-001",)}
    )
    with pytest.raises(ValidationError, match="未知前置"):
        CampaignCatalog(schema_version=1, cases=[unknown])

    first = baseline.model_copy(
        update={"case_id": "CYCLE-001", "prerequisites": ("CYCLE-002",)}
    )
    second = baseline.model_copy(
        update={"case_id": "CYCLE-002", "prerequisites": ("CYCLE-001",)}
    )
    with pytest.raises(ValidationError, match="循环依赖"):
        CampaignCatalog(schema_version=1, cases=[first, second])


def test_fixture_manifest_contains_metadata_only_and_rejects_inline_media(
    tmp_path: Path,
) -> None:
    fixture_path = tmp_path / "teacher-short.mp4"
    descriptor = FixtureDescriptor(
        fixture_id="teacher-short",
        kind=FixtureKind.SHORT_TEACHER_VIDEO,
        path=str(fixture_path),
        size_bytes=1024,
        duration_seconds=30,
        sha256="a" * 64,
    )
    manifest = FixtureManifest(schema_version=1, fixtures=[descriptor])

    assert manifest.fixtures[0].path == str(fixture_path)
    assert "content" not in manifest.model_dump_json()
    with pytest.raises(ValidationError):
        FixtureDescriptor(
            fixture_id="inline",
            kind=FixtureKind.ONLINE_IMAGE,
            path="data:image/png;base64,AA==",
            size_bytes=1,
            sha256="b" * 64,
        )
    with pytest.raises(ValidationError):
        FixtureDescriptor.model_validate(
            {
                **descriptor.model_dump(),
                "fixture_id": "leak",
                "content": "AA==",
            }
        )


def test_external_fixture_template_covers_all_media_kinds_without_bytes() -> None:
    template = external_fixture_manifest_template()
    serialized = str(template).lower()

    assert {entry["kind"] for entry in template["fixtures"]} == {
        kind.value for kind in FixtureKind
    }
    assert "base64" not in serialized
    assert "content" not in serialized


def test_repository_fixture_template_matches_catalog_authority() -> None:
    path = Path("deploy/templates/extreme-load-fixtures.example.yaml")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert document == external_fixture_manifest_template()


def test_load_profile_freezes_required_extreme_tiers() -> None:
    profile = default_load_profile()

    assert profile.unique_submission_bursts == (100, 300, 1000)
    assert profile.idempotent_submission_bursts == (30, 100, 300, 1000)
    assert profile.long_course_concurrency == (3, 6, 12, 24, 36)
    assert profile.image_concurrency[-1] == 1000
    assert profile.realtime_asr_sessions == (1, 10, 24, 30, 60, 90, 150)
    assert profile.query_qps == (50, 100, 300, 1000)
    assert profile.soak_hours == (4, 8)


def test_versioned_catalog_and_profile_accept_json_yaml_list_shapes() -> None:
    catalog = default_catalog()
    profile = default_load_profile()

    assert CampaignCatalog.model_validate(catalog.model_dump(mode="json")) == catalog
    assert type(profile).model_validate(profile.model_dump(mode="json")) == profile

    fixture = FixtureManifest.model_validate(
        {
            "schema_version": 1,
            "fixtures": [
                {
                    "fixture_id": "online-image",
                    "kind": "online_image",
                    "path": "/fixtures/online.png",
                    "size_bytes": 128,
                    "duration_seconds": None,
                    "sha256": "c" * 64,
                }
            ],
        }
    )
    assert fixture.fixtures[0].kind is FixtureKind.ONLINE_IMAGE


def test_same_seed_replays_ids_but_new_campaign_namespace_changes_them() -> None:
    first = ReproducibleIdentity("campaign-a", seed=260823)
    replay = ReproducibleIdentity("campaign-a", seed=260823)
    other = ReproducibleIdentity("campaign-b", seed=260823)

    assert first.task_id("OFFLINE-001", 7) == replay.task_id("OFFLINE-001", 7)
    assert first.trace_id("OFFLINE-001", 7) == replay.trace_id("OFFLINE-001", 7)
    assert first.task_id("OFFLINE-001", 7) != other.task_id("OFFLINE-001", 7)


def test_case_aggregation_requires_exactly_one_evidence_per_required_case() -> None:
    catalog = default_catalog()
    complete = [
        CaseExecution(case_id=case.case_id, status="passed", evidence_path=case.evidence_path)
        for case in catalog.cases
    ]

    assert validate_case_executions(catalog, complete).passed is True
    assert validate_case_executions(catalog, complete[:-1]).passed is False
    assert validate_case_executions(catalog, [*complete, complete[0]]).passed is False
    missing_evidence = complete[0].model_copy(update={"evidence_path": ""})
    assert validate_case_executions(catalog, [missing_evidence, *complete[1:]]).passed is False
    unexpected = CaseExecution(
        case_id="UNKNOWN-CASE",
        status="passed",
        evidence_path="campaign/unknown.json",
    )
    assert validate_case_executions(catalog, [*complete, unexpected]).unexpected == (
        "UNKNOWN-CASE",
    )

    optional_id = "SOAK-8H-OPTIONAL"
    without_optional = [item for item in complete if item.case_id != optional_id]
    assert validate_case_executions(catalog, without_optional).passed is True
