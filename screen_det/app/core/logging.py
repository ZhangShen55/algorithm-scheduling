from __future__ import annotations

from pathlib import Path

from packages.operator_registry_client import (
    FileLoggingSettings,
)
from packages.operator_registry_client import (
    configure_logging as configure_shared_logging,
)

from app.core.config import BASE_DIR, LoggingConfig


def setup_logging(config: LoggingConfig) -> None:
    log_dir = Path(config.directory)
    project_root = BASE_DIR
    if log_dir.is_absolute():
        try:
            log_dir = log_dir.resolve().relative_to(BASE_DIR)
        except ValueError:
            project_root = Path(config.directory).resolve().parent
            log_dir = Path(config.directory).resolve().name
    configure_shared_logging(
        FileLoggingSettings.from_mapping(
            {
                "level": config.level,
                "directory": str(log_dir),
                "file_name": config.file_name,
                "max_file_size_mib": config.max_file_size_mib,
                "retention_days": config.retention_days,
                "stdout_enabled": config.stdout_enabled,
                "file_enabled": config.file_enabled,
            },
            service_name="screen_det",
            project_root=project_root,
        )
    )
