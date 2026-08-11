from fastapi import FastAPI

from ..api.routes import create_orchestrator_api
from ..core.config import OrchestratorSettings
from ..infrastructure.runtime import OrchestratorRuntime
from ..infrastructure.settings_adapter import to_platform_settings


def create_app(
    settings: OrchestratorSettings | None = None,
    *,
    runtime: OrchestratorRuntime | None = None,
) -> FastAPI:
    resolved = settings or OrchestratorSettings.load()
    resolved_runtime = runtime or OrchestratorRuntime(resolved)
    app = create_orchestrator_api(
        to_platform_settings(resolved),
        service_lifespan=resolved_runtime.lifespan,
    )
    app.state.service_settings = resolved
    resolved_runtime.attach(app)
    return app
