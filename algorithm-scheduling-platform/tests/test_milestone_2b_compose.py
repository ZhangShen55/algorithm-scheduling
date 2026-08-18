from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = PLATFORM_ROOT / "deploy/docker-compose.operators.yml"
PLATFORM_COMPOSE_PATH = PLATFORM_ROOT / "deploy/docker-compose.platform.yml"
REQUIRED_REGISTRY_TOKEN_EXPRESSION = (
    "${OPERATOR_REGISTRY_TOKEN:?OPERATOR_REGISTRY_TOKEN is required}"
)

GPU_OPERATORS = {
    "asr-offline": (8083, 18083, "asr_offline", "asr_offline.gpu.toml"),
    "asr-online": (8084, 18084, "asr_online", "asr_online.gpu.toml"),
    "ocr": (8866, 18866, "ocr", "ocr.gpu.toml"),
    "vbas": (8981, 18981, "vbas", "vbas.gpu.toml"),
    "facerec": (8000, 18003, "facerec", "facerec.gpu.toml"),
    "screen-det": (8880, 18880, "screen_det", "screen_det.gpu.toml"),
}
CPU_OPERATORS = {
    "ppt-slice": (9001, 19001, "ppt_slice.cpu.toml"),
    "text-analysis": (8000, 18000, "text_analysis.cpu.toml"),
}


def load_operator_compose() -> dict[str, Any]:
    return cast(
        dict[str, Any], yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    )


def expected_service_names() -> set[str]:
    return {
        *(f"{operator}-gpu{index}" for operator in GPU_OPERATORS for index in range(3)),
        *(f"{operator}-cpu{index}" for operator in CPU_OPERATORS for index in range(3)),
    }


def _volume_sources(service: dict[str, Any]) -> dict[str, tuple[str, bool]]:
    volumes: dict[str, tuple[str, bool]] = {}
    for volume in service["volumes"]:
        assert isinstance(volume, dict)
        assert volume["type"] == "bind"
        volumes[volume["target"]] = (
            volume["source"],
            volume.get("read_only", False),
        )
    return volumes


