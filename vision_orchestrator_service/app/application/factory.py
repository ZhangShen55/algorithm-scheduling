from fastapi import FastAPI

from packages.platform_common.application import create_service_app
from packages.platform_common.config import PlatformSettings

from ..api import router
from ..core.config import VisionSettings
from ..infrastructure.runtime import VisionOrchestratorRuntime


def create_vision_orchestrator_app(
    settings: VisionSettings | None = None,
    *,
    runtime: VisionOrchestratorRuntime | None = None,
) -> FastAPI:
    resolved = settings or VisionSettings()
    resolved_runtime = runtime or VisionOrchestratorRuntime(resolved)
    platform_settings = PlatformSettings(
        service_name=resolved.service.name,
        environment=resolved.service.environment,
        log_level=resolved.service.log_level,
        trace_header=resolved.service.trace_header,
        postgres_dsn=resolved.postgres.dsn,
        kafka_bootstrap_servers=resolved.kafka.bootstrap_servers,
        control_service_url=resolved.control.base_url,
        course_root=resolved.storage.course_root,
        result_root=resolved.storage.result_root,
    )
    app = create_service_app(
        platform_settings,
        service_lifespan=resolved_runtime.lifespan,
    )
    app.state.service_settings = resolved
    resolved_runtime.attach(app)
    app.include_router(router)
    return app
