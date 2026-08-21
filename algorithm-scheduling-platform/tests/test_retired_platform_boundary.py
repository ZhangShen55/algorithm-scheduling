from __future__ import annotations

import json
from pathlib import Path

import pytest

from deploy.scripts.operator_topology import CURRENT_TOPOLOGY
from scripts.milestone_2b_case_catalog import SCHEMA_VERSION as CATALOG_SCHEMA_VERSION
from scripts.milestone_2b_case_catalog import load_case_catalog
from scripts.milestone_2b_report_contract import (
    EXECUTION_SCHEMA_VERSION,
    load_report_plan_bytes,
    validate_cases_envelope,
)
from scripts.milestone_2b_report_contract import (
    SCHEMA_VERSION as PLAN_SCHEMA_VERSION,
)
from scripts.run_milestone_2b_case_batch import resolve_runner
from scripts.verify_retired_text_analysis_exclusion import find_violations

PLATFORM_ROOT = Path(__file__).resolve().parents[1]


def test_current_topology_is_the_exact_seven_operator_authority() -> None:
    assert CURRENT_TOPOLOGY.totals == {
        "operator_types": 7,
        "instances": 21,
        "gpu_instances": 18,
        "cpu_instances": 3,
        "config_authority_processes": 14,
        "operator_smoke_types": 7,
    }
    assert len(CURRENT_TOPOLOGY.instance_ids) == 21
    assert "text_analysis" not in CURRENT_TOPOLOGY.by_code


def test_active_platform_files_exclude_retired_runtime_identifiers() -> None:
    assert find_violations() == []


def test_case_catalog_uses_new_schema_and_exact_retirement_replacements() -> None:
    catalog = load_case_catalog(PLATFORM_ROOT / "deploy/milestone-2b-case-catalog.yaml")
    case_ids = {case.case_id for case in catalog.cases}
    assert catalog.schema_version == CATALOG_SCHEMA_VERSION == 2
    assert len(case_ids) == 243
    assert {f"RET-{number:03d}" for number in range(1, 11)} <= case_ids
    assert "DEP-008" not in case_ids
    assert not {f"KEY-{number:03d}" for number in range(1, 6)} & case_ids
    assert not {f"ASR-{number:03d}" for number in range(14, 18)} & case_ids
    for number in range(1, 11):
        resolve_runner(f"retirement.ret_{number:03d}")


def test_legacy_eight_operator_report_schema_is_rejected() -> None:
    legacy_plan = json.loads(
        (PLATFORM_ROOT / "deploy/milestone-2b-report-plan.json").read_text(
            encoding="utf-8"
        )
    )
    legacy_plan["schema_version"] = 1
    with pytest.raises(ValueError, match=str(PLAN_SCHEMA_VERSION)):
        load_report_plan_bytes(json.dumps(legacy_plan).encode("utf-8"))

    with pytest.raises(ValueError):
        validate_cases_envelope(
            {
                "schema_version": 2,
                "release_tag": "v1.0_260821",
                "git_sha": "a" * 40,
                "plan_sha256": "b" * 64,
                "coverage": {},
                "cases": [],
            }
        )
    assert PLAN_SCHEMA_VERSION == 2
    assert EXECUTION_SCHEMA_VERSION == 3
