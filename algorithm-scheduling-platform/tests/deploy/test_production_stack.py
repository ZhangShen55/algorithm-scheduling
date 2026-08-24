from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from deploy.scripts import production_stack
from deploy.scripts.operator_topology import CURRENT_TOPOLOGY

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _container(
    service: str,
    project: str,
    *,
    running: bool = True,
    healthy: bool = True,
) -> dict[str, object]:
    labels = {
        "com.docker.compose.project": project,
        "com.docker.compose.service": service,
    }
    container_port, host_port, remote = production_stack._port_contract(service)
    environment: list[str] = []
    device_requests: list[dict[str, object]] = []
    if "-gpu" in service:
        gpu_id = service.rsplit("gpu", 1)[1]
        environment = [f"PLATFORM_GPU_ID={gpu_id}", f"NVIDIA_VISIBLE_DEVICES={gpu_id}"]
        device_requests = [{"Driver": "nvidia", "DeviceIDs": [gpu_id]}]
    return {
        "Id": (service.encode().hex() + "0" * 64)[:64],
        "Image": "sha256:" + "a" * 64,
        "Config": {"Labels": labels, "Env": environment},
        "HostConfig": {"DeviceRequests": device_requests},
        "NetworkSettings": {
            "Ports": {
                f"{container_port}/tcp": [
                    {
                        "HostIp": "0.0.0.0" if remote else "127.0.0.1",
                        "HostPort": str(host_port),
                    }
                ]
            }
        },
        "State": {
            "Running": running,
            "Health": {"Status": "healthy" if healthy else "unhealthy"},
        },
    }


def test_start_plan_uses_persistent_stages_not_canonical_restore() -> None:
    plan = production_stack.build_start_plan(
        deploy_root=PROJECT_ROOT / "deploy",
        git_sha="a" * 40,
        release_tag="v1.0_260823",
        reports_root=PROJECT_ROOT / "deploy/reports/production",
    )

    assert [step.name for step in plan] == [
        "infrastructure",
        "migrations",
        "platform",
        "runtime-preflight",
        "gpu0",
        "gpu0-readiness",
        "gpu1",
        "gpu1-readiness",
        "gpu2",
        "gpu2-readiness",
        "cpu",
        "cpu-readiness",
        "operators-full-readiness",
    ]
    rendered = "\n".join(" ".join(step.command) for step in plan)
    assert "restore-existing-containers" not in rendered
    assert "operator_lifecycle" not in rendered
    assert "run-milestone-2b-8a7" not in rendered
    assert "down" not in rendered
    assert "--remove-orphans" not in rendered
    assert plan[1].command == (
        str(PROJECT_ROOT / "deploy/scripts/apply-database-migrations"),
        "--git-sha",
        "a" * 40,
        "--adopt-existing",
    )
    full_readiness = plan[-1].command
    assert full_readiness.count("--git-sha") == 1


