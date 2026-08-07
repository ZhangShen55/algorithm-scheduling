from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from packages.platform_contracts.status import TaskType
from services.control_service.app.api.routes import create_control_app
from services.control_service.app.core.config import ControlSettings
from services.control_service.app.infrastructure.settings_adapter import to_platform_settings


def create_app(
    settings: ControlSettings | None = None,
    **dependencies: Any,
) -> FastAPI:
    resolved = settings or ControlSettings.load()
    app = create_control_app(
        settings=to_platform_settings(resolved),
        enabled_task_types={
            TaskType(task_type) for task_type in resolved.features.enabled_task_types
        },
        **dependencies,
    )
    app.state.service_settings = resolved
    return app
