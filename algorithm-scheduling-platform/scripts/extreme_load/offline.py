from __future__ import annotations

import copy
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from .core import HttpRequestSpec, NorthboundTargets, ReproducibleIdentity

ASR_OPTIONS_DEFAULT: dict[str, object] = {
    "language": "auto",
    "showSpk": True,
    "showEmotion": True,
    "showRoleIdentify": False,
    "wordTimestamps": False,
    "hotWords": [],
}

_DEFAULT_NOT_FOUND_MEDIA_URL = "http://192.168.29.12:5555/missing-404.mp4"
_DEFAULT_TIMEOUT_MEDIA_URL = "http://192.168.29.12:5556/timeout.mp4"


class TaskCombination(StrEnum):
    PPT_ONLY = "ppt_only"
    ASR_ONLY = "asr_only"
    TEACHER_ONLY = "teacher_only"
    STUDENT_ONLY = "student_only"
    PPT_ASR = "ppt_asr"
    TEACHER_STUDENT = "teacher_student"
    ALL = "all"

    @property
    def task_types(self) -> tuple[str, ...]:
        return {
            TaskCombination.PPT_ONLY: ("PPT",),
            TaskCombination.ASR_ONLY: ("ASR",),
            TaskCombination.TEACHER_ONLY: ("TEACHER_BEHAVIOR",),
            TaskCombination.STUDENT_ONLY: ("STUDENT_BEHAVIOR",),
            TaskCombination.PPT_ASR: ("PPT", "ASR"),
            TaskCombination.TEACHER_STUDENT: (
                "TEACHER_BEHAVIOR",
                "STUDENT_BEHAVIOR",
            ),
            TaskCombination.ALL: (
                "PPT",
                "ASR",
                "TEACHER_BEHAVIOR",
                "STUDENT_BEHAVIOR",
            ),
        }[self]


@dataclass(frozen=True)
class CourseMedia:
    teacher_video_path: str
    student_video_path: str
    slides_video_path: str

    def __post_init__(self) -> None:
        if not all(
            (parsed := urlsplit(value)).scheme in {"http", "https"} and bool(parsed.netloc)
            for value in self.urls
        ):
            raise ValueError("课程媒体必须是 HTTP/HTTPS URL")

    @property
    def urls(self) -> tuple[str, str, str]:
        return (
            self.teacher_video_path,
            self.student_video_path,
            self.slides_video_path,
        )


