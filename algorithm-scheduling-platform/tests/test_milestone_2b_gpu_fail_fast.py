import tomllib
from pathlib import Path
from typing import Any

import yaml

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = PLATFORM_ROOT / "deploy/docker-compose.operators.yml"
GPU_OPERATORS = (
    "asr-offline",
    "asr-online",
    "ocr",
    "vbas",
    "facerec",
    "screen-det",
)
CPU_OPERATORS = ("ppt-slice",)


def _load_services() -> dict[str, Any]:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    return compose["services"]


def _require_gpu_from_mounted_toml(service: dict[str, Any]) -> bool:
    config_path = service["environment"]["CONFIG_PATH"]
    mount = next(
        item for item in service["volumes"] if item.get("target") == config_path
    )
    source = (COMPOSE_PATH.parent / mount["source"]).resolve()
    config = tomllib.loads(source.read_text(encoding="utf-8"))
    return config["runtime"]["require_gpu"]


def test_all_gpu_instances_require_gpu_and_cpu_instances_do_not() -> None:
    services = _load_services()

    for operator in GPU_OPERATORS:
        for index in range(3):
            service = services[f"{operator}-gpu{index}"]
            assert "REQUIRE_GPU" not in service["environment"]
            assert _require_gpu_from_mounted_toml(service) is True

    for operator in CPU_OPERATORS:
        for index in range(3):
            service = services[f"{operator}-cpu{index}"]
            assert "REQUIRE_GPU" not in service["environment"]
            assert _require_gpu_from_mounted_toml(service) is False


def test_offline_asr_gpu_instances_omit_retired_pyannote_compatibility() -> None:
    services = _load_services()

    for index in range(3):
        environment = services[f"asr-offline-gpu{index}"]["environment"]
        assert "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD" not in environment
