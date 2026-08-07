"""Stable API facade for the existing control-service routes."""

from .control import create_control_app

__all__ = ["create_control_app"]