def assert_operator_compose_matrix(compose: dict[str, Any]) -> None:
    services = compose["services"]
    assert set(services) == expected_service_names()
    assert len(services) == 24
    assert compose["networks"]["algorithm-platform"]["external"] is True

    for operator, (container_port, base_port, process_name, config_name) in GPU_OPERATORS.items():
        for gpu_index in range(3):
            name = f"{operator}-gpu{gpu_index}"
            service = services[name]
            environment = service["environment"]
            assert service["profiles"] == [f"gpu{gpu_index}"]
            assert service["ports"] == [
                f"127.0.0.1:{base_port + gpu_index * 10000}:{container_port}"
            ]
            assert service["image"].endswith(":v1.0_260812}")
            assert environment["PLATFORM_REGISTRATION_ENABLED"] == "true"
            assert environment["PLATFORM_CONTROL_SERVICE_URL"] == "http://control-service:18100"
            assert environment["PLATFORM_OPERATOR_REGISTRY_TOKEN"] == (
                REQUIRED_REGISTRY_TOKEN_EXPRESSION
            )
            assert environment["PLATFORM_INSTANCE_ID"] == name
            assert environment["PLATFORM_SERVICE_URL"] == f"http://{name}:{container_port}"
            assert int(environment["PLATFORM_DECLARED_CAPACITY"]) > 0
            assert environment["PLATFORM_GPU_ID"] == str(gpu_index)
            assert environment["GPU_PROCESS_NAME"] == process_name
            assert environment["UVICORN_WORKERS"] == "1"
            assert environment["REQUIRE_GPU"] == "true"
            assert environment["NVIDIA_VISIBLE_DEVICES"] == str(gpu_index)
            if operator == "facerec":
                assert environment["CONFIG_PATH"] == "/config/config.toml"
                assert environment["FACEREC_MONGO_USERNAME"] == (
                    "${MONGO_ROOT_USERNAME:-root}"
                )
                assert environment["FACEREC_MONGO_PASSWORD"] == (
                    "${MONGO_ROOT_PASSWORD:-root}"
                )
                healthcheck = " ".join(service["healthcheck"]["test"])
                assert "json.load" in healthcheck
                assert "status" in healthcheck
                assert "healthy" in healthcheck
            if operator == "screen-det":
                assert environment["CONFIG_PATH"] == "/app/config.toml"
            devices = service["deploy"]["resources"]["reservations"]["devices"]
            assert devices == [
                {
                    "driver": "nvidia",
                    "device_ids": [str(gpu_index)],
                    "capabilities": ["gpu"],
                }
            ]
            volumes = _volume_sources(service)
            assert volumes["/data/course"][0] == "${COURSE_ROOT:-/data/course}"
            assert volumes["/data/result"][0] == "${RESULT_ROOT:-/data/result}"
            assert volumes[_config_target(operator)] == (
                f"./config/operators/{config_name}",
                True,
            )
            if operator == "screen-det":
                assert volumes["/app/config.toml"] == (
                    "./config/operators/screen_det.gpu.toml",
                    True,
                )
            assert "/ops/health" in " ".join(service["healthcheck"]["test"])

    for operator, (container_port, base_port, config_name) in CPU_OPERATORS.items():
        for instance_index in range(3):
            name = f"{operator}-cpu{instance_index}"
            service = services[name]
            environment = service["environment"]
            assert service["profiles"] == ["cpu"]
            assert service["ports"] == [
                f"127.0.0.1:{base_port + instance_index * 10000}:{container_port}"
            ]
            assert service["image"].endswith(":v1.0_260812}")
            assert environment["PLATFORM_REGISTRATION_ENABLED"] == "true"
            assert environment["PLATFORM_CONTROL_SERVICE_URL"] == "http://control-service:18100"
            assert environment["PLATFORM_OPERATOR_REGISTRY_TOKEN"] == (
                REQUIRED_REGISTRY_TOKEN_EXPRESSION
            )
            assert environment["PLATFORM_INSTANCE_ID"] == name
            assert environment["PLATFORM_SERVICE_URL"] == f"http://{name}:{container_port}"
            assert int(environment["PLATFORM_DECLARED_CAPACITY"]) > 0
            assert environment["UVICORN_WORKERS"] == "1"
            assert "PLATFORM_GPU_ID" not in environment
            assert "GPU_PROCESS_NAME" not in environment
            assert "REQUIRE_GPU" not in environment
            assert "deploy" not in service
            volumes = _volume_sources(service)
            assert volumes["/data/course"][0] == "${COURSE_ROOT:-/data/course}"
            assert volumes["/data/result"][0] == "${RESULT_ROOT:-/data/result}"
            assert volumes[_config_target(operator)] == (
                f"./config/operators/{config_name}",
                True,
            )
            assert "/ops/health" in " ".join(service["healthcheck"]["test"])


def _config_target(operator: str) -> str:
    return {
        "asr-offline": "/config.toml",
        "asr-online": "/config.toml",
        "ocr": "/app/config.toml",
        "vbas": "/workspace/config.toml",
        "facerec": "/config/config.toml",
        "screen-det": "/app/config.toml",
        "ppt-slice": "/workspace/config.toml",
        "text-analysis": "/app/config.toml",
    }[operator]


def test_compose_declares_exact_three_gpu_and_three_cpu_operator_matrix() -> None:
    assert_operator_compose_matrix(load_operator_compose())


def test_platform_compose_requires_explicit_operator_registry_token() -> None:
    compose = yaml.safe_load(PLATFORM_COMPOSE_PATH.read_text(encoding="utf-8"))

    control_environment = compose["services"]["control-service"]["environment"]

    assert control_environment["CONTROL_OPERATOR_REGISTRY__MANAGEMENT_TOKEN"] == (
        REQUIRED_REGISTRY_TOKEN_EXPRESSION
    )


def test_facerec_healthcheck_uses_the_interpreter_available_in_its_image() -> None:
    services = load_operator_compose()["services"]

    for gpu_index in range(3):
        healthcheck = services[f"facerec-gpu{gpu_index}"]["healthcheck"]["test"]
        assert healthcheck[:2] == ["CMD", "python3"]
