from fastapi import FastAPI, Request
from fastapi.responses import Response

from packages.platform_common.config import PlatformSettings
from packages.platform_common.logging import configure_logging
from packages.platform_common.metrics import PlatformMetrics
from packages.platform_common.trace import trace_context


def create_gateway_base_app(settings: PlatformSettings) -> FastAPI:
    configure_logging(
        service_name=settings.service_name,
        level=settings.log_level,
        instance_id=settings.logging.instance_id,
        project_root=settings.project_root,
        logging_config=settings.logging.model_dump(),
    )
    metrics = PlatformMetrics()
    app = FastAPI(title=settings.service_name)
    app.state.settings = settings
    app.state.platform_metrics = metrics

    @app.middleware("http")
    async def add_trace_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        requested_trace = request.headers.get(settings.trace_header)
        with trace_context(requested_trace) as trace_id:
            response = await call_next(request)
            response.headers[settings.trace_header] = trace_id
            return response

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"service": settings.service_name, "status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        return Response(
            content=metrics.render(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return app
