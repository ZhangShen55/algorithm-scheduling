from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

SERVICE_ROOT = Path(__file__).resolve().parents[1]
def test_service_has_complete_fastapi_project_layout() -> None:
    for relative_path in (
        "app/__init__.py",
        "app/main.py",
        "app/api/__init__.py",
        "app/application/__init__.py",
        "app/domain/__init__.py",
        "app/infrastructure/__init__.py",
        "app/core/__init__.py",
        "config.toml",
        "requirements.txt",
        "docker/Dockerfile",
        "README.md",
    ):
        assert (SERVICE_ROOT / relative_path).is_file(), relative_path


def test_canonical_entrypoint_exposes_online_routes() -> None:
    from app.main import app

    assert app.title == "online-gateway-service"
    assert set(app.openapi()["paths"]) >= {
        "/health",
        "/ready",
        "/api/online/vbas/analyze",
        "/api/online/face/recognize",
        "/api/online/image-quality/detect",
        "/api/online/ocr/recognize",
    }
    assert str(app.url_path_for("stream_realtime_asr")) == "/api/online/asr/stream"


def test_default_lifespan_does_not_require_storage_directories() -> None:
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/health").json() == {
            "service": "online-gateway-service",
            "status": "ok",
        }


def test_app_main_imports_from_service_working_directory() -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", "from app.main import app; print(app.title)"],
        cwd=SERVICE_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "online-gateway-service"


def test_config_has_only_online_gateway_concerns() -> None:
    config_path = SERVICE_ROOT / "config.toml"
    raw = config_path.read_text(encoding="utf-8")
    parsed = tomllib.loads(raw)

    assert set(parsed) == {
        "service",
        "control",
        "http",
        "leases",
        "base64",
        "body",
        "websocket",
        "readiness",
    }
    lowered = raw.lower()
    for forbidden in ("postgres", "kafka", "redis", "storage"):
        assert forbidden not in lowered


def test_service_code_does_not_depend_on_the_shared_registry_module() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in (SERVICE_ROOT / "app").rglob("*.py")
    )
    assert "platform_common.operator_registry" not in sources


def test_every_toml_field_has_an_adjacent_chinese_comment() -> None:
    lines = (SERVICE_ROOT / "config.toml").read_text(encoding="utf-8").splitlines()
    field_indexes = [
        index
        for index, line in enumerate(lines)
        if line.strip() and not line.lstrip().startswith(("#", "["))
    ]
    for index in field_indexes:
        assert index > 0
        preceding = lines[index - 1].strip()
        assert preceding.startswith("#")
        assert any("\u4e00" <= character <= "\u9fff" for character in preceding)


def test_settings_precedence_is_defaults_then_toml_then_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.core.config import OnlineGatewaySettings

    override = tmp_path / "online.toml"
    override.write_text(
        "[service]\nport = 8010\nworkers = 2\n\n[body]\nmax_bytes = 2048\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(override))
    monkeypatch.setenv("ONLINE_SERVICE__PORT", "8020")

    settings = OnlineGatewaySettings()

    assert settings.service.port == 8020
    assert settings.service.workers == 2
    assert settings.body.max_bytes == 2048
    assert settings.service.name == "online-gateway-service"


def test_gateway_defaults_target_the_control_service_port() -> None:
    from app.core.config import OnlineGatewaySettings

    assert OnlineGatewaySettings().control.base_url == "http://127.0.0.1:18100"
