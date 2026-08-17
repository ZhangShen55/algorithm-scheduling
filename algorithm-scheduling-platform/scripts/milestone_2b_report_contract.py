from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
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
COVERAGE_EXPECTED = {
    "registration_full": 1,
    "registration_profiles": 4,
    "registration_recovery": 18,
    "registration_facerec": 1,
    "gpu_running": 18,
    "gpu_stopped": 18,
    "smoke_full": 8,
    "smoke_gpu_trigger": 18,
    "smoke_cpu_instance": 6,
    "negative_declarations": 217,
    "load_declarations": 26,
}
COVERAGE_PARTIAL_OBSERVED_KEYS = frozenset({"smoke_gpu_trigger"})
DECLARATION_COVERAGE_KEYS = frozenset(
    {"negative_declarations", "load_declarations"}
)
FIXED_CASE_KIND_COVERAGE = {
    "registration_full": "registration_full",
    "registration_profile": "registration_profiles",
    "registration_recovery": "registration_recovery",
    "registration_facerec": "registration_facerec",
    "gpu_running": "gpu_running",
    "gpu_stopped": "gpu_stopped",
    "smoke_full": "smoke_full",
}
CASE_KINDS = frozenset(
    {
        *FIXED_CASE_KIND_COVERAGE,
        "smoke_gpu_trigger",
        "smoke_cpu_instance",
        "execution_declaration",
    }
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
DECLARATION_CATEGORY_BY_CASE_ID = {
    f"{prefix}-{number:03d}": category
    for category, ranges in EXPECTED_RANGES.items()
    for prefix, first, last in ranges
    for number in range(first, last + 1)
}
DECLARATION_PLACEHOLDER = "NOT_EXECUTED"
DECLARATION_TARGET = "controlled-target-server"
SMOKE_FULL_OPERATOR_BY_SOURCE_CASE_ID = {
    "INF-ASR-OFFLINE": "asr_offline",
    "INF-ASR-ONLINE": "asr_online",
    "INF-FACEREC": "facerec",
    "INF-OCR": "ocr",
    "INF-PPT-SLICE": "ppt_slice",
    "INF-SCREEN-DET": "screen_det",
    "INF-TEXT-ANALYSIS": "text_analysis",
    "INF-VBAS": "vbas",
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


class _CoverageCase(TypedDict):
    case_id: str
    source_case_id: str
    target: str
    run_id: str
    status: str
    mock: bool


def overall_status(cases: Sequence[Mapping[str, object]]) -> str:
    statuses: list[str] = []
    real_execution_cases = 0
    for index, case in enumerate(cases):
        raw_mock = case.get("mock")
        if type(raw_mock) is not bool:
            raise ValueError(f"cases[{index}].mock must be a boolean")
        case_kind = case.get("case_kind")
        if type(case_kind) is not str or not case_kind:
            raise ValueError(f"cases[{index}].case_kind must be a non-empty string")
        status = case.get("status")
        if type(status) is not str or status not in STATUSES:
            raise ValueError(f"cases[{index}].status is unknown: {status}")
        if raw_mock:
            continue
        statuses.append(status)
        if case_kind != "execution_declaration":
            real_execution_cases += 1
    if real_execution_cases == 0:
        raise ValueError("至少需要一个非 Mock 的真实执行用例")
    if "失败" in statuses:
        return "失败"
    if "未执行及原因" in statuses:
        return "未执行及原因"
    return "通过"


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


def load_report_plan_bytes(content: bytes) -> dict[str, object]:
    if type(content) is not bytes:
        raise ValueError("report plan content must be bytes")
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("report plan bytes must be valid UTF-8") from exc
    try:
        loaded = strict_json_loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("report plan bytes must contain strict JSON") from exc
    return _validate_report_plan(loaded)


def load_report_plan(path: str | Path) -> dict[str, object]:
    try:
        content = Path(path).read_bytes()
    except OSError as exc:
        raise ValueError(f"failed to read report plan: {path}") from exc
    return load_report_plan_bytes(content)


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


def _validate_execution_declaration(
    *,
    strings: dict[str, str],
    evidence: list[str],
    mock: object,
    context: str,
) -> None:
    case_id = strings["case_id"]
    source_case_id = strings["source_case_id"]
    case_kind = strings["case_kind"]
    declaration_like = (
        case_kind == "execution_declaration"
        or case_id in DECLARATION_CATEGORY_BY_CASE_ID
        or source_case_id in DECLARATION_CATEGORY_BY_CASE_ID
    )
    if not declaration_like:
        return
    if case_id != source_case_id:
        raise ValueError(
            f"{context}.case_id and {context}.source_case_id must be identical "
            "for an execution declaration"
        )
    category = DECLARATION_CATEGORY_BY_CASE_ID.get(case_id)
    if category is None:
        raise ValueError(f"{context}.case_id is not a report plan declaration ID")
    if case_kind != "execution_declaration":
        raise ValueError(f"{context}.case_kind must equal execution_declaration")

    expected_strings = {
        "run_id": DECLARATION_PLACEHOLDER,
        "status": "未执行及原因",
        "started_at": DECLARATION_PLACEHOLDER,
        "finished_at": DECLARATION_PLACEHOLDER,
        "target": DECLARATION_TARGET,
        "command": DECLARATION_PLACEHOLDER,
        "reason": DECLARATION_REASON,
    }
    for field, expected in expected_strings.items():
        if strings[field] != expected:
            raise ValueError(f"{context}.{field} must equal {expected}")
    expected_evidence = [f"{category}/cases.json"]
    if evidence != expected_evidence:
        raise ValueError(f"{context}.evidence must equal {expected_evidence}")
    if mock is not False:
        raise ValueError(f"{context}.mock must be false for execution_declaration")


def _status_coverage(cases: list[_CoverageCase], expected: int) -> Coverage:
    return {
        "expected": expected,
        "observed": len(cases),
        "passed": sum(
            case["mock"] is False and case["status"] == "通过" for case in cases
        ),
    }


def _require_case_identity(
    case: _CoverageCase,
    context: str,
    *,
    case_id: str,
    target: str,
    run_id: str,
) -> None:
    if case["case_id"] != case_id:
        raise ValueError(f"{context}.case_id must equal {case_id}")
    if case["target"] != target:
        raise ValueError(f"{context}.target must equal {target}")
    if case["run_id"] != run_id:
        raise ValueError(f"{context}.run_id must equal {run_id}")


def _recompute_real_case_coverage(
    cases_by_kind: dict[str, list[_CoverageCase]],
) -> dict[str, Coverage]:
    recomputed: dict[str, Coverage] = {}
    for case_kind, coverage_key in FIXED_CASE_KIND_COVERAGE.items():
        cases = cases_by_kind[case_kind]
        expected = COVERAGE_EXPECTED[coverage_key]
        if len(cases) != expected:
            raise ValueError(
                f"{case_kind} case count must equal {expected}, got {len(cases)}"
            )
        recomputed[coverage_key] = _status_coverage(cases, expected)

    full_registration = cases_by_kind["registration_full"][0]
    _require_case_identity(
        full_registration,
        "registration_full",
        case_id="REG-FULL",
        target="operator-registry",
        run_id="full",
    )

    expected_profiles = frozenset({"gpu0", "gpu1", "gpu2", "cpu"})
    profile_cases = cases_by_kind["registration_profile"]
    profile_targets = {case["target"] for case in profile_cases}
    if profile_targets != expected_profiles:
        raise ValueError(
            "registration_profile targets must equal "
            f"{sorted(expected_profiles)}, got {sorted(profile_targets)}"
        )
    for case in profile_cases:
        profile = case["target"]
        _require_case_identity(
            case,
            f"registration_profile[{profile}]",
            case_id=f"REG-PROFILE-{profile}",
            target=profile,
            run_id=profile,
        )

    facerec_registration = cases_by_kind["registration_facerec"][0]
    _require_case_identity(
        facerec_registration,
        "registration_facerec",
        case_id="REG-FACEREC-THREE",
        target="facerec-three",
        run_id="facerec-three",
    )

    running_cases = cases_by_kind["gpu_running"]
    stopped_cases = cases_by_kind["gpu_stopped"]
    running_targets = {case["target"] for case in running_cases}
    stopped_targets = {case["target"] for case in stopped_cases}
    expected_gpu_targets = COVERAGE_EXPECTED["gpu_running"]
    if len(running_targets) != expected_gpu_targets:
        raise ValueError(
            "gpu_running targets must contain exactly "
            f"{expected_gpu_targets} distinct values"
        )
    if len(stopped_targets) != COVERAGE_EXPECTED["gpu_stopped"]:
        raise ValueError(
            "gpu_stopped targets must contain exactly "
            f"{COVERAGE_EXPECTED['gpu_stopped']} distinct values"
        )
    if running_targets != stopped_targets:
        raise ValueError("gpu_running and gpu_stopped target sets must be identical")

    running_by_target = {case["target"]: case for case in running_cases}
    stopped_by_target = {case["target"]: case for case in stopped_cases}
    stopped_passed = 0
    for target, running in running_by_target.items():
        stopped = stopped_by_target[target]
        for case_kind, case, expected_case_id in (
            ("gpu_running", running, f"GPU-RUN-{target}"),
            ("gpu_stopped", stopped, f"GPU-STOP-{target}"),
        ):
            for field in ("case_id", "source_case_id"):
                if case[field] != expected_case_id:
                    raise ValueError(
                        f"{case_kind}[{target}].{field} must equal {expected_case_id}"
                    )
        if stopped["run_id"] != running["run_id"]:
            raise ValueError(
                "gpu_stopped.run_id must equal gpu_running.run_id for target "
                f"{target}"
            )
        if stopped["status"] == "通过" and running["status"] != "通过":
            raise ValueError(
                "gpu_stopped status 通过 requires gpu_running status 通过 for target "
                f"{target}"
            )
        if (
            running["mock"] is False
            and stopped["mock"] is False
            and running["status"] == "通过"
            and stopped["status"] == "通过"
        ):
            stopped_passed += 1
    recomputed["gpu_stopped"] = {
        "expected": COVERAGE_EXPECTED["gpu_stopped"],
        "observed": len(stopped_cases),
        "passed": stopped_passed,
    }

    recovery_cases = cases_by_kind["registration_recovery"]
    recovery_targets = {case["target"] for case in recovery_cases}
    if len(recovery_targets) != len(recovery_cases):
        raise ValueError("registration_recovery targets must be unique")
    if recovery_targets != running_targets:
        raise ValueError(
            "registration_recovery targets must equal gpu_running targets: "
            f"missing={sorted(running_targets - recovery_targets)}, "
            f"unknown={sorted(recovery_targets - running_targets)}"
        )
    for case in recovery_cases:
        target = case["target"]
        _require_case_identity(
            case,
            f"registration_recovery[{target}]",
            case_id=f"REG-RECOVERY-{target}",
            target=target,
            run_id=target,
        )

    gpu_smoke_by_key: dict[tuple[str, str], _CoverageCase] = {}
    for case in cases_by_kind["smoke_gpu_trigger"]:
        target = case["target"]
        if target not in running_targets:
            raise ValueError(
                "smoke_gpu_trigger target is not present in gpu_running targets: "
                f"{target}"
            )
        key = (target, case["run_id"])
        if key in gpu_smoke_by_key:
            raise ValueError(
                "smoke_gpu_trigger contains a duplicate target/run_id key: "
                f"{target}/{case['run_id']}"
            )
        gpu_smoke_by_key[key] = case

    gpu_observed = 0
    gpu_passed = 0
    for running in running_cases:
        key = (running["target"], running["run_id"])
        linked = gpu_smoke_by_key.get(key)
        if linked is None:
            if running["status"] == "通过":
                raise ValueError(
                    "passing gpu_running case has no matching smoke_gpu_trigger "
                    f"target/run_id/status link: {running['target']}/{running['run_id']}"
                )
            continue
        if linked["status"] != running["status"]:
            raise ValueError(
                "gpu_running and linked smoke_gpu_trigger statuses differ for "
                f"{running['target']}/{running['run_id']}"
            )
        gpu_observed += 1
        if (
            running["mock"] is False
            and linked["mock"] is False
            and running["status"] == "通过"
            and linked["status"] == "通过"
        ):
            gpu_passed += 1
    recomputed["smoke_gpu_trigger"] = {
        "expected": COVERAGE_EXPECTED["smoke_gpu_trigger"],
        "observed": gpu_observed,
        "passed": gpu_passed,
    }

    cpu_cases = cases_by_kind["smoke_cpu_instance"]
    cpu_keys: set[tuple[str, str]] = set()
    cpu_targets: set[str] = set()
    passing_cpu_targets: set[str] = set()
    for case in cpu_cases:
        key = (case["target"], case["run_id"])
        if key in cpu_keys:
            raise ValueError(
                "smoke_cpu_instance contains a duplicate target/run_id key: "
                f"{case['target']}/{case['run_id']}"
            )
        cpu_keys.add(key)
        cpu_targets.add(case["target"])
        if case["mock"] is False and case["status"] == "通过":
            passing_cpu_targets.add(case["target"])
    recomputed["smoke_cpu_instance"] = {
        "expected": COVERAGE_EXPECTED["smoke_cpu_instance"],
        "observed": len(cpu_targets),
        "passed": len(passing_cpu_targets),
    }
    return recomputed


def _require_recomputed_coverage(
    reported: dict[str, object], recomputed: dict[str, Coverage]
) -> None:
    for coverage_key in COVERAGE_KEYS:
        item = cast(dict[str, object], reported[coverage_key])
        expected_item = recomputed[coverage_key]
        expected_values = (
            ("expected", expected_item["expected"]),
            ("observed", expected_item["observed"]),
            ("passed", expected_item["passed"]),
        )
        for field, expected_value in expected_values:
            if item[field] != expected_value:
                raise ValueError(
                    f"coverage.{coverage_key}.{field} must equal recomputed "
                    f"value {expected_value}"
                )


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
        authority_expected = COVERAGE_EXPECTED[key]
        if expected != authority_expected:
            raise ValueError(
                f"coverage.{key}.expected must equal {authority_expected}"
            )
        observed = _require_nonnegative_int(
            item["observed"], f"coverage.{key}.observed"
        )
        passed = _require_nonnegative_int(item["passed"], f"coverage.{key}.passed")
        if passed > observed or observed > expected:
            raise ValueError(
                f"coverage.{key} must satisfy passed <= observed <= expected"
            )
        if key not in COVERAGE_PARTIAL_OBSERVED_KEYS and observed != expected:
            raise ValueError(f"coverage.{key}.observed must equal {expected}")
        if key in DECLARATION_COVERAGE_KEYS and passed != 0:
            raise ValueError(f"coverage.{key}.passed must equal 0")

    seen_case_ids: set[str] = set()
    declaration_case_ids: set[str] = set()
    cases_by_kind: dict[str, list[_CoverageCase]] = {
        case_kind: [] for case_kind in CASE_KINDS
    }
    for index, raw_case in enumerate(_require_list(envelope["cases"], "cases")):
        context = f"cases[{index}]"
        case = _require_exact_object(raw_case, CASE_FIELDS, context)
        canonical_smoke_operator: str | None = None
        strings = {
            field: _require_string(case[field], f"{context}.{field}")
            for field in _CASE_STRING_FIELDS
            if field != "run_id"
        }
        case_kind = strings["case_kind"]
        if case_kind not in CASE_KINDS:
            raise ValueError(f"{context}.case_kind is unknown: {case_kind}")
        raw_run_id = case["run_id"]
        if case_kind == "smoke_full":
            if type(raw_run_id) is not str or raw_run_id != "":
                raise ValueError(
                    f"{context}.run_id must be empty for canonical full Smoke cases"
                )
            source_case_id = strings["source_case_id"]
            canonical_smoke_operator = SMOKE_FULL_OPERATOR_BY_SOURCE_CASE_ID.get(
                source_case_id
            )
            if canonical_smoke_operator is None:
                raise ValueError(
                    f"{context}.source_case_id is not a canonical full Smoke source"
                )
            expected_case_id = f"SMOKE-FULL-{source_case_id}"
            if strings["case_id"] != expected_case_id:
                raise ValueError(
                    f"{context}.case_id must equal {expected_case_id} for full Smoke"
                )
            strings["run_id"] = ""
        else:
            strings["run_id"] = _require_string(raw_run_id, f"{context}.run_id")
        case_id = strings["case_id"]
        if case_id in seen_case_ids:
            raise ValueError(f"{context}.case_id is duplicate: {case_id}")
        seen_case_ids.add(case_id)
        if strings["status"] not in STATUSES:
            raise ValueError(f"{context}.status is unknown: {strings['status']}")
        if case_kind == "execution_declaration" and strings["status"] != "未执行及原因":
            raise ValueError(
                f"{context} execution_declaration status must be 未执行及原因"
            )

        evidence = [
            _require_string(
                evidence_path, f"{context}.evidence[{evidence_index}]"
            )
            for evidence_index, evidence_path in enumerate(
                _require_list(case["evidence"], f"{context}.evidence")
            )
        ]
        raw_mock = case["mock"]
        if type(raw_mock) is not bool:
            raise ValueError(f"{context}.mock must be a boolean")
        mock = raw_mock
        _validate_execution_declaration(
            strings=strings,
            evidence=evidence,
            mock=mock,
            context=context,
        )
        if case_kind == "execution_declaration":
            declaration_case_ids.add(case_id)
        if canonical_smoke_operator is not None:
            if mock is not False:
                raise ValueError(f"{context}.mock must be false for full Smoke")
            if strings["target"] != canonical_smoke_operator:
                raise ValueError(
                    f"{context}.target does not match the canonical full Smoke operator"
                )
            expected_evidence = [f"smoke/{canonical_smoke_operator}.json"]
            if evidence != expected_evidence:
                raise ValueError(
                    f"{context}.evidence must equal {expected_evidence} for full Smoke"
                )
        if strings["release_tag"] != release_tag:
            raise ValueError(f"{context}.release_tag does not match the envelope")
        if strings["git_sha"] != git_sha:
            raise ValueError(f"{context}.git_sha does not match the envelope")
        cases_by_kind[case_kind].append(
            {
                "case_id": strings["case_id"],
                "source_case_id": strings["source_case_id"],
                "target": strings["target"],
                "run_id": strings["run_id"],
                "status": strings["status"],
                "mock": mock,
            }
        )

    expected_declaration_ids = set(DECLARATION_CATEGORY_BY_CASE_ID)
    if declaration_case_ids != expected_declaration_ids:
        raise ValueError(
            "execution declaration authority mismatch: "
            f"missing={sorted(expected_declaration_ids - declaration_case_ids)}, "
            f"unknown={sorted(declaration_case_ids - expected_declaration_ids)}"
        )

    recomputed = _recompute_real_case_coverage(cases_by_kind)
    recomputed["negative_declarations"] = {
        "expected": COVERAGE_EXPECTED["negative_declarations"],
        "observed": sum(
            case_id.startswith(tuple(prefix for prefix, _, _ in EXPECTED_RANGES["negative"]))
            for case_id in declaration_case_ids
        ),
        "passed": 0,
    }
    recomputed["load_declarations"] = {
        "expected": COVERAGE_EXPECTED["load_declarations"],
        "observed": sum(case_id.startswith("LOAD-") for case_id in declaration_case_ids),
        "passed": 0,
    }
    _require_recomputed_coverage(coverage, recomputed)
