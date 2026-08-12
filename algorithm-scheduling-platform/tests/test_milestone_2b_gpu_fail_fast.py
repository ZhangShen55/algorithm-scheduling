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
CPU_OPERATORS = ("ppt-slice", "text-analysis")


def _load_services() -> dict[str, Any]:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    return compose["services"]


def test_all_gpu_instances_require_gpu_and_cpu_instances_do_not() -> None:
    services = _load_services()

    for operator in GPU_OPERATORS:
        for index in range(3):
            environment = services[f"{operator}-gpu{index}"]["environment"]
            assert environment["REQUIRE_GPU"] == "true"

    for operator in CPU_OPERATORS:
        for index in range(3):
            environment = services[f"{operator}-cpu{index}"]["environment"]
            assert "REQUIRE_GPU" not in environment


def test_offline_asr_gpu_instances_omit_retired_pyannote_compatibility() -> None:
    services = _load_services()

    for index in range(3):
        environment = services[f"asr-offline-gpu{index}"]["environment"]
        assert "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD" not in environment
