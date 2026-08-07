from __future__ import annotations

import asyncio
import time
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, cast

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect

from packages.platform_common.config import PlatformSettings
from packages.platform_common.metrics import PlatformMetrics
from packages.platform_contracts.responses import BusinessResponse

from ..core.config import OnlineGatewaySettings
from ..core.service_app import create_gateway_base_app
from ..domain import is_base64_image, vbas_route
from ..infrastructure.capacity import (
    CapacityLease,
    OnlineCapacityLeaseClient,
    OnlineCapacityLeaseError,
)
from ..infrastructure.websocket_proxy import (
    AsrWebSocketConnector,
    WebsocketsAsrConnector,
    operator_websocket_url,
    proxy_websocket,
)

JsonObject = dict[str, Any]


class OnlineLeaseClient(Protocol):
    def acquire(
        self,
        capability: str,
        *,
        ttl_seconds: int = 60,
    ) -> AbstractAsyncContextManager[CapacityLease]: ...


def create_online_gateway_app(
    settings: PlatformSettings | OnlineGatewaySettings | None = None,
) -> FastAPI:
    if isinstance(settings, PlatformSettings):
        service_settings = OnlineGatewaySettings(
            service={
                "name": settings.service_name,
                "environment": settings.environment,
                "log_level": settings.log_level,
                "trace_header": settings.trace_header,
            },
            control={"base_url": settings.control_service_url},
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
        capability, endpoint = route
        lease_client = cast(OnlineLeaseClient, request.app.state.online_lease_client)
        operator_http = cast(httpx.AsyncClient, request.app.state.online_http_client)
        metrics = cast(PlatformMetrics, request.app.state.platform_metrics)
        try:
            async with lease_client.acquire(capability, ttl_seconds=60) as lease:
                started = time.perf_counter()
                success = False
                try:
                    response = await operator_http.post(
                        f"{lease.service_url.rstrip('/')}{endpoint}",
                        json=request_body,
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
        except (httpx.HTTPError, ValueError):
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
        if not isinstance(photo, str) or not is_base64_image(photo):
            return BusinessResponse[JsonObject].failure(
                40001,
                "人脸对比请求必须包含有效的 Base64 图片",
            )
        lease_client = cast(OnlineLeaseClient, request.app.state.online_lease_client)
        operator_http = cast(httpx.AsyncClient, request.app.state.online_http_client)
        metrics = cast(PlatformMetrics, request.app.state.platform_metrics)
        try:
            async with lease_client.acquire("recognize", ttl_seconds=60) as lease:
                started = time.perf_counter()
                success = False
                try:
                    response = await operator_http.post(
                        f"{lease.service_url.rstrip('/')}/recognize",
                        json=request_body,
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
        except (httpx.HTTPError, ValueError):
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
        if not isinstance(image, str) or not is_base64_image(image):
            return BusinessResponse[JsonObject].failure(
                40001,
                "图像质量检测请求必须包含有效的 Base64 图片",
            )
        lease_client = cast(OnlineLeaseClient, request.app.state.online_lease_client)
        operator_http = cast(httpx.AsyncClient, request.app.state.online_http_client)
        metrics = cast(PlatformMetrics, request.app.state.platform_metrics)
        try:
            async with lease_client.acquire("detect_all", ttl_seconds=60) as lease:
                started = time.perf_counter()
                success = False
                try:
                    response = await operator_http.post(
                        f"{lease.service_url.rstrip('/')}/detect_all",
                        json=request_body,
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
        except (httpx.HTTPError, ValueError):
            return BusinessResponse[JsonObject].failure(
                50000,
                "图像质量检测算子调用失败",
            )
        return BusinessResponse[JsonObject].success(
            body,
            message="图像质量检测完成",
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
            async with lease_client.acquire("asr_online", ttl_seconds=3_600) as lease:
                url = operator_websocket_url(lease.service_url)
                async with connector.connect(url) as upstream:
                    await proxy_websocket(websocket, upstream)
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
