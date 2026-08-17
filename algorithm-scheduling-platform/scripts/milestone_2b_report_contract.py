from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import TypedDict, cast

SCHEMA_VERSION = 1
STATUSES = ("通过", "失败", "未执行及原因")
COVERAGE_KEYS = (
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
CASE_FIELDS = (
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

DECLARATION_REASON = (
    "当前仓库没有该用例的受控目标服务器 runner 与运行证据 schema；"
    "本 release 未执行，现有本地单元测试或正向健康检查不等价于现场执行。"
)
REGISTRATION_FIELDS = (
    "profiles",
    "require_full",
    "require_gpu_recovery_instances",
    "facerec_instances",
)
SMOKE_FIELDS = (
    "require_full",
    "require_gpu_linked_runs",
    "require_cpu_instances",
)
DECLARATION_FIELDS = ("reason", "negative", "load")
RANGE_FIELDS = ("prefix", "first", "last")
COVERAGE_FIELDS = ("expected", "observed", "passed")
EXPECTED_PROFILES = ("gpu0", "gpu1", "gpu2", "cpu")
EXPECTED_FACEREC_INSTANCES = (
    "facerec-gpu0",
    "facerec-gpu1",
    "facerec-gpu2",
)
EXPECTED_RANGES = {
    "negative": (
        ("DEP", 1, 20),
        ("GPU", 1, 20),
        ("REG", 1, 20),
        ("INF", 1, 16),
        ("JOB", 1, 20),
        ("FILE", 1, 16),
        ("PPT", 1, 15),
        ("OCR", 1, 5),
        ("KEY", 1, 5),
        ("ASR", 1, 18),
        ("VIS", 1, 28),
        ("ONL", 1, 20),
        ("FACE", 1, 14),
    ),
    "load": (("LOAD", 1, 26),),
}

_PREFIX_PATTERN = re.compile(r"[A-Z]+")
_GIT_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
_PLAN_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_CASE_STRING_FIELDS = tuple(
    field for field in CASE_FIELDS if field not in {"evidence", "mock"}
)


class Coverage(TypedDict):
    expected: int
    observed: int
    passed: int


class CaseRecord(TypedDict):
    case_id: str
    source_case_id: str
    case_kind: str
    run_id: str
    status: str
    started_at: str
    finished_at: str
    target: str
    command: str
    evidence: list[str]
    reason: str
    mock: bool
    release_tag: str
    git_sha: str


class DeclarationCase(TypedDict):
    case_id: str
    source_case_id: str
    case_kind: str
    status: str
    reason: str


def _reject_duplicate_json_fields(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate JSON field: {key}")
        document[key] = value
    return document


def _reject_nonstandard_json_constant(constant: str) -> object:
    raise ValueError(f"non-standard JSON constant is not allowed: {constant}")


def _parse_finite_json_float(number: str) -> float:
    parsed = float(number)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number is not allowed: {number}")
    return parsed


def strict_json_loads(text: str) -> object:
    if type(text) is not str:
        raise ValueError("JSON text must be a string")
    try:
        loaded: object = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_fields,
            parse_constant=_reject_nonstandard_json_constant,
            parse_float=_parse_finite_json_float,
        )
    except RecursionError as exc:
        raise ValueError("JSON nesting is too deep") from exc
    return loaded


def _require_exact_object(
    value: object, expected_fields: tuple[str, ...], context: str
) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError(f"{context} must be an object")
    raw = cast(dict[object, object], value)
    if any(type(key) is not str for key in raw):
        raise ValueError(f"{context} contains a non-string field name")
    document = cast(dict[str, object], raw)
    actual = set(document)
    expected = set(expected_fields)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        raise ValueError(
            f"{context} fields invalid: missing={missing}, unknown={unknown}"
        )
    return document


def _require_list(value: object, context: str) -> list[object]:
    if type(value) is not list:
        raise ValueError(f"{context} must be a list")
    return cast(list[object], value)


def _require_string(value: object, context: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{context} must be a string")
    string = value
    if not string.strip():
        raise ValueError(f"{context} must not be empty")
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in string):
        raise ValueError(f"{context} contains a control character")
    return string


def _require_fixed_string_list(
    value: object, expected: tuple[str, ...], context: str
) -> None:
    items = _require_list(value, context)
    actual = tuple(
        _require_string(item, f"{context}[{index}]")
        for index, item in enumerate(items)
    )
    if actual != expected:
        raise ValueError(f"{context} must equal {list(expected)}")


def _require_true(value: object, context: str) -> None:
    if type(value) is not bool or value is not True:
        raise ValueError(f"{context} must be true")


def _require_schema_version(value: object, context: str) -> None:
    if type(value) is not int or value != SCHEMA_VERSION:
        raise ValueError(f"{context} must equal {SCHEMA_VERSION}")


def _require_positive_int(value: object, context: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{context} must be a positive integer")
    return value


def _require_nonnegative_int(value: object, context: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{context} must be a non-negative integer")
    return value


def _validate_ranges(
    value: object, kind: str, seen_prefixes: set[str]
) -> list[tuple[str, int, int]]:
    ranges: list[tuple[str, int, int]] = []
    for index, raw_range in enumerate(_require_list(value, f"declarations.{kind}")):
        context = f"declarations.{kind}[{index}]"
        range_document = _require_exact_object(raw_range, RANGE_FIELDS, context)
        prefix = _require_string(range_document["prefix"], f"{context}.prefix")
        if _PREFIX_PATTERN.fullmatch(prefix) is None:
            raise ValueError(f"{context}.prefix has an invalid format")
        if prefix in seen_prefixes:
            raise ValueError(f"duplicate declaration prefix: {prefix}")
        seen_prefixes.add(prefix)
        first = _require_positive_int(range_document["first"], f"{context}.first")
        last = _require_positive_int(range_document["last"], f"{context}.last")
        if first > last:
            raise ValueError(f"{context} first must not exceed last")
        ranges.append((prefix, first, last))
    return ranges


def _expected_case_ids() -> set[str]:
    return {
        f"{prefix}-{number:03d}"
        for ranges in EXPECTED_RANGES.values()
        for prefix, first, last in ranges
        for number in range(first, last + 1)
    }


def _validate_report_plan(value: object) -> dict[str, object]:
    plan = _require_exact_object(
        value,
        ("schema_version", "registration", "smoke", "declarations"),
        "report plan",
    )
    _require_schema_version(plan["schema_version"], "schema_version")

    registration = _require_exact_object(
        plan["registration"], REGISTRATION_FIELDS, "registration"
    )
    _require_fixed_string_list(
        registration["profiles"], EXPECTED_PROFILES, "registration.profiles"
    )
    _require_true(registration["require_full"], "registration.require_full")
    _require_true(
        registration["require_gpu_recovery_instances"],
        "registration.require_gpu_recovery_instances",
    )
    _require_fixed_string_list(
        registration["facerec_instances"],
        EXPECTED_FACEREC_INSTANCES,
        "registration.facerec_instances",
    )

    smoke = _require_exact_object(plan["smoke"], SMOKE_FIELDS, "smoke")
    for field in SMOKE_FIELDS:
        _require_true(smoke[field], f"smoke.{field}")

    declarations = _require_exact_object(
        plan["declarations"], DECLARATION_FIELDS, "declarations"
    )
    reason = _require_string(declarations["reason"], "declarations.reason")
    if reason != DECLARATION_REASON:
        raise ValueError("declarations.reason does not match the release authority")

    seen_prefixes: set[str] = set()
    actual_ranges = {
        kind: tuple(_validate_ranges(declarations[kind], kind, seen_prefixes))
        for kind in ("negative", "load")
    }
    if actual_ranges != EXPECTED_RANGES:
        raise ValueError(
            f"declaration ranges do not match the authority: actual={actual_ranges}"
        )
    actual_ids = {
        f"{prefix}-{number:03d}"
        for ranges in actual_ranges.values()
        for prefix, first, last in ranges
        for number in range(first, last + 1)
    }
    expected_ids = _expected_case_ids()
    if actual_ids != expected_ids or len(actual_ids) != 243:
        raise ValueError("expanded declaration case set does not match the 243-case authority")
    return plan


def load_report_plan(path: str | Path) -> dict[str, object]:
    try:
        loaded = strict_json_loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"failed to read report plan: {path}") from exc
    return _validate_report_plan(loaded)


def expand_declaration_cases(plan: object) -> list[DeclarationCase]:
    validated = _validate_report_plan(plan)
    declarations = _require_exact_object(
        validated["declarations"], DECLARATION_FIELDS, "declarations"
    )
    reason = _require_string(declarations["reason"], "declarations.reason")
    expanded: list[DeclarationCase] = []
    for kind in ("negative", "load"):
        for prefix, first, last in EXPECTED_RANGES[kind]:
            for number in range(first, last + 1):
                source_case_id = f"{prefix}-{number:03d}"
                expanded.append(
                    {
                        "case_id": source_case_id,
                        "source_case_id": source_case_id,
                        "case_kind": kind,
                        "status": "未执行及原因",
                        "reason": reason,
                    }
                )
    return expanded


def validate_cases_envelope(document: object) -> None:
    envelope = _require_exact_object(
        document,
        ("schema_version", "release_tag", "git_sha", "plan_sha256", "coverage", "cases"),
        "cases envelope",
    )
    _require_schema_version(envelope["schema_version"], "schema_version")
    release_tag = _require_string(envelope["release_tag"], "release_tag")
    git_sha = _require_string(envelope["git_sha"], "git_sha")
    if _GIT_SHA_PATTERN.fullmatch(git_sha) is None:
        raise ValueError("git_sha must be 40 lowercase hexadecimal characters")
    plan_sha256 = _require_string(envelope["plan_sha256"], "plan_sha256")
    if _PLAN_SHA256_PATTERN.fullmatch(plan_sha256) is None:
        raise ValueError("plan_sha256 must be 64 lowercase hexadecimal characters")

    coverage = _require_exact_object(envelope["coverage"], COVERAGE_KEYS, "coverage")
    for key in COVERAGE_KEYS:
        item = _require_exact_object(coverage[key], COVERAGE_FIELDS, f"coverage.{key}")
        expected = _require_nonnegative_int(
            item["expected"], f"coverage.{key}.expected"
        )
        observed = _require_nonnegative_int(
            item["observed"], f"coverage.{key}.observed"
        )
        passed = _require_nonnegative_int(item["passed"], f"coverage.{key}.passed")
        if passed > observed or observed > expected:
            raise ValueError(
                f"coverage.{key} must satisfy passed <= observed <= expected"
            )

    seen_case_ids: set[str] = set()
    for index, raw_case in enumerate(_require_list(envelope["cases"], "cases")):
        context = f"cases[{index}]"
        case = _require_exact_object(raw_case, CASE_FIELDS, context)
        strings = {
            field: _require_string(case[field], f"{context}.{field}")
            for field in _CASE_STRING_FIELDS
            if field != "run_id"
        }
        raw_run_id = case["run_id"]
        if raw_run_id == "" and strings["case_kind"] == "smoke_full":
            strings["run_id"] = ""
        else:
            strings["run_id"] = _require_string(raw_run_id, f"{context}.run_id")
        case_id = strings["case_id"]
        if case_id in seen_case_ids:
            raise ValueError(f"{context}.case_id is duplicate: {case_id}")
        seen_case_ids.add(case_id)
        if strings["status"] not in STATUSES:
            raise ValueError(f"{context}.status is unknown: {strings['status']}")

        evidence = _require_list(case["evidence"], f"{context}.evidence")
        for evidence_index, evidence_path in enumerate(evidence):
            _require_string(
                evidence_path, f"{context}.evidence[{evidence_index}]"
            )
        if type(case["mock"]) is not bool:
            raise ValueError(f"{context}.mock must be a boolean")
        if strings["release_tag"] != release_tag:
            raise ValueError(f"{context}.release_tag does not match the envelope")
        if strings["git_sha"] != git_sha:
            raise ValueError(f"{context}.git_sha does not match the envelope")
