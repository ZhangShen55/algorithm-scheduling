from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def test_control_service_has_independent_fastapi_delivery_files() -> None:
    for relative_path in (
        "app/main.py",
        "app/api/routes.py",
        "app/application/factory.py",
        "app/domain/models.py",
        "app/infrastructure/settings_adapter.py",
        "app/core/config.py",
        "config.toml",
        "requirements.txt",
        "docker/Dockerfile",
    ):
        assert (SERVICE_ROOT / relative_path).is_file(), relative_path


def test_control_settings_use_toml_then_environment_precedence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.core.config import ControlSettings

    config_path = tmp_path / "control.toml"
    config_path.write_text(
        """
[service]
port = 19000
workers = 1

[postgres]
pool_size = 7

[redis]
key_prefix = "toml-prefix:"

[features]
enabled_task_types = ["PPT", "ASR"]
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("CONTROL_SERVICE__PORT", "19100")
    monkeypatch.setenv("CONTROL_POSTGRES__POOL_SIZE", "11")

    settings = ControlSettings.load()

    assert settings.service.name == "control-service"
    assert settings.service.port == 19100
    assert settings.service.workers == 1
    assert settings.postgres.pool_size == 11
    assert settings.redis.key_prefix == "toml-prefix:"
    assert settings.features.enabled_task_types == ["PPT", "ASR"]
    assert not hasattr(settings, "kafka")


def test_control_entrypoint_uses_the_service_local_app_package() -> None:
    from app.main import app

    assert app.title == "control-service"
    assert "/health" in app.openapi()["paths"]


def test_default_control_config_authorizes_the_exact_operator_matrix() -> None:
    from app.core.config import ControlSettings

    settings = ControlSettings.load(SERVICE_ROOT / "config.toml")

    assert len(settings.operator_registry.trusted_service_urls) == 24
    assert settings.operator_registry.trusted_service_urls["vbas-gpu0"] == (
        "http://vbas-gpu0:8981"
    )
    assert settings.operator_registry.trusted_service_urls["ppt-slice-cpu2"] == (
        "http://ppt-slice-cpu2:9001"
    )


def test_control_service_rejects_multiple_uvicorn_workers(tmp_path: Path) -> None:
    from app.core.config import ControlSettings

    config_path = tmp_path / "control.toml"
    config_path.write_text("[service]\nworkers = 2\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        ControlSettings.load(config_path)
