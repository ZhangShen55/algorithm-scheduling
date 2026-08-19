from __future__ import annotations

import asyncio
import time
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, cast
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError, field_validator

from packages.platform_common.config import PlatformSettings
from packages.platform_common.metrics import PlatformMetrics
from packages.platform_common.trace import get_trace_id, new_trace_id
from packages.platform_contracts.responses import BusinessResponse

from ..core.config import ControlConfig, OnlineGatewaySettings, ServiceConfig
from ..core.service_app import create_gateway_base_app
from ..domain import decoded_base64_size, vbas_route
from ..infrastructure.capacity import (
    CapacityLease,
    OnlineCapacityLeaseClient,
    OnlineCapacityLeaseError,
    OnlineWorkContext,
)
from ..infrastructure.websocket_proxy import (
    AsrWebSocketConnector,
    WebsocketsAsrConnector,
    operator_websocket_url,
    proxy_websocket,
)

JsonObject = dict[str, Any]
IMAGE_ROUTE_PATHS = {
    "/api/online/vbas/analyze",
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
    ) -> AbstractAsyncContextManager[CapacityLease]: ...


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


def _valid_online_image(value: object, settings: OnlineGatewaySettings) -> bool:
    return isinstance(value, str) and decoded_base64_size(
        value,
        max_decoded_bytes=settings.base64.max_decoded_bytes,
        allow_data_uri=settings.base64.allow_data_uri,
    ) is not None


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
    )
    app.state.asr_websocket_connector = WebsocketsAsrConnector()

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

    @app.post("/api/online/vbas/analyze")
    async def analyze_vbas(
        request_body: JsonObject,
        request: Request,
    ) -> BusinessResponse[JsonObject]:
        route = vbas_route(request_body)
        if route is None:
            return BusinessResponse[JsonObject].failure(
                40001,
                "VBas 在线请求必须包含有效的 stream_type 和 Base64 图片",
            )
        if not all(
            _valid_online_image(item.get("StoragePath"), service_settings)
            for item in request_body["ImageList"]
        ):
            return BusinessResponse[JsonObject].failure(
                40001,
                "VBas 在线图片超过配置上限",
            )
        capability, endpoint = route
        lease_client = cast(OnlineLeaseClient, request.app.state.online_lease_client)
        operator_http = cast(httpx.AsyncClient, request.app.state.online_http_client)
        metrics = cast(PlatformMetrics, request.app.state.platform_metrics)
        try:
            async with lease_client.acquire(
                capability,
                ttl_seconds=service_settings.leases.request_ttl_seconds,
                work_context=_online_work_context(
                    "online_vbas",
                    trace_id=get_trace_id(),
                ),
            ) as lease:
                started = time.perf_counter()
                success = False
                try:
                    response = await asyncio.wait_for(
                        operator_http.post(
                            f"{lease.service_url.rstrip('/')}{endpoint}",
                            json=request_body,
                        ),
                        timeout=service_settings.http.hard_timeout_seconds,
                    )
                    response.raise_for_status()
                    body = response.json()
                    if not isinstance(body, dict):
                        raise ValueError("VBas 响应不是 JSON 对象")
                    success = True
                finally:
                    metrics.observe_operator_request(
                        operator_code="vbas",
                        capability=capability,
                        instance_id=lease.instance_id,
                        elapsed_seconds=time.perf_counter() - started,
                        success=success,
                    )
        except OnlineCapacityLeaseError:
            return BusinessResponse[JsonObject].failure(
                50301,
                "暂无可用 VBas 算子容量",
            )
        except (TimeoutError, httpx.HTTPError, ValueError):
            return BusinessResponse[JsonObject].failure(
                50000,
                "VBas 在线分析调用失败",
            )
        return BusinessResponse[JsonObject].success(
            body,
            message="VBas 在线分析完成",
        )

    @app.post("/api/online/face/recognize")
    async def recognize_face(
        request_body: JsonObject,
        request: Request,
    ) -> BusinessResponse[JsonObject]:
        photo = request_body.get("photo")
        if not _valid_online_image(photo, service_settings):
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
                        raise ValueError("人脸对比响应不是 JSON 对象")
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

    @app.post("/api/online/image-quality/detect")
    async def detect_image_quality(
        request_body: JsonObject,
        request: Request,
    ) -> BusinessResponse[JsonObject]:
        image = request_body.get("image")
        if not _valid_online_image(image, service_settings):
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
                        raise ValueError("图像质量检测响应不是 JSON 对象")
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
        if not _valid_online_image(parsed.image, service_settings):
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
                        raise ValueError("OCR 响应不是 JSON 对象")
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
        except Exception:
            await websocket.close(code=1011, reason="实时 ASR 算子连接中断")

    @app.get("/ready")
    async def readiness() -> dict[str, str]:
        return {"service": service_settings.service.name, "status": "ready"}

    return app
