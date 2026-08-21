"""PPT Slice logging facade backed by the operator logging contract."""

from __future__ import annotations

import logging
from pathlib import Path

from packages.operator_registry_client import FileLoggingSettings, configure_logging

from app.core.config import PROJECT_ROOT, settings


class LoggerManager:
    """Keep the historical get_logger API while configuring the root once."""

    _configured = False

    @classmethod
    def _ensure_configured(cls) -> None:
        if cls._configured:
            return
        directory = Path(settings.LOG_DIR)
        project_root = PROJECT_ROOT
        if directory.is_absolute():
            try:
                directory = directory.resolve().relative_to(PROJECT_ROOT)
            except ValueError:
                project_root = Path(settings.LOG_DIR).resolve().parent
                directory = Path(settings.LOG_DIR).resolve().name
        configure_logging(
            FileLoggingSettings.from_mapping(
                {
                    "level": settings.LOG_LEVEL,
                    "directory": str(directory),
                    "file_name": settings.LOG_FILE,
                    "max_file_size_mib": settings.LOG_MAX_FILE_SIZE_MIB,
                    "retention_days": settings.LOG_RETENTION_DAYS,
                    "stdout_enabled": settings.LOG_STDOUT_ENABLED,
                    "file_enabled": settings.LOG_FILE_ENABLED,
                },
                service_name="ppt_slice",
                project_root=project_root,
            )
        )
        cls._configured = True

    @classmethod
    def get_logger(cls, name: str = "app") -> logging.Logger:
        cls._ensure_configured()
        return logging.getLogger(name)


def get_logger(name: str = "app") -> logging.Logger:
    return LoggerManager.get_logger(name)


logger = get_logger("app")
