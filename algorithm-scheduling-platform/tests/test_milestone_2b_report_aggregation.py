from __future__ import annotations

import copy
import importlib
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, get_type_hints

import pytest

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = PLATFORM_ROOT / "deploy" / "milestone-2b-report-plan.json"
CONTRACT_PATH = PLATFORM_ROOT / "scripts" / "milestone_2b_report_contract.py"

REASON = (
    "当前仓库没有该用例的受控目标服务器 runner 与运行证据 schema；"
    "本 release 未执行，现有本地单元测试或正向健康检查不等价于现场执行。"
)
EXPECTED_COUNTS = {
    "DEP": 20,
    "GPU": 20,
    "REG": 20,
    "INF": 16,
    "JOB": 20,
    "FILE": 16,
    "PPT": 15,
    "OCR": 5,
    "KEY": 5,
    "ASR": 18,
    "VIS": 28,
    "ONL": 20,
    "FACE": 14,
    "LOAD": 26,
}
EXPECTED_COVERAGE_KEYS = (
    "registration_full",
    "registration_profiles",
    "registration_recovery",
    "registration_facerec",
    "gpu_running",
    "gpu_stopped",
    "smoke_full",
    "smoke_gpu_trigger",
    "smoke_cpu_instance",
    "negative_declarations",
    "load_declarations",
)
EXPECTED_CASE_FIELDS = (
    "case_id",
    "source_case_id",
    "case_kind",
    "run_id",
    "status",
    "started_at",
    "finished_at",
    "target",
    "command",
    "evidence",
    "reason",
    "mock",
    "release_tag",
    "git_sha",
)
RELEASE_TAG = "v1.0_260817"
GIT_SHA = "a" * 40
PLAN_SHA256 = "b" * 64


def _contract_module() -> ModuleType:
    assert CONTRACT_PATH.is_file(), f"权威报告合同不存在: {CONTRACT_PATH}"
    return importlib.import_module("scripts.milestone_2b_report_contract")


def _plan_document() -> dict[str, Any]:
    assert PLAN_PATH.is_file(), f"权威 report plan 不存在: {PLAN_PATH}"
    loaded = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _valid_case(case_id: str = "DEP-001") -> dict[str, Any]:
    return {
        "case_id": case_id,
        "source_case_id": "DEP-001",
        "case_kind": "negative",
        "run_id": "declaration",
        "status": "未执行及原因",
        "started_at": "2026-08-17T00:00:00Z",
        "finished_at": "2026-08-17T00:00:00Z",
        "target": "root@192.168.29.11",
        "command": "not-run",
        "evidence": ["negative/DEP-001.json"],
        "reason": REASON,
        "mock": False,
        "release_tag": RELEASE_TAG,
        "git_sha": GIT_SHA,
    }


def _valid_envelope() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "release_tag": RELEASE_TAG,
        "git_sha": GIT_SHA,
        "plan_sha256": PLAN_SHA256,
        "coverage": {
            key: {"expected": 1, "observed": 1, "passed": 1}
            for key in EXPECTED_COVERAGE_KEYS
        },
        "cases": [_valid_case()],
    }


