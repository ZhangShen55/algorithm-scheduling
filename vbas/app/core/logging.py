from __future__ import annotations

from packages.operator_registry_client import FileLoggingSettings, configure_logging

from .settings import LOGGING_CONFIG, PROJECT_ROOT, settings


def setup_logging() -> None:
    configured = dict(LOGGING_CONFIG)
    configure_logging(
        FileLoggingSettings.from_mapping(
            configured,
            service_name="vbas",
            project_root=PROJECT_ROOT,
            instance_id=str(getattr(settings, "InstanceId", "vbas-local")),
        )
    )


__all__ = ["setup_logging"]
