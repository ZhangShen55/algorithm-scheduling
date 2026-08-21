from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml  # type: ignore[import-untyped]

from deploy.scripts.deployment_contracts import (
    DeploymentContractError,
    validate_existing_algorithm_containers,
    validate_logging_root,
    validate_operator_config_mounts,
    validate_operator_service_contracts,
    validate_operator_toml_contract,
    validate_registry_wheel_dockerfile,
    validate_release_architecture,
    validate_release_tag,
    validate_root_disk,
    validate_writable_directory,
)

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_MODULE = PLATFORM_ROOT / "deploy/scripts/deployment_contracts.py"
CHECKOUT_RELEASE = PLATFORM_ROOT / "deploy/scripts/checkout-release"
OPERATOR_COMPOSE = PLATFORM_ROOT / "deploy/docker-compose.operators.yml"


def _valid_service(
    name: str = "ppt-slice-cpu0",
    *,
    config_path: str = "/workspace/config.toml",
) -> dict[str, Any]:
    return {
        "environment": {
            "CONFIG_PATH": config_path,
            "UVICORN_WORKERS": "1",
        },
        "volumes": [
            {
                "type": "bind",
                "source": "/release/config/operators/ppt_slice.cpu.toml",
                "target": config_path,
                "read_only": True,
            }
        ],
    }


def test_deployment_contract_module_is_a_production_entrypoint() -> None:
    assert CONTRACT_MODULE.is_file(), "缺少可复用的生产部署合同入口"


def test_dep_001_rejects_arm_image_on_x86_release_host() -> None:
    with pytest.raises(DeploymentContractError, match="image architecture"):
        validate_release_architecture("x86_64", ["arm64"])

    validate_release_architecture("x86_64", ["amd64", "x86_64"])


@pytest.mark.parametrize("tag", ["V1.0_260818", "latest", "v1.0_20260818"])
def test_dep_003_rejects_noncanonical_release_tags(tag: str) -> None:
    with pytest.raises(DeploymentContractError, match="release tag"):
        validate_release_tag(tag)

    assert validate_release_tag("v1.0_260818") == "v1.0_260818"


@pytest.mark.parametrize("service_name", ["vbas-gpu0", "ppt-slice-cpu0"])
def test_dep_007_rejects_multi_worker_operator_services(
    service_name: str,
) -> None:
    service = _valid_service(service_name)
    service["environment"]["UVICORN_WORKERS"] = "2"

    with pytest.raises(DeploymentContractError, match="exactly one Uvicorn worker"):
        validate_operator_service_contracts({service_name: service})


def test_operator_service_contract_accepts_canonical_compose() -> None:
    document = cast(
        dict[str, Any], yaml.safe_load(OPERATOR_COMPOSE.read_text(encoding="utf-8"))
    )

    validate_operator_service_contracts(document["services"])
    validate_operator_config_mounts(
        document["services"],
        compose_directory=OPERATOR_COMPOSE.parent,
    )


