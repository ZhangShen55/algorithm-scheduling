from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import tomllib
from fastapi.testclient import TestClient
from pydantic import ValidationError

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
            "/online/vbas/teacher",
            "/online/vbas/student",
            "/online/vbas/person-count",
        "/api/online/face/recognize",
        "/api/online/face/persons",
        "/api/online/face/persons/batch",
        "/api/online/face/persons/search",
        "/api/online/face/persons/delete",
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


def test_metrics_contract_and_cors_preflight_are_available() -> None:
    from app.api.routes import create_online_gateway_app

    app = create_online_gateway_app()
    metrics = app.state.platform_metrics
    metrics.observe_operator_request(
        operator_code="ocr",
        capability="ocr",
        instance_id="ocr-gpu0",
        elapsed_seconds=0.025,
        success=False,
    )
    metrics.record_capacity_lease_event(
        capability="ocr",
        outcome="rejected",
        instance_id="ocr-gpu0",
    )

    with TestClient(app) as client:
        preflight = client.options(
            "/metrics",
            headers={
                "Origin": "http://192.168.29.11:5174",
                "Access-Control-Request-Method": "GET",
            },
        )
        response = client.get("/metrics")

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "*"
    assert response.status_code == 200
    assert "algorithm_operator_request_latency_seconds_count" in response.text
    assert "algorithm_operator_request_errors_total" in response.text
    assert "algorithm_capacity_lease_events_total" in response.text


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
        "logging",
        "control",
        "face_persons",
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
    monkeypatch: pytest.MonkeyPatch,
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

    settings = OnlineGatewaySettings()

    assert settings.control.base_url == "http://127.0.0.1:18100"
    assert settings.face_persons.base_url == "http://127.0.0.1:8003"
    assert settings.http.max_connections == 2048
    assert settings.http.max_keepalive_connections == 512
    assert settings.http.pool_timeout_seconds > 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_connections": 0},
        {"max_connections": -1},
        {"max_keepalive_connections": 0},
        {"max_keepalive_connections": -1},
        {"max_connections": 10, "max_keepalive_connections": 11},
        {"pool_timeout_seconds": 0.0},
        {"pool_timeout_seconds": -1.0},
        {"pool_timeout_seconds": float("nan")},
        {"pool_timeout_seconds": float("inf")},
        {"pool_timeout_seconds": float("-inf")},
    ],
)
def test_http_pool_configuration_rejects_invalid_bounds(
    overrides: dict[str, object],
) -> None:
    from app.core.config import HttpConfig

    with pytest.raises(ValidationError):
        HttpConfig.model_validate(overrides)


def test_gateway_applies_configured_http_pool_limits_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.routes import create_online_gateway_app
    from app.core.config import HttpConfig, OnlineGatewaySettings

    real_async_client = httpx.AsyncClient
    captured: dict[str, Any] = {}

    def recording_async_client(*args: object, **kwargs: Any) -> httpx.AsyncClient:
        captured.update(kwargs)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", recording_async_client)
    app = create_online_gateway_app(
        OnlineGatewaySettings(
            http=HttpConfig(
                max_connections=2048,
                max_keepalive_connections=512,
                pool_timeout_seconds=3.25,
            )
        )
    )

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    limits = captured["limits"]
    timeout = captured["timeout"]
    assert isinstance(limits, httpx.Limits)
    assert limits.max_connections == 2048
    assert limits.max_keepalive_connections == 512
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.pool == 3.25
