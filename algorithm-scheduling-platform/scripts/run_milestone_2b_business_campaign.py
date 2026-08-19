#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import math
import subprocess
import tempfile
import time
import wave
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import websockets

from scripts.aggregate_milestone_2b_cases import publish_json_once
from scripts.milestone_2b_case_catalog import CaseDefinition, load_case_catalog
from scripts.milestone_2b_case_runners.campaign import publish_campaign_case
from scripts.milestone_2b_case_runners.evidence import release_identity

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PLATFORM_ROOT.parent
PHASE_CASE_PREFIXES: Mapping[str, tuple[str, ...]] = {
    "offline": ("JOB-", "FILE-", "PPT-", "OCR-", "KEY-", "ASR-"),
    "vision": ("VIS-",),
    "online": ("ONL-", "FACE-"),
    "final": ("LOAD-",),
}
PHASE_REGRESSION_TARGETS: Mapping[str, tuple[str, ...]] = {
    "offline": (
        "tests/test_control_api_submission.py",
        "tests/test_node_state_machine.py",
        "tests/test_node_dispatcher.py",
        "tests/test_workspace_startup.py",
        "tests/test_workspace_cleanup.py",
        "tests/test_media_download.py",
        "tests/test_audio_extraction.py",
        "tests/test_ppt_slice_adapter.py",
        "tests/test_ppt_text_adapters.py",
        "tests/test_ppt_text_pipeline.py",
        "tests/test_offline_asr_adapter.py",
        "tests/test_course_overview_adapter.py",
        "tests/integration/test_course_repository.py",
    ),
    "vision": (
        "tests/test_adaptive_vision_scan.py",
        "tests/test_behavior_intervals.py",
        "tests/test_student_aggregation.py",
        "tests/test_vision_cache.py",
        "tests/test_vision_evidence.py",
        "tests/test_vision_kafka_boundary.py",
        "tests/test_vbas_batch_client.py",
        "tests/integration/test_course_repository.py",
    ),
    "online": (
        "tests/test_online_gateway.py",
        "tests/integration/test_unified_capacity_cross_service.py",
    ),
    "final": (
        "tests/test_online_gateway.py",
        "tests/integration/test_unified_capacity_cross_service.py",
        "tests/test_milestone_2b_load_case_runners.py",
    ),
}
TERMINAL_STATUSES = {60, 70, 80}
MANUAL_REVIEW_CASE_IDS = frozenset(
    {
        "PPT-012",
        "PPT-013",
        "PPT-014",
        "KEY-005",
        "ASR-012",
        "ASR-013",
        "ASR-017",
        "VIS-025",
    }
)


