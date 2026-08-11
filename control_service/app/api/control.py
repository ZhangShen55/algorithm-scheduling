from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from packages.platform_common.application import create_service_app
from packages.platform_common.config import PlatformSettings
from packages.platform_common.operator_audit_repository import OperatorInstanceEvent
from packages.platform_common.operator_registry import (
    CapacityLease,
    CapacityLeaseNotFoundError,
    CapacityUnavailableError,
    OperatorCode,
    OperatorInstance,
    OperatorInstanceNotFoundError,
    OperatorLifecycle,
    OperatorRegistry,
)
from packages.platform_common.repository import (
    NodeRecord,
    OperationsQueueSnapshot,
    TaskTypeRecord,
    TaskTypeWrite,
)
from packages.platform_contracts.responses import BusinessCode, BusinessResponse
from packages.platform_contracts.status import Priority, TaskType, status_text

from ..infrastructure.runtime import ControlReadinessChecker, ControlRuntime


class CourseTaskRepository(Protocol):
    def create_task_types(
        self,
        *,
        task_id: str,
        writes: list[TaskTypeWrite],
        input_snapshot: dict[str, Any] | None = None,
    ) -> list[TaskTypeRecord]: ...

    def list_task_types(self, task_id: str) -> list[TaskTypeRecord]: ...

    def list_nodes(self, course_task_type_id: int) -> list[NodeRecord]: ...

    def operations_queue_snapshot(self) -> OperationsQueueSnapshot: ...


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
    showSpk: bool = True
    showEmotion: bool = True
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
    declared_capacity: int = Field(gt=0)
    labels: dict[str, str] = Field(default_factory=dict)


class OperatorHeartbeatRequest(BaseModel):
    instance_id: str = Field(min_length=1)
    inflight: int = Field(ge=0)
    model_ready: bool


class OperatorUnregisterRequest(BaseModel):
    instance_id: str = Field(min_length=1)


class OperatorLifecycleRequest(BaseModel):
    instance_id: str = Field(min_length=1)
    lifecycle: OperatorLifecycle


class CapacityLeaseRequest(BaseModel):
    capability: str = Field(min_length=1)
    ttl_seconds: int = Field(default=60, gt=0, le=3600)


class CapacityReleaseRequest(BaseModel):
    lease_id: str = Field(min_length=1)


class CapacityLeaseRenewRequest(BaseModel):
    lease_id: str = Field(min_length=1)
    ttl_seconds: int = Field(default=60, gt=0, le=3600)


class SubmissionValidationError(ValueError):
    pass


REGISTRY_INFRASTRUCTURE_ERRORS = (RuntimeError, RedisError, SQLAlchemyError)


def _task_database_error() -> BusinessResponse[dict[str, Any]]:
    return BusinessResponse[dict[str, Any]].failure(
        BusinessCode.INTERNAL_ERROR,
        "任务数据库暂不可用",
    )


def _registry_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="算子注册中心暂不可用")


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
    }


def _node_response(record: NodeRecord) -> dict[str, Any]:
    response: dict[str, Any] = {
        "node_code": record.node_code,
        "status": record.status.value,
        "status_text": status_text(record.status),
        "reason": record.reason,
        "priority": record.priority.value,
        "required_capability": record.required_capability,
        "progress": record.progress,
        "effective_params": record.effective_params,
        "updated_at": record.updated_at,
    }
    if record.artifact_path is not None:
        response["path"] = record.artifact_path
    if record.artifact_count is not None:
        response["count"] = record.artifact_count
    if record.result is not None:
        response["result"] = record.result
    return response


def _queried_task_response(
    record: TaskTypeRecord,
    nodes: list[NodeRecord],
) -> dict[str, Any]:
    response = _task_response(record)
    response["effective_params"] = record.effective_params
    response["nodes"] = [_node_response(node) for node in nodes]
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


def _directory_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _storage_root_response(root: Path, kind: str) -> dict[str, Any]:
    usage = shutil.disk_usage(root)
    return {
        "kind": kind,
        "path": str(root),
        "directory_bytes": _directory_bytes(root),
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
                    nodes = repository.list_nodes(record.id)
                except (RuntimeError, SQLAlchemyError):
                    return _task_database_error()
                tasks.append(_queried_task_response(record, nodes))
        return BusinessResponse[dict[str, Any]].success(
            {"task_id": task_id, "tasks": tasks},
            message="课程任务查询成功",
        )

    @app.post("/api/operator-instances/register", status_code=status.HTTP_201_CREATED)
    def register_operator(
        payload: OperatorRegistrationRequest,
        request: Request,
    ) -> OperatorInstance:
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
                )
            )
        except REGISTRY_INFRASTRUCTURE_ERRORS as exc:
            raise _registry_unavailable() from exc

    @app.post("/api/operator-instances/heartbeat")
    def heartbeat_operator(
        payload: OperatorHeartbeatRequest,
        request: Request,
    ) -> OperatorInstance:
        try:
            return _operator_registry(request).heartbeat(
                payload.instance_id,
                inflight=payload.inflight,
                model_ready=payload.model_ready,
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
            return _operator_registry(request).lease(
                payload.capability,
                payload.ttl_seconds,
            )
        except CapacityUnavailableError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"暂无可用算子容量: {payload.capability}",
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
                    nodes = repository.list_nodes(record.id)
                except (RuntimeError, SQLAlchemyError) as exc:
                    raise HTTPException(
                        status_code=503,
                        detail="任务数据库暂不可用",
                    ) from exc
                tasks.append(_queried_task_response(record, nodes))
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

    @app.post("/ops/operator-instances/{instance_id}/drain")
    def drain_operator_instance(
        instance_id: str,
        request: Request,
    ) -> OperatorInstance:
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
    def inspect_storage(request: Request) -> dict[str, Any]:
        try:
            roots = [
                _storage_root_response(resolved.course_root, "course"),
                _storage_root_response(resolved.result_root, "result"),
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
