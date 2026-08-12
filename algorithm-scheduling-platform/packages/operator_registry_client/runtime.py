from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from packages.operator_registry_client.client import (
    OperatorRegistryClient,
    OperatorRegistryClientConfig,
    OperatorRuntimeStatus,
)
from packages.operator_registry_client.lifecycle import OperatorLifecycle
from packages.operator_registry_client.ops import OperatorOpsStatus

ModelReadyProvider = Callable[[], bool]
InflightProvider = Callable[[], int]
BeforeRegistryShutdown = Callable[[], None]


class OperatorRuntime:
    def __init__(
        self,
        *,
        declared_capacity: int,
        model_ready_provider: ModelReadyProvider,
        inflight_provider: InflightProvider | None = None,
    ) -> None:
        if declared_capacity <= 0:
            raise ValueError("算子声明容量必须大于 0")
        self._declared_capacity = declared_capacity
        self._model_ready_provider = model_ready_provider
        self._inflight_provider = inflight_provider
        self._lifecycle = OperatorLifecycle.ONLINE
        self._inflight = 0

    @property
    def accepting_work(self) -> bool:
        return self._lifecycle is OperatorLifecycle.ONLINE

    def enter(self) -> None:
        self._inflight += 1

    def leave(self) -> None:
        self._inflight = max(0, self._inflight - 1)

    def drain(self) -> None:
        self._lifecycle = OperatorLifecycle.DRAINING

    def status(self) -> OperatorOpsStatus:
        inflight = self._inflight if self._inflight_provider is None else self._inflight_provider()
        return OperatorOpsStatus(
            lifecycle=self._lifecycle,
            model_ready=self._model_ready_provider(),
            inflight=inflight,
            declared_capacity=self._declared_capacity,
        )

    def heartbeat_status(self) -> OperatorRuntimeStatus:
        status = self.status()
        return OperatorRuntimeStatus(
            inflight=status.inflight,
            model_ready=status.model_ready,
        )


class OperatorAdmissionMiddleware:
    def __init__(self, app: Any, *, runtime: OperatorRuntime) -> None:
        self._app = app
        self._runtime = runtime

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        path = str(scope.get("path", ""))
        tracked = scope_type in {"http", "websocket"} and not _is_ops_path(path)
        if tracked and not self._runtime.accepting_work:
            if scope_type == "websocket":
                await send({"type": "websocket.close", "code": 1013})
            else:
                body = json.dumps(
                    {"detail": "算子实例正在排空，拒绝新请求"},
                    ensure_ascii=False,
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 503,
                        "headers": [(b"content-type", b"application/json; charset=utf-8")],
                    }
                )
                await send({"type": "http.response.body", "body": body})
            return
        if tracked:
            self._runtime.enter()
        try:
            await self._app(scope, receive, send)
        finally:
            if tracked:
                self._runtime.leave()


def install_operator_runtime(
    app: FastAPI,
    *,
    operator_code: str,
    capabilities: list[str],
    default_port: int,
    declared_capacity: int = 1,
    model_ready_provider: ModelReadyProvider = lambda: True,
    inflight_provider: InflightProvider | None = None,
    before_registry_shutdown: BeforeRegistryShutdown | None = None,
    registration_enabled: bool | None = None,
) -> OperatorRuntime:
    if not operator_code or not capabilities:
        raise ValueError("算子代码和能力列表不能为空")
    configured_capacity = int(
        os.getenv("PLATFORM_DECLARED_CAPACITY", str(declared_capacity))
    )
    runtime = OperatorRuntime(
        declared_capacity=configured_capacity,
        model_ready_provider=model_ready_provider,
        inflight_provider=inflight_provider,
    )
    app.add_middleware(OperatorAdmissionMiddleware, runtime=runtime)
    _add_missing_ops_routes(app, runtime)

    enabled = (
        _env_bool("PLATFORM_REGISTRATION_ENABLED", default=False)
        if registration_enabled is None
        else registration_enabled
    )
    if enabled:
        control_service_url = os.getenv("PLATFORM_CONTROL_SERVICE_URL", "").strip()
        if not control_service_url:
            raise ValueError("启用算子注册时必须配置 PLATFORM_CONTROL_SERVICE_URL")
        instance_id = os.getenv(
            "PLATFORM_INSTANCE_ID",
            f"{operator_code}-{socket.gethostname()}-{default_port}",
        )
        service_url = os.getenv(
            "PLATFORM_SERVICE_URL",
            f"http://127.0.0.1:{default_port}",
        )
        registry_client = OperatorRegistryClient(
            OperatorRegistryClientConfig(
                control_service_url=control_service_url,
                instance_id=instance_id,
                operator_code=operator_code,
                capabilities=capabilities,
                service_url=service_url,
                declared_capacity=configured_capacity,
                model_version=os.getenv("PLATFORM_MODEL_VERSION") or None,
                api_version=os.getenv("PLATFORM_API_VERSION") or None,
                labels=_env_labels(),
                heartbeat_interval_seconds=float(
                    os.getenv("PLATFORM_HEARTBEAT_INTERVAL_SECONDS", "5")
                ),
            ),
            status_provider=runtime.heartbeat_status,
        )
        _wrap_lifespan(
            app,
            registry_client,
            before_registry_shutdown=before_registry_shutdown,
        )
    app.state.operator_runtime = runtime
    return runtime


def _add_missing_ops_routes(app: FastAPI, runtime: OperatorRuntime) -> None:
    existing = {
        path
        for route in app.routes
        if isinstance(path := getattr(route, "path", None), str)
    }
    if "/ops/health" not in existing:

        @app.get("/ops/health", tags=["operator-ops"])
        async def operator_health() -> dict[str, str]:
            return {"status": "alive"}

    if "/ops/status" not in existing:

        @app.get("/ops/status", tags=["operator-ops"])
        async def operator_status() -> OperatorOpsStatus:
            return runtime.status()

    if "/ops/drain" not in existing:

        @app.post("/ops/drain", tags=["operator-ops"])
        async def operator_drain() -> OperatorOpsStatus:
            runtime.drain()
            return runtime.status()


def _wrap_lifespan(
    app: FastAPI,
    registry_client: OperatorRegistryClient,
    *,
    before_registry_shutdown: BeforeRegistryShutdown | None = None,
) -> None:
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(application: FastAPI):  # type: ignore[no-untyped-def]
        async with original_lifespan(application):
            await registry_client.start()
            try:
                yield
            finally:
                if before_registry_shutdown is not None:
                    before_registry_shutdown()
                try:
                    await registry_client.stop()
                finally:
                    await registry_client.aclose()

    app.router.lifespan_context = combined_lifespan


def _is_ops_path(path: str) -> bool:
    return path.startswith("/ops/") or path in {
        "/health",
        "/metrics",
        "/AE/Health",
        "/AE/WorkerStatus",
        "/AE/Drain",
    }


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_labels() -> dict[str, str]:
    raw = os.getenv("PLATFORM_INSTANCE_LABELS", "").strip()
    if not raw:
        gpu = os.getenv("PLATFORM_GPU_ID", "").strip()
        return {"gpu": gpu} if gpu else {}
    value = json.loads(raw)
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError("PLATFORM_INSTANCE_LABELS 必须是字符串键值 JSON 对象")
    return value
