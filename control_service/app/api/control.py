from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from secrets import compare_digest
from typing import Annotated, Any, Literal, Protocol, cast
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from packages.platform_common.application import create_service_app
from packages.platform_common.config import PlatformSettings
from packages.platform_common.operator_audit_repository import OperatorInstanceEvent
from packages.platform_common.operator_operations import (
    OperatorCapacitySnapshot,
    OperatorOperationsRegistry,
    build_operator_capacity_snapshot,
)
from packages.platform_common.operator_registry import (
    ActiveCapacityLease,
    CapacityLease,
    CapacityLeaseContextConflictError,
    CapacityLeaseNotFoundError,
    CapacityUnavailableError,
    OperatorActiveLeases,
    OperatorCode,
    OperatorInstance,
    OperatorInstanceNotFoundError,
    OperatorLifecycle,
    OperatorRegistry,
    WorkContext,
)
from packages.platform_common.repository import (
    AsrRunRecord,
    CourseJobSummary,
    NodeRecord,
    OperationsQueueSnapshot,
    OutboxEventRecord,
    TaskTypeRecord,
    TaskTypeWrite,
)
from packages.platform_contracts.asr import asr_params_fingerprint
from packages.platform_contracts.responses import BusinessCode, BusinessResponse
from packages.platform_contracts.status import (
    NodeStatus,
    Priority,
    TaskType,
    status_text,
)
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from ..infrastructure.audited_operator_registry import canonical_operator_origin
from ..infrastructure.runtime import ControlReadinessChecker, ControlRuntime

logger = logging.getLogger(__name__)


class CourseTaskRepository(Protocol):
    def create_task_types(
        self,
        *,
        task_id: str,
        writes: list[TaskTypeWrite],
        input_snapshot: dict[str, Any] | None = None,
    ) -> list[TaskTypeRecord]: ...

    def list_task_types(self, task_id: str) -> list[TaskTypeRecord]: ...

    def list_course_jobs(
        self,
        *,
        offset: int,
        limit: int,
        sort_by: str,
        descending: bool,
        task_types: tuple[TaskType, ...] = (),
        overall_status: NodeStatus | None = None,
        task_status_type: TaskType | None = None,
        task_status: NodeStatus | None = None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
        task_id_like: str | None = None,
    ) -> tuple[list[CourseJobSummary], int]: ...

    def get_course_job_summary(self, task_id: str) -> CourseJobSummary | None: ...

    def list_nodes(
        self,
        course_task_type_id: int,
        run_id: Any | None = None,
    ) -> list[NodeRecord]: ...

    def operations_queue_snapshot(self) -> OperationsQueueSnapshot: ...

    def list_asr_runs(self, course_task_type_id: int) -> list[AsrRunRecord]: ...

    def list_outbox_events(
        self,
        *,
        offset: int,
        limit: int,
        task_id: str | None = None,
        task_id_like: str | None = None,
        event_type: str | None = None,
        publish_status: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        descending: bool = True,
    ) -> tuple[list[OutboxEventRecord], int]: ...

    def get_outbox_event(self, event_id: UUID) -> OutboxEventRecord | None: ...


class CourseJobSubmission(BaseModel):
    model_config = ConfigDict(extra="allow")

    task_id: str = Field(min_length=1, max_length=200)
    task_types: list[TaskType] = Field(min_length=1)
    priority: Priority = Priority.NORMAL
    teacher_video_path: Any | None = None
    student_video_path: Any | None = None
    slides_video_path: Any | None = None
    front_points: Any | None = None
    back_point: Any | None = None
    student_count: Any | None = None
    asr_options: Any | None = None


class AsrOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str = "auto"
    showSpk: bool = False
    showEmotion: bool = False
    showRoleIdentify: bool = False
    wordTimestamps: bool = False
    hotWords: list[str] = Field(default_factory=list)


class OperatorRegistrationRequest(BaseModel):
    instance_id: str = Field(min_length=1)
    operator_code: OperatorCode
    capabilities: list[str] = Field(min_length=1)
    service_url: str = Field(pattern=r"^https?://")
    model_version: str | None = None
    api_version: str | None = None
    declared_capacity: Annotated[StrictInt, Field(gt=0)]
    labels: dict[str, str] = Field(default_factory=dict)
    capacity_pools: dict[str, StrictInt] = Field(default_factory=dict)


class OperatorHeartbeatRequest(BaseModel):
    instance_id: str = Field(min_length=1)
    inflight: int = Field(ge=0)
    model_ready: bool
    inflight_by_pool: dict[str, int] = Field(default_factory=dict)


class OperatorUnregisterRequest(BaseModel):
    instance_id: str = Field(min_length=1)


class OperatorLifecycleRequest(BaseModel):
    instance_id: str = Field(min_length=1)
    lifecycle: OperatorLifecycle


class WorkContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_service: str = Field(min_length=1, max_length=200)
    work_type: str = Field(min_length=1, max_length=200)
    work_id: str = Field(min_length=1, max_length=200)
    task_id: str | None = Field(default=None, min_length=1, max_length=200)
    node_id: str | None = Field(default=None, min_length=1, max_length=200)
    item_id: str | None = Field(default=None, min_length=1, max_length=200)
    trace_id: str | None = Field(default=None, min_length=1, max_length=200)
    capacity_pool: str = Field(default="default", min_length=1, max_length=200)

    def to_domain(self) -> WorkContext:
        return WorkContext(**self.model_dump(exclude_none=True))


class CapacityLeaseRequest(BaseModel):
    capability: str = Field(min_length=1)
    ttl_seconds: int = Field(default=60, gt=0, le=3600)
    work_context: WorkContextRequest | None = None
    capacity_pool: str = Field(default="default", min_length=1, max_length=200)


class CapacityLeaseContextRequest(BaseModel):
    lease_id: str = Field(min_length=1)
    work_context: WorkContextRequest


class CapacityReleaseRequest(BaseModel):
    lease_id: str = Field(min_length=1)