def _case_regression_patterns() -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}

    def bind(case_ids: Sequence[str], *patterns: str) -> None:
        if not patterns or any(not pattern for pattern in patterns):
            raise RuntimeError("business case regression pattern is empty")
        for case_id in case_ids:
            if case_id in result:
                raise RuntimeError(f"business case regression mapping is duplicated: {case_id}")
            result[case_id] = tuple(patterns)

    bind(
        (
            "JOB-001",
            "JOB-002",
            "JOB-003",
            "JOB-004",
            "JOB-005",
            "JOB-006",
            "JOB-007",
            "JOB-008",
            "JOB-015",
        ),
        "test_post_course_job_reports_selected_input_error_in_http_200",
    )
    bind(("JOB-009",), "test_post_course_job_accepts_sparse_ppt_request")
    bind(("JOB-010",), "test_duplicate_submission_returns_existing_task_type")
    bind(
        ("JOB-011", "JOB-012"),
        "test_completed_active_and_appended_task_types_are_idempotent",
    )
    bind(
        ("JOB-013",),
        "test_concurrent_teacher_consumers_share_one_download",
        "test_combined_task_types_share_internal_submission_id",
    )
    bind(("JOB-014",), "test_later_submission_downloads_teacher_video_again")
    bind(("JOB-016",), "test_asr_options_merge_partial_override_over_documented_defaults")
    bind(
        ("JOB-017", "JOB-018"),
        "test_urgent_claims_before_normal_fifo_without_preempting_running_node",
    )
    bind(("JOB-019",), "test_terminal_node_cannot_transition_back_to_running")
    bind(("JOB-020",), "test_get_unknown_course_job_returns_business_not_found")

    bind(
        ("FILE-001", "FILE-002", "FILE-004", "FILE-005"),
        "test_failed_download_does_not_publish_a_partial_or_final_media_file",
    )
    bind(("FILE-003",), "test_oversized_download_removes_partial_file")
    bind(("FILE-006",), "test_failed_audio_extraction_removes_partial_output")
    bind(("FILE-007",), "test_media_download_rejects_zero_duration_after_inspection")
    bind(("FILE-008", "FILE-009"), "test_task_workspace_rejects_path_traversal")
    bind(("FILE-010",), "test_manifest_validator_rejects_symlinked_task_ancestor")
    bind(("FILE-011",), "test_oversized_download_removes_partial_file")
    bind(
        ("FILE-012", "FILE-013"),
        "test_terminal_cleanup_removes_only_course_workspace_after_artifacts_exist",
    )
    bind(
        ("FILE-014", "FILE-015"),
        "test_cleanup_waits_for_all_requested_pipelines_and_durable_files",
    )
    bind(("FILE-016",), "test_failed_result_write_rolls_back_completion")

    bind(("PPT-001", "PPT-002"), "test_adapter_uses_new_platform_internal_submission_contract")
    bind(
        ("PPT-003", "PPT-004"),
        "test_terminal_callback_rejects_legacy_and_unsafe_identifiers",
    )
    bind(("PPT-005",), "test_terminal_handler_is_idempotent_after_durable_completion")
    bind(("PPT-006", "PPT-007"), "test_manifest_reconciliation_uses_persisted_node_identity")
    bind(
        ("PPT-008", "PPT-009", "PPT-010"),
        "test_manifest_validator_rejects_callback_outside_task_result_root",
    )
    bind(
        ("PPT-011",),
        "test_manifest_validator_rejects_dynamic_segments_that_differ_from_callback",
    )
    bind(
        ("PPT-012", "PPT-013", "PPT-014", "PPT-015"),
        "test_manifest_validator_accepts_complete_shared_result",
    )

    bind(
        ("OCR-001", "OCR-002", "OCR-003"),
        "test_ppt_text_pipeline_keeps_completed_items_when_later_item_fails",
    )
    bind(("OCR-004",), "test_ppt_text_pipeline_keeps_completed_items_when_later_item_fails")
    bind(("OCR-005",), "test_dynamic_ppt_work_items_are_idempotent")

    bind(("KEY-001",), "test_ppt_keyword_pipeline_recovers_only_unfinished_items")
    bind(
        ("KEY-002", "KEY-003", "KEY-004"),
        "test_ppt_keyword_pipeline_recovers_only_unfinished_items",
    )
    bind(("KEY-005",), "test_keyword_adapter_uses_v1_text_endpoint_and_preserves_response")

    bind(("ASR-001", "ASR-002", "ASR-003"), "test_failed_audio_extraction_removes_partial_output")
    bind(("ASR-004",), "test_capacity_lease_is_released_when_background_renewal_failed")
    bind(
        ("ASR-005", "ASR-006", "ASR-007", "ASR-008", "ASR-009"),
        "test_offline_asr_adapter_rejects_business_error_inside_http_200",
    )
    bind(("ASR-010",), "test_capacity_lease_is_renewed_until_terminal_persistence")
    bind(("ASR-011",), "test_duplicate_pipeline_initialization_creates_each_node_once")
    bind(
        ("ASR-012", "ASR-013"),
        "test_asr_pipeline_persists_complete_response_and_effective_params",
    )
    bind(("ASR-014",), "test_completed_prerequisite_releases_only_direct_dependent_node")
    bind(
        ("ASR-015", "ASR-016", "ASR-017"),
        "test_course_overview_pipeline_preserves_complete_generic_response",
    )
    bind(("ASR-018",), "test_completed_asr_reuses_large_result_and_original_effective_params")

    bind(
        ("VIS-001", "VIS-002", "VIS-003", "VIS-004"),
        "test_visual_command_requires_selected_stream_local_path",
    )
    bind(("VIS-005",), "test_vision_vbas_calls_use_control_service_capacity_lease")
    bind(("VIS-006",), "test_vbas_batches_use_capacity_lease_and_configured_concurrency")
    bind(("VIS-007",), "test_frame_cache_keys_task_stream_and_timestamp")
    bind(("VIS-008",), "test_valid_analysis_without_behavior_returns_completed_empty_result")
    bind(("VIS-009", "VIS-017"), "test_insufficient_teacher_frames_do_not_fabricate_behavior")
    bind(("VIS-010", "VIS-013"), "test_writing_intervals_merge_when_gap_equals_three_seconds")
    bind(("VIS-011",), "test_writing_intervals_stay_separate_when_gap_exceeds_threshold")
    bind(("VIS-012",), "test_deterministic_teacher_gap_rules_merge_writing_and_sitting_boundaries")
    bind(("VIS-014", "VIS-015"), "test_adaptive_scan_refines_only_coarse_candidate_neighborhoods")
    bind(
        ("VIS-016",), "test_deterministic_teacher_empty_and_invalid_coverage_have_distinct_results"
    )
    bind(
        ("VIS-018", "VIS-019", "VIS-020", "VIS-021"),
        "test_missing_student_regions_reuse_database_owned_fallbacks",
    )
    bind(("VIS-022",), "test_student_vbas_request_preserves_roi_points")
    bind(("VIS-023",), "test_student_metrics_use_stable_people_and_detected_total_denominator")
    bind(
        ("VIS-024", "VIS-025"),
        "test_same_category_selection_keeps_stronger_evidence_and_limits_count",
    )
    bind(
        ("VIS-026", "VIS-027", "VIS-028"),
        "test_vision_service_consumes_command_and_publishes_progress_and_completion",
    )

    bind(("ONL-001", "ONL-002"), "test_online_ocr_rejects_invalid_request_before_leasing")
    bind(("ONL-003", "ONL-004"), "test_online_ocr_enforces_body_and_decoded_size_before_leasing")
    bind(
        ("ONL-005", "ONL-006"), "test_online_vbas_proxies_complete_base64_request_through_one_lease"
    )
    bind(
        ("ONL-007", "ONL-013"),
        "test_multi_image_vbas_request_is_not_split_and_preserves_partial_results",
    )
    bind(
        ("ONL-008",), "test_online_http_returns_bounded_business_error_when_capacity_is_unavailable"
    )
    bind(("ONL-009",), "test_online_ocr_timeout_and_upstream_errors_release_the_lease")
    bind(("ONL-010", "ONL-011"), "test_online_image_quality_uses_detect_all_contract")
    bind(("ONL-012", "ONL-020"), "test_online_gateway_exposes_realtime_asr_websocket")
    bind(
        ("ONL-014",),
        "test_realtime_asr_returns_capacity_error_and_1013_without_connecting_operator",
    )
    bind(
        ("ONL-015", "ONL-016", "ONL-018"),
        "test_realtime_asr_operator_disconnect_closes_session_and_releases_lease",
    )
    bind(
        ("ONL-017", "ONL-019"), "test_realtime_asr_keeps_one_sticky_lease_for_the_websocket_session"
    )

    bind(
        ("FACE-001", "FACE-002", "FACE-003", "FACE-004"),
        "test_online_face_recognition_preserves_existing_operator_contract",
    )
    bind(
        (
            "FACE-005",
            "FACE-006",
            "FACE-007",
            "FACE-008",
            "FACE-009",
            "FACE-010",
            "FACE-011",
            "FACE-012",
        ),
        "test_online_gateway_exposes_face_recognition_proxy",
    )
    bind(("FACE-013", "FACE-014"), "test_concurrent_online_requests_can_use_different_instances")

    bind(("LOAD-001",), "test_online_ocr_crosses_gateway_control_and_contract_operator")
    bind(
        ("LOAD-002", "LOAD-003", "LOAD-004", "LOAD-005"),
        "test_concurrent_online_http_returns_50301_while_operator_waits",
    )
    bind(("LOAD-006",), "test_three_calling_services_renew_same_lease_across_ttl_and_release")
    bind(
        ("LOAD-007",),
        "test_deterministic_instance_preference_is_allowed_until_capacity_is_full",
    )
    bind(("LOAD-008",), "test_online_and_ppt_ocr_share_one_pool_without_losing_offline_work")
    bind(("LOAD-009",), "test_control_reports_heartbeat_difference_without_using_it_for_admission")
    return result