def _write_plan(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "report-plan.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def _remove_key(document: dict[str, Any], key: str) -> None:
    del document[key]


def _add_key(document: dict[str, Any], key: str) -> None:
    document[key] = "unexpected"


def _set_registration_profile(document: dict[str, Any]) -> None:
    document["registration"]["profiles"] = ["gpu0", "gpu1", "cpu"]


def _set_facerec_instance(document: dict[str, Any]) -> None:
    document["registration"]["facerec_instances"][2] = "facerec-cpu"


def _set_registration_bool(document: dict[str, Any]) -> None:
    document["registration"]["require_full"] = 1


def _set_smoke_bool(document: dict[str, Any]) -> None:
    document["smoke"]["require_cpu_instances"] = "true"


def _set_reason(document: dict[str, Any]) -> None:
    document["declarations"]["reason"] = "本地测试已通过"


def _set_range_prefix(document: dict[str, Any], value: object) -> None:
    document["declarations"]["negative"][0]["prefix"] = value


def _set_range_bound(
    document: dict[str, Any], field: str, value: object
) -> None:
    document["declarations"]["negative"][0][field] = value


def _duplicate_prefix(document: dict[str, Any]) -> None:
    document["declarations"]["negative"][1]["prefix"] = "DEP"


def _replace_load_prefix(document: dict[str, Any]) -> None:
    document["declarations"]["load"][0]["prefix"] = "DEP"


def _drop_declaration_range(document: dict[str, Any]) -> None:
    document["declarations"]["negative"].pop()


def test_report_plan_expands_exact_243_design_cases() -> None:
    plan_document = _plan_document()
    contract = _contract_module()

    plan = contract.load_report_plan(PLAN_PATH)
    expanded = contract.expand_declaration_cases(plan)

    assert plan == plan_document
    assert len(expanded) == 243
    assert Counter(case["case_kind"] for case in expanded) == {
        "negative": 217,
        "load": 26,
    }
    assert Counter(
        case["source_case_id"].split("-", maxsplit=1)[0] for case in expanded
    ) == EXPECTED_COUNTS
    assert {case["source_case_id"] for case in expanded} == {
        f"{prefix}-{number:03d}"
        for prefix, count in EXPECTED_COUNTS.items()
        for number in range(1, count + 1)
    }
    assert {case["status"] for case in expanded} == {"未执行及原因"}
    assert {case["reason"] for case in expanded} == {REASON}


def test_cases_envelope_rejects_missing_coverage_key() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    del envelope["coverage"]["registration_full"]

    with pytest.raises(ValueError, match=r"coverage.*registration_full"):
        contract.validate_cases_envelope(envelope)


def test_contract_exports_frozen_constants_and_typed_dicts() -> None:
    contract = _contract_module()

    assert contract.SCHEMA_VERSION == 1
    assert contract.STATUSES == ("通过", "失败", "未执行及原因")
    assert contract.COVERAGE_KEYS == EXPECTED_COVERAGE_KEYS
    assert contract.CASE_FIELDS == EXPECTED_CASE_FIELDS
    assert tuple(contract.Coverage.__annotations__) == ("expected", "observed", "passed")
    assert tuple(contract.CaseRecord.__annotations__) == EXPECTED_CASE_FIELDS
    assert get_type_hints(contract.Coverage) == {
        "expected": int,
        "observed": int,
        "passed": int,
    }
    assert get_type_hints(contract.CaseRecord) == {
        "case_id": str,
        "source_case_id": str,
        "case_kind": str,
        "run_id": str,
        "status": str,
        "started_at": str,
        "finished_at": str,
        "target": str,
        "command": str,
        "evidence": list[str],
        "reason": str,
        "mock": bool,
        "release_tag": str,
        "git_sha": str,
    }


def test_report_plan_freezes_registration_smoke_and_declaration_authority() -> None:
    document = _plan_document()

    assert set(document) == {"schema_version", "registration", "smoke", "declarations"}
    assert document["schema_version"] == 1
    assert document["registration"] == {
        "profiles": ["gpu0", "gpu1", "gpu2", "cpu"],
        "require_full": True,
        "require_gpu_recovery_instances": True,
        "facerec_instances": ["facerec-gpu0", "facerec-gpu1", "facerec-gpu2"],
    }
    assert document["smoke"] == {
        "require_full": True,
        "require_gpu_linked_runs": True,
        "require_cpu_instances": True,
    }
    assert set(document["declarations"]) == {"reason", "negative", "load"}
    assert document["declarations"]["reason"] == REASON


@pytest.mark.parametrize(
    ("container", "mutation"),
    (
        ((), lambda value: _remove_key(value, "smoke")),
        ((), lambda value: _add_key(value, "unknown")),
        (("registration",), lambda value: _remove_key(value, "require_full")),
        (("registration",), lambda value: _add_key(value, "unknown")),
        (("smoke",), lambda value: _remove_key(value, "require_full")),
        (("smoke",), lambda value: _add_key(value, "unknown")),
        (("declarations",), lambda value: _remove_key(value, "reason")),
        (("declarations",), lambda value: _add_key(value, "unknown")),
        (
            ("declarations", "negative", 0),
            lambda value: _remove_key(value, "last"),
        ),
        (
            ("declarations", "load", 0),
            lambda value: _add_key(value, "unknown"),
        ),
    ),
)
def test_report_plan_rejects_missing_or_unknown_nested_fields(
    tmp_path: Path,
    container: tuple[str | int, ...],
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    contract = _contract_module()
    document = copy.deepcopy(_plan_document())
    target: Any = document
    for part in container:
        target = target[part]
    mutation(target)

    with pytest.raises(ValueError):
        contract.load_report_plan(_write_plan(tmp_path, document))


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: value.__setitem__("schema_version", 2),
        lambda value: value.__setitem__("schema_version", True),
        _set_registration_profile,
        _set_facerec_instance,
        _set_registration_bool,
        _set_smoke_bool,
        _set_reason,
    ),
)
def test_report_plan_rejects_noncanonical_fixed_values(
    tmp_path: Path, mutation: Callable[[dict[str, Any]], None]
) -> None:
    contract = _contract_module()
    document = copy.deepcopy(_plan_document())
    mutation(document)

    with pytest.raises(ValueError):
        contract.load_report_plan(_write_plan(tmp_path, document))


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: _set_range_prefix(value, "dep"),
        lambda value: _set_range_prefix(value, "DEP-"),
        lambda value: _set_range_prefix(value, ""),
        lambda value: _set_range_prefix(value, 1),
        lambda value: _set_range_bound(value, "first", True),
        lambda value: _set_range_bound(value, "last", False),
        lambda value: _set_range_bound(value, "first", 0),
        lambda value: _set_range_bound(value, "last", -1),
        lambda value: _set_range_bound(value, "first", 2),
        lambda value: _set_range_bound(value, "last", 21),
        _duplicate_prefix,
        _replace_load_prefix,
        _drop_declaration_range,
    ),
)
def test_report_plan_rejects_invalid_or_noncanonical_ranges(
    mutation: Callable[[dict[str, Any]], None]
) -> None:
    contract = _contract_module()
    document = copy.deepcopy(_plan_document())
    mutation(document)

    with pytest.raises(ValueError):
        contract.expand_declaration_cases(document)


