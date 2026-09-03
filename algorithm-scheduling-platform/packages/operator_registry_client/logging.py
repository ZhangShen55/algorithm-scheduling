"""Shared file/stdout logging for algorithm operator processes."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc  # noqa: UP017 - 保持 Python 3.10 wheel 兼容。

_STANDARD_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__)
_ALLOWED_CONTEXT_FIELDS = frozenset(
    {
        "audit_type",
        "task_id",
        "task_type",
        "node",
        "operator_code",
        "operator_task_id",
        "lease_id",
        "capability",
        "capacity_pool",
        "source_service",
        "work_type",
        "work_id",
        "attempt",
        "instance_id",
        "model_version",
        "api_version",
        "batch_id",
        "image_id",
        "status",
        "stage",
        "exception_type",
        "elapsed_ms",
        "elapsed_seconds",
        "remaining_seconds",
        "duration_ms",
        "outcome",
        "event_size_bytes",
    }
)
_SENSITIVE_KEY_RE = re.compile(
    r"(?:authorization|token|password|passwd|secret|cookie|dsn|base64|pcm|audio|image|media|"
    r"asr(?:_text|_result)?|ocr(?:_text|_result)?)",
    re.IGNORECASE,
)
_DATA_URI_RE = re.compile(r"data:[^,;\s]+(?:;[^,\s]+)*;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)
_CREDENTIAL_RE = re.compile(
    r"(?i)(authorization|token|password|passwd|secret|cookie|dsn)\s*[:=]\s*[^\s,;]+"
)


@dataclass(frozen=True, slots=True)
class FileLoggingSettings:
    """Validated logging values shared by all operator entrypoints."""

    service_name: str
    instance_id: str
    project_root: Path
    level: str = "INFO"
    directory: str = "logs"
    file_name: str = "application.log"
    max_file_size_mib: int = 100
    retention_days: int = 7
    stdout_enabled: bool = True
    file_enabled: bool = True

    def __post_init__(self) -> None:
        if not self.service_name.strip():
            raise ValueError("logging.service_name 不能为空")
        _validate_component(self.instance_id, field_name="logging.instance_id")
        if self.level.upper() not in {
            "CRITICAL",
            "ERROR",
            "WARNING",
            "INFO",
            "DEBUG",
            "NOTSET",
        }:
            raise ValueError("logging.level 不是有效的 Python 日志级别")
        if type(self.max_file_size_mib) is not int or self.max_file_size_mib <= 0:
            raise ValueError("logging.max_file_size_mib 必须是正整数")
        if type(self.retention_days) is not int or self.retention_days <= 0:
            raise ValueError("logging.retention_days 必须是正整数")
        if type(self.stdout_enabled) is not bool or type(self.file_enabled) is not bool:
            raise ValueError("logging.stdout_enabled/file_enabled 必须是布尔值")
        root = self.project_root.expanduser().resolve()
        directory = Path(self.directory)
        if directory.is_absolute() or ".." in directory.parts:
            raise ValueError("logging.directory 必须是项目根目录下的相对路径")
        if not self.file_name or Path(self.file_name).name != self.file_name:
            raise ValueError("logging.file_name 必须是单个文件名")
        if self.file_name in {".", ".."} or self.file_name.startswith("."):
            raise ValueError("logging.file_name 不能是隐藏或越界路径")
        object.__setattr__(self, "project_root", root)
        object.__setattr__(self, "level", self.level.upper())

    @property
    def log_directory(self) -> Path:
        path = (self.project_root / self.directory / self.instance_id).resolve()
        try:
            path.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError("日志目录越过项目根目录") from exc
        return path

    @property
    def log_path(self) -> Path:
        return self.log_directory / self.file_name

    @classmethod
    def from_mapping(
        cls,
        mapping: Mapping[str, Any] | None,
        *,
        service_name: str,
        project_root: str | Path,
        instance_id: str | None = None,
    ) -> FileLoggingSettings:
        values = dict(mapping or {})
        configured_instance = values.pop("instance_id", None)
        selected_instance = (
            instance_id
            or configured_instance
            or os.getenv("PLATFORM_INSTANCE_ID")
            or "local"
        )
        return cls(
            service_name=service_name,
            instance_id=str(selected_instance),
            project_root=Path(project_root),
            level=str(values.get("level", "INFO")),
            directory=str(values.get("directory", "logs")),
            file_name=str(values.get("file_name", "application.log")),
            max_file_size_mib=values.get("max_file_size_mib", 100),
            retention_days=values.get("retention_days", 7),
            stdout_enabled=values.get("stdout_enabled", True),
            file_enabled=values.get("file_enabled", True),
        )


def _validate_component(value: str, *, field_name: str) -> None:
    if not value or value in {".", ".."} or Path(value).name != value:
        raise ValueError(f"{field_name} 不能包含路径分隔符")


def _redact_text(value: str) -> str:
    value = _DATA_URI_RE.sub("<已脱敏媒体数据>", value)
    return _CREDENTIAL_RE.sub(lambda match: f"{match.group(1)}=<已脱敏>", value)


def _safe_context(record: logging.LogRecord) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_RECORD_FIELDS or key.startswith("_"):
            continue
        if key not in _ALLOWED_CONTEXT_FIELDS or _SENSITIVE_KEY_RE.search(key):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            context[key] = _redact_text(str(value)) if isinstance(value, str) else value
    return context


class JsonFormatter(logging.Formatter):
    def __init__(self, *, service_name: str, instance_id: str) -> None:
        super().__init__()
        self.service_name = service_name
        self.instance_id = instance_id

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "service": self.service_name,
            "instance_id": self.instance_id,
            "level": record.levelname,
            "logger": record.name,
            "event": _redact_text(record.getMessage()),
            "trace_id": getattr(record, "trace_id", None)
            or os.getenv("TRACE_ID", "-"),
        }
        payload.update(_safe_context(record))
        if record.exc_info:
            payload["exception"] = _redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


class SizeAndAgeRotatingFileHandler(logging.Handler):
    """Rotate before the size limit and remove only old files in this instance directory."""

    def __init__(self, settings: FileLoggingSettings) -> None:
        super().__init__()
        self.settings = settings
        self.settings.log_directory.mkdir(parents=True, exist_ok=True)
        self._stream: Any = None
        self._lock = threading.RLock()
        self._max_bytes = settings.max_file_size_mib * 1024 * 1024
        self._open()
        self._cleanup_expired()

    def _open(self) -> None:
        self._stream = self.settings.log_path.open("a", encoding="utf-8")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            with self._lock:
                message = self.format(record)
                message = _bound_message(message, self._max_bytes)
                encoded_size = len((message + "\n").encode("utf-8"))
                if self.settings.log_path.exists() and (
                    self.settings.log_path.stat().st_size + encoded_size > self._max_bytes
                ):
                    self._rollover()
                self._stream.write(message + "\n")
                self._stream.flush()
        except Exception:
            self.handleError(record)

    def _rollover(self) -> None:
        self._stream.close()
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        archive = self.settings.log_directory / f"{self.settings.file_name}.{timestamp}"
        suffix = 1
        while archive.exists() or archive.is_symlink():
            archive = self.settings.log_directory / (
                f"{self.settings.file_name}.{timestamp}.{suffix}"
            )
            suffix += 1
        self.settings.log_path.replace(archive)
        self._open()
        self._cleanup_expired()

    def _cleanup_expired(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(days=self.settings.retention_days)
        prefix = f"{self.settings.file_name}."
        for candidate in self.settings.log_directory.iterdir():
            if (
                candidate.is_symlink()
                or not candidate.is_file()
                or not candidate.name.startswith(prefix)
            ):
                continue
            modified = datetime.fromtimestamp(candidate.stat().st_mtime, tz=UTC)
            if modified < cutoff:
                candidate.unlink()

    def close(self) -> None:
        with self._lock:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
        super().close()


def _bound_message(message: str, max_bytes: int) -> str:
    encoded = (message + "\n").encode("utf-8")
    if len(encoded) <= max_bytes:
        return message
    # 单条事件也必须受文件上限约束，优先保留合法 JSON 的状态字段。
    try:
        payload = json.loads(message)
        if isinstance(payload, dict):
            payload["event"] = "<日志事件过大，内容已截断>"
            payload.pop("exception", None)
            message = json.dumps(payload, ensure_ascii=False, default=str)
            encoded = (message + "\n").encode("utf-8")
    except (TypeError, ValueError):
        pass
    if len(encoded) > max_bytes:
        message = encoded[: max_bytes - 1].decode("utf-8", errors="ignore")
    return message


def configure_logging(settings: FileLoggingSettings) -> None:
    """Install one idempotent JSON configuration for root and Uvicorn loggers."""
    handlers: list[logging.Handler] = []
    formatter = JsonFormatter(
        service_name=settings.service_name,
        instance_id=settings.instance_id,
    )
    if settings.stdout_enabled:
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(formatter)
        handlers.append(stdout_handler)
    if settings.file_enabled:
        file_handler = SizeAndAgeRotatingFileHandler(settings)
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    if not handlers:
        raise ValueError("至少启用 stdout 或文件日志之一")

    root = logging.getLogger()
    for old_handler in root.handlers[:]:
        root.removeHandler(old_handler)
        old_handler.close()
    root.setLevel(settings.level)
    for handler in handlers:
        root.addHandler(handler)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
        logger.setLevel(settings.level)


__all__ = [
    "FileLoggingSettings",
    "JsonFormatter",
    "SizeAndAgeRotatingFileHandler",
    "configure_logging",
]
