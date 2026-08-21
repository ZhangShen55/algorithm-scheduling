from __future__ import annotations

import asyncio
import os
import signal
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path

from scripts.milestone_2b_case_catalog import CaseDefinition

from .base import CaseContext, CaseOutcome
from .evidence import publish_case_evidence

PLATFORM_ROOT = Path(__file__).resolve().parents[2]
CASE_TESTS: Mapping[str, tuple[str, ...]] = {
    "RET-001": (
        "tests/test_pipeline_initializer.py::test_ppt_command_initializes_only_ppt_pipeline_nodes",
    ),
    "RET-002": (
        "tests/test_pipeline_initializer.py::test_asr_command_does_not_initialize_visual_or_ppt_nodes",
    ),
    "RET-003": (
        "tests/test_pipeline_initializer.py::test_replayed_course_command_keeps_the_current_pipeline_definition",
    ),
    "RET-004": (
        "tests/test_operator_registry_api.py::test_retired_text_analysis_operator_code_is_rejected_and_absent_from_openapi",
    ),
    "RET-005": (
        "tests/test_milestone_2b_compose.py::test_compose_declares_exact_three_gpu_and_three_cpu_operator_matrix",
    ),
    "RET-006": (
        "tests/test_milestone_2b_image_build.py::test_operator_image_manifest_matches_the_frozen_matrix",
        "tests/test_milestone_2b_report_aggregation.py::test_smoke_manifest_loader_matches_the_real_compose_operator_set",
    ),
    "RET-007": (
        "tests/integration/test_redis_operator_registry.py::test_retired_operator_left_in_redis_is_not_listed_or_routable",
    ),
    "RET-008": (
        "tests/integration/test_retired_text_analysis_boundary.py::test_terminal_historical_retired_nodes_remain_queryable_and_do_not_block",
    ),
    "RET-009": (
        "tests/integration/test_retired_text_analysis_boundary.py::test_active_retired_node_preflight_fails_closed_for_status_10_through_50",
    ),
    "RET-010": (
        "tests/test_retired_platform_boundary.py::test_legacy_eight_operator_report_schema_is_rejected",
    ),
}


def _assert_case_contract(case: CaseDefinition, case_id: str) -> None:
    if (
        case.case_id != case_id
        or case.category != "negative"
        or case.phase != "final"
        or case.runner != f"retirement.{case_id.lower().replace('-', '_')}"
        or case.safety != "read_only"
    ):
        raise ValueError(f"{case_id} catalog contract changed")


def _junit_counts(path: Path, case_id: str) -> dict[str, int]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        raise ValueError(f"{case_id} JUnit evidence is unreadable") from error
    suites = (root,) if root.tag == "testsuite" else tuple(root.findall("testsuite"))
    if not suites:
        raise ValueError(f"{case_id} did not execute any regression test")
    counts = {name: 0 for name in ("tests", "failures", "errors", "skipped")}
    for suite in suites:
        for name in counts:
            raw = suite.attrib.get(name)
            if raw is None:
                raise ValueError(f"{case_id} JUnit is missing {name}")
            counts[name] += int(raw)
    if counts["tests"] <= 0 or any(
        counts[name] != 0 for name in ("failures", "errors", "skipped")
    ):
        raise ValueError(f"{case_id} regression did not pass without skips: {counts}")
    return counts


async def _run_process(command: Sequence[str], timeout_seconds: int) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=PLATFORM_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output, _ = await asyncio.wait_for(
            process.communicate(), timeout=float(timeout_seconds)
        )
    except TimeoutError:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
        raise ValueError("retirement regression timed out") from None
    return process.returncode or 0, output.decode("utf-8", errors="replace")[-16000:]


async def _run(
    context: CaseContext, case: CaseDefinition, case_id: str
) -> CaseOutcome:
    _assert_case_contract(case, case_id)
    tests = CASE_TESTS[case_id]
    with tempfile.TemporaryDirectory(prefix=f"m2b-{case_id.lower()}-") as directory:
        junit = Path(directory) / "junit.xml"
        command = (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            *tests,
            f"--junitxml={junit}",
        )
        returncode, output_tail = await _run_process(command, case.timeout_seconds)
        if returncode != 0:
            raise ValueError(f"{case_id} regression failed: {output_tail}")
        counts = _junit_counts(junit, case_id)
    evidence = publish_case_evidence(
        context=context,
        case=case,
        name="retirement-regression.json",
        payload={
            "tests": list(tests),
            "junit": counts,
            "command": [sys.executable, "-m", "pytest", "-q", *tests],
            "output_tail": output_tail,
        },
    )
    return CaseOutcome(
        status="通过",
        reason=f"退役边界反例符合预期：{case.expected}",
        evidence=(evidence,),
    )


async def ret_001(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "RET-001")


async def ret_002(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "RET-002")


async def ret_003(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "RET-003")


async def ret_004(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "RET-004")


async def ret_005(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "RET-005")


async def ret_006(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "RET-006")


async def ret_007(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "RET-007")


async def ret_008(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "RET-008")


async def ret_009(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "RET-009")


async def ret_010(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "RET-010")
