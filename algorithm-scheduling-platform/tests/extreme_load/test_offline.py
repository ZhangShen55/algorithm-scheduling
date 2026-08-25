from __future__ import annotations

import pytest

from scripts.extreme_load.core import NorthboundTargets, ReproducibleIdentity
from scripts.extreme_load.offline import (
    ASR_OPTIONS_DEFAULT,
    CourseMedia,
    MediaDownloadSample,
    NegativeMediaEndpoints,
    NegativeSubmissionExpectation,
    NegativeSubmissionKind,
    TaskCombination,
    build_append_task_type_sequence,
    build_completed_result_reuse_request,
    build_idempotent_burst,
    build_long_course_ladder,
    build_media_download_baseline,
    build_negative_submission_mix,
    build_priority_sequence,
    build_submission,
    build_unique_submission_burst,
)

TARGETS = NorthboundTargets(
    control_origin="http://192.168.29.11:18100",
    gateway_origin="http://192.168.29.11:18103",
)
MEDIA = CourseMedia(
    teacher_video_path="http://192.168.29.12:5555/course/T.mp4",
    student_video_path="http://192.168.29.12:5555/course/S.mp4",
    slides_video_path="http://192.168.29.12:5555/course/P.mp4",
)


def test_submission_preserves_approved_a_service_fields_without_renaming() -> None:
    payload = build_submission(
        task_id="course-001",
        combination=TaskCombination.ALL,
        media=MEDIA,
        priority="URGENT",
        front_points=[{"X": 0, "Y": 0}],
        back_point=[{"X": 1, "Y": 1}],
        student_count=38,
        asr_options={**ASR_OPTIONS_DEFAULT, "hotWords": ["NOTAM"]},
    )

    assert set(payload) == {
        "task_id",
        "task_types",
        "priority",
        "teacher_video_path",
        "student_video_path",
        "slides_video_path",
        "front_points",
        "back_point",
        "student_count",
        "asr_options",
    }
    assert "expected_student_count" not in payload
    assert payload["student_count"] == 38


def test_sparse_combinations_include_only_relevant_media_fields() -> None:
    ppt = build_submission("ppt", TaskCombination.PPT_ONLY, MEDIA)
    asr = build_submission("asr", TaskCombination.ASR_ONLY, MEDIA)
    teacher = build_submission("teacher", TaskCombination.TEACHER_ONLY, MEDIA)
    student = build_submission(
        "student", TaskCombination.STUDENT_ONLY, MEDIA, student_count=38
    )

    assert set(ppt) == {"task_id", "task_types", "priority", "slides_video_path"}
    assert set(asr) == {
        "task_id",
        "task_types",
        "priority",
        "teacher_video_path",
        "asr_options",
    }
    assert "student_video_path" not in teacher
    assert student["student_count"] == 38

    explicit_empty_options = build_submission(
        "asr-empty-options",
        TaskCombination.ASR_ONLY,
        MEDIA,
        asr_options={},
    )
    assert explicit_empty_options["asr_options"] == {}


def test_unique_and_idempotent_bursts_are_deterministic() -> None:
    identity = ReproducibleIdentity("campaign-001", 42)
    unique = build_unique_submission_burst(
        targets=TARGETS,
        identity=identity,
        case_id="OFFLINE-UNIQUE",
        count=100,
        combination=TaskCombination.PPT_ONLY,
        media=MEDIA,
    )
    repeated = build_idempotent_burst(
        targets=TARGETS,
        identity=identity,
        case_id="OFFLINE-IDEMPOTENT",
        count=30,
        combination=TaskCombination.PPT_ONLY,
        media=MEDIA,
    )

    assert len({request.json_body["task_id"] for request in unique}) == 100
    assert len({request.json_body["task_id"] for request in repeated}) == 1
    assert all(request.url.endswith(":18100/api/course-jobs") for request in unique)


def test_conflicting_media_and_completed_result_reuse_keep_same_task_id() -> None:
    identity = ReproducibleIdentity("campaign-001", 42)
    conflicting = CourseMedia(
        teacher_video_path=MEDIA.teacher_video_path,
        student_video_path=MEDIA.student_video_path,
        slides_video_path="http://192.168.29.12:5555/course/other-P.mp4",
    )
    repeated = build_idempotent_burst(
        TARGETS,
        identity,
        "OFFLINE-CONFLICT",
        4,
        TaskCombination.PPT_ONLY,
        MEDIA,
        conflicting_media=conflicting,
    )
    reuse = build_completed_result_reuse_request(
        TARGETS,
        identity,
        "OFFLINE-REUSE",
        task_id="already-completed-course",
        combination=TaskCombination.PPT_ONLY,
        media=MEDIA,
    )

    assert len({request.json_body["task_id"] for request in repeated}) == 1
    assert {request.json_body["slides_video_path"] for request in repeated} == {
        MEDIA.slides_video_path,
        conflicting.slides_video_path,
    }
    assert reuse.json_body["task_id"] == "already-completed-course"
    assert reuse.work_type == "completed_result_reuse"


def test_append_and_priority_sequences_preserve_business_semantics() -> None:
    identity = ReproducibleIdentity("campaign-001", 42)
    appended = build_append_task_type_sequence(TARGETS, identity, "APPEND-001", MEDIA)
    priorities = build_priority_sequence(
        TARGETS,
        identity,
        "PRIORITY-001",
        MEDIA,
        normal_count=100,
        urgent_count=10,
    )

    assert len({request.json_body["task_id"] for request in appended}) == 1
    assert [request.json_body["task_types"] for request in appended] == [
        ["PPT"],
        ["ASR"],
        ["TEACHER_BEHAVIOR", "STUDENT_BEHAVIOR"],
    ]
    assert all(item.json_body["priority"] == "NORMAL" for item in priorities[:100])
    assert all(item.json_body["priority"] == "URGENT" for item in priorities[100:])


