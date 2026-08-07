"""Compatibility entrypoint; new deployments use services.control_service.app.main."""

from services.control_service.app.main import app, create_app

__all__ = ["app", "create_app"]
