"""Compatibility entrypoint; new deployments use services.orchestrator_service.app.main."""

from services.orchestrator_service.app.main import app, create_app

__all__ = ["app", "create_app"]