def test_report_plan_loader_rejects_non_object_json(tmp_path: Path) -> None:
    contract = _contract_module()

    with pytest.raises(ValueError, match="object"):
        contract.load_report_plan(_write_plan(tmp_path, []))


def test_cases_envelope_accepts_strict_valid_document() -> None:
    contract = _contract_module()

    contract.validate_cases_envelope(_valid_envelope())


@pytest.mark.parametrize("action", ("missing", "unknown"))
def test_cases_envelope_rejects_top_level_field_drift(action: str) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    if action == "missing":
        del envelope["plan_sha256"]
    else:
        envelope["unknown"] = "value"

    with pytest.raises(ValueError):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize("schema_version", (True, 0, 2, "1"))
def test_cases_envelope_rejects_wrong_schema_version(schema_version: object) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["schema_version"] = schema_version

    with pytest.raises(ValueError, match="schema_version"):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("release_tag", ""),
        ("release_tag", "release\nname"),
        ("release_tag", 1),
        ("git_sha", "A" * 40),
        ("git_sha", "a" * 39),
        ("git_sha", "g" * 40),
        ("plan_sha256", "B" * 64),
        ("plan_sha256", "b" * 63),
        ("plan_sha256", "z" * 64),
    ),
)
def test_cases_envelope_rejects_invalid_release_or_digest_metadata(
    field: str, value: object
) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope[field] = value

    with pytest.raises(ValueError, match=field):
        contract.validate_cases_envelope(envelope)


def test_cases_envelope_rejects_unknown_coverage_key() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["coverage"]["unexpected"] = {"expected": 0, "observed": 0, "passed": 0}

    with pytest.raises(ValueError, match=r"coverage.*unexpected"):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize(
    "coverage",
    (
        [],
        {"expected": 1, "observed": 1},
        {"expected": 1, "observed": 1, "passed": 1, "extra": 0},
        {"expected": True, "observed": 1, "passed": 1},
        {"expected": 1, "observed": False, "passed": 0},
        {"expected": 1, "observed": 1, "passed": True},
        {"expected": -1, "observed": 0, "passed": 0},
        {"expected": 1, "observed": -1, "passed": 0},
        {"expected": 1, "observed": 0, "passed": -1},
        {"expected": 1.0, "observed": 1, "passed": 1},
        {"expected": 1, "observed": 2, "passed": 1},
        {"expected": 2, "observed": 1, "passed": 2},
    ),
)
def test_cases_envelope_rejects_invalid_coverage_object(coverage: object) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["coverage"]["registration_full"] = coverage

    with pytest.raises(ValueError, match="registration_full"):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize("cases", ({}, (), "cases", None))
def test_cases_envelope_requires_cases_list(cases: object) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["cases"] = cases

    with pytest.raises(ValueError, match="cases"):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize("action", ("missing", "unknown"))
def test_cases_envelope_rejects_case_field_drift(action: str) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    if action == "missing":
        del envelope["cases"][0]["run_id"]
    else:
        envelope["cases"][0]["unknown"] = "value"

    with pytest.raises(ValueError):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize(
    "field",
    (
        "case_id",
        "source_case_id",
        "case_kind",
        "run_id",
        "status",
        "started_at",
        "finished_at",
        "target",
        "command",
        "reason",
        "release_tag",
        "git_sha",
    ),
)
@pytest.mark.parametrize("value", ("", "contains\x00control", 7))
def test_cases_envelope_rejects_invalid_case_strings(field: str, value: object) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["cases"][0][field] = value

    with pytest.raises(ValueError, match=field):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize("status", ("PASS", "成功", "未执行", 1))
def test_cases_envelope_rejects_unknown_status(status: object) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["cases"][0]["status"] = status

    with pytest.raises(ValueError, match="status"):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize(
    "evidence",
    (
        "negative/DEP-001.json",
        ("negative/DEP-001.json",),
        [1],
        [""],
        ["negative/DEP-001.json\n"],
    ),
)
def test_cases_envelope_rejects_invalid_evidence(evidence: object) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["cases"][0]["evidence"] = evidence

    with pytest.raises(ValueError, match="evidence"):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize("mock", (0, 1, "false", None))
def test_cases_envelope_requires_real_bool_mock(mock: object) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["cases"][0]["mock"] = mock

    with pytest.raises(ValueError, match="mock"):
        contract.validate_cases_envelope(envelope)


def test_cases_envelope_rejects_duplicate_case_id() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["cases"].append(copy.deepcopy(envelope["cases"][0]))

    with pytest.raises(ValueError, match=r"case_id.*DEP-001"):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize("field", ("release_tag", "git_sha"))
def test_cases_envelope_preserves_release_provenance(field: str) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["cases"][0][field] = "b" * 40 if field == "git_sha" else "other-release"

    with pytest.raises(ValueError, match=field):
        contract.validate_cases_envelope(envelope)
