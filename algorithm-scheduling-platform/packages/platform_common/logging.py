import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from packages.operator_registry_client.logging import (
    FileLoggingSettings,
)
from packages.operator_registry_client.logging import (
    JsonFormatter as _OperatorJsonFormatter,
)
from packages.operator_registry_client.logging import (
    configure_logging as _configure_file_logging,
)
from packages.platform_common.trace import get_trace_id


class JsonFormatter(_OperatorJsonFormatter):
    """Keep the platform formatter's historical optional instance argument."""

    def __init__(self, *, service_name: str, instance_id: str = "local") -> None:
        super().__init__(service_name=service_name, instance_id=instance_id)

    def format(self, record: logging.LogRecord) -> str:
        if not getattr(record, "trace_id", None):
            record.trace_id = get_trace_id()
        return super().format(record)


def configure_logging(
    *,
    service_name: str,
    level: str = "INFO",
    instance_id: str | None = None,
    project_root: str | Path = ".",
    logging_config: Mapping[str, Any] | None = None,
) -> None:
    values = dict(logging_config or {})
    values.setdefault("level", level)
    settings = FileLoggingSettings.from_mapping(
        values,
        service_name=service_name,
        project_root=project_root,
        instance_id=instance_id,
    )
    _configure_file_logging(settings)
    for handler in logging.getLogger().handlers:
        handler.addFilter(_TraceContextFilter())


class _TraceContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()
        return True


def log_node_audit(
    logger: logging.Logger,
    *,
    event: str,
    task_id: str,
    task_type: str,
    node: str,
    attempt: int,
    instance_id: str,
    model_version: str | None,
    elapsed_ms: float,
    outcome: str,
) -> None:
    if attempt < 0:
        raise ValueError("节点执行 attempt 不能小于 0")
    if elapsed_ms < 0:
        raise ValueError("节点执行耗时不能小于 0")
    logger.info(
        event,
        extra={
            "audit_type": "node_execution",
            "task_id": task_id,
            "task_type": task_type,
            "node": node,
            "attempt": attempt,
            "instance_id": instance_id,
            "model_version": model_version,
            "elapsed_ms": elapsed_ms,
            "outcome": outcome,
        },
    )
