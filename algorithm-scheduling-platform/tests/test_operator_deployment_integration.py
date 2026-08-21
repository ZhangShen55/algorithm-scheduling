import ast
import json
import tomllib
from pathlib import Path

import yaml

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_REQUIREMENT = "algorithm-operator-registry-client==0.2.0"
REGISTRY_WHEEL = "algorithm_operator_registry_client-0.2.0-py3-none-any.whl"
CAPACITY_BASELINE = (
    WORKSPACE_ROOT
    / "algorithm-scheduling-platform/harness/baselines/"
    "unified-operator-capacity-leases-and-online-ocr.json"
)
OPERATOR_TOPOLOGY = (
    WORKSPACE_ROOT / "algorithm-scheduling-platform/deploy/operator-topology.json"
)
LOCAL_CONFIGS = {
    "asr_online": "config.toml",
    "asr_offline": "config.toml",
    "facerec": "config.example.toml",
    "ocr": "config.toml.example",
    "screen_det": "config.toml",
    "ppt_slice": "config.toml",
    "vbas": "config.toml",
}


def _declared_routes(project: str) -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for source_path in (WORKSPACE_ROOT / project / "app").rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not decorator.args:
                    continue
                function = decorator.func
                if not isinstance(function, ast.Attribute):
                    continue
                method = function.attr.upper()
                if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "WEBSOCKET"}:
                    continue
                path_node = decorator.args[0]
                if isinstance(path_node, ast.Constant) and isinstance(path_node.value, str):
                    routes.add((method, path_node.value))
    return routes


def test_unified_capacity_change_has_machine_readable_compatibility_baseline() -> None:
    baseline = json.loads(CAPACITY_BASELINE.read_text(encoding="utf-8"))
    topology = json.loads(OPERATOR_TOPOLOGY.read_text(encoding="utf-8"))
    assert baseline["baseline_git_revision"] == "bd59541"
    capacities = baseline["approved_current_capacities"]
    current_projects = {
        operator["project_directory"] for operator in topology["operators"]
    }
    assert current_projects == set(LOCAL_CONFIGS)
    deploy_names = {
        "asr_online": "asr_online.gpu.toml",
        "asr_offline": "asr_offline.gpu.toml",
        "facerec": "facerec.gpu.toml",
        "ocr": "ocr.gpu.toml",
        "screen_det": "screen_det.gpu.toml",
        "ppt_slice": "ppt_slice.cpu.toml",
        "vbas": "vbas.gpu.toml",
    }

    for project in sorted(current_projects):
        contract = baseline["operators"][project]
        current_routes = _declared_routes(project)
        expected_routes = {
            (route["method"], route["path"])
            for route in contract["business_routes"]
        }
        assert expected_routes.issubset(current_routes), project

        root_config = tomllib.loads(
            (WORKSPACE_ROOT / project / LOCAL_CONFIGS[project]).read_text(
                encoding="utf-8"
            )
        )
        assert root_config["platform"] == {
            "registration_enabled": False,
            "control_service_url": "",
            "heartbeat_interval_seconds": 5,
            "max_concurrent_requests": capacities[project],
        }
        assert root_config["runtime"]["require_gpu"] is False

        deploy_config = tomllib.loads(
            (
                WORKSPACE_ROOT
                / "algorithm-scheduling-platform/deploy/config/operators"
                / deploy_names[project]
            ).read_text(encoding="utf-8")
        )
        assert (
            deploy_config["platform"]["max_concurrent_requests"]
            == capacities[project]
        )

    compose = yaml.safe_load(
        (
            WORKSPACE_ROOT
            / "algorithm-scheduling-platform/deploy/docker-compose.operators.yml"
        ).read_text(encoding="utf-8")
    )
    forbidden = set(baseline["compose_type_configuration_environment"])
    for service in compose["services"].values():
        assert forbidden.isdisjoint(service["environment"])