def test_long_course_ladder_estimates_storage_before_each_level() -> None:
    ladder = build_long_course_ladder(per_course_input_bytes=6_000_000_000)

    assert [level.active_courses for level in ladder] == [3, 6, 12, 24, 36]
    assert ladder[-1].estimated_input_bytes == 216_000_000_000
    assert all(level.requires_guardrail_check for level in ladder)


def test_negative_mix_is_reproducible_and_does_not_mutate_normal_requests() -> None:
    identity = ReproducibleIdentity("campaign-001", 42)
    base = build_unique_submission_burst(
        TARGETS,
        identity,
        "NEGATIVE-001",
        100,
        TaskCombination.ALL,
        MEDIA,
        student_count=38,
    )
    mixed = build_negative_submission_mix(base, ratio=0.20, seed=7)
    replay = build_negative_submission_mix(base, ratio=0.20, seed=7)

    assert mixed == replay
    sync_rejections = [request for request in mixed if request.expected_business_rejection]
    async_failures = [
        request for request in mixed if request.expected_task_terminal == "failed"
    ]
    assert len(sync_rejections) == 8
    assert len(async_failures) == 12
    assert all(not request.expected_business_rejection for request in base)
    negative_kinds = {
        request.work_type.partition(":")[2]
        for request in mixed
        if request.work_type.startswith("negative_submission:")
    }
    assert negative_kinds == {kind.value for kind in NegativeSubmissionKind}
    assert "conflicting_task_id" not in negative_kinds
    assert {kind: kind.expectation for kind in NegativeSubmissionKind} == {
        NegativeSubmissionKind.MISSING_REQUIRED_PATH: NegativeSubmissionExpectation.SYNC_REJECTION,
        NegativeSubmissionKind.NOT_FOUND_MEDIA: (
            NegativeSubmissionExpectation.ASYNC_TERMINAL_FAILURE
        ),
        NegativeSubmissionKind.TIMEOUT_MEDIA: NegativeSubmissionExpectation.ASYNC_TERMINAL_FAILURE,
        NegativeSubmissionKind.UNKNOWN_TASK_TYPE: NegativeSubmissionExpectation.SYNC_REJECTION,
        NegativeSubmissionKind.INVALID_REGION: NegativeSubmissionExpectation.ASYNC_TERMINAL_FAILURE,
    }


def test_negative_mix_uses_plan_bound_media_endpoints() -> None:
    identity = ReproducibleIdentity("campaign-endpoints", 42)
    base = build_unique_submission_burst(
        TARGETS,
        identity,
        "NEGATIVE-ENDPOINTS",
        100,
        TaskCombination.ALL,
        MEDIA,
        student_count=38,
    )
    endpoints = NegativeMediaEndpoints(
        not_found_url="http://media.example.test:5555/not-found.mp4",
        timeout_url="http://media.example.test:5556/timeout.mp4",
    )

    mixed = build_negative_submission_mix(
        base,
        ratio=0.20,
        seed=7,
        endpoints=endpoints,
    )

    by_kind = {request.work_type: request for request in mixed}
    assert (
        by_kind["negative_submission:not_found_media"].json_body[
            "slides_video_path"
        ]
        == endpoints.not_found_url
    )
    assert (
        by_kind["negative_submission:timeout_media"].json_body[
            "slides_video_path"
        ]
        == endpoints.timeout_url
    )
    assert by_kind["negative_submission:not_found_media"].json_body["task_types"] == [
        "PPT"
    ]
    assert by_kind["negative_submission:timeout_media"].json_body["task_types"] == [
        "PPT"
    ]
    invalid_region = by_kind["negative_submission:invalid_region"].json_body
    assert invalid_region["task_types"] == ["STUDENT_BEHAVIOR"]
    assert "teacher_video_path" not in invalid_region
    assert "slides_video_path" not in invalid_region
    assert invalid_region["student_video_path"] == MEDIA.student_video_path
    assert invalid_region["student_count"] == 38


@pytest.mark.parametrize(
    "url",
    (
        "file:///tmp/timeout.mp4",
        "http://user:password@media.example.test/timeout.mp4",
        "http://media.example.test/timeout.mp4#fragment",
    ),
)
def test_negative_media_endpoints_reject_unsafe_urls(url: str) -> None:
    with pytest.raises(ValueError, match="HTTP\\(S\\) URL"):
        NegativeMediaEndpoints(timeout_url=url)


def test_media_download_baseline_is_a_descriptor_not_a_real_download() -> None:
    baseline = build_media_download_baseline(MEDIA)

    assert baseline.concurrency_levels == (1, 3, 10, 30)
    assert baseline.urls == MEDIA.urls
    assert baseline.collects_source_and_target_resources is True
    assert "target_inbound_network" in baseline.required_metrics
    sample = MediaDownloadSample(
        url=MEDIA.teacher_video_path,
        concurrency=3,
        size_bytes=1_000,
        connect_seconds=0.1,
        elapsed_seconds=2,
        succeeded=True,
    )
    assert sample.bytes_per_second == 500