def build_submission(
    task_id: str,
    combination: TaskCombination,
    media: CourseMedia,
    priority: str = "NORMAL",
    *,
    front_points: object | None = None,
    back_point: object | None = None,
    student_count: int | None = None,
    asr_options: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if not task_id or len(task_id) > 200:
        raise ValueError("task_id 必须是 1–200 位字符")
    if priority not in {"NORMAL", "URGENT"}:
        raise ValueError("priority 只允许 NORMAL 或 URGENT")
    task_types = combination.task_types
    payload: dict[str, object] = {
        "task_id": task_id,
        "task_types": list(task_types),
        "priority": priority,
    }
    if "PPT" in task_types:
        payload["slides_video_path"] = media.slides_video_path
    if "ASR" in task_types or "TEACHER_BEHAVIOR" in task_types:
        payload["teacher_video_path"] = media.teacher_video_path
    if "ASR" in task_types:
        selected_options = ASR_OPTIONS_DEFAULT if asr_options is None else asr_options
        payload["asr_options"] = copy.deepcopy(dict(selected_options))
    if "STUDENT_BEHAVIOR" in task_types:
        if (
            isinstance(student_count, bool)
            or not isinstance(student_count, int)
            or student_count < 0
        ):
            raise ValueError("学生行为任务需要非负整数 student_count")
        payload["student_video_path"] = media.student_video_path
        payload["student_count"] = student_count
        if front_points is not None:
            payload["front_points"] = front_points
        if back_point is not None:
            payload["back_point"] = back_point
    return payload


def _submission_request(
    targets: NorthboundTargets,
    identity: ReproducibleIdentity,
    case_id: str,
    index: int,
    payload: Mapping[str, object],
) -> HttpRequestSpec:
    return HttpRequestSpec(
        request_id=identity.request_id(case_id, index),
        method="POST",
        url=targets.control_url("/api/course-jobs"),
        json_body=payload,
        headers={"X-Trace-ID": identity.trace_id(case_id, index)},
        work_type="course_submission",
    )


def build_unique_submission_burst(
    targets: NorthboundTargets,
    identity: ReproducibleIdentity,
    case_id: str,
    count: int,
    combination: TaskCombination,
    media: CourseMedia,
    *,
    priority: str = "NORMAL",
    student_count: int | None = None,
) -> tuple[HttpRequestSpec, ...]:
    if count <= 0:
        raise ValueError("count 必须为正数")
    return tuple(
        _submission_request(
            targets,
            identity,
            case_id,
            index,
            build_submission(
                identity.task_id(case_id, index),
                combination,
                media,
                priority,
                student_count=student_count,
            ),
        )
        for index in range(count)
    )


def build_idempotent_burst(
    targets: NorthboundTargets,
    identity: ReproducibleIdentity,
    case_id: str,
    count: int,
    combination: TaskCombination,
    media: CourseMedia,
    *,
    student_count: int | None = None,
    conflicting_media: CourseMedia | None = None,
) -> tuple[HttpRequestSpec, ...]:
    if count <= 0:
        raise ValueError("count 必须为正数")
    task_id = identity.task_id(case_id, 0)
    requests: list[HttpRequestSpec] = []
    for index in range(count):
        selected_media = conflicting_media if conflicting_media and index % 2 else media
        payload = build_submission(
            task_id,
            combination,
            selected_media,
            student_count=student_count,
        )
        requests.append(_submission_request(targets, identity, case_id, index, payload))
    return tuple(requests)


def build_completed_result_reuse_request(
    targets: NorthboundTargets,
    identity: ReproducibleIdentity,
    case_id: str,
    *,
    task_id: str,
    combination: TaskCombination,
    media: CourseMedia,
    student_count: int | None = None,
) -> HttpRequestSpec:
    request = _submission_request(
        targets,
        identity,
        case_id,
        0,
        build_submission(
            task_id,
            combination,
            media,
            student_count=student_count,
        ),
    )
    return replace(request, work_type="completed_result_reuse")


def build_append_task_type_sequence(
    targets: NorthboundTargets,
    identity: ReproducibleIdentity,
    case_id: str,
    media: CourseMedia,
    *,
    student_count: int = 0,
) -> tuple[HttpRequestSpec, ...]:
    task_id = identity.task_id(case_id, 0)
    combinations = (
        TaskCombination.PPT_ONLY,
        TaskCombination.ASR_ONLY,
        TaskCombination.TEACHER_STUDENT,
    )
    return tuple(
        _submission_request(
            targets,
            identity,
            case_id,
            index,
            build_submission(
                task_id,
                combination,
                media,
                student_count=(
                    student_count if combination is TaskCombination.TEACHER_STUDENT else None
                ),
            ),
        )
        for index, combination in enumerate(combinations)
    )


def build_priority_sequence(
    targets: NorthboundTargets,
    identity: ReproducibleIdentity,
    case_id: str,
    media: CourseMedia,
    *,
    normal_count: int,
    urgent_count: int,
) -> tuple[HttpRequestSpec, ...]:
    if normal_count <= 0 or urgent_count <= 0:
        raise ValueError("优先级负载数量必须为正数")
    result: list[HttpRequestSpec] = []
    for index in range(normal_count + urgent_count):
        priority = "NORMAL" if index < normal_count else "URGENT"
        payload = build_submission(
            identity.task_id(case_id, index),
            TaskCombination.PPT_ONLY,
            media,
            priority,
        )
        result.append(_submission_request(targets, identity, case_id, index, payload))
    return tuple(result)


@dataclass(frozen=True)
class LongCourseLevel:
    active_courses: int
    estimated_input_bytes: int
    requires_guardrail_check: bool = True
    temporary_course_root: str = "/data/course"
    persistent_result_root: str = "/data/result"
    records_storage_growth: bool = True


def build_long_course_ladder(per_course_input_bytes: int) -> tuple[LongCourseLevel, ...]:
    if per_course_input_bytes <= 0:
        raise ValueError("单课程输入大小必须为正数")
    return tuple(
        LongCourseLevel(
            active_courses=count,
            estimated_input_bytes=count * per_course_input_bytes,
        )
        for count in (3, 6, 12, 24, 36)
    )


class NegativeSubmissionExpectation(StrEnum):
    SYNC_REJECTION = "sync_rejection"
    ASYNC_TERMINAL_FAILURE = "async_terminal_failure"


class NegativeSubmissionKind(StrEnum):
    MISSING_REQUIRED_PATH = "missing_required_path"
    NOT_FOUND_MEDIA = "not_found_media"
    TIMEOUT_MEDIA = "timeout_media"
    UNKNOWN_TASK_TYPE = "unknown_task_type"
    INVALID_REGION = "invalid_region"

    @property
    def expectation(self) -> NegativeSubmissionExpectation:
        if self in {
            NegativeSubmissionKind.MISSING_REQUIRED_PATH,
            NegativeSubmissionKind.UNKNOWN_TASK_TYPE,
        }:
            return NegativeSubmissionExpectation.SYNC_REJECTION
        return NegativeSubmissionExpectation.ASYNC_TERMINAL_FAILURE


@dataclass(frozen=True)
class NegativeMediaEndpoints:
    not_found_url: str = _DEFAULT_NOT_FOUND_MEDIA_URL
    timeout_url: str = _DEFAULT_TIMEOUT_MEDIA_URL

    def __post_init__(self) -> None:
        for field_name, value in (
            ("not_found_url", self.not_found_url),
            ("timeout_url", self.timeout_url),
        ):
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.fragment
            ):
                raise ValueError(f"{field_name} 必须是无凭据、无 fragment 的 HTTP(S) URL")


