from __future__ import annotations

import logging
from contextvars import ContextVar
from pathlib import Path

from packages.operator_registry_client import FileLoggingSettings, configure_logging

from app.core.config import PROJECT_ROOT, settings

LEVEL = settings.logger.level
LOG_PATH = settings.logger.log_path
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = request_id_ctx.get("-")
        return True


_initialized = False


def setup_logging() -> None:
    global _initialized
    if _initialized:
        return
    directory = Path(settings.logger.directory)
    project_root = PROJECT_ROOT
    if directory.is_absolute():
        try:
            directory = directory.resolve().relative_to(PROJECT_ROOT)
        except ValueError:
            project_root = directory.resolve().parent
            directory = directory.resolve().name
    configure_logging(
        FileLoggingSettings.from_mapping(
            {
                "level": settings.logger.level,
                "directory": str(directory),
                "file_name": settings.logger.file_name,
                "max_file_size_mib": settings.logger.max_file_size_mib,
                "retention_days": settings.logger.retention_days,
                "stdout_enabled": settings.logger.stdout_enabled,
                "file_enabled": settings.logger.file_enabled,
            },
            service_name="facerec",
            project_root=project_root,
        )
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(RequestIdFilter())
    _initialized = True


def get_logger(name: str = "app") -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)


def new_request_id() -> str:
    import uuid

    return uuid.uuid4().hex[:16]


setup_logging()
logger = get_logger(__name__)
logger.info("日志初始化完成 service=%s", "facerec")
