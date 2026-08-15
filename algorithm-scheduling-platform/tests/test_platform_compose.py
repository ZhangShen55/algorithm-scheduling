import json
import re
import subprocess
from pathlib import Path
from typing import cast

import pytest
import yaml  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
COMPOSE_PATH = PROJECT_ROOT / "deploy/docker-compose.platform.yml"
INFRASTRUCTURE_COMPOSE_PATH = PROJECT_ROOT / "deploy/docker-compose.infrastructure.yml"
PLATFORM_SERVICE_SPECS = (
    ("control_service", "18100"),
    ("orchestrator_service", "18101"),
    ("vision_orchestrator_service", "8010"),
    ("online_gateway_service", "8001"),
)
PLATFORM_SERVICE_NAMES = tuple(service for service, _ in PLATFORM_SERVICE_SPECS)
WHEEL_BUILD_RUN = (
    "RUN --mount=type=cache,id=algorithm-platform-pip-cache,"
    "target=/root/.cache/pip,sharing=locked python -m pip wheel "
    "--wheel-dir /wheelhouse --timeout 300 --retries 10 --prefer-binary "
    "-r /tmp/requirements.txt /tmp/platform"
)
OFFLINE_INSTALL_RUN = (
    "RUN --mount=type=bind,from=builder,source=/wheelhouse,target=/tmp/wheelhouse "
    "--mount=type=bind,from=builder,source=/tmp/requirements.txt,"
    "target=/tmp/requirements.txt python -m pip install --no-cache-dir --no-index "
    "--find-links=/tmp/wheelhouse -r /tmp/requirements.txt "
    "algorithm-scheduling-platform"
)
FFMPEG_INSTALL_RUN = (
    'RUN sed -i -e "s|http://deb.debian.org/debian-security|'
    '${DEBIAN_SECURITY_MIRROR}|g" '
    '-e "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" '
    "/etc/apt/sources.list.d/debian.sources "
    "&& apt-get -o Acquire::Retries=10 -o Acquire::http::Timeout=300 "
    "-o Acquire::https::Timeout=300 update "
    "&& apt-get -o Acquire::Retries=10 -o Acquire::http::Timeout=300 "
    "-o Acquire::https::Timeout=300 install -y --no-install-recommends ffmpeg "
    "&& rm -rf /var/lib/apt/lists/*"
)


def _render_compose(compose_path: Path) -> dict[str, object]:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "config",
            "--format",
            "json",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return cast(dict[str, object], json.loads(result.stdout))


def test_platform_compose_closes_the_four_service_single_machine_topology() -> None:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "docker-compose.infrastructure.yml" in compose
    for service in (
        "control-service",
        "orchestrator-service",
        "vision-orchestrator-service",
        "online-gateway-service",
    ):
        assert f"  {service}:" in compose
    assert compose.count("restart: unless-stopped") >= 1
    assert compose.count("healthcheck:") >= 4
    assert compose.count("resources:") >= 1
    assert "algorithm-platform" in compose


def test_platform_include_renders_one_infrastructure_project_and_volume_set() -> None:
    rendered = _render_compose(COMPOSE_PATH)
    infrastructure = _render_compose(INFRASTRUCTURE_COMPOSE_PATH)

    assert rendered["name"] == "algorithm-scheduling-platform"
    assert rendered["name"] == infrastructure["name"]
    assert set(cast(dict[str, object], rendered["services"])) == {
        "postgres",
        "kafka",
        "redis",
        "mongodb",
        "control-service",
        "orchestrator-service",
        "vision-orchestrator-service",
        "online-gateway-service",
    }
    expected_volumes = {
        "postgres_data",
        "kafka_data",
        "redis_data",
        "mongodb_data",
    }
    assert set(cast(dict[str, object], rendered["volumes"])) == expected_volumes
    assert set(cast(dict[str, object], infrastructure["volumes"])) == expected_volumes


def test_platform_compose_uses_container_addresses_and_shared_storage() -> None:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "postgres:5432" in compose
    assert "kafka:29092" in compose
    assert "redis:6379" in compose
    assert "http://control-service:18100" in compose
    assert "http://orchestrator-service:18101" in compose
    assert "${COURSE_ROOT:-/data/course}:/data/course" in compose
    assert "${RESULT_ROOT:-/data/result}:/data/result" in compose


