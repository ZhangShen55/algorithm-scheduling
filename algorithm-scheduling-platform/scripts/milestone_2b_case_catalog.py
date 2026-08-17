from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import yaml  # type: ignore[import-untyped]

CaseCategory = Literal["negative", "load"]
CasePhase = Literal["deployment", "offline", "vision", "online", "final"]
CaseSafety = Literal["read_only", "isolated_mutation", "canonical_runtime"]

TOP_LEVEL_FIELDS = {"schema_version", "cases"}
CASE_FIELDS = {
    "case_id",
    "category",
    "phase",
    "title",
    "expected",
    "runner",
    "timeout_seconds",
    "safety",
}
CASE_CATEGORIES = {"negative", "load"}
CASE_PHASES = {"deployment", "offline", "vision", "online", "final"}
CASE_SAFETY_LEVELS = {"read_only", "isolated_mutation", "canonical_runtime"}
EXPECTED_RANGES = {
    "DEP": (1, 20),
    "GPU": (1, 20),
    "REG": (1, 20),
    "INF": (1, 16),
    "JOB": (1, 20),
    "FILE": (1, 16),
    "PPT": (1, 15),
    "OCR": (1, 5),
    "KEY": (1, 5),
    "ASR": (1, 18),
    "VIS": (1, 28),
    "ONL": (1, 20),
    "FACE": (1, 14),
    "LOAD": (1, 26),
}
EXPECTED_CASE_IDS = {
    f"{prefix}-{number:03d}"
    for prefix, (first, last) in EXPECTED_RANGES.items()
    for number in range(first, last + 1)
}
RUNNER_PATTERN = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*")


@dataclass(frozen=True, slots=True)
class CaseDefinition:
    case_id: str
    category: CaseCategory
    phase: CasePhase
    title: str
    expected: str
    runner: str
    timeout_seconds: int
    safety: CaseSafety


@dataclass(frozen=True, slots=True)
class CaseCatalog:
    schema_version: int
    cases: tuple[CaseDefinition, ...]

    def count_by_category(self) -> dict[str, int]:
        return dict(Counter(case.category for case in self.cases))

    def count_by_prefix(self) -> dict[str, int]:
        return dict(Counter(case.case_id.split("-", 1)[0] for case in self.cases))


def load_case_catalog(path: str | Path) -> CaseCatalog:
    catalog_path = Path(path)
    if catalog_path.is_symlink():
        raise ValueError("2B 用例目录不能是软链接")
    if not catalog_path.is_file():
        raise ValueError("2B 用例目录必须是普通文件")
    try:
        content = catalog_path.read_bytes().decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("2B 用例目录必须是有效 UTF-8") from exc
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError("2B 用例目录不是有效 YAML") from exc
    if not isinstance(document, dict):
        raise ValueError("2B 用例目录顶层必须是对象")
    unknown_top_level = set(document) - TOP_LEVEL_FIELDS
    if unknown_top_level:
        raise ValueError(f"2B 用例目录包含未知字段: {sorted(unknown_top_level)}")
    if set(document) != TOP_LEVEL_FIELDS:
        raise ValueError("2B 用例目录缺少 schema_version 或 cases")
    if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
        raise ValueError("2B 用例目录 schema_version 必须为 1")
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("2B 用例目录 cases 必须是数组")
    cases: list[CaseDefinition] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        context = f"cases[{index}]"
        if not isinstance(raw_case, dict):
            raise ValueError(f"{context} 必须是对象")
        case = cast(dict[str, object], raw_case)
        unknown_fields = set(case) - CASE_FIELDS
        if unknown_fields:
            raise ValueError(f"{context} 包含未知字段: {sorted(unknown_fields)}")
        if set(case) != CASE_FIELDS:
            raise ValueError(f"{context} 字段不完整")
        case_id = _required_string(case["case_id"], f"{context}.case_id")
        if case_id in seen_ids:
            raise ValueError(f"2B 用例目录包含重复 case_id: {case_id}")
        seen_ids.add(case_id)
        category = _required_choice(
            case["category"], CASE_CATEGORIES, f"{context}.category"
        )
        expected_category = "load" if case_id.startswith("LOAD-") else "negative"
        if category != expected_category:
            raise ValueError(f"{context}.category 与 case_id 不一致")
        phase = _required_choice(case["phase"], CASE_PHASES, f"{context}.phase")
        safety = _required_choice(
            case["safety"], CASE_SAFETY_LEVELS, f"{context}.safety"
        )
        timeout_seconds = case["timeout_seconds"]
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ValueError(f"{context}.timeout_seconds 必须是正整数")
        runner = _required_string(case["runner"], f"{context}.runner")
        if RUNNER_PATTERN.fullmatch(runner) is None:
            raise ValueError(f"{context}.runner 格式不合法")
        cases.append(
            CaseDefinition(
                case_id=case_id,
                category=cast(CaseCategory, category),
                phase=cast(CasePhase, phase),
                title=_required_string(case["title"], f"{context}.title"),
                expected=_required_string(case["expected"], f"{context}.expected"),
                runner=runner,
                timeout_seconds=timeout_seconds,
                safety=cast(CaseSafety, safety),
            )
        )
    if len(cases) != 243:
        raise ValueError("2B 用例目录必须精确包含 243 条")
    actual_ids = {case.case_id for case in cases}
    if actual_ids != EXPECTED_CASE_IDS:
        raise ValueError(
            "2B 用例目录 ID 与权威矩阵不一致: "
            f"missing={sorted(EXPECTED_CASE_IDS - actual_ids)}, "
            f"unknown={sorted(actual_ids - EXPECTED_CASE_IDS)}"
        )
    return CaseCatalog(schema_version=1, cases=tuple(cases))


def _required_string(value: object, context: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{context} 必须是非空字符串")
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value):
        raise ValueError(f"{context} 不能包含控制字符")
    return value


def _required_choice(value: object, choices: set[str], context: str) -> str:
    string = _required_string(value, context)
    if string not in choices:
        raise ValueError(f"{context} 取值不合法: {string}")
    return string
