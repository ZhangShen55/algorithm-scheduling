"""Compatibility imports for the former flat API module."""

from .app.api.routes import create_online_gateway_app
from .app.domain.request_validation import is_base64_image as _is_base64_image
from .app.domain.request_validation import vbas_route as _vbas_route

__all__ = ["_is_base64_image", "_vbas_route", "create_online_gateway_app"]
