from typing import Protocol

from fastapi import FastAPI, HTTPException

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


def create_orchestrator_api(
    settings: PlatformSettings,
    *,
    ppt_terminal_handler: PptTerminalHandler | None = None,
) -> FastAPI:
    """Create the operational API without claiming background loops are running."""

    app = create_service_app(settings)
    app.state.ppt_terminal_handler = ppt_terminal_handler

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