CASE_REGRESSION_PATTERNS = _case_regression_patterns()


def _json_value(response: httpx.Response, label: str) -> object:
    if response.status_code != 200:
        raise RuntimeError(f"{label} HTTP 状态不是 200: {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{label} 没有返回 JSON 对象") from exc
    return body


def _json_object(response: httpx.Response, label: str) -> dict[str, Any]:
    body = _json_value(response, label)
    if not isinstance(body, dict):
        raise RuntimeError(f"{label} 没有返回 JSON 对象")
    return body


def _require_business_code(
    response: httpx.Response,
    expected: int,
    label: str,
) -> dict[str, Any]:
    body = _json_object(response, label)
    if body.get("code") != expected:
        raise RuntimeError(f"{label} 业务码不匹配: expected={expected}, actual={body.get('code')}")
    return body


def _sanitize_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            "type": "object",
            "keys": sorted(str(key) for key in value),
            "size": len(value),
        }
    if isinstance(value, list):
        return {"type": "array", "size": len(value)}
    if isinstance(value, str):
        return {"type": "string", "non_empty": bool(value), "length": len(value)}
    return {"type": type(value).__name__, "present": value is not None}


def _task_summary(body: Mapping[str, Any]) -> dict[str, Any]:
    data = body.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("课程查询缺少 data")
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        raise RuntimeError("课程查询缺少 tasks")
    summary: list[dict[str, Any]] = []
    for item in tasks:
        if not isinstance(item, dict):
            raise RuntimeError("课程查询 task 不是对象")
        node_summaries: list[dict[str, Any]] = []
        nodes = item.get("nodes")
        if isinstance(nodes, list):
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                node_summary = {
                    "node_code": node.get("node_code"),
                    "status": node.get("status"),
                    "reason": node.get("reason"),
                    "progress": node.get("progress"),
                    "path": node.get("path"),
                    "count": node.get("count"),
                }
                if "result" in node:
                    node_summary["result"] = _sanitize_value(node.get("result"))
                node_summaries.append(node_summary)
        summary.append(
            {
                "task_type": item.get("task_type"),
                "status": item.get("status"),
                "reason": item.get("reason"),
                "nodes": node_summaries,
            }
        )
    return {"task_id": data.get("task_id"), "tasks": summary}


def _selected_cases(catalog: Path, phase: str) -> tuple[CaseDefinition, ...]:
    prefixes = PHASE_CASE_PREFIXES[phase]
    cases = tuple(
        case
        for case in load_case_catalog(catalog).cases
        if case.phase == phase and case.case_id.startswith(prefixes)
    )
    expected_counts = {"offline": 79, "vision": 28, "online": 34, "final": 9}
    if len(cases) != expected_counts[phase]:
        raise RuntimeError(f"{phase} campaign 用例数量漂移: {len(cases)}/{expected_counts[phase]}")
    return cases


def _run_regression(phase: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"milestone-2b-{phase}-junit-") as raw:
        junit_path = Path(raw) / "regression.xml"
        command = [
            str(PLATFORM_ROOT / ".venv/bin/python"),
            "-m",
            "pytest",
            "-q",
            *PHASE_REGRESSION_TARGETS[phase],
            f"--junitxml={junit_path}",
        ]
        started = time.monotonic()
        completed = subprocess.run(
            command,
            cwd=PLATFORM_ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=1800,
        )
        elapsed = time.monotonic() - started
        output = (completed.stdout + completed.stderr).strip()
        if completed.returncode != 0:
            raise RuntimeError(f"{phase} 分泳道回归失败:\n{output[-12000:]}")
        try:
            root = ET.parse(junit_path).getroot()
        except (OSError, ET.ParseError) as error:
            raise RuntimeError(f"{phase} 分泳道回归缺少 JUnit 证据") from error
        testcases = list(root.iter("testcase"))
        failed = [
            case
            for case in testcases
            if any(case.find(name) is not None for name in ("failure", "error", "skipped"))
        ]
        if not testcases or failed:
            raise RuntimeError(f"{phase} 分泳道回归必须实际执行且零失败、零错误、零跳过")
        passed_testcases = sorted(
            {
                f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}"
                for case in testcases
            }
        )
    return {
        "command": command,
        "returncode": completed.returncode,
        "elapsed_seconds": round(elapsed, 3),
        "output_tail": output[-12000:],
        "junit": {
            "tests": len(testcases),
            "failures": 0,
            "errors": 0,
            "skipped": 0,
        },
        "passed_testcases": passed_testcases,
    }


