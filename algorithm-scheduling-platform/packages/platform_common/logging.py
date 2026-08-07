import json
import logging
from datetime import UTC, datetime
from typing import Any

from packages.platform_common.trace import get_trace_id

_STANDARD_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__)


class JsonFormatter(logging.Formatter):
    def __init__(self, *, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "service": self.service_name,
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "trace_id": get_trace_id(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(*, service_name: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service_name=service_name))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())


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