class CapacityLeaseRenewRequest(BaseModel):
    lease_id: str = Field(min_length=1)
    ttl_seconds: int = Field(default=60, gt=0, le=3600)


class SubmissionValidationError(ValueError):
    pass


REGISTRY_INFRASTRUCTURE_ERRORS = (RuntimeError, RedisError, SQLAlchemyError)


def _kafka_metric_values(text: str) -> tuple[int, int, int]:
    published = 0
    publish_failed = 0
    consumer_lag = 0
    metric_pattern = re.compile(r"^([\w:]+)(?:\{([^}]*)\})?\s+([-+\d.eE]+)$")
    for line in text.splitlines():
        match = metric_pattern.match(line)
        if match is None:
            continue
        metric, raw_labels, raw_value = match.groups()
        try:
            value = int(float(raw_value))
        except ValueError:
            continue
        labels = dict(re.findall(r'(\w+)="((?:\\.|[^"])*)"', raw_labels or ""))
        if metric == "algorithm_outbox_publish_total" and labels.get("outcome") in {"published", "success"}:
            published += value
        elif metric == "algorithm_outbox_publish_total" and labels.get("outcome") in {"failed", "error"}:
            publish_failed += value
        elif metric == "algorithm_kafka_consumer_lag":
            consumer_lag += value
    return published, publish_failed, consumer_lag


def _task_database_error() -> BusinessResponse[dict[str, Any]]:
    return BusinessResponse[dict[str, Any]].failure(
        BusinessCode.INTERNAL_ERROR,
        "任务数据库暂不可用",
    )


def _registry_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="算子注册中心暂不可用")


def _authorize_operator_management(request: Request, expected_token: str) -> None:
    supplied_token = request.headers.get("X-Operator-Registry-Token", "")
    if not compare_digest(supplied_token, expected_token):
        raise HTTPException(status_code=401, detail="算子注册管理认证失败")


def _authorize_operator_origin(
    *,
    instance_id: str,
    service_url: str,
    trusted_service_urls: dict[str, str],
) -> None:
    trusted_url = trusted_service_urls.get(instance_id)
    try:
        supplied_origin = canonical_operator_origin(service_url)
        trusted_origin = (
            None if trusted_url is None else canonical_operator_origin(trusted_url)
        )
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="算子服务地址不在可信配置中") from exc
    if trusted_origin is None or supplied_origin != trusted_origin:
        raise HTTPException(status_code=403, detail="算子服务地址不在可信配置中")


def _course_repository(request: Request) -> CourseTaskRepository:
    runtime = cast(ControlRuntime, request.app.state.control_runtime)
    if runtime.repository is None:
        raise HTTPException(status_code=503, detail="任务数据库尚未就绪")
    return cast(CourseTaskRepository, runtime.repository)


def _operator_registry(request: Request) -> OperatorRegistry:
    runtime = cast(ControlRuntime, request.app.state.control_runtime)
    if runtime.operator_registry is None:
        raise HTTPException(status_code=503, detail="算子注册中心尚未就绪")
    return cast(OperatorRegistry, runtime.operator_registry)


def _readiness_checker(request: Request) -> ControlReadinessChecker:
    runtime = cast(ControlRuntime, request.app.state.control_runtime)
    checker = runtime.readiness_checker
    if checker is None:
        checker = ControlReadinessChecker(runtime.engine, runtime.redis_client)
    return checker


def _operator_events(
    request: Request,
    instance_id: str,
    *,
    limit: int,
) -> list[OperatorInstanceEvent]:
    runtime = cast(ControlRuntime, request.app.state.control_runtime)
    if runtime.audit_repository is None:
        raise HTTPException(status_code=503, detail="算子审计数据库尚未就绪")
    return runtime.audit_repository.list_events(instance_id, limit=limit)


def _required_url(submission: CourseJobSubmission, field_name: str) -> str:
    value = getattr(submission, field_name)
    if not isinstance(value, str):
        raise SubmissionValidationError(f"所选任务缺少有效的 {field_name}")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SubmissionValidationError(f"{field_name} 必须是有效的 HTTP/HTTPS 地址")
    return value


def _student_count(submission: CourseJobSubmission) -> int:
    value = submission.student_count
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SubmissionValidationError("STUDENT_BEHAVIOR 任务需要非负整数 student_count")
    return value


def _task_write(submission: CourseJobSubmission, task_type: TaskType) -> TaskTypeWrite:
    payload: dict[str, Any]
    effective_params: dict[str, Any] | None = None
    if task_type is TaskType.PPT:
        payload = {"slides_video_path": _required_url(submission, "slides_video_path")}
    elif task_type is TaskType.ASR:
        payload = {"teacher_video_path": _required_url(submission, "teacher_video_path")}
        try:
            effective_params = AsrOptions.model_validate(
                submission.asr_options if submission.asr_options is not None else {}
            ).model_dump()
        except ValidationError as exc:
            field_name = ".".join(str(part) for part in exc.errors()[0].get("loc", []))
            raise SubmissionValidationError(
                f"asr_options 参数不合法: {field_name or '对象格式错误'}"
            ) from exc
    elif task_type is TaskType.TEACHER_BEHAVIOR:
        payload = {"teacher_video_path": _required_url(submission, "teacher_video_path")}
    else:
        payload = {
            "student_video_path": _required_url(submission, "student_video_path"),
            "student_count": _student_count(submission),
        }
        if submission.front_points is not None:
            payload["front_points"] = submission.front_points
        if submission.back_point is not None:
            payload["back_point"] = submission.back_point

    return TaskTypeWrite(
        task_type=task_type,
        priority=submission.priority,
        request_payload=payload,
        effective_params=effective_params,
        params_fingerprint=(
            asr_params_fingerprint(effective_params)
            if task_type is TaskType.ASR and effective_params is not None
            else None
        ),
    )


