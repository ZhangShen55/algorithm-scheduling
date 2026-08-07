from fastapi import FastAPI

from ..api.routes import create_orchestrator_api
from ..core.config import OrchestratorSettings
from ..infrastructure.settings_adapter import to_platform_settings


def create_app(settings: OrchestratorSettings | None = None) -> FastAPI:
    resolved = settings or OrchestratorSettings.load()
    app = create_orchestrator_api(to_platform_settings(resolved))
    app.state.service_settings = resolved
    # Kafka consumers, Outbox publishing and node execution are wired in a later runtime step.
    app.state.runtime_loops_started = False
    return app
