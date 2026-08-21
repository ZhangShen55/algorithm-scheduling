"""统一日志配置的根配置与部署权威合同。"""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_ROOT = WORKSPACE_ROOT / "algorithm-scheduling-platform"
TARGET_PROJECTS = (
    "asr_offline",
    "asr_online",
    "facerec",
    "ocr",
    "screen_det",
    "ppt_slice",
    "vbas",
    "control_service",
    "orchestrator_service",
    "vision_orchestrator_service",
    "online_gateway_service",
)
OPERATOR_DEPLOYMENT_CONFIGS = (
    "asr_offline.gpu.toml",
    "asr_online.gpu.toml",
    "facerec.gpu.toml",
    "ocr.gpu.toml",
    "ppt_slice.cpu.toml",
    "screen_det.gpu.toml",
    "vbas.gpu.toml",
)
LOGGING_FIELDS = {
    "level",
    "directory",
    "file_name",
    "max_file_size_mib",
    "retention_days",
    "stdout_enabled",
    "file_enabled",
}


def _load(path: Path) -> dict[str, object]:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _assert_logging_contract(path: Path) -> None:
    data = _load(path)
    logging = data.get("logging")
    assert isinstance(logging, dict), path
    assert LOGGING_FIELDS.issubset(logging), path
    assert logging["directory"] == "logs", path
    assert logging["file_name"] == "application.log", path
    assert logging["max_file_size_mib"] == 100, path
    assert logging["retention_days"] == 7, path
    assert logging["stdout_enabled"] is True, path
    assert logging["file_enabled"] is True, path
    assert "#" in path.read_text(encoding="utf-8"), path


def test_target_root_configs_use_the_unified_logging_contract() -> None:
    for project in TARGET_PROJECTS:
        _assert_logging_contract(WORKSPACE_ROOT / project / "config.toml")


def test_target_root_configs_are_tracked_for_clean_clone() -> None:
    for project in TARGET_PROJECTS:
        config_path = f"{project}/config.toml"
        completed = subprocess.run(
            ["git", "ls-files", "--error-unmatch", config_path],
            cwd=WORKSPACE_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, config_path


def test_current_operator_deployment_configs_use_the_unified_logging_contract() -> None:
    config_root = PLATFORM_ROOT / "deploy/config/operators"
    for config_name in OPERATOR_DEPLOYMENT_CONFIGS:
        _assert_logging_contract(config_root / config_name)


def test_text_analysis_is_not_part_of_the_logging_configuration_scope() -> None:
    assert "text_analysis" not in TARGET_PROJECTS
    compose_text = (PLATFORM_ROOT / "deploy/docker-compose.logs.yml").read_text(
        encoding="utf-8"
    )
    assert "text_analysis" not in compose_text


def test_logging_harness_baseline_has_an_independent_schema() -> None:
    baseline_path = PLATFORM_ROOT / "harness/baselines/service-file-logging-standardization.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert baseline["schema"] == "service-file-logging-standardization"
    assert baseline["scope"]["excluded_projects"] == ["text_analysis"]
    assert baseline["defaults"]["max_file_size_mib"] == 100
    assert baseline["defaults"]["retention_days"] == 7
