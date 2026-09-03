from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
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


def test_canonical_entrypoint_exposes_the_service_contract() -> None:
    from app.main import app

    assert app.title == "vision-orchestrator-service"
    assert set(app.openapi()["paths"]) >= {"/health", "/ready"}


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
    assert result.stdout.strip() == "vision-orchestrator-service"


def test_config_has_only_the_required_top_level_sections() -> None:
    config_path = SERVICE_ROOT / "config.toml"
    raw = config_path.read_text(encoding="utf-8")
    parsed = tomllib.loads(raw)

    assert set(parsed) == {
        "service",
        "logging",
        "postgres",
        "kafka",
        "control",
        "lease_renewal",
        "worker",
        "storage",
        "scan",
        "media",
        "vbas",
        "cache",
        "teacher_behavior",
        "student_behavior",
        "evidence",
        "readiness",
    }
    lowered = raw.lower()
    assert "redis" not in lowered
    assert "mysql" not in lowered
    assert parsed["media"]["max_concurrent_processes"] == 2


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
    from app.core.config import VisionSettings

    override = tmp_path / "vision.toml"
    override.write_text(
        "[service]\nport = 9100\nworkers = 3\n\n[scan]\nbatch_size = 12\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(override))
    monkeypatch.setenv("VISION_SERVICE__PORT", "9200")

    settings = VisionSettings()

    assert settings.service.port == 9200
    assert settings.service.workers == 3
    assert settings.scan.batch_size == 12
    assert settings.service.name == "vision-orchestrator-service"


def test_runtime_contract_defaults_match_platform_topics_and_control_port() -> None:
    from app.core.config import VisionSettings

    settings = VisionSettings()

    assert settings.kafka.command_topic == "algorithm.visual.commands"
    assert settings.kafka.event_topic == "algorithm.visual.events"
    assert settings.kafka.enable_auto_commit is False
    assert settings.kafka.max_poll_records >= 1
    assert settings.kafka.poll_timeout_seconds > 0
    assert settings.control.base_url == "http://127.0.0.1:18100"
    assert settings.media.ffmpeg_binary == "ffmpeg"
    assert settings.scan.end_frame_margin_seconds == 0.5
    assert settings.vbas.max_batch_size == 8
    assert settings.vbas.capacity_snapshot_refresh_seconds == 1.0
    assert settings.vbas.transient_max_attempts == 3


@pytest.mark.parametrize("value", (0, -1, True, 1.5, "2"))
def test_media_process_limit_must_be_strictly_positive(value: object) -> None:
    from app.core.config import VisionSettings

    with pytest.raises(ValidationError, match="max_concurrent_processes"):
        VisionSettings(media={"max_concurrent_processes": value})
