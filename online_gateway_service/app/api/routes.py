from __future__ import annotations

import asyncio
import logging
import time
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol, cast
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError, field_validator

from packages.platform_common.config import PlatformSettings
from packages.platform_common.lease_resilience import (
    LeaseRenewalPolicy,
    remaining_deadline_seconds,
    wait_for_retry,
)
from packages.platform_common.metrics import PlatformMetrics
from packages.platform_common.trace import get_trace_id, new_trace_id
from packages.platform_contracts.responses import BusinessResponse

from ..core.config import (
    SERVICE_ROOT,
    ControlConfig,
    OnlineGatewaySettings,
    ServiceConfig,
)
from ..core.service_app import create_gateway_base_app
from ..domain import valid_base64_image
from ..infrastructure.capacity import (
    CapacityLease,
    ControlLeaseProtocolError,
    ControlServiceUnavailableError,
    OnlineCapacityLeaseClient,
    OnlineCapacityLeaseError,
    OnlineCapacityWaitTimeoutError,
    OnlineWorkContext,
)
from ..infrastructure.persons import FacePersonClient, FacePersonClientError
from ..infrastructure.websocket_proxy import (
    AsrWebSocketConnector,
    WebsocketsAsrConnector,
    operator_websocket_url,
    proxy_websocket,
)

JsonObject = dict[str, Any]
logger = logging.getLogger(__name__)
IMAGE_ROUTE_PATHS = {
    "/online/vbas/teacher",
    "/online/vbas/student",
    "/online/vbas/person-count",
    "/api/online/face/recognize",
    "/api/online/image-quality/detect",
    "/api/online/ocr/recognize",
}


class OnlineOcrRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    image: str
    image_id: str | None = None
    enable_formula: StrictBool = False

    @field_validator("image")
    @classmethod
    def image_must_not_be_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("image 不能为空")
        return value

    @field_validator("image_id")
    @classmethod
    def image_id_must_not_be_empty(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("image_id 不能为空")
        return value


class OnlineLeaseClient(Protocol):
    def acquire(
        self,
        capability: str,
        *,
        ttl_seconds: int = 60,
        work_context: OnlineWorkContext | None = None,
        renew_interval_seconds: float | None = None,
        capacity_pool: str = "online",
        deadline: float | None = None,
    ) -> AbstractAsyncContextManager[CapacityLease]: ...


class _VbasRetryableCallError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        instance_id: str,
        timeout: bool = False,
    ) -> None:
        super().__init__(message)
        self.instance_id = instance_id
        self.timeout = timeout


class _VbasDeterministicCallError(RuntimeError):
    def __init__(self, message: str, *, business_code: int) -> None:
        super().__init__(message)
        self.business_code = business_code


def _online_work_context(work_type: str, *, trace_id: str | None) -> OnlineWorkContext:
    safe_trace_id = trace_id
    if (
        not safe_trace_id
        or len(safe_trace_id) > 200
        or any(character in safe_trace_id for character in ("\x00", "\r", "\n"))
    ):
        safe_trace_id = new_trace_id()
    work_id = f"{work_type}-{uuid4().hex}"
    return OnlineWorkContext(
        source_service="online-gateway-service",
        work_type=work_type,
        work_id=work_id,
        item_id=work_id,
        trace_id=safe_trace_id,
    )


async def _valid_online_image(value: object, settings: OnlineGatewaySettings) -> bool:
    if not isinstance(value, str):
        return False
    return await asyncio.to_thread(
        valid_base64_image,
        value,
        max_decoded_bytes=settings.base64.max_decoded_bytes,
        allow_data_uri=settings.base64.allow_data_uri,
    )