def test_all_current_operator_entrypoints_install_the_shared_registry_runtime() -> None:
    expected = {
        "asr_offline": ("app/main.py", "asr_offline", ["asr_offline"]),
        "asr_online": ("app/main.py", "asr_online", ["asr_online"]),
        "ppt_slice": ("app/main.py", "ppt_slice", ["ppt_slice"]),
        "ocr": ("app/main.py", "ocr", ["ocr"]),
        "vbas": ("app/main.py", "vbas", ["student_behavior", "teacher_behavior"]),
        "facerec": ("app/main.py", "facerec", ["recognize"]),
        "screen_det": ("app/application.py", "screen_det", ["detect_all"]),
    }

    for project, (relative_path, operator_code, capabilities) in expected.items():
        source = (WORKSPACE_ROOT / project / relative_path).read_text(encoding="utf-8")
        assert "install_operator_runtime" in source, project
        assert f'operator_code="{operator_code}"' in source, project
        for capability in capabilities:
            assert f'"{capability}"' in source, (project, capability)

    ppt_source = (WORKSPACE_ROOT / "ppt_slice/app/main.py").read_text(encoding="utf-8")
    assert (
        "max_concurrent_requests=(\n"
        "                operator_deployment.platform.max_concurrent_requests"
    ) in ppt_source
    assert "inflight_provider=task_manager.get_task_count" in ppt_source


def test_shared_registry_wheel_defines_stable_read_only_metadata_route() -> None:
    runtime_source = (
        WORKSPACE_ROOT
        / "algorithm-scheduling-platform/packages/operator_registry_client/runtime.py"
    ).read_text(encoding="utf-8")
    ops_source = (
        WORKSPACE_ROOT
        / "algorithm-scheduling-platform/packages/operator_registry_client/ops.py"
    ).read_text(encoding="utf-8")

    assert '@app.get("/ops/metadata"' in runtime_source
    assert "class OperatorOpsMetadata(BaseModel):" in ops_source
    assert "instance_id: str" in ops_source
    assert "operator_code: str" in ops_source
    assert "capabilities: list[str]" in ops_source
    assert "model_version: str | None" in ops_source
    assert "api_version: str | None" in ops_source


def test_asr_images_run_one_registered_uvicorn_endpoint_without_internal_nginx() -> None:
    expected_ports = {"asr_offline": 8083, "asr_online": 8084}

    for project, port in expected_ports.items():
        project_root = WORKSPACE_ROOT / project
        start_script = (project_root / "docker" / "start.sh").read_text(encoding="utf-8")
        dockerfiles = list((project_root / "docker").glob("Dockerfile*"))
        docker_text = "\n".join(path.read_text(encoding="utf-8") for path in dockerfiles)
        config = (project_root / "config.toml").read_text(encoding="utf-8")

        assert "nginx" not in start_script.lower(), project
        assert "instance_count" not in start_script, project
        assert "--workers 1" in start_script, project
        assert f'${{PORT:-{port}}}' in start_script, project
        assert "nginx" not in docker_text.lower(), project
        assert f"EXPOSE {port}" in docker_text, project
        assert "instance_count" not in config, project
        assert not (project_root / "docker" / "nginx.conf").exists(), project


def test_asr_images_use_python311_asr_environment_and_versioned_registry_wheel() -> None:
    registry_wheel = (
        "algorithm_operator_registry_client-0.2.0-py3-none-any.whl"
    )
    for project in ("asr_offline", "asr_online"):
        project_root = WORKSPACE_ROOT / project
        start_script = (project_root / "docker" / "start.sh").read_text(encoding="utf-8")
        dockerfiles = list((project_root / "docker").glob("Dockerfile*"))

        assert '${CONDA_ENV_NAME:-asr}' in start_script, project
        for dockerfile in dockerfiles:
            source = dockerfile.read_text(encoding="utf-8")
            assert "python=3.11" in source, dockerfile
            assert "/opt/conda/envs/asr" in source, dockerfile
            assert registry_wheel in source, dockerfile
            assert "pip install --no-deps" in source, dockerfile


