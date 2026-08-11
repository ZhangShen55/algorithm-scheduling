from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from packages.platform_contracts.status import TaskType

from ..api.routes import create_control_app
from ..core.config import ControlSettings
from ..infrastructure.runtime import ControlRuntime
from ..infrastructure.settings_adapter import to_platform_settings


def create_app(
    settings: ControlSettings | None = None,
    **dependencies: Any,
) -> FastAPI:
    resolved = settings or ControlSettings.load()
    runtime = ControlRuntime(
        resolved,
        repository=dependencies.pop("repository", None),
        operator_registry=dependencies.pop("operator_registry", None),
    )
    app = create_control_app(
        settings=to_platform_settings(resolved),
        enabled_task_types={
            TaskType(task_type) for task_type in resolved.features.enabled_task_types
        },
        runtime=runtime,
        **dependencies,
    )
    app.state.service_settings = resolved
    return app