def create_online_gateway_app(
    settings: PlatformSettings | OnlineGatewaySettings | None = None,
) -> FastAPI:
    if isinstance(settings, PlatformSettings):
        service_settings = OnlineGatewaySettings(
            service=ServiceConfig(
                name=settings.service_name,
                environment=settings.environment,
                log_level=settings.log_level,
                trace_header=settings.trace_header,
            ),
            logging=settings.logging,
            control=ControlConfig(base_url=settings.control_service_url),
        )
        platform_settings = settings
    else:
        service_settings = settings or OnlineGatewaySettings()
        platform_settings = PlatformSettings(
            service_name=service_settings.service.name,
            environment=service_settings.service.environment,
            log_level=service_settings.service.log_level,
            trace_header=service_settings.service.trace_header,
            logging=service_settings.logging,
            project_root=SERVICE_ROOT,
            control_service_url=service_settings.control.base_url,
        )
    app = create_gateway_base_app(platform_settings)
    timeout = httpx.Timeout(
        connect=service_settings.http.connect_timeout_seconds,
        read=service_settings.http.read_timeout_seconds,
        write=service_settings.http.write_timeout_seconds,
        pool=service_settings.http.pool_timeout_seconds,
    )
    limits = httpx.Limits(
        max_connections=service_settings.http.max_connections,
        max_keepalive_connections=service_settings.http.max_keepalive_connections,
    )
    http_client = httpx.AsyncClient(timeout=timeout, limits=limits)
    app.state.service_settings = service_settings
    app.state.online_http_client = http_client
    app.state.online_lease_client = OnlineCapacityLeaseClient(
        http_client,
        control_service_url=platform_settings.control_service_url,
        metrics=cast(PlatformMetrics, app.state.platform_metrics),
        renewal_policy=LeaseRenewalPolicy(
            max_attempts=service_settings.leases.renewal_max_attempts,
            base_delay_seconds=(
                service_settings.leases.renewal_base_delay_seconds
            ),
            max_delay_seconds=service_settings.leases.renewal_max_delay_seconds,
            safety_margin_seconds=(
                service_settings.leases.renewal_safety_margin_seconds
            ),
        ),
        acquire_wait_timeout_seconds=service_settings.leases.acquire_wait_timeout_seconds,
        acquire_retry_interval_seconds=service_settings.leases.acquire_retry_interval_seconds,
    )
    app.state.face_person_client = FacePersonClient(
        http_client,
        base_url=service_settings.face_persons.base_url,
        hard_timeout_seconds=service_settings.http.hard_timeout_seconds,
    )
    app.state.asr_websocket_connector = WebsocketsAsrConnector()

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI):  # type: ignore[no-untyped-def]
        async with original_lifespan(application):
            try:
                yield
            finally:
                await http_client.aclose()

    app.router.lifespan_context = lifespan

    @app.middleware("http")
    async def enforce_online_image_body_limit(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method == "POST" and request.url.path in IMAGE_ROUTE_PATHS:
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    declared_size = service_settings.body.max_bytes + 1
                if declared_size > service_settings.body.max_bytes:
                    failure = BusinessResponse[None].failure(
                        40001,
                        "在线图片请求体超过配置上限",
                    )
                    return JSONResponse(status_code=200, content=failure.model_dump())
            body = await request.body()
            if len(body) > service_settings.body.max_bytes:
                failure = BusinessResponse[None].failure(
                    40001,
                    "在线图片请求体超过配置上限",
                )
                return JSONResponse(status_code=200, content=failure.model_dump())
        return await call_next(request)

    async def forward_vbas(
        request_body: JsonObject,
        request: Request,
        *,
        capability: str,
        endpoint: str,
        operation: str,
    ) -> JsonObject | BusinessResponse[JsonObject]:
        started_at = time.monotonic()
        deadline = started_at + service_settings.http.hard_timeout_seconds
        image_list = request_body.get("ImageList")
        if not isinstance(image_list, list) or not image_list:
            return BusinessResponse[JsonObject].failure(40001, "VBas 请求必须包含 ImageList")
        try:
            image_validity = await asyncio.wait_for(
                asyncio.gather(
                    *(
                        _valid_online_image(
                            item.get("StoragePath") or item.get("Data"),
                            service_settings,
                        )
                        for item in image_list
                        if isinstance(item, dict)
                    )
                )
                ,
                timeout=remaining_deadline_seconds(deadline),
            )
        except TimeoutError:
            return BusinessResponse[JsonObject].failure(
                50401,
                "VBas 在线分析总处理超时",
            )
        if len(image_validity) != len(image_list) or not all(image_validity):
            return BusinessResponse[JsonObject].failure(
                40001,
                "VBas 在线图片超过配置上限",
            )
        lease_client = cast(OnlineLeaseClient, request.app.state.online_lease_client)
        operator_http = cast(httpx.AsyncClient, request.app.state.online_http_client)
        metrics = cast(PlatformMetrics, request.app.state.platform_metrics)
        work_context = _online_work_context(operation, trace_id=get_trace_id())
        last_call_error: _VbasRetryableCallError | None = None
        for attempt in range(1, service_settings.http.operator_max_attempts + 1):
            current_instance_id = "unknown"
            attempt_started_at = time.monotonic()
            try:
                async with lease_client.acquire(
                    capability,
                    ttl_seconds=service_settings.leases.request_ttl_seconds,
                    work_context=work_context,
                    capacity_pool="online",
                    deadline=deadline,
                ) as lease:
                    current_instance_id = lease.instance_id
                    remaining = remaining_deadline_seconds(deadline)
                    if remaining <= 0:
                        raise _VbasRetryableCallError(
                            "VBas 在线分析总预算耗尽",
                            instance_id=lease.instance_id,
                            timeout=True,
                        )
                    response = await asyncio.wait_for(
                        operator_http.post(
                            f"{lease.service_url.rstrip('/')}{endpoint}",
                            json=request_body,
                            headers={"X-Algorithm-Work-Type": "online"},
                        ),
                        timeout=remaining,
                    )
                    if response.status_code in {429, 502, 503, 504}:
                        raise _VbasRetryableCallError(
                            f"VBas 返回可恢复状态: HTTP {response.status_code}",
                            instance_id=lease.instance_id,
                        )
                    if response.status_code in {400, 422}:
                        raise _VbasDeterministicCallError(
                            f"VBas 拒绝请求参数: HTTP {response.status_code}",
                            business_code=40001,
                        )
                    if response.is_error:
                        raise _VbasDeterministicCallError(
                            f"VBas 返回不可恢复状态: HTTP {response.status_code}",
                            business_code=50000,
                        )
                    response.raise_for_status()
                    try:
                        body = response.json()
                    except ValueError as exc:
                        raise _VbasRetryableCallError(
                            "VBas 响应不是合法 JSON",
                            instance_id=lease.instance_id,
                        ) from exc
                    if not isinstance(body, dict):
                        raise _VbasRetryableCallError(
                            "VBas 响应不是 JSON 对象",
                            instance_id=lease.instance_id,
                        )
                    metrics.observe_operator_request(
                        operator_code="vbas",
                        capability=capability,
                        instance_id=lease.instance_id,
                        elapsed_seconds=time.monotonic() - attempt_started_at,
                        success=True,
                    )
                    return body
            except OnlineCapacityWaitTimeoutError:
                return BusinessResponse[JsonObject].failure(
                    50301,
                    "等待 VBas 在线容量超时",
                )
            except ControlServiceUnavailableError:
                return BusinessResponse[JsonObject].failure(
                    50302,
                    "Control 容量服务在允许时间内未恢复",
                )
            except ControlLeaseProtocolError:
                return BusinessResponse[JsonObject].failure(
                    50000,
                    "Control 容量租约响应不可恢复",
                )
            except OnlineCapacityLeaseError:
                return BusinessResponse[JsonObject].failure(
                    50302,
                    "Control 容量租约处理失败",
                )
            except asyncio.CancelledError:
                raise
            except _VbasDeterministicCallError as exc:
                return BusinessResponse[JsonObject].failure(
                    exc.business_code,
                    (
                        "VBas 在线请求参数被拒绝"
                        if exc.business_code == 40001
                        else "VBas 在线分析返回不可恢复错误"
                    ),
                )
            except (TimeoutError, httpx.ReadTimeout) as exc:
                last_call_error = _VbasRetryableCallError(
                    "VBas 在线分析响应超时",
                    instance_id=current_instance_id,
                    timeout=True,
                )
                last_call_error.__cause__ = exc
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_call_error = _VbasRetryableCallError(
                    f"VBas 在线分析连接或协议失败: {type(exc).__name__}",
                    instance_id=current_instance_id,
                )
                last_call_error.__cause__ = exc
            except _VbasRetryableCallError as exc:
                last_call_error = exc
            except Exception as exc:
                logger.exception(
                    "VBas 在线分析发生未分类错误",
                    extra={
                        "trace_id": work_context.trace_id,
                        "capability": capability,
                        "stage": "operator_call",
                        "exception_type": type(exc).__name__,
                        "attempt": attempt,
                        "elapsed_seconds": round(time.monotonic() - started_at, 3),
                        "remaining_seconds": round(
                            remaining_deadline_seconds(deadline), 3
                        ),
                        "outcome": "failed",
                    },
                )
                return BusinessResponse[JsonObject].failure(
                    50000,
                    "VBas 在线分析调用失败",
                )

            assert last_call_error is not None
            metrics.observe_operator_request(
                operator_code="vbas",
                capability=capability,
                instance_id=last_call_error.instance_id,
                elapsed_seconds=time.monotonic() - attempt_started_at,
                success=False,
            )
            metrics.record_capacity_recovery_event(
                capacity_pool="online",
                capability=capability,
                instance_id=last_call_error.instance_id,
                stage="operator_call",
                exception_type=(
                    "timeout"
                    if last_call_error.timeout
                    else type(last_call_error.__cause__ or last_call_error).__name__
                ),
                outcome=(
                    "retrying"
                    if attempt < service_settings.http.operator_max_attempts
                    and remaining_deadline_seconds(deadline) > 0
                    else "exhausted"
                ),
            )
            remaining = remaining_deadline_seconds(deadline)
            logger.warning(
                "VBas 在线分析准备重选实例",
                extra={
                    "trace_id": work_context.trace_id,
                    "capability": capability,
                    "instance_id": last_call_error.instance_id,
                    "stage": "operator_call",
                    "exception_type": type(last_call_error.__cause__ or last_call_error).__name__,
                    "attempt": attempt,
                    "elapsed_seconds": round(time.monotonic() - started_at, 3),
                    "remaining_seconds": round(remaining, 3),
                    "outcome": (
                        "retrying"
                        if attempt < service_settings.http.operator_max_attempts
                        and remaining > 0
                        else "exhausted"
                    ),
                },
            )
            if attempt >= service_settings.http.operator_max_attempts or remaining <= 0:
                break
            retry_allowed = await wait_for_retry(
                deadline=deadline,
                attempt=attempt,
                base_delay_seconds=service_settings.http.retry_base_delay_seconds,
                max_delay_seconds=service_settings.http.retry_max_delay_seconds,
            )
            if not retry_allowed:
                break

        if last_call_error is not None and last_call_error.timeout:
            return BusinessResponse[JsonObject].failure(
                50401,
                "VBas 在线分析响应超时",
            )
        return BusinessResponse[JsonObject].failure(
            50201,
            "VBas 在线分析连接或协议失败",
        )

    @app.post("/online/vbas/teacher", response_model=None)
    async def analyze_teacher_vbas(
        request_body: JsonObject,
        request: Request,
    ) -> JsonObject | BusinessResponse[JsonObject]:
        return await forward_vbas(
            request_body,
            request,
            capability="teacher_behavior",
            endpoint="/ImageDetect/teacher/v1.0.0",
            operation="online_vbas_teacher",
        )

    @app.post("/online/vbas/student", response_model=None)
    async def analyze_student_vbas(
        request_body: JsonObject,
        request: Request,
    ) -> JsonObject | BusinessResponse[JsonObject]:
        return await forward_vbas(
            request_body,
            request,
            capability="student_behavior",
            endpoint="/ImageDetect/student/v1.0.0",
            operation="online_vbas_student",
        )

    @app.post("/online/vbas/person-count", response_model=None)
    async def analyze_person_count_vbas(
        request_body: JsonObject,
        request: Request,
    ) -> JsonObject | BusinessResponse[JsonObject]:
        return await forward_vbas(
            request_body,
            request,
            capability="person_count",
            endpoint="/AE/SyncTasks2",
            operation="online_vbas_person_count",
        )

    @app.post("/api/online/face/recognize")
    async def recognize_face(
        request_body: JsonObject,
        request: Request,
    ) -> BusinessResponse[JsonObject]:
        photo = request_body.get("photo")
        if not await _valid_online_image(photo, service_settings):
            return BusinessResponse[JsonObject].failure(
                40001,
                "人脸对比请求必须包含有效的 Base64 图片",
            )
        lease_client = cast(OnlineLeaseClient, request.app.state.online_lease_client)
        operator_http = cast(httpx.AsyncClient, request.app.state.online_http_client)
        metrics = cast(PlatformMetrics, request.app.state.platform_metrics)
        try:
            async with lease_client.acquire(
                "recognize",
                ttl_seconds=service_settings.leases.request_ttl_seconds,
                work_context=_online_work_context(
                    "online_face_recognize",
                    trace_id=get_trace_id(),
                ),
            ) as lease:
                started = time.perf_counter()
                success = False
                try:
                    response = await asyncio.wait_for(
                        operator_http.post(
                            f"{lease.service_url.rstrip('/')}/recognize",
                            json=request_body,
                        ),
                        timeout=service_settings.http.hard_timeout_seconds,
                    )
                    response.raise_for_status()
                    body = response.json()
                    if not isinstance(body, dict):
                        raise ValueError("人脸对比响应不是 JSON 对象")  # noqa: TRY004
                    success = True
                finally:
                    metrics.observe_operator_request(
                        operator_code="facerec",
                        capability="recognize",
                        instance_id=lease.instance_id,
                        elapsed_seconds=time.perf_counter() - started,
                        success=success,
                    )
        except OnlineCapacityLeaseError:
            return BusinessResponse[JsonObject].failure(
                50301,
                "暂无可用人脸对比算子容量",
            )
        except (TimeoutError, httpx.HTTPError, ValueError):
            return BusinessResponse[JsonObject].failure(
                50000,
                "人脸对比算子调用失败",
            )
        return BusinessResponse[JsonObject].success(
            body,
            message="人脸对比完成",
        )

    async def call_face_persons(
        request: Request,
        operation: str,
        request_body: JsonObject | None = None,
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> BusinessResponse[JsonObject]:
        client = cast(FacePersonClient, request.app.state.face_person_client)
        try:
            if operation == "create":
                body = await client.create(request_body or {})
                message = "人物录入完成"
            elif operation == "batch":
                body = await client.create_batch(request_body or {})
                message = "批量人物录入完成"
            elif operation == "list":
                body = await client.list(skip=skip, limit=limit)
                message = "人物列表查询完成"
            elif operation == "search":
                body = await client.search(request_body or {})
                message = "人物查询完成"
            elif operation == "delete":
                body = await client.delete(request_body or {})
                message = "人物删除完成"
            else:  # pragma: no cover - route wiring uses a closed operation set
                raise FacePersonClientError("不支持的人物管理操作")
        except FacePersonClientError:
            return BusinessResponse[JsonObject].failure(
                50000,
                "人脸库管理调用失败",
            )
        return BusinessResponse[JsonObject].success(body, message=message)

    @app.post("/api/online/face/persons")
    async def create_face_person(
        request_body: JsonObject,
        request: Request,
    ) -> BusinessResponse[JsonObject]:
        return await call_face_persons(request, "create", request_body)

    @app.post("/api/online/face/persons/batch")
    async def create_face_persons_batch(
        request_body: JsonObject,
        request: Request,
    ) -> BusinessResponse[JsonObject]:
        return await call_face_persons(request, "batch", request_body)

    @app.get("/api/online/face/persons")
    async def list_face_persons(
        request: Request,
        skip: int = 0,
        limit: int = 100,
    ) -> BusinessResponse[JsonObject]:
        return await call_face_persons(request, "list", skip=skip, limit=limit)

    @app.post("/api/online/face/persons/search")
    async def search_face_persons(
        request_body: JsonObject,
        request: Request,
    ) -> BusinessResponse[JsonObject]:
        return await call_face_persons(request, "search", request_body)

    @app.delete("/api/online/face/persons/delete")
    async def delete_face_person(
        request_body: JsonObject,
        request: Request,
    ) -> BusinessResponse[JsonObject]:
        return await call_face_persons(request, "delete", request_body)

    @app.post("/api/online/image-quality/detect")
    async def detect_image_quality(
        request_body: JsonObject,
        request: Request,
    ) -> BusinessResponse[JsonObject]:
        image = request_body.get("image")
        if not await _valid_online_image(image, service_settings):
            return BusinessResponse[JsonObject].failure(
                40001,
                "图像质量检测请求必须包含有效的 Base64 图片",
            )
        lease_client = cast(OnlineLeaseClient, request.app.state.online_lease_client)
        operator_http = cast(httpx.AsyncClient, request.app.state.online_http_client)
        metrics = cast(PlatformMetrics, request.app.state.platform_metrics)
        try:
            async with lease_client.acquire(
                "detect_all",
                ttl_seconds=service_settings.leases.request_ttl_seconds,
                work_context=_online_work_context(
                    "online_image_quality",
                    trace_id=get_trace_id(),
                ),
            ) as lease:
                started = time.perf_counter()
                success = False
                try:
                    response = await asyncio.wait_for(
                        operator_http.post(
                            f"{lease.service_url.rstrip('/')}/detect_all",
                            json=request_body,
                        ),
                        timeout=service_settings.http.hard_timeout_seconds,
                    )
                    response.raise_for_status()
                    body = response.json()
                    if not isinstance(body, dict):
                        raise ValueError("图像质量检测响应不是 JSON 对象")  # noqa: TRY004
                    success = True
                finally:
                    metrics.observe_operator_request(
                        operator_code="screen_det",
                        capability="detect_all",
                        instance_id=lease.instance_id,
                        elapsed_seconds=time.perf_counter() - started,
                        success=success,
                    )
        except OnlineCapacityLeaseError:
            return BusinessResponse[JsonObject].failure(
                50301,
                "暂无可用图像质量检测算子容量",
            )
        except (TimeoutError, httpx.HTTPError, ValueError):
            return BusinessResponse[JsonObject].failure(
                50000,
                "图像质量检测算子调用失败",
            )
        return BusinessResponse[JsonObject].success(
            body,
            message="图像质量检测完成",
        )

    @app.post("/api/online/ocr/recognize")
    async def recognize_ocr(
        request_body: JsonObject,
        request: Request,
    ) -> BusinessResponse[JsonObject]:
        try:
            parsed = OnlineOcrRequest.model_validate(request_body)
        except ValidationError:
            return BusinessResponse[JsonObject].failure(
                40001,
                "OCR 在线请求参数不合法",
            )
        if not await _valid_online_image(parsed.image, service_settings):
            return BusinessResponse[JsonObject].failure(
                40001,
                "OCR 在线请求必须包含有效且未超限的 Base64 图片",
            )
        image_id = parsed.image_id or f"online-ocr-{uuid4().hex}"
        lease_client = cast(OnlineLeaseClient, request.app.state.online_lease_client)
        operator_http = cast(httpx.AsyncClient, request.app.state.online_http_client)
        metrics = cast(PlatformMetrics, request.app.state.platform_metrics)
        try:
            async with lease_client.acquire(
                "ocr",
                ttl_seconds=service_settings.leases.request_ttl_seconds,
                work_context=_online_work_context(
                    "online_ocr",
                    trace_id=get_trace_id(),
                ),
            ) as lease:
                started = time.perf_counter()
                success = False
                try:
                    response = await asyncio.wait_for(
                        operator_http.post(
                            f"{lease.service_url.rstrip('/')}/ocr/prediction",
                            json={
                                "key": [image_id],
                                "value": [parsed.image],
                                "enable_formula": parsed.enable_formula,
                            },
                        ),
                        timeout=service_settings.http.hard_timeout_seconds,
                    )
                    response.raise_for_status()
                    body = response.json()
                    if not isinstance(body, dict):
                        raise ValueError("OCR 响应不是 JSON 对象")  # noqa: TRY004
                    success = True
                finally:
                    metrics.observe_operator_request(
                        operator_code="ocr",
                        capability="ocr",
                        instance_id=lease.instance_id,
                        elapsed_seconds=time.perf_counter() - started,
                        success=success,
                    )
        except OnlineCapacityLeaseError:
            return BusinessResponse[JsonObject].failure(
                50301,
                "暂无可用 OCR 算子容量",
            )
        except (TimeoutError, httpx.HTTPError, ValueError):
            return BusinessResponse[JsonObject].failure(
                50000,
                "OCR 在线识别调用失败",
            )
        return BusinessResponse[JsonObject].success(
            body,
            message="OCR 在线识别完成",
        )

    @app.websocket("/api/online/asr/stream")
    async def stream_realtime_asr(websocket: WebSocket) -> None:
        await websocket.accept()
        lease_client = cast(OnlineLeaseClient, websocket.app.state.online_lease_client)
        connector = cast(
            AsrWebSocketConnector,
            websocket.app.state.asr_websocket_connector,
        )
        try:
            async with lease_client.acquire(
                "asr_online",
                ttl_seconds=service_settings.leases.websocket_ttl_seconds,
                work_context=_online_work_context(
                    "online_asr_session",
                    trace_id=websocket.headers.get(service_settings.service.trace_header),
                ),
            ) as lease:
                url = operator_websocket_url(lease.service_url)
                async with connector.connect(url) as upstream:
                    await asyncio.wait_for(
                        proxy_websocket(websocket, upstream),
                        timeout=service_settings.websocket.session_timeout_seconds,
                    )
        except OnlineCapacityLeaseError:
            await websocket.send_json(
                {
                    "code": 50301,
                    "message": "暂无可用实时 ASR 算子容量",
                    "data": None,
                }
            )
            await websocket.close(code=1013)
        except asyncio.CancelledError:
            return
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001 - WebSocket 边界统一关闭连接
            await websocket.close(code=1011, reason="实时 ASR 算子连接中断")

    @app.get("/ready")
    async def readiness() -> dict[str, str]:
        return {"service": service_settings.service.name, "status": "ready"}

    return app
