from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def test_orchestrator_service_has_independent_fastapi_delivery_files() -> None:
    for relative_path in (
        "app/main.py",
        "app/api/routes.py",
        "app/application/components.py",
        "app/domain/models.py",
        "app/infrastructure/adapters.py",
        "app/core/config.py",
        "config.toml",
        "requirements.txt",
        "docker/Dockerfile",
    ):
        assert (SERVICE_ROOT / relative_path).is_file(), relative_path


def test_orchestrator_settings_use_toml_then_environment_precedence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.core.config import OrchestratorSettings

    config_path = tmp_path / "orchestrator.toml"
    config_path.write_text(
        """
[service]
port = 19200
workers = 1

[kafka]
bootstrap_servers = ["kafka-a:29092", "kafka-b:29092"]
max_poll_records = 8

[worker]
node_concurrency = 3

[storage]
course_root = "/tmp/course-from-toml"
result_root = "/tmp/result-from-toml"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("ORCHESTRATOR_SERVICE__PORT", "19300")
    monkeypatch.setenv("ORCHESTRATOR_WORKER__NODE_CONCURRENCY", "5")

    settings = OrchestratorSettings.load()

    assert settings.service.name == "orchestrator-service"
    assert settings.service.port == 19300
    assert settings.service.workers == 1
    assert settings.kafka.bootstrap_servers == ["kafka-a:29092", "kafka-b:29092"]
    assert settings.kafka.max_poll_records == 8
    assert settings.worker.node_concurrency == 5
    assert settings.storage.course_root == Path("/tmp/course-from-toml")
    assert not hasattr(settings, "redis")


def test_orchestrator_entrypoint_uses_the_service_local_app_package() -> None:
    from app.main import app

    assert app.title == "orchestrator-service"
    assert "/health" in app.openapi()["paths"]


def test_orchestrator_service_rejects_multiple_uvicorn_workers(tmp_path: Path) -> None:
    from app.core.config import OrchestratorSettings

    config_path = tmp_path / "orchestrator.toml"
    config_path.write_text("[service]\nworkers = 2\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        OrchestratorSettings.load(config_path)


def test_ppt_async_contract_has_renewal_and_reconciliation_settings() -> None:
    from app.core.config import OrchestratorSettings

    settings = OrchestratorSettings()

    assert settings.ppt.lease_renew_interval_seconds < settings.ppt.lease_ttl_seconds
    assert settings.ppt.terminal_callback_path == "/internal/ppt-slice/callback"
    assert settings.ppt.max_manifest_bytes > 0
    assert settings.ppt.reconcile_interval_seconds > 0