def _task_response(record: TaskTypeRecord) -> dict[str, Any]:
    return {
        "task_type": record.task_type.value,
        "status": record.status.value,
        "status_text": status_text(record.status),
        "reason": record.reason,
        "priority": record.priority.value,
        "created": record.created,
        "updated_at": record.updated_at,
        "run_id": str(record.run_id) if record.run_id is not None else None,
        "params_fingerprint": record.params_fingerprint,
        "effective_params": record.effective_params,
    }


def _elapsed_ms(started_at: datetime | None, finished_at: datetime | None) -> int | None:
    if started_at is None or finished_at is None:
        return None
    return max(0, round((finished_at - started_at).total_seconds() * 1000))


def _result_summary(record: NodeRecord) -> dict[str, Any]:
    result = record.result if isinstance(record.result, dict) else {}
    progress = record.progress or {}
    summary: dict[str, Any] = {
        "completed_count": progress.get("completed_count"),
        "total_count": progress.get("total_count"),
    }
    if record.node_code == "PPT_SLICE":
        images = result.get("images") if isinstance(result.get("images"), list) else []
        segments = (
            result.get("dynamic_segments")
            if isinstance(result.get("dynamic_segments"), list)
            else []
        )
        summary.update(
            {
                "slice_count": record.artifact_count
                if record.artifact_count is not None
                else len(images),
                "dynamic_segment_count": len(segments),
                "dynamic_duration_ms": sum(
                    max(0, int(item.get("end_ms", 0)) - int(item.get("start_ms", 0)))
                    for item in segments
                    if isinstance(item, dict)
                ),
            }
        )
    elif record.node_code == "PPT_OCR":
        pages = [item for item in result.values() if isinstance(item, dict)]
        success = sum(1 for item in pages if str(item.get("text", "")).strip())
        failed = sum(1 for item in pages if item.get("error"))
        summary.update(
            {
                "page_count": len(pages),
                "success_count": success,
                "empty_count": max(0, len(pages) - success - failed),
                "failed_count": failed,
            }
        )
    elif record.node_code == "ASR_TRANSCRIPTION":
        segments = result.get("segments") if isinstance(result.get("segments"), list) else []
        duration_seconds = 0.0
        for item in segments:
            if not isinstance(item, dict):
                continue
            try:
                duration_seconds = max(duration_seconds, float(item.get("ed", 0)))
            except (TypeError, ValueError):
                continue
        summary.update(
            {
                "audio_duration_seconds": duration_seconds or None,
                "language": result.get("language"),
                "segment_count": len(segments),
                "text_length": len(str(result.get("text", ""))),
                "load_audio_time_ms": result.get("load_audio_time_ms"),
                "gpu_time_ms": result.get("gpu_time_ms"),
            }
        )
    elif record.node_code == "TEACHER_BEHAVIOR_ANALYSIS":
        summary.update(
            {
                "duration_seconds": result.get("duration_seconds"),
                "analysis_quality": result.get("analysis_quality"),
                "total_frame_count": result.get("total_frame_count"),
                "valid_frame_count": result.get("valid_frame_count"),
                "valid_frame_ratio": result.get("valid_frame_ratio"),
                "writing_interval_count": len(result.get("writing_intervals") or []),
                "sitting_interval_count": len(result.get("sitting_intervals") or []),
                "standing_interval_count": len(result.get("standing_intervals") or []),
                "teaching_interval_count": len(result.get("teaching_intervals") or []),
                "evidence_count": len(result.get("evidence") or []),
            }
        )
    elif record.node_code == "STUDENT_BEHAVIOR_ANALYSIS":
        summary.update(
            {
                "duration_seconds": result.get("duration_seconds"),
                "student_count": result.get("student_count"),
                "stable_person_count": result.get("stable_person_count"),
                "recognized_total_person_count": result.get(
                    "recognized_total_person_count"
                ),
                "attendance_rate": result.get("attendance_rate"),
                "front_occupancy_ratio": result.get("front_occupancy_ratio"),
                "back_occupancy_ratio": result.get("back_occupancy_ratio"),
                "sample_interval_seconds": result.get("sample_interval_seconds"),
                "frame_count": len(result.get("frames") or []),
                "evidence_count": len(result.get("evidence") or []),
            }
        )
    return summary


def _node_response(record: NodeRecord, *, include_result: bool = True) -> dict[str, Any]:
    response: dict[str, Any] = {
        "node_code": record.node_code,
        "status": record.status.value,
        "status_text": status_text(record.status),
        "reason": record.reason,
        "priority": record.priority.value,
        "required_capability": record.required_capability,
        "progress": record.progress,
        "result_summary": _result_summary(record),
        "effective_params": record.effective_params,
        "ready_at": record.ready_at,
        "claimed_at": record.claimed_at,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "updated_at": record.updated_at,
        "queue_wait_ms": _elapsed_ms(record.ready_at, record.claimed_at),
        "startup_ms": _elapsed_ms(record.claimed_at, record.started_at),
        "processing_duration_ms": _elapsed_ms(record.started_at, record.finished_at),
        "total_duration_ms": _elapsed_ms(record.ready_at, record.finished_at),
        "run_id": str(record.run_id) if record.run_id is not None else None,
    }
    if record.artifact_path is not None:
        response["path"] = record.artifact_path
    if record.artifact_count is not None:
        response["count"] = record.artifact_count
    if include_result and record.result is not None:
        response["result"] = record.result
    return response


def _task_summary_response(
    record: TaskTypeRecord,
    nodes: list[NodeRecord],
) -> dict[str, Any]:
    response = _task_response(record)
    response["nodes"] = [
        _node_response(node, include_result=False)
        for node in nodes
    ]
    return response


def _outbox_status(record: OutboxEventRecord) -> str:
    return record.publish_status


def _outbox_response(
    record: OutboxEventRecord,
    *,
    include_payload: bool,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "event_id": str(record.event_id),
        "aggregate_type": record.aggregate_type,
        "aggregate_id": record.aggregate_id,
        "event_type": record.event_type,
        "task_id": record.payload.get("task_id"),
        "task_type": record.payload.get("task_type"),
        "publish_status": _outbox_status(record),
        "available_at": record.available_at,
        "claimed_at": record.claimed_at,
        "published_at": record.published_at,
        "publish_attempts": record.publish_attempts,
        "last_error": record.last_error,
        "created_at": record.created_at,
    }
    if include_payload:
        response["payload"] = _safe_event_payload(record.payload)
    return response


