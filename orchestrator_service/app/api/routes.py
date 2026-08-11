from contextlib import AbstractAsyncContextManager
from typing import Protocol

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from packages.platform_common.application import create_service_app
from packages.platform_common.config import PlatformSettings

from ..infrastructure.ppt_slice import (
    PptSliceCallbackError,
    PptSliceTerminalCallback,
    PptTerminalHandleResult,
)


class PptTerminalHandler(Protocol):
    def handle_callback(
        self,
        *,
        node_id: int,
        callback: PptSliceTerminalCallback,
    ) -> PptTerminalHandleResult: ...


class RuntimeReadiness(Protocol):
    async def readiness(self) -> dict[str, object]: ...


class RuntimeLifespan(Protocol):
    def __call__(self, app: FastAPI) -> AbstractAsyncContextManager[None]: ...


def create_orchestrator_api(
    settings: PlatformSettings,
    *,
    ppt_terminal_handler: PptTerminalHandler | None = None,
    service_lifespan: RuntimeLifespan | None = None,
) -> FastAPI:
    """Create the operational API; the runtime is attached by the service factory."""

    app = create_service_app(settings, service_lifespan=service_lifespan)
    app.state.ppt_terminal_handler = ppt_terminal_handler
    app.state.orchestrator_runtime = None

    @app.get("/ops/readiness")
    async def readiness() -> JSONResponse:
        runtime: RuntimeReadiness | None = app.state.orchestrator_runtime
        if runtime is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "checks": {
                        "runtime": {
                            "ready": False,
                            "detail": "orchestrator 运行时尚未装配",
                        }
                    },
                },
            )
        report = await runtime.readiness()
        return JSONResponse(
            status_code=200 if report["status"] == "ready" else 503,
            content=report,
        )

    @app.post("/internal/ppt-slice/callback/{node_id}")
    async def ppt_slice_terminal_callback(
        node_id: int,
        callback: PptSliceTerminalCallback,
    ) -> dict[str, object]:
        handler = app.state.ppt_terminal_handler
        if handler is None:
            raise HTTPException(status_code=503, detail="PPT 终态处理器尚未启动")
        try:
            result = handler.handle_callback(node_id=node_id, callback=callback)
        except PptSliceCallbackError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "node_id": node_id,
            "completed": result.completed,
            "duplicate": result.duplicate,
            "path": str(result.path) if result.path is not None else None,
            "count": result.count,
        }

    return app
