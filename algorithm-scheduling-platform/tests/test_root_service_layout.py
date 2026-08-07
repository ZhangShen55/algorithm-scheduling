import tomllib
from pathlib import Path

import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_ROOT = WORKSPACE_ROOT / "algorithm-scheduling-platform"
SERVICE_NAMES = (
    "control_service",
    "orchestrator_service",
    "vision_orchestrator_service",
    "online_gateway_service",
)
REQUIRED_FILES = (
    "app/main.py",
    "tests",
    "docker/Dockerfile",
    "config.toml",
    "requirements.txt",
    "README.md",
)
LEGACY_REFERENCE_PATTERNS = (
    "algorithm-scheduling-platform/services",
    *(f"services.{service_name}" for service_name in SERVICE_NAMES),
)
DELIVERY_TEXT_SUFFIXES = {".md", ".py", ".toml", ".yml", ".yaml"}


@pytest.mark.parametrize("service_name", SERVICE_NAMES)
def test_platform_service_is_a_root_level_fastapi_project(service_name: str) -> None:
    service_root = WORKSPACE_ROOT / service_name

    assert service_root.is_dir()
    for required_path in REQUIRED_FILES:
        assert (service_root / required_path).exists(), required_path


def test_legacy_services_package_is_removed() -> None:
    assert not (PLATFORM_ROOT / "services").exists()


def test_shared_distribution_does_not_package_service_projects() -> None:
    pyproject = tomllib.loads((PLATFORM_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    included = pyproject["tool"]["setuptools"]["packages"]["find"]["include"]

    assert "packages*" in included
    assert "services*" not in included


def _delivery_files() -> list[Path]:
    roots = [
        *(WORKSPACE_ROOT / service_name for service_name in SERVICE_NAMES),
        PLATFORM_ROOT / "deploy",
        PLATFORM_ROOT / "scripts",
        PLATFORM_ROOT / "README.md",
        PLATFORM_ROOT / "Makefile",
        WORKSPACE_ROOT / "docs",
    ]
    files: list[Path] = []
    for root in roots:
        candidates = [root] if root.is_file() else root.rglob("*")
        files.extend(
            path
            for path in candidates
            if path.is_file()
            and (path.suffix in DELIVERY_TEXT_SUFFIXES or path.name in {"Dockerfile", "Makefile"})
        )
    return files


def test_delivery_files_do_not_use_legacy_service_paths() -> None:
    offenders = [
        path.relative_to(WORKSPACE_ROOT).as_posix()
        for path in _delivery_files()
        if any(
            pattern in path.read_text(encoding="utf-8")
            for pattern in LEGACY_REFERENCE_PATTERNS
        )
    ]

    assert offenders == []


def test_legacy_path_gate_covers_runtime_build_and_documentation_surfaces() -> None:
    scanned = {path.relative_to(WORKSPACE_ROOT).as_posix() for path in _delivery_files()}
    expected = {
        "control_service/app/main.py",
        "control_service/docker/Dockerfile",
        "algorithm-scheduling-platform/deploy/docker-compose.platform.yml",
        "algorithm-scheduling-platform/scripts/check_migrations.py",
        "algorithm-scheduling-platform/Makefile",
        "algorithm-scheduling-platform/README.md",
        "docs/算法功能调度平台总体设计-v2.md",
    }

    assert expected <= scanned


def test_root_docker_context_uses_an_explicit_allowlist() -> None:
    dockerignore = (WORKSPACE_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert dockerignore.splitlines()[0] == "**"
    assert "!algorithm-scheduling-platform/pyproject.toml" in dockerignore
    assert "!algorithm-scheduling-platform/packages/**" in dockerignore
    for service_name in SERVICE_NAMES:
        assert f"!{service_name}/requirements.txt" in dockerignore
        assert f"!{service_name}/config.toml" in dockerignore
        assert f"!{service_name}/app/**" in dockerignore


def test_make_compose_check_parses_every_compose_definition() -> None:
    makefile = (PLATFORM_ROOT / "Makefile").read_text(encoding="utf-8")
    compose_check = makefile.split("compose-check:", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]

    assert "docker-compose.infrastructure.yml" in compose_check
    assert "docker-compose.operators.yml" in compose_check
    assert "docker-compose.platform.yml" in compose_check


@pytest.mark.parametrize("service_name", SERVICE_NAMES)
def test_service_container_uses_independent_app_entrypoint(service_name: str) -> None:
    dockerfile = (WORKSPACE_ROOT / service_name / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "app.main:app" in dockerfile
    assert "COPY services" not in dockerfile
