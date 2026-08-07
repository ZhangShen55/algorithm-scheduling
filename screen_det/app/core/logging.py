from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import BASE_DIR, LoggingConfig


def _beijing_time(timestamp: float | None = None) -> time.struct_time:
    current = time.time() if timestamp is None else timestamp
    return time.gmtime(current + 8 * 3600)


def setup_logging(config: LoggingConfig) -> None:
    log_dir = Path(config.log_dir)
    if not log_dir.is_absolute():
        log_dir = BASE_DIR / log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, config.level.upper(), logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    formatter.converter = _beijing_time

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    app_handler = RotatingFileHandler(
        log_dir / config.app_log,
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
        encoding="utf-8",
    )
    app_handler.setFormatter(formatter)
    app_handler.setLevel(level)

    access_handler = RotatingFileHandler(
        log_dir / config.access_log,
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
        encoding="utf-8",
    )
    access_handler.setFormatter(formatter)
    access_handler.setLevel(level)

    root.addHandler(console_handler)
    root.addHandler(app_handler)

    access_logger = logging.getLogger("tilt.access")
    access_logger.setLevel(level)
    access_logger.handlers.clear()
    access_logger.addHandler(access_handler)
    access_logger.propagate = False