def test_control_readiness_probe_allows_dependency_timeout_headroom() -> None:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "ops/readiness', timeout=4)" in compose


def test_orchestrator_healthcheck_uses_runtime_readiness() -> None:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    orchestrator = compose.split("  orchestrator-service:", 1)[1].split(
        "  vision-orchestrator-service:", 1
    )[0]

    assert "http://127.0.0.1:18101/ops/readiness" in orchestrator
    assert "http://127.0.0.1:18101/health" not in orchestrator


def _dockerfile_instructions(dockerfile: str) -> list[str]:
    instructions: list[str] = []
    continuation: list[str] = []

    for raw_line in dockerfile.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        continues = line.endswith("\\")
        continuation.append(line[:-1].rstrip() if continues else line)
        if not continues:
            instructions.append(" ".join(continuation))
            continuation = []

    assert not continuation, "Dockerfile contains an unterminated instruction"
    return instructions


def _platform_dockerfile_stages(dockerfile: str) -> tuple[list[str], list[str]]:
    instructions = _dockerfile_instructions(dockerfile)
    from_indexes = [
        index
        for index, instruction in enumerate(instructions)
        if instruction.split(maxsplit=1)[0].upper() == "FROM"
    ]

    assert len(from_indexes) == 2
    builder = instructions[from_indexes[0] : from_indexes[1]]
    runtime = instructions[from_indexes[1] :]
    assert builder[0].casefold() == "from python:3.11-slim as builder"
    assert runtime[0].casefold() == "from python:3.11-slim"
    return builder, runtime


def _instructions_named(stage: list[str], name: str) -> list[str]:
    return [
        instruction
        for instruction in stage
        if instruction.split(maxsplit=1)[0].upper() == name
    ]


def _instruction_names(stage: list[str]) -> list[str]:
    return [instruction.split(maxsplit=1)[0].upper() for instruction in stage]


def _assert_platform_dockerfile_contract(
    service: str,
    port: str,
    dockerfile: str,
) -> None:
    assert re.search(r"^\s*#\s*syntax\s*=", dockerfile, re.IGNORECASE | re.MULTILINE) is None, (
        service
    )
    builder, runtime = _platform_dockerfile_stages(dockerfile)

    assert _instruction_names(builder) == [
        "FROM",
        "ARG",
        "ENV",
        "WORKDIR",
        "COPY",
        "COPY",
        "COPY",
        "RUN",
    ], service
    expected_runtime_names = ["FROM"]
    if service == "orchestrator_service":
        expected_runtime_names.extend(["ARG", "ARG"])
    expected_runtime_names.extend(["ARG", "LABEL", "ENV", "WORKDIR", "RUN"])
    if service == "orchestrator_service":
        expected_runtime_names.append("RUN")
    expected_runtime_names.extend(["COPY", "COPY", "EXPOSE", "CMD"])
    assert _instruction_names(runtime) == expected_runtime_names, service

    assert _instructions_named(builder, "ARG") == [
        "ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple"
    ], service
    assert _instructions_named(builder, "ENV") == [
        "ENV PIP_INDEX_URL=${PIP_INDEX_URL} PIP_DEFAULT_TIMEOUT=300 PIP_RETRIES=10"
    ], service
    assert _instructions_named(builder, "WORKDIR") == ["WORKDIR /build"], service

    assert _instructions_named(builder, "COPY") == [
        f"COPY {service}/requirements.txt /tmp/requirements.txt",
        "COPY algorithm-scheduling-platform/pyproject.toml /tmp/platform/pyproject.toml",
        "COPY algorithm-scheduling-platform/packages /tmp/platform/packages",
    ], service
    assert _instructions_named(builder, "RUN") == [WHEEL_BUILD_RUN], service

    expected_runtime_args: list[str] = []
    if service == "orchestrator_service":
        expected_runtime_args = [
            "ARG DEBIAN_MIRROR=https://mirrors.aliyun.com/debian",
            "ARG DEBIAN_SECURITY_MIRROR=https://mirrors.aliyun.com/debian-security",
        ]
    expected_runtime_args.append("ARG EXPECTED_GIT_SHA")
    assert _instructions_named(runtime, "ARG") == expected_runtime_args, service
    assert _instructions_named(runtime, "LABEL") == [
        'LABEL org.opencontainers.image.revision="${EXPECTED_GIT_SHA}"'
    ], service
    assert _instructions_named(runtime, "ENV") == [
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1"
    ], service
    assert _instructions_named(runtime, "WORKDIR") == ["WORKDIR /app"], service

    assert _instructions_named(runtime, "COPY") == [
        f"COPY {service}/app /app/app",
        f"COPY {service}/config.toml /app/config.toml",
    ], service

    expected_runs = [OFFLINE_INSTALL_RUN]
    if service == "orchestrator_service":
        expected_runs.insert(0, FFMPEG_INSTALL_RUN)
    assert _instructions_named(runtime, "RUN") == expected_runs, service

    expected_command = (
        'CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", '
        f'"--port", "{port}", "--workers", "1"]'
    )
    assert _instructions_named(runtime, "EXPOSE") == [f"EXPOSE {port}"], service
    assert _instructions_named(runtime, "CMD") == [expected_command], service