def _first_media_field(payload: Mapping[str, Any]) -> str | None:
    return next(
        (
            field_name
            for field_name in (
                "slides_video_path",
                "teacher_video_path",
                "student_video_path",
            )
            if field_name in payload
        ),
        None,
    )


def _negative_payload(
    payload: Mapping[str, Any],
    kind: NegativeSubmissionKind,
    endpoints: NegativeMediaEndpoints,
) -> dict[str, Any]:
    mutated = copy.deepcopy(dict(payload))
    media_field = _first_media_field(mutated)
    if kind is NegativeSubmissionKind.MISSING_REQUIRED_PATH:
        if media_field is not None:
            mutated.pop(media_field)
    elif kind is NegativeSubmissionKind.NOT_FOUND_MEDIA:
        mutated = {
            key: mutated[key]
            for key in ("task_id", "priority")
            if key in mutated
        }
        mutated.update(
            {
                "task_types": ["PPT"],
                "slides_video_path": endpoints.not_found_url,
            }
        )
    elif kind is NegativeSubmissionKind.TIMEOUT_MEDIA:
        mutated = {
            key: mutated[key]
            for key in ("task_id", "priority")
            if key in mutated
        }
        mutated.update(
            {
                "task_types": ["PPT"],
                "slides_video_path": endpoints.timeout_url,
            }
        )
    elif kind is NegativeSubmissionKind.UNKNOWN_TASK_TYPE:
        mutated["task_types"] = ["UNKNOWN_TASK_TYPE"]
    elif kind is NegativeSubmissionKind.INVALID_REGION:
        mutated = {
            key: mutated[key]
            for key in (
                "task_id",
                "priority",
                "student_video_path",
                "student_count",
                "back_point",
            )
            if key in mutated
        }
        mutated["task_types"] = ["STUDENT_BEHAVIOR"]
        mutated.setdefault(
            "student_video_path",
            "http://192.168.29.12:5555/course/S.mp4",
        )
        mutated.setdefault("student_count", 1)
        mutated["front_points"] = [{"X": -1, "Y": "invalid"}]
    else:
        raise AssertionError(f"未处理的负向提交类型: {kind.value}")
    return mutated


def build_negative_submission_mix(
    requests: Sequence[HttpRequestSpec],
    *,
    ratio: float,
    seed: int,
    endpoints: NegativeMediaEndpoints | None = None,
) -> tuple[HttpRequestSpec, ...]:
    if ratio not in {0.01, 0.05, 0.20}:
        raise ValueError("负向比例只允许 1%、5% 或 20%")
    if not requests:
        raise ValueError("负向混合至少需要一个正常请求")
    count = round(len(requests) * ratio)
    if count == 0:
        count = 1
    selected_positions = random.Random(seed).sample(range(len(requests)), count)
    selected = {
        request_index: list(NegativeSubmissionKind)[variant_index % len(NegativeSubmissionKind)]
        for variant_index, request_index in enumerate(selected_positions)
    }
    selected_endpoints = endpoints or NegativeMediaEndpoints()
    result: list[HttpRequestSpec] = []
    for index, request in enumerate(requests):
        if index not in selected:
            result.append(request)
            continue
        assert request.json_body is not None
        kind = selected[index]
        expectation = kind.expectation
        result.append(
            replace(
                request,
                json_body=_negative_payload(request.json_body, kind, selected_endpoints),
                work_type=f"negative_submission:{kind.value}",
                expected_business_rejection=(
                    expectation is NegativeSubmissionExpectation.SYNC_REJECTION
                ),
                expected_task_terminal=(
                    "failed"
                    if expectation
                    is NegativeSubmissionExpectation.ASYNC_TERMINAL_FAILURE
                    else None
                ),
            )
        )
    return tuple(result)


@dataclass(frozen=True)
class MediaDownloadBaseline:
    urls: tuple[str, str, str]
    concurrency_levels: tuple[int, ...] = (1, 3, 10, 30)
    collects_source_and_target_resources: bool = True
    required_metrics: tuple[str, ...] = (
        "single_file_bytes_per_second",
        "aggregate_bytes_per_second",
        "connect_seconds",
        "failure_rate",
        "source_service_resources",
        "target_inbound_network",
    )


@dataclass(frozen=True)
class MediaDownloadSample:
    url: str
    concurrency: int
    size_bytes: int
    connect_seconds: float
    elapsed_seconds: float
    succeeded: bool

    def __post_init__(self) -> None:
        if self.concurrency not in {1, 3, 10, 30}:
            raise ValueError("媒体下载基线并发只允许 1/3/10/30")
        if self.size_bytes < 0 or self.connect_seconds < 0 or self.elapsed_seconds <= 0:
            raise ValueError("媒体下载样本指标不合法")

    @property
    def bytes_per_second(self) -> float:
        return self.size_bytes / self.elapsed_seconds


def build_media_download_baseline(media: CourseMedia) -> MediaDownloadBaseline:
    return MediaDownloadBaseline(urls=media.urls)