def test_facerec_image_installs_versioned_registry_wheel() -> None:
    source = (WORKSPACE_ROOT / "facerec/docker/Dockerfile").read_text(encoding="utf-8")

    assert "algorithm_operator_registry_client-0.2.0-py3-none-any.whl" in source
    assert "pip install --no-deps" in source


def test_all_current_operator_requirements_declare_registry_client() -> None:
    requirement_files = {
        "asr_offline": ("requirements.txt", "requirements-pip.txt"),
        "asr_online": ("requirements.txt",),
        "ppt_slice": ("requirements.txt",),
        "ocr": ("requirements.txt",),
        "vbas": ("requirements.txt",),
        "facerec": ("requirements.txt",),
        "screen_det": ("requirements.txt", "docker/requirements-docker.txt"),
    }

    for project, relative_paths in requirement_files.items():
        for relative_path in relative_paths:
            requirements = (WORKSPACE_ROOT / project / relative_path).read_text(
                encoding="utf-8"
            )
            assert REGISTRY_REQUIREMENT in requirements.splitlines(), (
                project,
                relative_path,
            )


def test_all_current_operator_images_install_staged_registry_wheel() -> None:
    dockerfiles = {
        "asr_offline": ("docker/Dockerfile",),
        "asr_online": ("docker/Dockerfile", "docker/Dockerfile.cython"),
        "ppt_slice": ("Dockerfile",),
        "ocr": ("docker/Dockerfile", "docker/Dockerfile.npu"),
        "vbas": ("docker/Dockerfile", "docker/Dockerfile.runtime"),
        "facerec": ("docker/Dockerfile",),
        "screen_det": ("docker/Dockerfile",),
    }

    for project, relative_paths in dockerfiles.items():
        for relative_path in relative_paths:
            source = (WORKSPACE_ROOT / project / relative_path).read_text(
                encoding="utf-8"
            )
            assert REGISTRY_WHEEL in source, (project, relative_path)
            assert "pip install --no-deps" in source, (project, relative_path)


def test_registry_wheel_staging_entrypoint_always_rebuilds_before_staging() -> None:
    source = (
        WORKSPACE_ROOT
        / "algorithm-scheduling-platform/scripts/stage_operator_registry_wheel.py"
    ).read_text(encoding="utf-8")

    assert "build_and_stage_registry_wheel" in source
    assert "shutil.copy2" not in source


def test_current_operator_business_routes_and_default_ports_remain_compatible() -> None:
    contracts = {
        "asr_offline": {
            "port": "8083",
            "sources": {
                "app/api/routes/asr.py": ["/v1.1.8/seacraft_asr"],
            },
        },
        "asr_online": {
            "port": "8084",
            "sources": {
                "app/api/routes/ws_online.py": ["/v1.0.1/seacraft_asr_online"],
            },
        },
        "facerec": {
            "port": "8003",
            "sources": {"app/router/faces.py": ["/recognize"]},
        },
        "ocr": {
            "port": "8866",
            "sources": {"app/api/routes/ocr.py": ["/ocr/prediction"]},
        },
        "screen_det": {
            "port": "8880",
            "sources": {"app/api/v1/aggregate.py": ["/detect_all"]},
        },
        "ppt_slice": {
            "port": "9001",
            "sources": {
                "app/api/v1/video.py": ["/LocalVideoPPTSliceTasks/v1.0.0"]
            },
        },
        "vbas": {
            "port": "8981",
            "sources": {
                "app/api/stu_tea_behavior.py": [
                    "/ImageDetect/student/v1.0.0",
                    "/ImageDetect/teacher/v1.0.0",
                ]
            },
        },
    }

    for project, contract in contracts.items():
        project_root = WORKSPACE_ROOT / project
        searchable = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                project_root / "app" / "main.py",
                project_root / "app" / "application.py",
                project_root / "config.toml",
                project_root / "AGENTS.md",
            )
            if path.exists()
        )
        assert contract["port"] in searchable, project
        for relative_path, paths in contract["sources"].items():
            source = (project_root / relative_path).read_text(encoding="utf-8")
            for route_path in paths:
                assert route_path in source, (project, route_path)