@pytest.mark.parametrize(("service", "port"), PLATFORM_SERVICE_SPECS)
def test_platform_dockerfile_preserves_exact_stage_contract(
    service: str,
    port: str,
) -> None:
    dockerfile = (WORKSPACE_ROOT / service / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    _assert_platform_dockerfile_contract(service, port, dockerfile)


@pytest.mark.parametrize(
    "extra_instruction",
    (
        'RUN ["python", "-m", "pip", "install", "requests"]',
        'COPY ["control_service/requirements.txt", "/app/config.toml"]',
        "COPY control_service/requirements.txt config.toml",
    ),
)
def test_platform_dockerfile_contract_rejects_extra_runtime_instruction(
    extra_instruction: str,
) -> None:
    service = "control_service"
    dockerfile = (WORKSPACE_ROOT / service / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    with pytest.raises(AssertionError):
        _assert_platform_dockerfile_contract(
            service,
            "18100",
            f"{dockerfile}\n{extra_instruction}\n",
        )


@pytest.mark.parametrize(
    ("needle", "replacement"),
    (
        ("\nWORKDIR /app\n", "\n"),
        ("", '\nENTRYPOINT ["python"]\n'),
        ("", "\nADD control_service/config.toml /app/config.toml\n"),
    ),
    ids=("missing-runtime-workdir", "extra-entrypoint", "extra-add"),
)
def test_platform_dockerfile_contract_rejects_stage_shape_mutation(
    needle: str,
    replacement: str,
) -> None:
    service = "control_service"
    dockerfile = (WORKSPACE_ROOT / service / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    if needle:
        assert dockerfile.count(needle) == 1
        mutated_dockerfile = dockerfile.replace(needle, replacement)
    else:
        mutated_dockerfile = f"{dockerfile}{replacement}"

    with pytest.raises(AssertionError):
        _assert_platform_dockerfile_contract(
            service,
            "18100",
            mutated_dockerfile,
        )


def test_platform_compose_builds_and_mounts_root_level_services() -> None:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")

    assert compose.count("context: ../..") == 4
    for service in PLATFORM_SERVICE_NAMES:
        assert f"dockerfile: {service}/docker/Dockerfile" in compose
        assert f"../../{service}/config.toml:/config/config.toml:ro" in compose


def test_platform_compose_limits_host_exposure_and_passes_optional_revision_arg() -> None:
    document = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    services = document["services"]
    expected_ports = {
        "control-service": ["18100:18100"],
        "orchestrator-service": ["127.0.0.1:18101:18101"],
        "vision-orchestrator-service": ["127.0.0.1:18102:8010"],
        "online-gateway-service": ["18103:8001"],
    }

    for service_name, ports in expected_ports.items():
        service = services[service_name]
        assert service["ports"] == ports
        assert service["build"]["args"] == {
            "EXPECTED_GIT_SHA": "${EXPECTED_GIT_SHA:-}"
        }
    assert "${EXPECTED_GIT_SHA:?" not in COMPOSE_PATH.read_text(encoding="utf-8")
