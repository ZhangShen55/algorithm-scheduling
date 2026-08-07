from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import Response

from packages.platform_common.config import PlatformSettings
from packages.platform_common.logging import configure_logging
from packages.platform_common.metrics import PlatformMetrics
from packages.platform_common.trace import trace_context
from packages.platform_common.workspace import ensure_workspace_roots

ServiceLifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_service_app(
    settings: PlatformSettings | None = None,
    *,
    service_lifespan: ServiceLifespan | None = None,
) -> FastAPI:
    resolved = settings or PlatformSettings()
    configure_logging(service_name=resolved.service_name, level=resolved.log_level)
    metrics = PlatformMetrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ensure_workspace_roots(resolved)
        metrics.update_disk_usage(resolved.course_root, kind="course")
        metrics.update_disk_usage(resolved.result_root, kind="result")
        if service_lifespan is None:
            yield
        else:
            async with service_lifespan(app):
                yield

    app = FastAPI(title=resolved.service_name, lifespan=lifespan)
    app.state.settings = resolved
    app.state.platform_metrics = metrics

    @app.middleware("http")
    async def add_trace_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        requested_trace = request.headers.get(resolved.trace_header)
        with trace_context(requested_trace) as trace_id:
            response = await call_next(request)
            response.headers[resolved.trace_header] = trace_id
            return response

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"service": resolved.service_name, "status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        metrics.update_disk_usage(resolved.course_root, kind="course")
        metrics.update_disk_usage(resolved.result_root, kind="result")
        return Response(
            content=metrics.render(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return app
