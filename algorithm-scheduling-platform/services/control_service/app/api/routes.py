"""Stable API facade for the existing control-service routes."""

from services.control_service.api import create_control_app

__all__ = ["create_control_app"]