def test_text_analysis_is_retained_but_excluded_from_current_deployment() -> None:
    topology = json.loads(OPERATOR_TOPOLOGY.read_text(encoding="utf-8"))
    current_projects = {
        operator["project_directory"] for operator in topology["operators"]
    }
    compose = yaml.safe_load(
        (
            WORKSPACE_ROOT
            / "algorithm-scheduling-platform/deploy/docker-compose.operators.yml"
        ).read_text(encoding="utf-8")
    )
    deploy_config = (
        WORKSPACE_ROOT / "algorithm-scheduling-platform/deploy/config/operators"
    )

    assert (WORKSPACE_ROOT / "text_analysis").is_dir()
    assert "text_analysis" not in current_projects
    assert all("text-analysis" not in name for name in compose["services"])
    assert not (deploy_config / "text_analysis.cpu.toml").exists()


def test_operator_compose_declares_restart_health_mounts_and_instance_identity() -> None:
    compose_path = (
        Path(__file__).resolve().parents[1]
        / "deploy"
        / "docker-compose.operators.yml"
    )
    services = yaml.safe_load(compose_path.read_text(encoding="utf-8"))["services"]
    expected = {
        *(
            f"{operator}-gpu{index}"
            for operator in (
                "asr-offline",
                "asr-online",
                "ocr",
                "vbas",
                "facerec",
                "screen-det",
            )
            for index in range(3)
        ),
        *(f"ppt-slice-cpu{index}" for index in range(3)),
    }
    assert set(services) == expected
    capacities = {
        "asr-offline": 4,
        "asr-online": 10,
        "ocr": 256,
        "vbas": 128,
        "facerec": 128,
        "screen-det": 128,
        "ppt-slice": 10,
    }
    gpu_operators = {
        "asr-offline",
        "asr-online",
        "ocr",
        "vbas",
        "facerec",
        "screen-det",
    }
    forbidden_environment = {
        "PLATFORM_REGISTRATION_ENABLED",
        "PLATFORM_CONTROL_SERVICE_URL",
        "PLATFORM_HEARTBEAT_INTERVAL_SECONDS",
        "PLATFORM_DECLARED_CAPACITY",
        "REQUIRE_GPU",
        "GPU_PROCESS_NAME",
    }
    for name, service in services.items():
        environment = service["environment"]
        operator_name = next(
            operator for operator in capacities if name.startswith(f"{operator}-")
        )
        assert service["restart"] == "unless-stopped"
        assert service["networks"] == ["algorithm-platform"]
        assert "/ops/health" in " ".join(service["healthcheck"]["test"])
        assert forbidden_environment.isdisjoint(environment)
        assert environment["PLATFORM_OPERATOR_REGISTRY_TOKEN"] == (
            "${OPERATOR_REGISTRY_TOKEN:?OPERATOR_REGISTRY_TOKEN is required}"
        )
        assert environment["PLATFORM_INSTANCE_ID"] == name
        assert environment["UVICORN_WORKERS"] == "1"
        volume_targets = {volume["target"]: volume for volume in service["volumes"]}
        assert "/data/course" in volume_targets
        assert "/data/result" in volume_targets
        config_volumes = [
            volume
            for target, volume in volume_targets.items()
            if target.endswith("config.toml")
        ]
        assert len(config_volumes) == 1
        assert config_volumes[0]["read_only"] is True
        config_path = (
            compose_path.parent / config_volumes[0]["source"]
        ).resolve()
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        assert config["platform"] == {
            "registration_enabled": True,
            "control_service_url": "http://control-service:18100",
            "heartbeat_interval_seconds": 5,
            "max_concurrent_requests": capacities[operator_name],
        }
        assert config["runtime"]["require_gpu"] is (
            operator_name in gpu_operators
        )
