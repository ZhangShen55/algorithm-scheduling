from fastapi import FastAPI

from services.orchestrator_service.app.api.routes import create_orchestrator_api
from services.orchestrator_service.app.core.config import OrchestratorSettings
from services.orchestrator_service.app.infrastructure.settings_adapter import to_platform_settings


def create_app(settings: OrchestratorSettings | None = None) -> FastAPI:
    resolved = settings or OrchestratorSettings.load()
    app = create_orchestrator_api(to_platform_settings(resolved))
    app.state.service_settings = resolved
    # Kafka consumers, Outbox publishing and node execution are wired in a later runtime step.
    app.state.runtime_loops_started = False
    return app
