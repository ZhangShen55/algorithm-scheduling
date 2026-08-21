from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.operator_registry_client.logging import (
    FileLoggingSettings,
    SizeAndAgeRotatingFileHandler,
    configure_logging,
)


def _settings(tmp_path: Path, **overrides: object) -> FileLoggingSettings:
    values: dict[str, object] = {
        "service_name": "test-operator",
        "instance_id": "gpu0",
        "project_root": tmp_path,
        "max_file_size_mib": 1,
        "retention_days": 7,
    }
    values.update(overrides)
    return FileLoggingSettings(**values)


def test_logging_settings_resolve_project_root_and_reject_escape(tmp_path: Path) -> None:
    settings = FileLoggingSettings.from_mapping(
        {"directory": "logs", "file_name": "application.log"},
        service_name="ocr",
        project_root=tmp_path,
        instance_id="ocr-gpu0",
    )
    assert settings.log_path == tmp_path / "logs" / "ocr-gpu0" / "application.log"
    with pytest.raises(ValueError, match="项目根目录"):
        FileLoggingSettings(
            service_name="ocr",
            instance_id="ocr-gpu0",
            project_root=tmp_path,
            directory="../outside",
        )


def test_logging_settings_reject_invalid_values(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="正整数"):
        _settings(tmp_path, max_file_size_mib=0)
    with pytest.raises(ValueError, match="文件名"):
        _settings(tmp_path, file_name="nested/application.log")


def test_file_handler_rotates_before_size_limit_and_cleans_expired(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_file_size_mib=1)
    handler = SizeAndAgeRotatingFileHandler(settings)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("operator-rotation-test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.info("x" * (1024 * 1024))
    logger.info("second event")
    handler.close()
    files = list(settings.log_directory.iterdir())
    assert settings.log_path.exists()
    assert any(path.name.startswith("application.log.") for path in files)
    assert all(path.stat().st_size <= 1024 * 1024 for path in files)

    expired = settings.log_directory / "application.log.expired"
    expired.write_text("old", encoding="utf-8")
    old_time = (datetime.now(UTC) - timedelta(days=8)).timestamp()
    os.utime(expired, (old_time, old_time))
    second_handler = SizeAndAgeRotatingFileHandler(settings)
    second_handler.close()
    assert not expired.exists()


def test_json_logging_is_structured_and_redacts_sensitive_values(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    configure_logging(settings)
    logger = logging.getLogger("operator-redaction-test")
    logger.info(
        "received image data:image/png;base64,QUJD",
        extra={
            "task_id": "course-001",
            "token": "secret-token",
            "outcome": "success",
        },
    )
    for handler in logging.getLogger().handlers:
        handler.flush()
    payload = json.loads(settings.log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["service"] == "test-operator"
    assert payload["instance_id"] == "gpu0"
    assert payload["task_id"] == "course-001"
    assert payload["outcome"] == "success"
    assert "QUJD" not in settings.log_path.read_text(encoding="utf-8")
    assert "secret-token" not in settings.log_path.read_text(encoding="utf-8")


def test_configure_logging_is_idempotent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    configure_logging(settings)
    configure_logging(settings)
    logger = logging.getLogger("operator-idempotence-test")
    logger.info("one event")
    for handler in logging.getLogger().handlers:
        handler.flush()
    lines = settings.log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