def test_operator_toml_contract_rejects_invalid_capacity(tmp_path: Path) -> None:
    config = tmp_path / "ocr.toml"
    config.write_text(
        """
[platform]
registration_enabled = true
control_service_url = "http://control-service:18100"
heartbeat_interval_seconds = 5
max_concurrent_requests = 0
[runtime]
require_gpu = true
[ocr]
image_max_bytes = 52428800
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(DeploymentContractError, match="max_concurrent_requests"):
        validate_operator_toml_contract("ocr-gpu0", config)


@pytest.mark.parametrize(
    "source",
    [
        "FROM python:3.11\nCOPY app /app\n",
        (
            "FROM python:3.11\n"
            "COPY wheel/algorithm_operator_registry_client-0.1.0-py3-none-any.whl "
            "/tmp/client.whl\n"
        ),
    ],
)
def test_dep_010_and_011_require_the_exact_registry_wheel(source: str) -> None:
    with pytest.raises(DeploymentContractError, match="registry client wheel"):
        validate_registry_wheel_dockerfile(source, "operator/Dockerfile")

    validate_registry_wheel_dockerfile(
                "COPY wheel/algorithm_operator_registry_client-0.2.0-py3-none-any.whl "
        "/tmp/client.whl\n",
        "operator/Dockerfile",
    )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("missing", "CONFIG_PATH"),
        ("wrong_target", "CONFIG_PATH"),
        ("writable", "read-only"),
        ("duplicate", "exactly one config bind"),
    ],
)
def test_dep_014_requires_one_read_only_config_path_bind(
    mutation: str,
    expected: str,
) -> None:
    service = _valid_service()
    if mutation == "missing":
        service["environment"].pop("CONFIG_PATH")
    elif mutation == "wrong_target":
        service["environment"]["CONFIG_PATH"] = "/wrong/config.toml"
    elif mutation == "writable":
        service["volumes"][0]["read_only"] = False
    elif mutation == "duplicate":
        service["volumes"].append(
            {
                "type": "bind",
                "source": "/release/other.toml",
                "target": "/other/config.toml",
                "read_only": True,
            }
        )

    with pytest.raises(DeploymentContractError, match=expected):
        validate_operator_service_contracts({"ppt-slice-cpu0": service})


def test_dep_015_and_016_probe_directory_writability_without_leaving_files(
    tmp_path: Path,
) -> None:
    writable = tmp_path / "writable"
    writable.mkdir(mode=0o700)
    validate_writable_directory(writable)
    assert list(writable.iterdir()) == []

    unwritable = tmp_path / "unwritable"
    unwritable.mkdir(mode=0o500)
    try:
        with pytest.raises(DeploymentContractError, match="not writable"):
            validate_writable_directory(unwritable)
    finally:
        unwritable.chmod(0o700)


def test_logging_root_is_created_and_checked_only_for_optional_mounts(tmp_path: Path) -> None:
    log_root = tmp_path / "logs" / "algorithm-scheduling"
    validate_logging_root(log_root, minimum_free_gib=0)
    assert log_root.is_dir()


def test_logging_root_rejects_symlink_components(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "logs"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(DeploymentContractError, match="symlink"):
        validate_logging_root(link / "algorithm-scheduling", minimum_free_gib=0)


def test_dep_017_enforces_the_root_disk_threshold() -> None:
    with pytest.raises(DeploymentContractError, match="root disk"):
        validate_root_disk(99 * 1024 * 1024, 100)

    validate_root_disk(100 * 1024 * 1024, 100)


def test_dep_018_rejects_unknown_algorithm_prefixed_containers() -> None:
    unknown = [{"Name": "/algorithm-control-service", "Config": {"Labels": {}}}]
    allowed = {
        ("algorithm-scheduling-platform", "control-service"),
        ("algorithm-operators", "vbas-gpu0"),
    }

    with pytest.raises(DeploymentContractError, match="unknown algorithm container"):
        validate_existing_algorithm_containers(unknown, allowed)

    canonical = [
        {
            "Name": "/algorithm-scheduling-platform-control-service-1",
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "algorithm-scheduling-platform",
                    "com.docker.compose.service": "control-service",
                }
            },
        }
    ]
    validate_existing_algorithm_containers(canonical, allowed)


def test_retired_text_analysis_containers_are_allowed_only_as_exact_exited_assets() -> None:
    allowed = {
        ("algorithm-scheduling-platform", "control-service"),
        ("algorithm-operators", "vbas-gpu0"),
    }
    retired = [
        {
            "Name": f"/algorithm-operators-text-analysis-cpu{gpu_id}-1",
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "algorithm-operators",
                    "com.docker.compose.service": f"text-analysis-cpu{gpu_id}",
                }
            },
            "State": {"Status": "exited", "Running": False},
        }
        for gpu_id in range(3)
    ]
    validate_existing_algorithm_containers(retired, allowed)

    running = dict(retired[0])
    running["State"] = {"Status": "running", "Running": True}
    with pytest.raises(DeploymentContractError, match="unknown algorithm container"):
        validate_existing_algorithm_containers([running], allowed)

    disguised = dict(retired[0])
    disguised["Name"] = "/algorithm-operators-text-analysis-cpu0-copy"
    with pytest.raises(DeploymentContractError, match="unknown algorithm container"):
        validate_existing_algorithm_containers([disguised], allowed)


def test_dep_020_checkout_release_fails_closed_when_fixed_commit_cannot_be_read(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/usr/bin/env bash\nexit 23\n", encoding="utf-8")
    fake_git.chmod(0o755)
    identity = tmp_path / "deploy-key"
    identity.write_text("test-only-key", encoding="utf-8")
    identity.chmod(0o600)
    destination = tmp_path / "release"
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"

    completed = subprocess.run(
        [
            str(CHECKOUT_RELEASE),
            "--repository",
            "git@example.invalid:team/repository.git",
            "--git-sha",
            "a" * 40,
            "--destination",
            str(destination),
            "--identity-file",
            str(identity),
        ],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert completed.returncode != 0
    assert "fixed commit checkout failed" in completed.stderr
    assert not destination.exists()
    assert not list(tmp_path.glob(".checkout-release-*"))


def test_release_entrypoints_consume_the_shared_contract_module() -> None:
    preflight = (PLATFORM_ROOT / "deploy/scripts/preflight").read_text(
        encoding="utf-8"
    )
    build_images = (PLATFORM_ROOT / "deploy/scripts/build-images").read_text(
        encoding="utf-8"
    )
    build_contexts = (
        PLATFORM_ROOT / "deploy/scripts/verify-operator-build-contexts"
    ).read_text(encoding="utf-8")

    assert "deployment_contracts.py" in preflight
    assert "deployment_contracts.py" in build_images
    assert "deployment_contracts import" in build_contexts