def _safe_event_payload(value: Any) -> Any:
    blocked_keys = {
        "claim_token",
        "password",
        "secret",
        "token",
        "authorization",
        "base64",
        "image",
        "audio",
        "video",
        "media_bytes",
    }
    if isinstance(value, dict):
        return {
            str(key): _safe_event_payload(item)
            for key, item in value.items()
            if str(key).lower() not in blocked_keys
        }
    if isinstance(value, list):
        return [_safe_event_payload(item) for item in value]
    return value


def _paged_result(items: list[Any], *, page: int, page_size: int) -> dict[str, Any]:
    total = len(items)
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
    }


def _result_section(
    node: NodeRecord,
    *,
    section: str,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    result = node.result if isinstance(node.result, dict) else {}
    if section == "summary":
        return {"value": _result_summary(node)}
    if section == "parameters":
        return {"value": node.effective_params or {}}
    if section == "dynamic_segments":
        items = result.get("dynamic_segments")
    elif section == "ocr_pages":
        items = [
            {"page": key, **value}
            for key, value in sorted(result.items(), key=lambda item: str(item[0]))
            if isinstance(value, dict)
        ]
    elif section == "transcript":
        return {"value": result.get("text")}
    elif section == "segments":
        items = result.get("segments")
    elif section == "speed_info":
        items = result.get("speed_info")
    elif section == "behavior_intervals":
        items = [
            {"kind": key.removesuffix("_intervals"), **interval}
            for key, intervals in result.items()
            if key.endswith("_intervals") and isinstance(intervals, list)
            for interval in intervals
            if isinstance(interval, dict)
        ]
    elif section in {"frames", "evidence"}:
        items = result.get(section)
    elif section == "scan":
        return {"value": result.get("scan") or {}}
    else:
        raise ValueError(f"不支持的结果区块: {section}")
    if not isinstance(items, list):
        items = []
    return _paged_result(items, page=page, page_size=page_size)


def _queried_task_response(
    record: TaskTypeRecord,
    nodes: list[NodeRecord],
    runs: list[AsrRunRecord] | None = None,
) -> dict[str, Any]:
    response = _task_response(record)
    response["effective_params"] = record.effective_params
    response["nodes"] = [_node_response(node) for node in nodes]
    if runs is not None:
        response["runs"] = [
            {
                "run_id": str(run.run_id),
                "params_fingerprint": run.params_fingerprint,
                "effective_params": run.effective_params,
                "status": run.status.value,
                "status_text": status_text(run.status),
                "reason": run.reason,
                "result": run.result,
                "created_at": run.created_at,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
            }
            for run in runs
        ]
    return response


def _unrequested_task_response(task_type: TaskType) -> dict[str, Any]:
    return {
        "task_type": task_type.value,
        "status": 0,
        "status_text": status_text(0),
        "reason": "未请求该任务",
        "priority": None,
        "effective_params": None,
        "nodes": [],
        "updated_at": None,
    }


def _course_job_status(summary: CourseJobSummary) -> NodeStatus:
    statuses = [item.status for item in summary.task_types]
    if not statuses:
        return NodeStatus.UNREQUESTED
    if NodeStatus.FAILED in statuses:
        return NodeStatus.FAILED
    if NodeStatus.CANCELLED in statuses:
        return NodeStatus.CANCELLED
    active = [item for item in statuses if item not in {NodeStatus.COMPLETED}]
    if active:
        if NodeStatus.RUNNING in active:
            return NodeStatus.RUNNING
        return max(active)
    return NodeStatus.COMPLETED


def _course_job_summary_response(summary: CourseJobSummary) -> dict[str, Any]:
    status = _course_job_status(summary)
    return {
        "task_id": summary.task_id,
        "created_at": summary.created_at,
        "updated_at": summary.updated_at,
        "status": status.value,
        "status_text": status_text(status),
        "task_count": len(summary.task_types),
        "tasks": [
            {
                "task_type": item.task_type.value,
                "status": item.status.value,
                "status_text": status_text(item.status),
                "priority": item.priority.value,
                "updated_at": item.updated_at,
            }
            for item in summary.task_types
        ],
    }


def _directory_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _storage_root_response(
    root: Path,
    kind: str,
    *,
    include_directory_bytes: bool,
) -> dict[str, Any]:
    usage = shutil.disk_usage(root)
    return {
        "kind": kind,
        "path": str(root),
        "directory_bytes": _directory_bytes(root) if include_directory_bytes else None,
        "filesystem": {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
        },
    }


def create_control_app(
    *,
    repository: CourseTaskRepository | None = None,
    operator_registry: OperatorRegistry | None = None,
    settings: PlatformSettings | None = None,
    enabled_task_types: set[TaskType] | None = None,
    runtime: ControlRuntime | None = None,
) -> FastAPI:
    resolved = settings or PlatformSettings(service_name="control-service")
    resolved_enabled_task_types = (
        set(TaskType) if enabled_task_types is None else set(enabled_task_types)
    )
    resolved_runtime = runtime or ControlRuntime.from_platform_settings(
        resolved,
        repository=repository,
        operator_registry=operator_registry,
    )
    app = create_service_app(resolved, service_lifespan=resolved_runtime.lifespan)
    resolved_runtime.attach(app)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        if not request.url.path.startswith("/api/course-jobs"):
            return JSONResponse(
                status_code=422,
                content={"detail": jsonable_encoder(exc.errors())},
            )
        first_error = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first_error.get("loc", []))
        message = f"请求参数校验失败: {location or '请求体'}"
        body = BusinessResponse[dict[str, Any]].failure(
            BusinessCode.VALIDATION_ERROR,
            message,
        )
        return JSONResponse(status_code=200, content=body.model_dump(mode="json"))

    @app.post("/api/course-jobs")
    def submit_course_job(
        submission: CourseJobSubmission,
        request: Request,
    ) -> BusinessResponse[dict[str, Any]]:
        try:
            selected_types = list(dict.fromkeys(submission.task_types))
            disabled_types = [
                task_type
                for task_type in selected_types
                if task_type not in resolved_enabled_task_types
            ]
            if disabled_types:
                disabled = ", ".join(task_type.value for task_type in disabled_types)
                raise SubmissionValidationError(f"任务类型未启用: {disabled}")
            writes = [_task_write(submission, task_type) for task_type in selected_types]
        except SubmissionValidationError as exc:
            return BusinessResponse[dict[str, Any]].failure(
                BusinessCode.VALIDATION_ERROR,
                str(exc),
            )

        try:
            records = _course_repository(request).create_task_types(
                task_id=submission.task_id,
                writes=writes,
                input_snapshot=submission.model_extra or {},
            )
        except (RuntimeError, SQLAlchemyError):
            return _task_database_error()
        return BusinessResponse[dict[str, Any]].success(
            {
                "task_id": submission.task_id,
                "tasks": [_task_response(record) for record in records],
            },
            message="课程任务已接收",
        )

    @app.get("/api/course-jobs/{task_id}")
    def get_course_job(
        task_id: str,
        request: Request,
    ) -> BusinessResponse[dict[str, Any]]:
        repository = _course_repository(request)
        try:
            records = repository.list_task_types(task_id)
        except (RuntimeError, SQLAlchemyError):
            return _task_database_error()
        if not records:
            return BusinessResponse[dict[str, Any]].failure(
                BusinessCode.NOT_FOUND,
                f"未找到课程任务: {task_id}",
            )

        records_by_type = {record.task_type: record for record in records}
        tasks: list[dict[str, Any]] = []
        for task_type in TaskType:
            record = records_by_type.get(task_type)
            if record is None:
                tasks.append(_unrequested_task_response(task_type))
            else:
                try:
                    nodes = (
                        repository.list_nodes(record.id)
                        if record.run_id is None
                        else repository.list_nodes(record.id, record.run_id)
                    )
                    runs = (
                        repository.list_asr_runs(record.id)
                        if record.task_type is TaskType.ASR
                        else None
                    )
                except (RuntimeError, SQLAlchemyError):
                    return _task_database_error()
                tasks.append(_queried_task_response(record, nodes, runs))
        return BusinessResponse[dict[str, Any]].success(
            {"task_id": task_id, "tasks": tasks},
            message="课程任务查询成功",
        )

    @app.post("/api/operator-instances/register", status_code=status.HTTP_201_CREATED)
    def register_operator(
        payload: OperatorRegistrationRequest,
        request: Request,
    ) -> OperatorInstance:
        _authorize_operator_management(request, resolved.operator_registry_token)
        _authorize_operator_origin(
            instance_id=payload.instance_id,
            service_url=payload.service_url,
            trusted_service_urls=resolved.trusted_operator_service_urls,
        )
        try:
            return _operator_registry(request).register(
                OperatorInstance(
                    instance_id=payload.instance_id,
                    operator_code=payload.operator_code,
                    capabilities=payload.capabilities,
                    service_url=payload.service_url,
                    model_version=payload.model_version,
                    api_version=payload.api_version,
                    declared_capacity=payload.declared_capacity,
                    labels=payload.labels,
                    capacity_pools=payload.capacity_pools,
                )
            )
        except REGISTRY_INFRASTRUCTURE_ERRORS as exc:
            raise _registry_unavailable() from exc

    @app.post("/api/operator-instances/heartbeat")
    def heartbeat_operator(
        payload: OperatorHeartbeatRequest,
        request: Request,
    ) -> OperatorInstance:
        _authorize_operator_management(request, resolved.operator_registry_token)
        try:
            return _operator_registry(request).heartbeat(
                payload.instance_id,
                inflight=payload.inflight,
                model_ready=payload.model_ready,
                inflight_by_pool=payload.inflight_by_pool,
            )
        except OperatorInstanceNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"算子实例不存在: {payload.instance_id}",
            ) from exc
        except REGISTRY_INFRASTRUCTURE_ERRORS as exc:
            raise _registry_unavailable() from exc

    @app.post("/api/operator-instances/unregister")
    def unregister_operator(
        payload: OperatorUnregisterRequest,
        request: Request,
    ) -> dict[str, str]:
        _authorize_operator_management(request, resolved.operator_registry_token)
        try:
            _operator_registry(request).unregister(payload.instance_id)
        except OperatorInstanceNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"算子实例不存在: {payload.instance_id}",
            ) from exc
        except REGISTRY_INFRASTRUCTURE_ERRORS as exc:
            raise _registry_unavailable() from exc
        return {"instance_id": payload.instance_id, "status": "OFFLINE"}

    @app.get("/api/operator-instances")
    def list_operator_instances(
        request: Request,
        capability: str | None = None,
        lifecycle: OperatorLifecycle | None = None,
    ) -> list[OperatorInstance]:
        try:
            instances = _operator_registry(request).list_instances()
        except REGISTRY_INFRASTRUCTURE_ERRORS as exc:
            raise _registry_unavailable() from exc
        if capability is not None:
            instances = [instance for instance in instances if capability in instance.capabilities]
        if lifecycle is not None:
            instances = [instance for instance in instances if instance.lifecycle is lifecycle]
        return instances

    @app.post("/api/operator-instances/lifecycle")
    def set_operator_lifecycle(
        payload: OperatorLifecycleRequest,
        request: Request,
    ) -> OperatorInstance:
        _authorize_operator_management(request, resolved.operator_registry_token)
        try:
            return _operator_registry(request).set_lifecycle(
                payload.instance_id,
                payload.lifecycle,
            )
        except OperatorInstanceNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"算子实例不存在: {payload.instance_id}",
            ) from exc
        except REGISTRY_INFRASTRUCTURE_ERRORS as exc:
            raise _registry_unavailable() from exc

    @app.post("/internal/operator-instances/lease")
    def lease_operator(
        payload: CapacityLeaseRequest,
        request: Request,
    ) -> CapacityLease:
        try:
            registry = _operator_registry(request)
            if payload.work_context is None:
                lease = registry.lease(
                    payload.capability,
                    payload.ttl_seconds,
                    capacity_pool=payload.capacity_pool,
                )
            else:
                lease = registry.lease(
                    payload.capability,
                    payload.ttl_seconds,
                    payload.work_context.to_domain(),
                    capacity_pool=payload.capacity_pool,
                )
            context = lease.work_context
            logger.info(
                "算子容量租约已取得",
                extra={
                    "audit_type": "operator_capacity_lease",
                    "lease_id": lease.lease_id,
                    "instance_id": lease.instance_id,
                    "capability": lease.capability,
                    "source_service": (
                        context.source_service if context is not None else None
                    ),
                    "work_type": context.work_type if context is not None else None,
                    "work_id": context.work_id if context is not None else None,
                    "task_id": context.task_id if context is not None else None,
                    "batch_id": (
                        context.item_id
                        if context is not None and context.item_id is not None
                        else (context.work_id if context is not None else None)
                    ),
                    "outcome": "acquired",
                },
            )
            return lease
        except CapacityUnavailableError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"暂无可用算子容量: {payload.capability}",
            ) from exc
        except REGISTRY_INFRASTRUCTURE_ERRORS as exc:
            raise _registry_unavailable() from exc

    @app.post("/internal/operator-instances/lease/context")
    def bind_operator_lease_context(
        payload: CapacityLeaseContextRequest,
        request: Request,
    ) -> CapacityLease:
        try:
            return _operator_registry(request).bind_lease_context(
                payload.lease_id,
                payload.work_context.to_domain(),
            )
        except CapacityLeaseNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"算子容量租约不存在或已过期: {payload.lease_id}",
            ) from exc
        except CapacityLeaseContextConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"算子容量租约已绑定其他工作上下文: {payload.lease_id}",
            ) from exc
        except REGISTRY_INFRASTRUCTURE_ERRORS as exc:
            raise _registry_unavailable() from exc

    @app.post("/internal/operator-instances/release")
    def release_operator(
        payload: CapacityReleaseRequest,
        request: Request,
    ) -> dict[str, str]:
        try:
            _operator_registry(request).release(payload.lease_id)
        except CapacityLeaseNotFoundError:
            return {"lease_id": payload.lease_id, "status": "ALREADY_RELEASED"}
        except REGISTRY_INFRASTRUCTURE_ERRORS as exc:
            raise _registry_unavailable() from exc
        return {"lease_id": payload.lease_id, "status": "RELEASED"}

    @app.post("/internal/operator-instances/lease/renew")
    def renew_operator_lease(
        payload: CapacityLeaseRenewRequest,
        request: Request,
    ) -> CapacityLease:
        try:
            return _operator_registry(request).renew(
                payload.lease_id,
                payload.ttl_seconds,
            )
        except CapacityLeaseNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"算子容量租约不存在或已过期: {payload.lease_id}",
            ) from exc
        except REGISTRY_INFRASTRUCTURE_ERRORS as exc:
            raise _registry_unavailable() from exc

    @app.get("/internal/operator-instances/lease/{lease_id}")
    def inspect_internal_capacity_lease(
        lease_id: str,
        request: Request,
    ) -> ActiveCapacityLease:
        """供崩溃恢复器确认原节点租约；不续期，也不改变容量归属。"""

        try:
            registry = _operator_registry(request)
            for instance in registry.list_instances():
                active = registry.list_active_leases(instance.instance_id)
                for lease in active.leases:
                    if lease.lease_id == lease_id:
                        return lease
        except REGISTRY_INFRASTRUCTURE_ERRORS as exc:
            raise _registry_unavailable() from exc
        raise HTTPException(status_code=404, detail=f"容量租约不存在: {lease_id}")

    @app.get("/ops/course-jobs")
    def inspect_course_jobs(
        request: Request,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=10, ge=1, le=100),
        sort_by: Literal["updated_at", "created_at", "task_id"] = Query(
            default="updated_at"
        ),
        order: Literal["asc", "desc"] = Query(default="desc"),
        task_type: list[TaskType] | None = Query(default=None),
        overall_status: NodeStatus | None = Query(default=None),
        task_status_type: TaskType | None = Query(default=None),
        task_status: NodeStatus | None = Query(default=None),
        updated_from: datetime | None = Query(default=None),
        updated_to: datetime | None = Query(default=None),
        task_id_like: str | None = Query(default=None, max_length=200),
    ) -> dict[str, Any]:
        if (task_status_type is None) != (task_status is None):
            raise HTTPException(
                status_code=422,
                detail="task_status_type 与 task_status 必须同时提供",
            )
        if updated_from is not None and updated_to is not None and updated_from > updated_to:
            raise HTTPException(status_code=422, detail="updated_from 不能晚于 updated_to")
        repository = _course_repository(request)
        try:
            records, total = repository.list_course_jobs(
                offset=(page - 1) * page_size,
                limit=page_size,
                sort_by=sort_by,
                descending=order == "desc",
                task_types=tuple(task_type or ()),
                overall_status=overall_status,
                task_status_type=task_status_type,
                task_status=task_status,
                updated_from=updated_from,
                updated_to=updated_to,
                task_id_like=(task_id_like or "").strip() or None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (RuntimeError, SQLAlchemyError) as exc:
            raise HTTPException(status_code=503, detail="任务数据库暂不可用") from exc
        total_pages = (total + page_size - 1) // page_size if total else 0
        return {
            "items": [_course_job_summary_response(record) for record in records],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "sort_by": sort_by,
            "order": order,
        }

    @app.get("/ops/course-jobs/{task_id}/summary")
    def inspect_course_job_summary(task_id: str, request: Request) -> dict[str, Any]:
        try:
            record = _course_repository(request).get_course_job_summary(task_id)
        except (RuntimeError, SQLAlchemyError) as exc:
            raise HTTPException(status_code=503, detail="任务数据库暂不可用") from exc
        if record is None:
            raise HTTPException(status_code=404, detail=f"未找到课程任务: {task_id}")
        return _course_job_summary_response(record)

    @app.get("/ops/course-jobs/{task_id}/task-types/{task_type}")
    def inspect_course_task_type(
        task_id: str,
        task_type: TaskType,
        request: Request,
    ) -> dict[str, Any]:
        repository = _course_repository(request)
        try:
            record = next(
                (item for item in repository.list_task_types(task_id) if item.task_type is task_type),
                None,
            )
            if record is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"未找到课程任务项: {task_id}/{task_type.value}",
                )
            nodes = repository.list_nodes(record.id, record.run_id)
        except HTTPException:
            raise
        except (RuntimeError, SQLAlchemyError) as exc:
            raise HTTPException(status_code=503, detail="任务数据库暂不可用") from exc
        return _task_summary_response(record, nodes)

    @app.get("/ops/course-jobs/{task_id}/task-types/{task_type}/result")
    def inspect_course_task_result(
        task_id: str,
        task_type: TaskType,
        request: Request,
        node_code: str | None = Query(default=None, min_length=1, max_length=100),
        section: str = Query(default="summary", min_length=1, max_length=100),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        repository = _course_repository(request)
        try:
            record = next(
                (item for item in repository.list_task_types(task_id) if item.task_type is task_type),
                None,
            )
            if record is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"未找到课程任务项: {task_id}/{task_type.value}",
                )
            nodes = repository.list_nodes(record.id, record.run_id)
            selected = [node for node in nodes if node_code is None or node.node_code == node_code]
            if not selected:
                raise HTTPException(status_code=404, detail=f"未找到任务节点: {node_code}")
            results = [
                {
                    "node_code": node.node_code,
                    **_result_section(node, section=section, page=page, page_size=page_size),
                }
                for node in selected
            ]
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (RuntimeError, SQLAlchemyError) as exc:
            raise HTTPException(status_code=503, detail="任务数据库暂不可用") from exc
        return {
            "task_id": task_id,
            "task_type": task_type.value,
            "section": section,
            "results": results,
        }

    @app.get("/ops/course-jobs/{task_id}/events")
    def inspect_course_job_events(
        task_id: str,
        request: Request,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        order: Literal["asc", "desc"] = Query(default="asc"),
    ) -> dict[str, Any]:
        try:
            records, total = _course_repository(request).list_outbox_events(
                offset=(page - 1) * page_size,
                limit=page_size,
                task_id=task_id,
                descending=order == "desc",
            )
        except (RuntimeError, SQLAlchemyError) as exc:
            raise HTTPException(status_code=503, detail="任务数据库暂不可用") from exc
        return {
            "items": [_outbox_response(item, include_payload=False) for item in records],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size if total else 0,
            "order": order,
        }

    @app.get("/ops/course-jobs/{task_id}")
    def inspect_course_job(task_id: str, request: Request) -> dict[str, Any]:
        repository = _course_repository(request)
        try:
            records = repository.list_task_types(task_id)
        except (RuntimeError, SQLAlchemyError) as exc:
            raise HTTPException(status_code=503, detail="任务数据库暂不可用") from exc
        if not records:
            raise HTTPException(status_code=404, detail=f"未找到课程任务: {task_id}")

        records_by_type = {record.task_type: record for record in records}
        tasks: list[dict[str, Any]] = []
        for task_type in TaskType:
            record = records_by_type.get(task_type)
            if record is None:
                tasks.append(_unrequested_task_response(task_type))
            else:
                try:
                    nodes = (
                        repository.list_nodes(record.id)
                        if record.run_id is None
                        else repository.list_nodes(record.id, record.run_id)
                    )
                    runs = (
                        repository.list_asr_runs(record.id)
                        if record.task_type is TaskType.ASR
                        else None
                    )
                except (RuntimeError, SQLAlchemyError) as exc:
                    raise HTTPException(
                        status_code=503,
                        detail="任务数据库暂不可用",
                    ) from exc
                tasks.append(_queried_task_response(record, nodes, runs))
        return {"task_id": task_id, "tasks": tasks}

    @app.get("/ops/operator-instances")
    def inspect_operator_instances(request: Request) -> list[OperatorInstance]:
        try:
            instances = _operator_registry(request).list_instances()
        except REGISTRY_INFRASTRUCTURE_ERRORS as exc:
            raise _registry_unavailable() from exc
        for instance in instances:
            request.app.state.platform_metrics.set_operator_instance(
                operator_code=instance.operator_code.value,
                lifecycle=instance.lifecycle.value,
                model_ready=instance.model_ready,
                gpu_label=instance.labels.get("gpu", ""),
                count=1,
            )
        return instances

    @app.get("/ops/operator-instances/snapshot")
    def inspect_operator_capacity_snapshot(
        request: Request,
    ) -> list[OperatorCapacitySnapshot]:
        try:
            registry = cast(OperatorOperationsRegistry, _operator_registry(request))
            return build_operator_capacity_snapshot(registry)
        except REGISTRY_INFRASTRUCTURE_ERRORS as exc:
            raise _registry_unavailable() from exc

    @app.get("/ops/operator-instances/{instance_id}/active-leases")
    def inspect_operator_active_leases(
        instance_id: str,
        request: Request,
    ) -> OperatorActiveLeases:
        try:
            return _operator_registry(request).list_active_leases(instance_id)
        except OperatorInstanceNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"算子实例不存在: {instance_id}",
            ) from exc
        except REGISTRY_INFRASTRUCTURE_ERRORS as exc:
            raise _registry_unavailable() from exc

    @app.post("/ops/operator-instances/{instance_id}/drain")
    def drain_operator_instance(
        instance_id: str,
        request: Request,
    ) -> OperatorInstance:
        _authorize_operator_management(request, resolved.operator_registry_token)
        try:
            return _operator_registry(request).set_lifecycle(
                instance_id,
                OperatorLifecycle.DRAINING,
            )
        except OperatorInstanceNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"算子实例不存在: {instance_id}",
            ) from exc
        except REGISTRY_INFRASTRUCTURE_ERRORS as exc:
            raise _registry_unavailable() from exc

    @app.get("/ops/operator-instances/{instance_id}/events")
    def inspect_operator_instance_events(
        instance_id: str,
        request: Request,
        limit: int = 100,
    ) -> list[OperatorInstanceEvent]:
        if not 1 <= limit <= 1000:
            raise HTTPException(status_code=422, detail="事件查询数量必须在 1 到 1000 之间")
        try:
            return _operator_events(request, instance_id, limit=limit)
        except SQLAlchemyError as exc:
            raise HTTPException(status_code=503, detail="算子审计数据库暂不可用") from exc

    @app.get("/ops/kafka")
    def inspect_kafka(request: Request) -> dict[str, Any]:
        """聚合 Outbox 和内部 orchestrator 的 Kafka 只读运行指标。"""
        repository = _course_repository(request)
        try:
            queue_snapshot = repository.operations_queue_snapshot()
        except (RuntimeError, SQLAlchemyError) as exc:
            raise HTTPException(status_code=503, detail="任务数据库暂不可用") from exc
        published = publish_failed = consumer_lag = 0
        publisher_status = "unavailable"
        try:
            response = httpx.get(
                resolved.orchestrator_metrics_url,
                timeout=2.0,
                headers={"Accept": "text/plain"},
            )
            response.raise_for_status()
            published, publish_failed, consumer_lag = _kafka_metric_values(response.text)
            publisher_status = "ok"
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("读取 orchestrator Kafka 指标失败: %s", exc)
        return {
            "status": "ok" if publisher_status == "ok" else "degraded",
            "outbox_pending": queue_snapshot.outbox_pending,
            "published": published,
            "publish_failed": publish_failed,
            "consumer_lag": consumer_lag,
            "publisher_status": publisher_status,
            "sampled_at": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/ops/kafka/events")
    def inspect_kafka_events(
        request: Request,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        task_id: str | None = Query(default=None, max_length=200),
        task_id_like: str | None = Query(default=None, max_length=200),
        event_type: str | None = Query(default=None, max_length=200),
        publish_status: Literal[
            "PENDING", "PUBLISHING", "RETRY_PENDING", "PUBLISHED"
        ]
        | None = Query(default=None),
        created_from: datetime | None = Query(default=None),
        created_to: datetime | None = Query(default=None),
        order: Literal["asc", "desc"] = Query(default="desc"),
    ) -> dict[str, Any]:
        if created_from is not None and created_to is not None and created_from > created_to:
            raise HTTPException(status_code=422, detail="created_from 不能晚于 created_to")
        try:
            records, total = _course_repository(request).list_outbox_events(
                offset=(page - 1) * page_size,
                limit=page_size,
                task_id=(task_id or "").strip() or None,
                task_id_like=(task_id_like or "").strip() or None,
                event_type=(event_type or "").strip() or None,
                publish_status=publish_status,
                created_from=created_from,
                created_to=created_to,
                descending=order == "desc",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (RuntimeError, SQLAlchemyError) as exc:
            raise HTTPException(status_code=503, detail="任务数据库暂不可用") from exc
        return {
            "items": [_outbox_response(item, include_payload=False) for item in records],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size if total else 0,
            "order": order,
        }

    @app.get("/ops/kafka/events/{event_id}")
    def inspect_kafka_event(event_id: UUID, request: Request) -> dict[str, Any]:
        try:
            record = _course_repository(request).get_outbox_event(event_id)
        except (RuntimeError, SQLAlchemyError) as exc:
            raise HTTPException(status_code=503, detail="任务数据库暂不可用") from exc
        if record is None:
            raise HTTPException(status_code=404, detail=f"未找到 Outbox 事件: {event_id}")
        return _outbox_response(record, include_payload=True)

    @app.get("/ops/queues")
    def inspect_queues(request: Request) -> dict[str, Any]:
        repository = _course_repository(request)
        try:
            snapshot = repository.operations_queue_snapshot()
        except (RuntimeError, SQLAlchemyError) as exc:
            raise HTTPException(status_code=503, detail="任务数据库暂不可用") from exc
        request.app.state.platform_metrics.set_outbox_pending(snapshot.outbox_pending)
        queues = []
        for item in snapshot.queues:
            request.app.state.platform_metrics.set_node_state(
                node_code=item.capability or "no_capability",
                status=item.status.value,
                count=item.count,
            )
            queues.append(
                {
                    "status": item.status.value,
                    "status_text": status_text(item.status),
                    "priority": item.priority.value,
                    "capability": item.capability,
                    "count": item.count,
                }
            )
        return {"queues": queues, "outbox_pending": snapshot.outbox_pending}

    @app.get("/ops/storage")
    def inspect_storage(
        request: Request,
        include_directory_bytes: bool = Query(default=False),
    ) -> dict[str, Any]:
        try:
            roots = [
                _storage_root_response(
                    resolved.course_root,
                    "course",
                    include_directory_bytes=include_directory_bytes,
                ),
                _storage_root_response(
                    resolved.result_root,
                    "result",
                    include_directory_bytes=include_directory_bytes,
                ),
            ]
        except OSError as exc:
            raise HTTPException(status_code=503, detail="存储目录暂不可用") from exc
        request.app.state.platform_metrics.update_disk_usage(
            resolved.course_root,
            kind="course",
        )
        request.app.state.platform_metrics.update_disk_usage(
            resolved.result_root,
            kind="result",
        )
        return {"roots": roots}

    @app.get("/ops/readiness")
    def readiness(request: Request) -> JSONResponse:
        result = _readiness_checker(request).check()
        response_status = 200 if result["status"] == "ready" else 503
        return JSONResponse(
            status_code=response_status,
            content=jsonable_encoder(result),
        )

    return app