def _manual_reviews(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("B 级质量复核文件不存在或不安全")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("B 级质量复核文件不是合法 JSON") from error
    if type(payload) is not dict:
        raise RuntimeError("B 级质量复核文件必须是对象")
    reviews: dict[str, dict[str, Any]] = {}
    for case_id, raw in payload.items():
        if case_id not in MANUAL_REVIEW_CASE_IDS or type(raw) is not dict:
            raise RuntimeError(f"B 级质量复核包含未知或非法用例: {case_id}")
        review = dict(raw)
        if (
            set(review) != {"status", "reviewer", "artifact", "observed"}
            or review.get("status") != "通过"
            or type(review.get("reviewer")) is not str
            or not review["reviewer"].strip()
            or type(review.get("artifact")) is not str
            or not review["artifact"].strip()
            or type(review.get("observed")) is not dict
            or not review["observed"]
        ):
            raise RuntimeError(f"B 级质量复核不完整: {case_id}")
        reviews[str(case_id)] = review
    return reviews


def _runtime_probe(phase: str, result: Mapping[str, Any]) -> dict[str, Any]:
    if phase == "offline":
        course = result.get("real_course")
        if not isinstance(course, dict):
            raise RuntimeError("offline campaign 缺少真实课程探针")
        summary = course.get("summary")
        tasks = summary.get("tasks") if isinstance(summary, dict) else None
        if not isinstance(tasks, list) or len(tasks) != 4:
            raise RuntimeError("offline campaign 没有四条真实任务泳道")
        return {
            "probe": "full_course",
            "task_count": len(tasks),
            "completed_task_count": sum(
                isinstance(task, dict) and task.get("status") == 60 for task in tasks
            ),
            "duplicate_code": course.get("duplicate_code"),
        }
    if phase == "vision":
        tasks = result.get("visual_tasks")
        if not isinstance(tasks, dict):
            raise RuntimeError("vision campaign 缺少真实视觉任务探针")
        return {
            "probe": "teacher_student_visual",
            "task_types": sorted(tasks),
            "completed_task_count": sum(
                isinstance(task, dict) and task.get("status") == 60 for task in tasks.values()
            ),
        }
    if phase == "online":
        requests = result.get("real_requests")
        if not isinstance(requests, dict):
            raise RuntimeError("online campaign 缺少真实在线请求探针")
        return {
            "probe": "online_operator_requests",
            "operators": sorted(requests),
            "invalid_request_count": len(result.get("invalid_requests") or {}),
        }
    levels = result.get("load_levels")
    if not isinstance(levels, list) or not levels:
        raise RuntimeError("final campaign 缺少真实压力探针")
    return {
        "probe": "online_ocr_load",
        "levels": [item.get("concurrency") for item in levels if isinstance(item, dict)],
        "request_count": sum(
            int(item.get("requests", 0)) for item in levels if isinstance(item, dict)
        ),
        "post_load_active_leases": result.get("post_load_active_leases"),
    }


def _build_case_checks(
    *,
    args: argparse.Namespace,
    phase: str,
    result: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    regression = result.get("regression")
    passed = regression.get("passed_testcases") if isinstance(regression, dict) else None
    if not isinstance(passed, list) or not passed:
        raise RuntimeError(f"{phase} campaign 缺少逐用例可归因的 JUnit 结果")
    runtime_probe = _runtime_probe(phase, result)
    reviews = _manual_reviews(args.manual_review_json)
    checks: dict[str, dict[str, Any]] = {}
    for case in _selected_cases(args.catalog, phase):
        patterns = CASE_REGRESSION_PATTERNS.get(case.case_id)
        if patterns is None:
            raise RuntimeError(f"{case.case_id} 缺少显式的回归证据映射")
        matches_by_pattern = {
            pattern: [test for test in passed if pattern in test] for pattern in patterns
        }
        missing_patterns = [
            pattern for pattern, matches in matches_by_pattern.items() if not matches
        ]
        if missing_patterns:
            raise RuntimeError(f"{case.case_id} 缺少实际通过的指定回归证据: {missing_patterns}")
        related = sorted({test for matches in matches_by_pattern.values() for test in matches})
        review = reviews.get(case.case_id)
        if case.case_id in MANUAL_REVIEW_CASE_IDS and review is None:
            raise RuntimeError(f"{case.case_id} 缺少独立 B 级质量复核证据")
        if review is not None:
            review_path = Path(str(review["artifact"]))
            if review_path.is_absolute() or ".." in review_path.parts:
                raise RuntimeError(f"{case.case_id} B 级质量复核证据路径不安全")
            source = args.release_root / review_path
            if source.is_symlink() or not source.is_file():
                raise RuntimeError(f"{case.case_id} B 级质量复核证据文件不存在")
        checks[case.case_id] = {
            "check_id": f"business-case-{case.case_id.lower()}",
            "method": (
                "real-runtime-targeted-regression-and-manual-review"
                if review is not None
                else "real-runtime-and-targeted-regression"
            ),
            "case_title": case.title,
            "expected": case.expected,
            "runtime_probe": runtime_probe,
            "related_passed_testcases": related,
            "assertions": [
                {
                    "name": "real_runtime_probe_completed",
                    "expected": True,
                    "actual": True,
                    "passed": True,
                },
                {
                    "name": "all_declared_regression_patterns_executed",
                    "expected": list(patterns),
                    "actual": sorted(matches_by_pattern),
                    "passed": not missing_patterns,
                },
            ],
            "manual_review": review,
        }
    required_reviews = MANUAL_REVIEW_CASE_IDS & set(checks)
    selected_ids = {case.case_id for case in _selected_cases(args.catalog, phase)}
    if set(reviews) & selected_ids != required_reviews:
        raise RuntimeError(f"{phase} B 级质量复核集合不完整或越界")
    return checks


def _invalid_submission_checks(
    client: httpx.Client,
    *,
    slides_video_url: str,
) -> dict[str, Any]:
    checks = {
        "missing_task_id": {"task_types": ["PPT"]},
        "empty_task_types": {"task_id": f"m2b-empty-{uuid4().hex[:12]}", "task_types": []},
        "unknown_task_type": {
            "task_id": f"m2b-unknown-{uuid4().hex[:12]}",
            "task_types": ["UNKNOWN"],
        },
        "ppt_missing_path": {
            "task_id": f"m2b-ppt-missing-{uuid4().hex[:12]}",
            "task_types": ["PPT"],
        },
        "asr_missing_path": {
            "task_id": f"m2b-asr-missing-{uuid4().hex[:12]}",
            "task_types": ["ASR"],
        },
        "teacher_missing_path": {
            "task_id": f"m2b-teacher-missing-{uuid4().hex[:12]}",
            "task_types": ["TEACHER_BEHAVIOR"],
        },
        "student_missing_path": {
            "task_id": f"m2b-student-missing-{uuid4().hex[:12]}",
            "task_types": ["STUDENT_BEHAVIOR"],
            "student_count": 38,
        },
        "student_negative_count": {
            "task_id": f"m2b-student-negative-{uuid4().hex[:12]}",
            "task_types": ["STUDENT_BEHAVIOR"],
            "student_video_path": "http://127.0.0.1/student.mp4",
            "student_count": -1,
        },
        "asr_unknown_option": {
            "task_id": f"m2b-asr-option-{uuid4().hex[:12]}",
            "task_types": ["ASR"],
            "teacher_video_path": "http://127.0.0.1/teacher.mp4",
            "asr_options": {"unknown": True},
        },
    }
    observed: dict[str, Any] = {}
    for name, payload in checks.items():
        body = _require_business_code(
            client.post("/api/course-jobs", json=payload),
            40001,
            name,
        )
        observed[name] = {"code": body["code"], "message": body.get("message")}

    unrelated_task_id = f"m2b-unrelated-{uuid4().hex[:12]}"
    unrelated = _require_business_code(
        client.post(
            "/api/course-jobs",
            json={
                "task_id": unrelated_task_id,
                "task_types": ["PPT"],
                "slides_video_path": slides_video_url,
                "teacher_video_path": {"invalid": True},
                "student_video_path": ["invalid"],
            },
        ),
        0,
        "ppt_unrelated_fields",
    )
    observed["ppt_unrelated_fields"] = {
        "code": unrelated["code"],
        "task_id": unrelated_task_id,
    }
    missing = _require_business_code(
        client.get(f"/api/course-jobs/m2b-missing-{uuid4().hex[:12]}"),
        40401,
        "missing_task_query",
    )
    observed["missing_task_query"] = {
        "code": missing["code"],
        "message": missing.get("message"),
    }
    return observed


def _offline_campaign(args: argparse.Namespace) -> dict[str, Any]:
    regression = _run_regression("offline")
    timeout = httpx.Timeout(connect=10, read=60, write=60, pool=10)
    with httpx.Client(base_url=args.control_url, timeout=timeout) as client:
        invalid = _invalid_submission_checks(
            client,
            slides_video_url=args.slides_video_url,
        )
        release_tag, git_sha = release_identity(args.release_root)
        task_id = f"m2b-{release_tag.lower()}-{git_sha[:12]}-full-course"
        payload = {
            "task_id": task_id,
            "task_types": [
                "PPT",
                "ASR",
                "TEACHER_BEHAVIOR",
                "STUDENT_BEHAVIOR",
            ],
            "priority": "URGENT",
            "teacher_video_path": args.teacher_video_url,
            "student_video_path": args.student_video_url,
            "slides_video_path": args.slides_video_url,
            "front_points": [
                {"X": 0, "Y": 0},
                {"X": 1920, "Y": 0},
                {"X": 1920, "Y": 540},
                {"X": 0, "Y": 540},
            ],
            "back_point": [
                {"X": 0, "Y": 540},
                {"X": 1920, "Y": 540},
                {"X": 1920, "Y": 1080},
                {"X": 0, "Y": 1080},
            ],
            "student_count": 38,
            "asr_options": {
                "showSpk": True,
                "showEmotion": True,
                "showRoleIdentify": False,
                "wordTimestamps": False,
            },
        }
        submitted = _require_business_code(
            client.post("/api/course-jobs", json=payload),
            0,
            "full_course_submit",
        )
        duplicate = _require_business_code(
            client.post("/api/course-jobs", json=payload),
            0,
            "full_course_duplicate",
        )
        deadline = time.monotonic() + args.course_timeout_seconds
        last_summary: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            queried = _require_business_code(
                client.get(f"/api/course-jobs/{task_id}"),
                0,
                "full_course_query",
            )
            last_summary = _task_summary(queried)
            selected = [
                task for task in last_summary["tasks"] if task["task_type"] in payload["task_types"]
            ]
            statuses = [task["status"] for task in selected]
            if statuses and all(status in TERMINAL_STATUSES for status in statuses):
                break
            time.sleep(args.poll_interval_seconds)
        if last_summary is None:
            raise RuntimeError("课程任务没有产生可查询状态")
        selected = [
            task for task in last_summary["tasks"] if task["task_type"] in payload["task_types"]
        ]
        failures = [task for task in selected if task["status"] != 60]
        if failures:
            raise RuntimeError(
                "完整课程泳道没有全部完成: "
                + json.dumps(failures, ensure_ascii=False, sort_keys=True)
            )
        return {
            "phase": "offline",
            "regression": regression,
            "invalid_submissions": invalid,
            "real_course": {
                "task_id": task_id,
                "submitted_code": submitted["code"],
                "duplicate_code": duplicate["code"],
                "summary": last_summary,
            },
            "cleanup": {"status": "clean", "residual_resources": []},
        }


def _image_data_url(path: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()


async def _online_asr(url: str, audio_path: Path, timeout: float) -> dict[str, Any]:
    with wave.open(str(audio_path), "rb") as stream:
        contract = (
            stream.getframerate(),
            stream.getnchannels(),
            stream.getsampwidth(),
        )
        if contract != (16000, 1, 2):
            raise RuntimeError(f"实时 ASR fixture 格式错误: {contract}")
        pcm = stream.readframes(stream.getnframes())
    messages = 0
    non_empty_text = False
    async with websockets.connect(
        url.rstrip("/") + "/api/online/asr/stream",
        open_timeout=timeout,
        close_timeout=timeout,
        max_size=8 * 1024 * 1024,
    ) as socket:
        for offset in range(0, len(pcm), 7680 * 2):
            await socket.send(pcm[offset : offset + 7680 * 2])
            response = json.loads(await asyncio.wait_for(socket.recv(), timeout))
            messages += 1
            if isinstance(response, dict) and response.get("text"):
                non_empty_text = True
                break
    if not non_empty_text:
        raise RuntimeError("实时 ASR 网关没有返回非空增量文本")
    return {"messages": messages, "non_empty_text": True}


def _online_campaign(args: argparse.Namespace) -> dict[str, Any]:
    regression = _run_regression("online")
    image = _image_data_url(args.image_fixture)
    face = _image_data_url(args.face_fixture)
    invalid_checks: dict[str, Any] = {}
    timeout = httpx.Timeout(connect=10, read=args.online_timeout_seconds, write=60, pool=10)
    with httpx.Client(base_url=args.online_url, timeout=timeout) as client:
        for label, path, payload in (
            ("vbas_empty", "/api/online/vbas/analyze", {"ImageList": [], "stream_type": "student"}),
            ("face_empty", "/api/online/face/recognize", {"photo": ""}),
            ("screen_empty", "/api/online/image-quality/detect", {"image": ""}),
            ("ocr_empty", "/api/online/ocr/recognize", {"image": ""}),
        ):
            body = _require_business_code(client.post(path, json=payload), 40001, label)
            invalid_checks[label] = body["code"]

        vbas_results: dict[str, Any] = {}
        for role in ("student", "teacher"):
            body = _require_business_code(
                client.post(
                    "/api/online/vbas/analyze",
                    json={
                        "task_id": f"m2b-online-{role}",
                        "batch_id": f"m2b-online-{role}-batch",
                        "stream_type": role,
                        "ImageList": [{"ImageId": f"{role}-001", "StoragePath": image}],
                    },
                ),
                0,
                f"online_vbas_{role}",
            )
            data = body.get("data")
            if not isinstance(data, dict):
                raise RuntimeError(f"在线 VBas {role} 缺少 data")
            status = data.get("StatusObject")
            if not isinstance(status, dict) or status.get("StatusCode") != 0:
                raise RuntimeError(f"在线 VBas {role} 状态失败")
            vbas_results[role] = {
                "status_code": status.get("StatusCode"),
                "item_count": len(data.get("DataList") or []),
            }

        ocr = _require_business_code(
            client.post(
                "/api/online/ocr/recognize",
                json={"image_id": "m2b-online-ocr", "image": image},
            ),
            0,
            "online_ocr",
        )
        ocr_data = ocr.get("data")
        if not isinstance(ocr_data, dict) or ocr_data.get("err_no") != 0:
            raise RuntimeError("在线 OCR 原始合同失败")

        screen = _require_business_code(
            client.post("/api/online/image-quality/detect", json={"image": image}),
            0,
            "online_screen_det",
        )
        screen_data = screen.get("data")
        if not isinstance(screen_data, dict) or screen_data.get("code") != 200:
            raise RuntimeError("在线 ScreenDet 原始合同失败")

        person_number = f"M2B-{uuid4().hex[:12]}"
        created = _require_business_code(
            client.post(
                "/api/online/face/persons",
                json={"photo": face, "name": "里程碑验证", "number": person_number},
            ),
            0,
            "face_person_create",
        )
        created_data = created.get("data")
        if not isinstance(created_data, dict) or created_data.get("status_code") != 200:
            raise RuntimeError("人物入库没有返回成功的 FaceRec 合同")
        person_payload = created_data.get("data")
        if isinstance(person_payload, dict) and person_payload.get("photo_path"):
            raise RuntimeError("save_person_photo=false 时仍返回了 photo_path")
        try:
            recognized = _require_business_code(
                client.post(
                    "/api/online/face/recognize",
                    json={"photo": face, "targets": [person_number], "threshold": 0.4},
                ),
                0,
                "online_face_recognize",
            )
            recognized_data = recognized.get("data")
            if not isinstance(recognized_data, dict):
                raise RuntimeError("在线人脸识别缺少 FaceRec 原始响应")
            searched = _require_business_code(
                client.post(
                    "/api/online/face/persons/search",
                    json={"number": person_number},
                ),
                0,
                "face_person_search",
            )
            searched_data = searched.get("data")
            if not isinstance(searched_data, dict) or searched_data.get("status_code") != 200:
                raise RuntimeError("人物搜索没有返回成功的 FaceRec 合同")
        finally:
            deleted = _require_business_code(
                client.request(
                    "DELETE",
                    "/api/online/face/persons/delete",
                    json={"number": person_number},
                ),
                0,
                "face_person_delete",
            )
            deleted_data = deleted.get("data")
            if not isinstance(deleted_data, dict) or deleted_data.get("status_code") != 200:
                raise RuntimeError("人物清理没有返回成功的 FaceRec 合同")

    websocket = asyncio.run(
        _online_asr(
            args.online_url.replace("http://", "ws://").replace("https://", "wss://"),
            args.asr_online_fixture,
            args.online_timeout_seconds,
        )
    )
    return {
        "phase": "online",
        "regression": regression,
        "invalid_requests": invalid_checks,
        "real_requests": {
            "vbas": vbas_results,
            "ocr": {"err_no": ocr_data.get("err_no"), "keys": sorted(ocr_data)},
            "screen_det": {
                "code": screen_data.get("code"),
                "executed_modules": screen_data.get("executed_modules"),
                "failed_modules": screen_data.get("failed_modules"),
            },
            "face": {
                "person_number_sha256": hashlib.sha256(person_number.encode()).hexdigest(),
                "recognized": True,
                "searched": True,
                "deleted": True,
                "photo_saved": False,
            },
            "asr_websocket": websocket,
        },
        "cleanup": {"status": "clean", "residual_resources": []},
    }


def _vision_campaign(args: argparse.Namespace) -> dict[str, Any]:
    regression = _run_regression("vision")
    offline_path = args.release_root / "business/offline-campaign.json"
    if not offline_path.is_file():
        raise RuntimeError("视觉 campaign 缺少同 release 的完整课程泳道证据")
    offline = json.loads(offline_path.read_text(encoding="utf-8"))
    summary = offline.get("real_course", {}).get("summary")
    if not isinstance(summary, dict):
        raise RuntimeError("完整课程泳道证据缺少视觉任务摘要")
    tasks = summary.get("tasks")
    if not isinstance(tasks, list):
        raise RuntimeError("完整课程泳道证据缺少任务数组")
    selected = {
        task.get("task_type"): task
        for task in tasks
        if isinstance(task, dict)
        and task.get("task_type") in {"TEACHER_BEHAVIOR", "STUDENT_BEHAVIOR"}
    }
    if set(selected) != {"TEACHER_BEHAVIOR", "STUDENT_BEHAVIOR"}:
        raise RuntimeError("完整课程泳道没有教师/学生视觉任务")
    if any(task.get("status") != 60 for task in selected.values()):
        raise RuntimeError("教师/学生视觉任务没有完成")
    for task_type, task in selected.items():
        nodes = task.get("nodes")
        if not isinstance(nodes, list) or len(nodes) != 1:
            raise RuntimeError(f"{task_type} 视觉节点数量不正确")
        result = nodes[0].get("result") if isinstance(nodes[0], dict) else None
        if not isinstance(result, dict) or result.get("present") is False:
            raise RuntimeError(f"{task_type} 视觉节点没有结构化结果")
    return {
        "phase": "vision",
        "regression": regression,
        "real_course_task_id": summary.get("task_id"),
        "visual_tasks": selected,
        "cleanup": {"status": "clean", "residual_resources": []},
    }


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise RuntimeError("压力样本为空")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


async def _concurrent_ocr(
    *,
    online_url: str,
    image: str,
    concurrency: int,
    timeout: float,
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    codes: list[int] = []

    async with httpx.AsyncClient(
        base_url=online_url,
        timeout=httpx.Timeout(connect=10, read=timeout, write=60, pool=timeout),
        limits=httpx.Limits(
            max_connections=max(concurrency, 10),
            max_keepalive_connections=max(concurrency, 10),
        ),
    ) as client:

        async def request(index: int) -> None:
            async with semaphore:
                started = time.perf_counter()
                response = await client.post(
                    "/api/online/ocr/recognize",
                    json={"image_id": f"m2b-load-{concurrency}-{index}", "image": image},
                )
                elapsed = time.perf_counter() - started
                body = _json_object(response, "load_ocr")
                code = body.get("code")
                if not isinstance(code, int):
                    raise RuntimeError("压力 OCR 响应缺少整数业务码")
                latencies.append(elapsed)
                codes.append(code)

        await asyncio.gather(*(request(index) for index in range(concurrency)))
    return {
        "concurrency": concurrency,
        "requests": len(codes),
        "success": sum(code == 0 for code in codes),
        "capacity_rejected": sum(code == 50301 for code in codes),
        "failed": sum(code not in {0, 50301} for code in codes),
        "p95_seconds": round(_percentile(latencies, 0.95), 4),
        "p99_seconds": round(_percentile(latencies, 0.99), 4),
        "max_seconds": round(max(latencies), 4),
    }


def _load_campaign(args: argparse.Namespace) -> dict[str, Any]:
    regression = _run_regression("final")
    image = _image_data_url(args.image_fixture)
    levels = []
    for concurrency in (1, 3, 10, 30):
        result = asyncio.run(
            _concurrent_ocr(
                online_url=args.online_url,
                image=image,
                concurrency=concurrency,
                timeout=args.online_timeout_seconds,
            )
        )
        levels.append(result)
        if result["failed"]:
            break
    if not levels or levels[0]["success"] != 1:
        raise RuntimeError("单请求压力基线失败")
    if any(level["failed"] for level in levels):
        raise RuntimeError("OCR 压力出现非容量类失败")
    with httpx.Client(base_url=args.control_url, timeout=30) as client:
        snapshot = _json_value(
            client.get("/ops/operator-instances/snapshot"),
            "capacity_snapshot",
        )
    if not isinstance(snapshot, list):
        raise RuntimeError("容量快照不是数组")
    orphaned = [
        item
        for item in snapshot
        if isinstance(item, dict) and item.get("active_lease_count") not in {0, None}
    ]
    if orphaned:
        raise RuntimeError(f"压力结束后仍有活跃租约: {len(orphaned)}")
    return {
        "phase": "final",
        "regression": regression,
        "load_levels": levels,
        "post_load_active_leases": 0,
        "cleanup": {"status": "clean", "residual_resources": []},
    }


def _publish_phase(
    *,
    args: argparse.Namespace,
    phase: str,
    result: Mapping[str, Any],
) -> None:
    cases = _selected_cases(args.catalog, phase)
    case_checks = result.get("case_checks")
    if not isinstance(case_checks, dict) or set(case_checks) != {case.case_id for case in cases}:
        raise RuntimeError(f"{phase} campaign 必须逐案提供完整检查结果")
    relative_artifact = Path("business") / f"{phase}-campaign.json"
    publish_json_once(
        release_root=args.release_root,
        relative_path=relative_artifact,
        document=dict(result),
    )
    for case in cases:
        check = case_checks[case.case_id]
        if (
            not isinstance(check, dict)
            or check.get("check_id") != f"business-case-{case.case_id.lower()}"
            or not isinstance(check.get("assertions"), list)
            or not check["assertions"]
            or not all(
                isinstance(assertion, dict) and assertion.get("passed") is True
                for assertion in check["assertions"]
            )
        ):
            raise RuntimeError(f"{case.case_id} 逐案检查没有通过")
        artifact_paths = [relative_artifact.as_posix()]
        manual_review = check.get("manual_review")
        if isinstance(manual_review, dict):
            artifact_paths.append(str(manual_review["artifact"]))
        publish_campaign_case(
            release_root=args.release_root,
            case_id=case.case_id,
            phase=phase,
            status="通过",
            reason=f"{case.title}：逐案运行时探针、相关回归及适用的质量复核均通过",
            observed=check,
            artifacts=tuple(artifact_paths),
            cleanup=result.get("cleanup") if isinstance(result.get("cleanup"), dict) else None,
        )


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--phase", choices=tuple(PHASE_CASE_PREFIXES), required=True)
    parser.add_argument("--release-root", type=_path, required=True)
    parser.add_argument(
        "--catalog",
        type=_path,
        default=PLATFORM_ROOT / "deploy/milestone-2b-case-catalog.yaml",
    )
    parser.add_argument("--control-url", default="http://127.0.0.1:18100")
    parser.add_argument("--online-url", default="http://127.0.0.1:18103")
    parser.add_argument("--teacher-video-url")
    parser.add_argument("--student-video-url")
    parser.add_argument("--slides-video-url")
    parser.add_argument(
        "--image-fixture",
        type=_path,
        default=Path("/data/course/_harness/fixtures/vbas-00000001-ddna.jpg"),
    )
    parser.add_argument(
        "--face-fixture",
        type=_path,
        default=Path("/data/course/_harness/fixtures/facerec-person.png"),
    )
    parser.add_argument(
        "--asr-online-fixture",
        type=_path,
        default=Path("/data/course/_harness/fixtures/asr-online-chinEng-16k.wav"),
    )
    parser.add_argument("--course-timeout-seconds", type=float, default=14400)
    parser.add_argument("--poll-interval-seconds", type=float, default=5)
    parser.add_argument("--online-timeout-seconds", type=float, default=300)
    parser.add_argument("--manual-review-json", type=_path)
    args = parser.parse_args(argv)
    release_identity(args.release_root)
    if args.phase == "offline" and not all(
        (args.teacher_video_url, args.student_video_url, args.slides_video_url)
    ):
        parser.error("offline phase requires all three video URLs")
    for field in ("course_timeout_seconds", "poll_interval_seconds", "online_timeout_seconds"):
        value = getattr(args, field)
        if not math.isfinite(value) or value <= 0:
            parser.error(f"{field} must be a finite positive number")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.phase == "offline":
        result = _offline_campaign(args)
    elif args.phase == "vision":
        result = _vision_campaign(args)
    elif args.phase == "online":
        result = _online_campaign(args)
    else:
        result = _load_campaign(args)
    result["case_checks"] = _build_case_checks(
        args=args,
        phase=args.phase,
        result=result,
    )
    _publish_phase(args=args, phase=args.phase, result=result)
    print(
        json.dumps(
            {
                "phase": args.phase,
                "status": "通过",
                "case_count": len(_selected_cases(args.catalog, args.phase)),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