def test_release_environment_and_default_ledger_follow_cli_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for variable in (
        "EXPECTED_GIT_SHA",
        "OPERATOR_REGISTRY_TOKEN",
        *production_stack.OPERATOR_IMAGE_REPOSITORIES,
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("OPERATOR_REGISTRY_TOKEN", "registry-token")

    production_stack._configure_release_environment(
        "a" * 40,
        "v1.0_260823",
        require_registry_token=True,
    )

    assert os.environ["EXPECTED_GIT_SHA"] == "a" * 40
    assert os.environ["OCR_IMAGE"] == "algorithm-ocr:v1.0_260823"
    assert production_stack._state_root(
        None,
        reports_root=tmp_path,
        release_tag="v1.0_260823",
        git_sha="a" * 40,
    ) == tmp_path / "milestone-2b/releases/v1.0_260823" / ("a" * 40) / "production"


def test_read_only_lifecycle_commands_do_not_require_registry_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPERATOR_REGISTRY_TOKEN", raising=False)

    production_stack._configure_release_environment(
        "a" * 40,
        "v1.0_260823",
        require_registry_token=False,
    )

    assert os.environ["OPERATOR_REGISTRY_TOKEN"] == "compose-read-only-placeholder"


def test_start_requires_explicit_registry_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPERATOR_REGISTRY_TOKEN", raising=False)

    with pytest.raises(production_stack.ProductionStackError, match="OPERATOR_REGISTRY_TOKEN"):
        production_stack._configure_release_environment(
            "a" * 40,
            "v1.0_260823",
            require_registry_token=True,
        )


def test_status_requires_exact_eight_platform_and_twenty_one_operator_services() -> None:
    records = [
        _container(service, "algorithm-scheduling-platform")
        for service in (
            *production_stack.INFRASTRUCTURE_SERVICES,
            *production_stack.PLATFORM_SERVICES,
        )
    ]
    for service in CURRENT_TOPOLOGY.instance_ids:
        records.append(_container(service, "algorithm-operators"))

    assert production_stack._validate_port_bindings(records) == {
        port: True for port in production_stack.REQUIRED_HOST_PORTS
    }

    status = production_stack.summarize_status(
        records,
        expected_git_sha="a" * 40,
        shared_directories={"/data/course": True, "/data/result": True},
        registration_count=21,
        active_lease_count=0,
        critical_ports={port: True for port in production_stack.REQUIRED_HOST_PORTS},
        image_revisions={"sha256:" + "a" * 64: "a" * 40},
    )

    assert status["status"] == "PASS"
    assert status["checked_at"].endswith("+00:00")
    assert status["summary"] == {
        "infrastructure": 4,
        "platform_services": 4,
        "operator_instances": 21,
        "gpu_instances": 18,
        "cpu_instances": 3,
        "registered_instances": 21,
        "active_leases": 0,
        "gpu_assignments": {
            gpu_id: sorted(
                service
                for service in CURRENT_TOPOLOGY.instance_ids
                if service.endswith(f"gpu{gpu_id}")
            )
            for gpu_id in ("0", "1", "2")
        },
    }


def test_status_rejects_gpu_device_request_or_host_binding_drift() -> None:
    gpu = _container("ocr-gpu1", "algorithm-operators")
    gpu["HostConfig"] = {"DeviceRequests": [{"Driver": "nvidia", "DeviceIDs": ["0"]}]}
    with pytest.raises(production_stack.ProductionStackError, match="GPU 环境或设备请求"):
        production_stack._validate_device_ownership("ocr-gpu1", gpu)

    control = _container("control-service", "algorithm-scheduling-platform")
    control["NetworkSettings"] = {
        "Ports": {"18100/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18100"}]}
    }
    with pytest.raises(production_stack.ProductionStackError, match="端口地址边界"):
        production_stack._validate_port_bindings([control])


def test_stop_plan_only_uses_exact_ids_from_authority_ledger(tmp_path: Path) -> None:
    container_ids = [f"{index:064x}" for index in range(1, 30)]
    services = [
        *production_stack.INFRASTRUCTURE_SERVICES,
        *production_stack.PLATFORM_SERVICES,
        *CURRENT_TOPOLOGY.instance_ids,
    ]
    ledger = tmp_path / "production-stack.json"
    ledger.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "git_sha": "a" * 40,
                "release_tag": "v1.0_260823",
                "containers": [
                    {
                        "container_id": container_id,
                        "service": service,
                        "compose_project": (
                            "algorithm-operators"
                            if service in CURRENT_TOPOLOGY.instance_ids
                            else "algorithm-scheduling-platform"
                        ),
                        "image_id": "sha256:" + "a" * 64,
                    }
                    for container_id, service in zip(container_ids, services, strict=True)
                ],
            }
        ),
        encoding="utf-8",
    )
    ledger.chmod(0o600)

    commands = production_stack.build_stop_plan(
        ledger,
        expected_git_sha="a" * 40,
    )

    assert commands == [
        production_stack.CommandStep(
            name="stop-authoritative-containers",
            command=("docker", "container", "stop", *container_ids),
        )
    ]
    assert all("rm" not in step.command for step in commands)
    assert all("down" not in step.command for step in commands)


@pytest.mark.parametrize(
    "entrypoint",
    ("start-production-stack", "status-production-stack", "stop-production-stack"),
)
def test_production_entrypoints_are_executable(entrypoint: str) -> None:
    path = PROJECT_ROOT / "deploy/scripts" / entrypoint
    assert path.is_file()
    assert os.access(path, os.X_OK)
    completed = subprocess.run(
        [str(path), "--help"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
