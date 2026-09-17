import logging
import json

from app.core import config
from app.core import logger as app_logger
from packages.operator_registry_client import FileLoggingSettings
from packages.operator_registry_client.logging import JsonFormatter


def test_load_config_does_not_log_database_password(caplog, tmp_path, monkeypatch) -> None:
    password = "never-log-this-password"
    config_file = tmp_path / "config.toml"
    source = config.config_path.read_text(encoding="utf-8")
    source = source.replace('password = "change-me"', f'password = "{password}"', 1)
    config_file.write_text(source, encoding="utf-8")
    monkeypatch.setattr(config, "config_path", config_file)
    monkeypatch.delenv("FACEREC_MONGO_PASSWORD", raising=False)

    with caplog.at_level(logging.DEBUG, logger=config.__name__):
        loaded = config.load_config()

    assert loaded.db.password == password
    assert password not in caplog.text


def test_json_logging_redacts_media_and_credentials() -> None:
    formatter = JsonFormatter(service_name="facerec", instance_id="test-instance")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=(
            "photo=data:image/png;base64,QUJDREVGRw== "
            "password=never-log-password token=never-log-token"
        ),
        args=(),
        exc_info=None,
    )

    payload = json.loads(formatter.format(record))

    assert payload["service"] == "facerec"
    assert payload["instance_id"] == "test-instance"
    assert "QUJDREVGRw==" not in payload["event"]
    assert "never-log-password" not in payload["event"]
    assert "never-log-token" not in payload["event"]


def test_logging_uses_instance_directory_and_required_retention(tmp_path) -> None:
    settings = FileLoggingSettings.from_mapping(
        {
            "directory": "logs",
            "file_name": "application.log",
            "max_file_size_mib": 100,
            "retention_days": 7,
        },
        service_name="facerec",
        project_root=tmp_path,
        instance_id="facerec-gpu0",
    )

    assert settings.log_path == tmp_path / "logs/facerec-gpu0/application.log"
    assert settings.max_file_size_mib == 100
    assert settings.retention_days == 7


def test_logging_setup_is_idempotent() -> None:
    before = tuple(logging.getLogger().handlers)

    app_logger.setup_logging()
    app_logger.setup_logging()

    assert tuple(logging.getLogger().handlers) == before
