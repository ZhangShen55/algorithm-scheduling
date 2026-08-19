from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.aggregate_milestone_2b_cases import load_operator_inventory
from scripts.milestone_2b_case_catalog import load_case_catalog
from scripts.milestone_2b_case_runners.base import CaseContext
from scripts.milestone_2b_case_runners.process import (
    CommandResult,
    CommandSpec,
    FoundationCheckAction,
    FoundationCleanupAction,
    foundation_cleanup_resources,
    validate_command_spec,
)
from scripts.milestone_2b_case_runners.safety import (
    MaintenanceLockGuard,
    ResourceSpec,
    _case_execution_scope,
)

PLATFORM_ROOT = Path(__file__).parents[1]
CATALOG_PATH = PLATFORM_ROOT / "deploy/milestone-2b-case-catalog.yaml"
FOUNDATION_RANGES = {
    "DEP": ("deployment", 20),
    "GPU": ("gpu", 20),
    "REG": ("registry", 20),
    "INF": ("infrastructure", 16),
}
FACEREC_PROBE_RESULT_MARKER = "@@M2B_FACEREC_PROBE_RESULT_V1@@"


def _action_resources(
    group: str, case_id: str | None = None
) -> tuple[ResourceSpec, ...]:
    resources = [ResourceSpec("filesystem", "/tmp/input.json")]
    if group == "deployment" and case_id in {
        "DEP-013",
        "DEP-015",
        "DEP-016",
        "DEP-019",
        "DEP-020",
    }:
        resources.append(
            ResourceSpec(
                "filesystem",
                f"/tmp/m2b-5-run-1-{case_id.lower()}-scratch-placeholder",
            )
        )
    elif group == "gpu":
        resources.append(ResourceSpec("container", "facerec-gpu0"))
    elif group == "registry":
        resources.append(ResourceSpec("redis_prefix", "m2b:run-1:case:"))
        if case_id in {"REG-014", "REG-015"}:
            safe_case = case_id.lower().replace("-", "_")
            resources.append(
                ResourceSpec("database", f"m2b_5_run_1_{safe_case}_test")
            )
    elif group == "infrastructure":
        resources.append(ResourceSpec("database", "m2b_5_run_1_case_test"))
    return tuple(resources)


def _foundation_cases() -> tuple[object, ...]:
    prefixes = set(FOUNDATION_RANGES)
    return tuple(
        case
        for case in load_case_catalog(CATALOG_PATH).cases
        if case.case_id.split("-", 1)[0] in prefixes
    )


def test_all_foundation_checkers_avoid_literal_runtime_claims() -> None:
    violations: list[str] = []
    for module_name in ("deployment", "gpu", "registry", "infrastructure"):
        path = (
            PLATFORM_ROOT
            / "scripts/milestone_2b_case_runners"
            / f"{module_name}.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not function.name.startswith("_check_"):
                continue
            for returned in ast.walk(function):
                if not isinstance(returned, ast.Return):
                    continue
                if not isinstance(returned.value, ast.Dict):
                    continue
                for key, value in zip(
                    returned.value.keys,
                    returned.value.values,
                    strict=True,
                ):
                    if (
                        isinstance(key, ast.Constant)
                        and isinstance(key.value, str)
                        and isinstance(value, ast.Constant)
                        and (
                            isinstance(value.value, bool)
                            or type(value.value) is int
                        )
                    ):
                        violations.append(
                            f"{module_name}.{function.name}:{key.value}="
                            f"{value.value!r}"
                        )

    assert violations == []


def test_dep_004_reports_production_validator_observation_not_routing_claim() -> None:
    deployment = importlib.import_module(
        "scripts.milestone_2b_case_runners.deployment"
    )

    observed = deployment._check_dep_004()

    assert "重复实例: m2b-controlled-duplicate" in observed["issues"]
    assert observed["validator_observed_instance_ids"] == [
        "m2b-controlled-duplicate"
    ]
    assert "routable_count" not in observed


def test_reg_007_reports_validator_observation_not_registration_claim() -> None:
    registry = importlib.import_module(
        "scripts.milestone_2b_case_runners.registry"
    )

    observed = registry._check_reg_007(
        object(),
        "m2b-reg-007",
        "http://127.0.0.1:18100",
    )

    assert "m2b-reg-007 capability 不匹配" in observed["issues"]
    assert observed["validator_observed_instance_ids"] == ["m2b-reg-007"]
    assert "registered" not in observed


def _release_root(tmp_path: Path) -> Path:
    release_root = tmp_path / "v1.0_260818" / ("1" * 40)
    release_root.mkdir(parents=True)
    return release_root


def _foundation_case(case_id: str) -> Any:
    return next(case for case in _foundation_cases() if case.case_id == case_id)


def _infrastructure_probe_scenario(case_id: str) -> dict[str, object]:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    run_id = "run-1"
    return {
        "schema_version": 1,
        "case_id": case_id,
        "mode": "controlled_input",
        "run_id": run_id,
        "mutation": {"case": case_id},
        "control_url": "http://127.0.0.1:18100",
        "orchestrator_url": "http://127.0.0.1:18101",
        "facerec_url": "http://127.0.0.1:18003",
        "mongodb_credentials": "m2b_test_invalid:m2b_test_invalid",
        **infrastructure._expected_names(run_id, case_id),
    }


def _facerec_probe_payload(
    case_id: str, scenario: dict[str, object]
) -> dict[str, object]:
    if case_id == "INF-014":
        return {
            "ready": False,
            "detail": "MongoDB Authentication failed",
            "database_ready": False,
            "authenticated": False,
            "person_lookup_attempts": 1,
            "person_write_attempts": 0,
            "empty_person_created": False,
            "persistence_error": "Authentication failed",
            "authentication_error_type": "OperationFailure",
            "authentication_cause_type": "OperationFailure",
            "authentication_error_code": 18,
            "authentication_error_code_name": "AuthenticationFailed",
            "authentication_error_wrapped": False,
            "person_count_after_auth_failure": 0,
            "isolated_database": scenario["mongodb_database"],
            "production_persistence_validator": (
                "app.services.person.update_or_create_person"
            ),
            "production_validator": "FaceRecReadiness",
        }
    return {
        "production_validator": "filter_candidate_embeddings",
        "production_candidate_query": "app.services.person.get_targets_embeddings",
        "production_recognition_validator": (
            "app.core.ai_engine.find_best_match_embedding"
        ),
        "isolated_database": scenario["mongodb_database"],
        "valid_records": 0,
        "valid_vectors": 0,
        "queried_records": 2,
        "rejections": [
            {"record": "missing", "reason": "embedding_missing"},
            {
                "record": "wrong-dimension",
                "reason": "embedding_dimension_invalid",
            },
        ],
        "recognition_skipped_bad_records": True,
    }


def _facerec_result_frame(payload: object) -> str:
    return FACEREC_PROBE_RESULT_MARKER + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _registry_probe_scenario(case_id: str) -> dict[str, object]:
    run_id = "run-1"
    return {
        "schema_version": 1,
        "case_id": case_id,
        "mode": "canonical_runtime",
        "run_id": run_id,
        "mutation": {"case": case_id},
        "control_url": "http://127.0.0.1:18100",
        "redis_prefix": f"m2b:{run_id}:{case_id.lower()}:registry:",
        "instance_id": f"m2b-{len(run_id)}-{run_id}-{case_id.lower()}-instance",
        "registration_checker": "deploy/scripts/verify_operator_registration.py",
    }


def _require_canonical_facerec_runtime() -> None:
    token = os.getenv("OPERATOR_REGISTRY_TOKEN")
    if token is None or not token.strip():
        pytest.skip("canonical FaceRec integration requires OPERATOR_REGISTRY_TOKEN")
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(PLATFORM_ROOT / "deploy/docker-compose.operators.yml"),
            "--profile",
            "gpu0",
            "ps",
            "-q",
            "facerec-gpu0",
        ],
        cwd=PLATFORM_ROOT,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    container_ids = [
        line.strip() for line in completed.stdout.splitlines() if line.strip()
    ]
    if (
        completed.returncode != 0
        or len(container_ids) != 1
        or re.fullmatch(r"[0-9a-f]{64}", container_ids[0]) is None
    ):
        pytest.skip("canonical facerec-gpu0 container is not running")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )


def _write_healthy_gpu_evidence(release_root: Path, instance_id: str) -> tuple[Path, ...]:
    inventory = load_operator_inventory(
        PLATFORM_ROOT / "deploy/docker-compose.operators.yml"
    )
    instance = next(
        item for item in inventory.gpu_instances if item.instance_id == instance_id
    )
    assert instance.physical_gpu is not None
    assert instance.process_name is not None
    git_sha = release_root.name
    container_id = "b" * 64
    host_pid = 20_000 + instance.physical_gpu
    target = {
        "container": instance.service_name,
        "instance_id": instance.instance_id,
        "physical_gpu": instance.physical_gpu,
        "process_name": instance.process_name,
    }
    container = {
        "id": container_id,
        "name": instance.service_name,
        "instance_id": instance.instance_id,
        "init_host_pid": 10_000 + instance.physical_gpu,
    }
    gpu = {
        "physical_index": instance.physical_gpu,
        "physical_uuid": f"GPU-{instance.physical_gpu}",
        "container_visible": str(instance.physical_gpu),
    }
    product_name = (
        "NVIDIA GeForce RTX 3090"
        if instance.physical_gpu == 2
        else "NVIDIA GeForce RTX 4090 D"
    )
    compute_capability = "8.6" if instance.physical_gpu == 2 else "8.9"
    running = {
        "schema_version": 1,
        "timestamp": "2026-08-18T10:00:00+08:00",
        "commands": ["verify-gpu-instance <redacted>"],
        "mode": "running-inference",
        "target": target,
        "status": "PASS",
        "release_sha": git_sha,
        "container": container,
        "gpu": gpu,
        "cuda_probe": {
            "framework_gpu_available": True,
            "device_count": 1,
            "current_device": 0,
            "framework": "test-framework",
            "container_cuda_runtime_version": "12.1",
        },
        "activity": {
            "instance_id": instance.instance_id,
            "operator_code": instance.operator_code,
            "run_id": "gpu-negative-run",
        },
        "synchronous_samples": [
            {
                "gpu_utilization_percent": 50,
                "hardware": {
                    "temperature_c": 55,
                    "temperature_limit_c": 90,
                    "power_watts": 120,
                    "power_limit_watts": 350,
                    "hardware_slowdown": False,
                },
                "processes": [
                    {
                        "process_name": instance.process_name,
                        "host_pid": host_pid,
                        "container_pid": 42,
                        "cpu_percent": 30,
                        "gpu_utilization": {
                            "sm_percent": 30,
                            "memory_percent": 10,
                            "encoder_percent": 0,
                            "decoder_percent": 0,
                        },
                        "mapping": {
                            "docker_top": True,
                            "cgroup_full_container_id": True,
                            "nspid": [host_pid, 42],
                        },
                    }
                ]
            }
        ],
        "failure": None,
        "hardware": {
            "temperature_c": 55,
            "temperature_limit_c": 90,
            "power_watts": 120,
            "power_limit_watts": 350,
            "hardware_slowdown": False,
        },
        "utilization": {
            "cpu_percent": 30,
            "gpu_percent": 50,
            "target_sm_percent": 30,
        },
        "compatibility": {
            "gpu": {
                "physical_index": instance.physical_gpu,
                "physical_uuid": gpu["physical_uuid"],
                "product_name": product_name,
                "compute_capability": compute_capability,
                "driver_version": "570.172.08",
                "driver_cuda_version": "12.8",
                "container_cuda_runtime_version": "12.1",
            },
            "trigger": {
                "instance_id": instance.instance_id,
                "operator_code": instance.operator_code,
                "run_id": "gpu-negative-run",
            },
            "result": {
                "status": "PASS",
                "real_trigger_completed": True,
                "sample_count": 1,
                "target_sm_max_percent": 30,
            },
        },
    }
    stopped = {
        "schema_version": 1,
        "timestamp": "2026-08-18T10:01:00+08:00",
        "commands": ["verify-gpu-instance --assert-stopped <redacted>"],
        "mode": "assert-stopped",
        "target": target,
        "status": "PASS",
        "release_sha": git_sha,
        "container": container,
        "gpu": gpu,
        "prior_cuda_pids": [host_pid],
        "remaining_cuda_pids": [],
    }
    registration_producer = importlib.import_module(
        "deploy.scripts.verify_operator_registration"
    )
    expected_registration = registration_producer.load_expected(
        registration_producer.COMPOSE_PATH
    )[instance.instance_id]
    validated_instance = {
        "instance_id": instance.instance_id,
        "operator_code": expected_registration["operator_code"],
        "capabilities": sorted(expected_registration["capabilities"]),
        "service_url": expected_registration["service_url"],
        "declared_capacity": expected_registration["declared_capacity"],
        "labels": {"gpu": expected_registration["gpu"]},
        "lifecycle": "ONLINE",
        "inflight": 0,
        "model_ready": True,
        "last_heartbeat_at": "2026-08-18T10:00:00+08:00",
    }
    registration = {
        "schema_version": 1,
        "evidence_type": "operator_registration",
        "mock": False,
        "target": "operator-registry",
        "release_tag": release_root.parent.name,
        "git_sha": git_sha,
        "started_at": "2026-08-18T10:00:00+08:00",
        "status": "通过",
        "finished_at": "2026-08-18T10:00:01+08:00",
        "control_endpoint": "http://127.0.0.1:18100",
        "selection": {"mode": "instance", "values": [instance.instance_id]},
        "summary": {"expected": 1, "observed": 1, "valid": 1},
        "validated_instances": [validated_instance],
        "issues": [],
    }
    paths = (
        release_root / "gpu-instances" / f"{instance_id}.json",
        release_root / "recovery" / f"{instance_id}-stopped.json",
        release_root
        / "registration"
        / f"operator-registration-instance-{instance_id}.json",
    )
    for path, payload in zip(paths, (running, stopped, registration), strict=True):
        _write_json(path, payload)
    return paths


GPU_CANONICAL_CASE_FACTS = {
    "GPU-003": "physical_gpu_rejection",
    "GPU-004": "device_count_rejection",
    "GPU-005": "cuda_unavailable_rejection",
    "GPU-006": "cpu_device_rejection",
    "GPU-007": "missing_pid_rejection",
    "GPU-008": "python_process_name_rejection",
    "GPU-009": "operator_process_name_rejection",
    "GPU-010": "asr_gpu_binding_rejection",
    "GPU-011": "remaining_pid_rejection",
    "GPU-012": "startup_oom_rejection",
    "GPU-013": "concurrent_oom_rejection",
    "GPU-014": "hardware_state_rejection",
    "GPU-015": "gpu2_compatibility_rejection",
    "GPU-016": "pid_mapping_rejection",
    "GPU-017": "container_device_rejection",
    "GPU-018": "registration_label_rejection",
    "GPU-019": "duplicate_model_rejection",
    "GPU-020": "zero_gpu_utilization_rejection",
}


def test_platform_runtime_installs_mongodb_client_for_infrastructure_cases() -> None:
    project = tomllib.loads(
        (PLATFORM_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert "pymongo>=4.10,<5" in project["project"]["dependencies"]


def test_clean_clone_base_dependencies_cover_foundation_cleanup_imports() -> None:
    project = tomllib.loads(
        (PLATFORM_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    aiokafka_requirement = "aiokafka>=0.14,<0.15"

    assert aiokafka_requirement in project["project"]["dependencies"]
    assert aiokafka_requirement not in project["project"]["optional-dependencies"].get(
        "kafka", []
    )
    assert aiokafka_requirement not in project["project"]["optional-dependencies"][
        "dev"
    ]

    source = (
        "import sys;"
        f"sys.path.insert(0,{str(PLATFORM_ROOT)!r});"
        "import scripts.milestone_2b_case_runners.cleanup;"
        "print('cleanup-import-ok')"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", source],
        cwd=PLATFORM_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "cleanup-import-ok"


def test_clean_clone_docs_record_aiokafka_import_and_version_evidence() -> None:
    agents = (PLATFORM_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    readme = (PLATFORM_ROOT / "deploy/README.md").read_text(encoding="utf-8")
    scenario = (
        PLATFORM_ROOT / "harness/scenarios/milestone-2b-deploy.md"
    ).read_text(encoding="utf-8")

    assert "`aiokafka`" in agents
    assert "`aiokafka`" in readme
    assert "四个模块" in readme
    assert "import aiokafka" in scenario
    assert '"aiokafka": metadata.version("aiokafka")' in scenario
    assert "四个 Harness 依赖" in scenario


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_id", "observed_key"),
    [
        ("DEP-001", "rejected"),
        ("REG-007", "issues"),
        ("INF-016", "contract_rejection"),
    ],
)
async def test_non_gpu_groups_execute_a_real_foundation_checker_subprocess(
    case_id: str,
    observed_key: str,
    tmp_path: Path,
) -> None:
    case = _foundation_case(case_id)
    module_name, runner_name = case.runner.split(".", 1)
    module = importlib.import_module(
        f"scripts.milestone_2b_case_runners.{module_name}"
    )
    context = CaseContext(_release_root(tmp_path), "run-1", "local")

    async with _case_execution_scope(context, case.safety, None, case):
        outcome = await vars(module)[runner_name](context, case)

    evidence = json.loads(
        (context.release_root / outcome.evidence[0]).read_text(encoding="utf-8")
    )
    checker_result = json.loads(evidence["payload"]["stdout"])
    assert evidence["payload"]["returncode"] == 0
    assert checker_result["status"] == "通过"
    assert checker_result["reason"] == module.CASE_SPECS[case_id].reason
    assert checker_result["observed"][observed_key]


def test_infrastructure_checker_rejects_cross_run_resource_names() -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    context = CaseContext(Path("/tmp/release").resolve(), "run-1", "local")
    case = _foundation_case("INF-016")
    scenario = infrastructure._infrastructure_scenario(context, case)
    scenario.update(
        {
            "schema_version": 1,
            "case_id": case.case_id,
            "run_id": context.run_id,
            "target": context.target,
            "mode": infrastructure.CASE_SPECS[case.case_id].mode,
            "database": "shared_test",
        }
    )

    result = infrastructure.evaluate_scenario(case.case_id, scenario)

    assert result["status"] == "失败"
    assert result["observed"]["input_valid"] is False
    assert "当前 run" in result["reason"]


@pytest.mark.parametrize(
    "case_id", [f"INF-{number:03d}" for number in range(1, 16)]
)
def test_infrastructure_case_has_a_concrete_isolated_injector(case_id: str) -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    checker = infrastructure._INFRASTRUCTURE_CHECKERS[case_id]

    assert "_missing_safe_injector" not in inspect.getsource(checker)


@pytest.mark.parametrize(
    ("module_name", "case_id", "production_entrypoint"),
    [
        ("deployment", "DEP-001", "deployment_contracts.validate_release_architecture"),
        ("deployment", "DEP-002", "preflight_checks.validate_image_revisions"),
        ("deployment", "DEP-003", "deploy/scripts/build-images"),
        ("deployment", "DEP-004", "validate_instances"),
        ("deployment", "DEP-005", "preflight_checks._published_ports"),
        ("deployment", "DEP-006", "preflight_checks._validate_port_contract"),
        (
            "deployment",
            "DEP-007",
            "deployment_contracts.validate_operator_service_contracts",
        ),
        (
            "deployment",
            "DEP-008",
            "deployment_contracts.validate_operator_service_contracts",
        ),
        ("deployment", "DEP-009", "preflight_checks._validate_gpu_service"),
        (
            "deployment",
            "DEP-010",
            "deployment_contracts.validate_registry_wheel_dockerfile",
        ),
        (
            "deployment",
            "DEP-011",
            "deployment_contracts.validate_registry_wheel_dockerfile",
        ),
        ("deployment", "DEP-012", "model_asset_transaction._actual_files"),
        ("deployment", "DEP-013", "model_asset_transaction._verify_tree"),
        (
            "deployment",
            "DEP-014",
            "deployment_contracts.validate_operator_service_contracts",
        ),
        (
            "deployment",
            "DEP-015",
            "deployment_contracts.validate_writable_directory",
        ),
        (
            "deployment",
            "DEP-016",
            "deployment_contracts.validate_writable_directory",
        ),
        ("deployment", "DEP-017", "deployment_contracts.validate_root_disk"),
        (
            "deployment",
            "DEP-018",
            "deployment_contracts.validate_existing_algorithm_containers",
        ),
        ("deployment", "DEP-019", "_production_snapshot_validator"),
        ("deployment", "DEP-020", "deploy/scripts/checkout-release"),
        ("infrastructure", "INF-001", '_run_production_readiness_probe("postgres_down")'),
        ("infrastructure", "INF-002", '_run_production_readiness_probe("postgres_auth")'),
        ("infrastructure", "INF-003", '_run_production_readiness_probe("schema_missing")'),
        ("infrastructure", "INF-004", '_run_production_readiness_probe("redis_down")'),
        ("infrastructure", "INF-005", "OperatorRegistryClient"),
        ("infrastructure", "INF-006", '_run_production_readiness_probe("kafka_down")'),
        ("infrastructure", "INF-007", "KafkaTopicManager"),
        ("infrastructure", "INF-008", '_run_real_flow_probe("pipeline_duplicate", scenario)'),
        (
            "infrastructure",
            "INF-009",
            '_run_real_flow_probe("consumer_failure_replay", scenario)',
        ),
        ("infrastructure", "INF-010", '_run_real_flow_probe("outbox_failure", scenario)'),
        (
            "infrastructure",
            "INF-011",
            '_run_real_flow_probe("publisher_duplicate", scenario)',
        ),
        (
            "infrastructure",
            "INF-012",
            '_run_real_flow_probe("consumer_commit_exit", scenario)',
        ),
        ("infrastructure", "INF-013", '_run_production_readiness_probe("mongodb_down")'),
        (
            "infrastructure",
            "INF-014",
            '_run_production_readiness_probe("mongodb_auth", scenario)',
        ),
        ("infrastructure", "INF-015", "_run_production_embedding_probe"),
        ("infrastructure", "INF-016", "_run_production_message_contract_probe"),
    ],
)
def test_foundation_case_calls_an_explicit_production_entrypoint(
    module_name: str,
    case_id: str,
    production_entrypoint: str,
) -> None:
    module = importlib.import_module(
        f"scripts.milestone_2b_case_runners.{module_name}"
    )
    checker = (
        module._DEPLOYMENT_CHECKERS[case_id]
        if module_name == "deployment"
        else module._INFRASTRUCTURE_CHECKERS[case_id]
    )

    assert production_entrypoint in inspect.getsource(checker)


def test_reg_018_executes_operator_registry_client_recovery_flow() -> None:
    registry = importlib.import_module(
        "scripts.milestone_2b_case_runners.registry"
    )

    observed = registry._check_reg_018(object(), "m2b-reg-018-instance", "")

    assert observed["production_client"] == "OperatorRegistryClient"
    assert observed["reported_success_before_recovery"] is False
    assert observed["reported_success_after_recovery"] is True
    assert observed["request_sequence"][:5] == [
        "/api/operator-instances/register",
        "/api/operator-instances/register",
        "/api/operator-instances/heartbeat",
        "/api/operator-instances/register",
        "/api/operator-instances/heartbeat",
    ]


def test_reg_009_routes_bad_service_url_through_registration_and_lease() -> None:
    registry_module = importlib.import_module(
        "scripts.milestone_2b_case_runners.registry"
    )
    calls: list[str] = []
    instance: Any = None

    class RecordingRegistry:
        def register(self, value: object) -> object:
            nonlocal instance
            calls.append("register")
            instance = value
            return value

        def heartbeat(self, instance_id: str, **kwargs: object) -> object:
            nonlocal instance
            del instance_id, kwargs
            calls.append("heartbeat")
            instance = replace(instance, model_ready=False)
            return instance

        def lease(self, capability: str, ttl_seconds: int) -> SimpleNamespace:
            del capability, ttl_seconds
            calls.append("lease-attempt")
            if not instance.model_ready:
                raise registry_module.CapacityUnavailableError("teacher_behavior")
            return SimpleNamespace(lease_id="unexpected")

    observed = registry_module._check_reg_009(
        RecordingRegistry(),
        "m2b-reg-009-instance",
        "",
    )

    assert calls == ["register", "heartbeat", "lease-attempt"]
    assert observed["health_verified"] is False
    assert observed["health_paths"] == ["/ops/health", "/ops/metadata"]
    assert observed["metadata_identity"] == {
        "instance_id": "ocr-gpu0",
        "operator_code": "ocr",
        "capabilities": ["ocr"],
        "model_version": "ocr-v6",
        "api_version": "v1",
    }
    assert observed["lease_rejection"] == "CapacityUnavailableError"


def test_reg_009_evaluate_scenario_builds_isolated_redis_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_module = importlib.import_module(
        "scripts.milestone_2b_case_runners.registry"
    )
    scenario = _registry_probe_scenario("REG-009")
    calls: list[str] = []
    instance: Any = None
    constructed: list[dict[str, object]] = []

    class RecordingClient:
        closed = False

        def ping(self) -> bool:
            return True

        def scan_iter(self, **kwargs: object) -> list[str]:
            assert kwargs == {"match": f"{scenario['redis_prefix']}*", "count": 100}
            return []

        def delete(self, *keys: object) -> None:
            raise AssertionError(f"empty isolated namespace deleted keys: {keys}")

        def close(self) -> None:
            self.closed = True

    class RecordingRegistry:
        def register(self, value: object) -> object:
            nonlocal instance
            calls.append("register")
            instance = value
            return value

        def heartbeat(self, instance_id: str, **kwargs: object) -> object:
            nonlocal instance
            del instance_id, kwargs
            calls.append("heartbeat")
            instance = replace(instance, model_ready=False)
            return instance

        def lease(self, capability: str, ttl_seconds: int) -> SimpleNamespace:
            del capability, ttl_seconds
            calls.append("lease-attempt")
            if not instance.model_ready:
                raise registry_module.CapacityUnavailableError("teacher_behavior")
            return SimpleNamespace(lease_id="unexpected")

    client = RecordingClient()

    def redis_registry(
        actual_client: object,
        *,
        heartbeat_ttl_seconds: int,
        key_prefix: str,
    ) -> RecordingRegistry:
        constructed.append(
            {
                "client": actual_client,
                "heartbeat_ttl_seconds": heartbeat_ttl_seconds,
                "key_prefix": key_prefix,
            }
        )
        return RecordingRegistry()

    monkeypatch.setattr(
        registry_module,
        "Redis",
        SimpleNamespace(from_url=lambda *args, **kwargs: client),
    )
    monkeypatch.setattr(registry_module, "RedisOperatorRegistry", redis_registry)

    result = registry_module.evaluate_scenario("REG-009", scenario)

    assert result["status"] == "通过"
    assert result["observed"]["health_verified"] is False
    assert result["observed"]["lease_rejection"] == "CapacityUnavailableError"
    assert calls == ["register", "heartbeat", "lease-attempt"]
    assert constructed == [
        {
            "client": client,
            "heartbeat_ttl_seconds": 1,
            "key_prefix": scenario["redis_prefix"],
        }
    ]
    assert client.closed is True


def test_reg_014_restarts_from_postgresql_after_exact_redis_prefix_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_module = importlib.import_module(
        "scripts.milestone_2b_case_runners.registry"
    )
    desired_lifecycles: dict[str, object] = {}
    repositories: list[object] = []
    realtime_registries: list[object] = []
    cleaned_prefixes: list[str] = []

    @contextmanager
    def isolated_database(scenario: object, *, migrate: bool) -> Any:
        assert scenario == {"database": "m2b_5_run_1_reg_014_test"}
        assert migrate is True
        yield object()

    class OperatorAuditRepository:
        def __init__(self, engine: object) -> None:
            del engine
            repositories.append(self)

        def record_registration(self, instance: object) -> None:
            desired_lifecycles.setdefault(instance.instance_id, instance.lifecycle)

        def get_desired_lifecycle(self, instance_id: str) -> object:
            return desired_lifecycles[instance_id]

        def record_heartbeat_summary(self, *args: object, **kwargs: object) -> bool:
            del args, kwargs
            return True

        def record_lifecycle(
            self,
            instance_id: str,
            lifecycle: object,
            **kwargs: object,
        ) -> bool:
            del kwargs
            desired_lifecycles[instance_id] = lifecycle
            return True

        def record_unregistration(self, instance_id: str, **kwargs: object) -> bool:
            del instance_id, kwargs
            return True

    class RealtimeRegistry:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            self.instance: Any = None
            realtime_registries.append(self)

        def register(self, instance: object) -> object:
            self.instance = instance
            return instance

        def heartbeat(
            self,
            instance_id: str,
            *,
            inflight: int,
            model_ready: bool,
        ) -> object:
            assert self.instance.instance_id == instance_id
            self.instance = replace(
                self.instance,
                inflight=inflight,
                model_ready=model_ready,
            )
            return self.instance

        def set_lifecycle(self, instance_id: str, lifecycle: object) -> object:
            assert self.instance.instance_id == instance_id
            self.instance = replace(self.instance, lifecycle=lifecycle)
            return self.instance

        def unregister(self, instance_id: str) -> None:
            assert self.instance.instance_id == instance_id
            self.instance = None

        def lease(self, capability: str, ttl_seconds: int) -> SimpleNamespace:
            del ttl_seconds
            if (
                self.instance is None
                or self.instance.lifecycle
                is not registry_module.OperatorLifecycle.ONLINE
                or not self.instance.model_ready
            ):
                raise registry_module.CapacityUnavailableError(capability)
            return SimpleNamespace(
                lease_id="lease-reg-014",
                instance_id=self.instance.instance_id,
            )

    class RedisClient:
        def ping(self) -> bool:
            return True

        def close(self) -> None:
            return None

    monkeypatch.setattr(registry_module, "_isolated_database", isolated_database)
    monkeypatch.setattr(
        registry_module,
        "OperatorAuditRepository",
        OperatorAuditRepository,
    )
    monkeypatch.setattr(registry_module, "RedisOperatorRegistry", RealtimeRegistry)
    monkeypatch.setattr(
        registry_module.Redis,
        "from_url",
        lambda *args, **kwargs: RedisClient(),
    )
    monkeypatch.setattr(
        registry_module,
        "_cleanup_prefix",
        lambda client, prefix: cleaned_prefixes.append(prefix),
    )
    initial_registry = RealtimeRegistry()

    observed = registry_module._check_reg_014(
        initial_registry,
        "m2b-reg-014-instance",
        json.dumps(
            {
                "database": "m2b_5_run_1_reg_014_test",
                "redis_prefix": "m2b:run-1:reg-014:registry:",
            }
        ),
    )

    assert observed["desired_lifecycle_after_reregistration"] == "DRAINING"
    assert observed["lease_rejection"] == "CapacityUnavailableError"
    assert observed["online_lease_instance_id"] == "m2b-reg-014-instance"
    assert observed["audit_database"] == "m2b_5_run_1_reg_014_test"
    assert observed["audit_repository"] == "OperatorAuditRepository"
    assert len(repositories) >= 2
    assert len(realtime_registries) == 2
    assert cleaned_prefixes == ["m2b:run-1:reg-014:registry:"]
    assert "_InMemoryOperatorAudit" not in inspect.getsource(
        registry_module._check_reg_014
    )


def test_reg_015_requeries_unregistration_through_a_new_production_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_module = importlib.import_module(
        "scripts.milestone_2b_case_runners.registry"
    )
    calls: list[str] = []
    removed = False
    current_instance: list[Any] = [None]
    repository_instances: list[object] = []
    events: list[object] = []

    @contextmanager
    def isolated_database(scenario: object, *, migrate: bool) -> Any:
        assert scenario == {"database": "m2b_5_run_1_reg_015_test"}
        assert migrate is True
        yield object()

    class OperatorAuditRepository:
        def __init__(self, engine: object) -> None:
            del engine
            repository_instances.append(self)

        def record_registration(self, registered: object) -> None:
            del registered

        def get_desired_lifecycle(self, instance_id: str) -> object:
            del instance_id
            return registry_module.OperatorLifecycle.ONLINE

        def record_heartbeat_summary(self, *args: object, **kwargs: object) -> bool:
            del args, kwargs
            return True

        def record_unregistration(self, instance_id: str, *, source: str) -> bool:
            events.append(
                registry_module.OperatorInstanceEvent(
                    id=1,
                    instance_id=instance_id,
                    event_type="UNREGISTERED",
                    event_payload={"source": source},
                    occurred_at=registry_module.datetime.now(registry_module.UTC),
                )
            )
            return True

        def list_events(self, instance_id: str, *, limit: int = 100) -> list[object]:
            del limit
            assert self is repository_instances[1]
            return [event for event in events if event.instance_id == instance_id]

    monkeypatch.setattr(registry_module, "_isolated_database", isolated_database)
    monkeypatch.setattr(
        registry_module,
        "OperatorAuditRepository",
        OperatorAuditRepository,
        raising=False,
    )

    class RealtimeRegistry:
        def register(self, instance: object) -> object:
            current_instance[0] = instance
            calls.append("register")
            return instance

        def heartbeat(self, instance_id: str, **kwargs: object) -> object:
            del instance_id
            calls.append("heartbeat")
            current_instance[0] = replace(
                current_instance[0],
                inflight=kwargs["inflight"],
                model_ready=kwargs["model_ready"],
            )
            return current_instance[0]

        def lease(self, capability: str, ttl_seconds: int) -> SimpleNamespace:
            del capability, ttl_seconds
            calls.append("lease")
            return SimpleNamespace(lease_id="lease-reg-015")

        def set_lifecycle(self, instance_id: str, lifecycle: object) -> SimpleNamespace:
            del instance_id
            calls.append(f"lifecycle:{lifecycle.value}")
            current_instance[0] = replace(
                current_instance[0], lifecycle=lifecycle
            )
            return current_instance[0]

        def unregister(self, instance_id: str) -> None:
            nonlocal removed
            del instance_id
            calls.append("unregister")
            removed = True
            current_instance[0] = None

        def renew(self, lease_id: str, ttl_seconds: int) -> None:
            del ttl_seconds
            if removed:
                raise registry_module.CapacityLeaseNotFoundError(lease_id)

    observed = registry_module._check_reg_015(
        RealtimeRegistry(),
        "m2b-reg-015-instance",
        "m2b_5_run_1_reg_015_test",
    )

    assert calls == [
        "register",
        "heartbeat",
        "heartbeat",
        "lease",
        "lifecycle:OFFLINE",
        "unregister",
    ]
    assert len(repository_instances) == 2
    assert observed["production_registry"] == "AuditedOperatorRegistry"
    assert observed["audit_repository"] == "OperatorAuditRepository"
    assert observed["audit_event_count_after_unregister"] == 1
    assert observed["lease_renewal_rejection"] == "CapacityLeaseNotFoundError"
    assert observed["audit_event_type"] == "UNREGISTERED"


def test_reg_017_dispatches_capacity_api_failure_to_waiting_operator_state() -> None:
    registry_module = importlib.import_module(
        "scripts.milestone_2b_case_runners.registry"
    )
    lifecycles: dict[str, object] = {}

    class OfflineRegistry:
        def register(self, instance: object) -> object:
            lifecycles[instance.instance_id] = registry_module.OperatorLifecycle.ONLINE
            return instance

        def heartbeat(self, instance_id: str, **kwargs: object) -> SimpleNamespace:
            del kwargs
            return SimpleNamespace(lifecycle=lifecycles[instance_id])

        def set_lifecycle(self, instance_id: str, lifecycle: object) -> SimpleNamespace:
            lifecycles[instance_id] = lifecycle
            return SimpleNamespace(lifecycle=lifecycle)

        def lease(self, capability: str, ttl_seconds: int) -> None:
            del ttl_seconds
            if all(
                lifecycle is registry_module.OperatorLifecycle.OFFLINE
                for lifecycle in lifecycles.values()
            ):
                raise registry_module.CapacityUnavailableError(capability)

    observed = registry_module._check_reg_017(
        OfflineRegistry(),
        "m2b-reg-017-instance",
        "",
    )

    assert observed["production_dispatcher"] == "LeaseAwareDispatcher"
    assert observed["production_capacity_client"] == "ControlLeaseClient"
    assert observed["capacity_api_status"] == 503
    assert observed["offline_status"] == 30
    assert observed["reservation"] is None
    assert observed["verification_scope"] == "component-level"
    assert observed["running_e2e_validated"] is False


def test_reg_019_requires_two_production_capacity_mismatch_snapshots() -> None:
    registry_module = importlib.import_module(
        "scripts.milestone_2b_case_runners.registry"
    )
    instance: Any = None
    heartbeat_calls = 0
    active_lease_count_calls = 0

    class MismatchRegistry:
        def register(self, value: object) -> object:
            nonlocal instance
            instance = value
            return value

        def heartbeat(
            self,
            instance_id: str,
            *,
            inflight: int,
            model_ready: bool,
        ) -> object:
            nonlocal heartbeat_calls, instance
            del instance_id
            heartbeat_calls += 1
            instance = replace(
                instance,
                inflight=inflight,
                model_ready=model_ready,
            )
            return instance

        def list_instances(self) -> list[object]:
            return [instance]

        def active_lease_count(self, instance_id: str) -> int:
            nonlocal active_lease_count_calls
            del instance_id
            active_lease_count_calls += 1
            return 0

    observed = registry_module._check_reg_019(
        MismatchRegistry(),
        "m2b-reg-019-instance",
        "",
    )

    assert heartbeat_calls == 2
    assert active_lease_count_calls == 2
    assert observed["production_snapshot"] == "build_operator_capacity_snapshot"
    assert observed["persistent_capacity_mismatch"] is True
    assert len(observed["snapshots"]) == 2
    assert all(snapshot["capacity_mismatch"] for snapshot in observed["snapshots"])


def test_dep_006_uses_production_port_contract_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment = importlib.import_module(
        "scripts.milestone_2b_case_runners.deployment"
    )
    calls: list[str] = []

    def reject_old_port(*args: object, **kwargs: object) -> None:
        del args, kwargs
        calls.append("port-contract")
        raise deployment.preflight_checks.PreflightError(
            "operator Compose port mapping is not canonical: vbas-gpu0"
        )

    monkeypatch.setattr(
        deployment.preflight_checks,
        "_validate_port_contract",
        reject_old_port,
    )

    observed = deployment._check_dep_006()

    assert calls == ["port-contract"]
    assert observed["rejected"] is True


def test_dep_003_executes_canonical_build_tag_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment = importlib.import_module(
        "scripts.milestone_2b_case_runners.deployment"
    )
    calls: list[tuple[str, ...]] = []

    def reject_tag(argv: list[str], **kwargs: object) -> SimpleNamespace:
        del kwargs
        calls.append(tuple(argv))
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="build-images: FAIL: image tag must match v<major>.<minor>_YYMMDD\n",
        )

    monkeypatch.setattr(
        deployment,
        "subprocess",
        SimpleNamespace(run=reject_tag),
        raising=False,
    )

    observed = deployment._check_dep_003()

    assert calls == [
        (
            str(PLATFORM_ROOT / "deploy/scripts/build-images"),
            "V1.0_260818",
        )
    ]
    assert observed["returncode"] == 1
    assert "image tag must match" in observed["stderr"]


def test_dep_013_uses_production_model_tree_validator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    deployment = importlib.import_module(
        "scripts.milestone_2b_case_runners.deployment"
    )
    model_assets = importlib.import_module(
        "deploy.scripts.model_asset_transaction"
    )
    calls: list[str] = []

    def reject_hash(*args: object, **kwargs: object) -> tuple[int, int]:
        del args, kwargs
        calls.append("model-tree")
        raise model_assets.AssetError("model file hash mismatch")

    monkeypatch.setattr(model_assets, "_verify_tree", reject_hash)

    observed = deployment._check_dep_013(tmp_path)

    assert calls == ["model-tree"]
    assert observed["rejected"] is True


def test_dep_014_uses_production_operator_service_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment = importlib.import_module(
        "scripts.milestone_2b_case_runners.deployment"
    )
    calls: list[str] = []

    def reject_service(services: object) -> None:
        del services
        calls.append("operator-service")
        raise deployment.deployment_contracts.DeploymentContractError(
            "text-analysis-cpu0 CONFIG_PATH must be /app/config.toml"
        )

    monkeypatch.setattr(
        deployment.deployment_contracts,
        "validate_operator_service_contracts",
        reject_service,
    )

    observed = deployment._check_dep_014()

    assert calls == ["operator-service"]
    assert observed["rejected"] is True


def test_dep_020_reports_checkout_wrapper_only_scope(tmp_path: Path) -> None:
    deployment = importlib.import_module(
        "scripts.milestone_2b_case_runners.deployment"
    )

    observed = deployment._check_dep_020(tmp_path)

    assert observed["wrapper_returncode"] == 1
    assert observed["destination_exists"] is False
    assert observed["partial_checkout_count"] == 0
    assert observed["scope"] == "checkout wrapper fail-closed only"


def test_dep_019_uses_production_snapshot_validator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    deployment = importlib.import_module(
        "scripts.milestone_2b_case_runners.deployment"
    )
    calls: list[str] = []

    def reject_snapshot(tool: str, path: Path) -> list[dict[str, object]]:
        del tool, path
        calls.append("snapshot")
        raise SystemExit("container-protection: incomplete snapshot line 1")

    monkeypatch.setattr(
        deployment,
        "_production_snapshot_validator",
        reject_snapshot,
        raising=False,
    )

    observed = deployment._check_dep_019(tmp_path)

    assert calls == ["snapshot"]
    assert observed["rejection"] == "SystemExit"
    assert observed["detail"] == "container-protection: incomplete snapshot line 1"


@pytest.mark.parametrize(
    ("case_id", "probe_name", "checker_name"),
    [
        ("INF-001", "postgres_down", "ControlReadinessChecker"),
        (
            "INF-002",
            "postgres_auth",
            "ControlReadinessChecker._check_postgresql",
        ),
        ("INF-003", "schema_missing", "ControlReadinessChecker._check_schema"),
        ("INF-004", "redis_down", "ControlReadinessChecker"),
        ("INF-006", "kafka_down", "OrchestratorRuntime._check_kafka"),
        ("INF-013", "mongodb_down", "FaceRecReadiness"),
        ("INF-014", "mongodb_auth", "FaceRecReadiness"),
    ],
)
def test_infrastructure_readiness_cases_execute_production_probe(
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    probe_name: str,
    checker_name: str,
) -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    calls: list[tuple[str, object | None]] = []

    def production_probe(
        name: str,
        scenario: object | None = None,
    ) -> dict[str, object]:
        calls.append((name, scenario))
        return {
            "ready": False,
            "detail": "受控依赖失败",
            "production_validator": checker_name,
        }

    monkeypatch.setattr(
        infrastructure,
        "_run_production_readiness_probe",
        production_probe,
        raising=False,
    )
    scenario = _infrastructure_probe_scenario(case_id)

    observed = infrastructure._INFRASTRUCTURE_CHECKERS[case_id](scenario)

    expected_scenario = scenario if case_id == "INF-014" else None
    assert calls == [(probe_name, expected_scenario)]
    assert observed["production_validator"] == checker_name
    assert observed["ready"] is False


@pytest.mark.parametrize(
    ("probe_name", "production_validator"),
    [
        ("postgres_auth", "ControlReadinessChecker._check_postgresql"),
        ("mongodb_auth", "FaceRecReadiness"),
    ],
)
def test_authentication_readiness_probes_execute_production_components(
    probe_name: str,
    production_validator: str,
) -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    if probe_name == "mongodb_auth":
        _require_canonical_facerec_runtime()

    scenario = (
        _infrastructure_probe_scenario("INF-014")
        if probe_name == "mongodb_auth"
        else None
    )
    observed = infrastructure._run_production_readiness_probe(probe_name, scenario)

    assert observed["ready"] is False
    assert observed["production_validator"] == production_validator
    assert "认证失败" in observed["detail"]


def test_orchestrator_readiness_probe_loads_through_canonical_app_entrypoint() -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )

    observed = infrastructure._run_production_readiness_probe("kafka_down")

    assert observed["ready"] is False
    assert observed["production_validator"] == "OrchestratorRuntime._check_kafka"


def test_orchestrator_runtime_supports_direct_package_import() -> None:
    completed = __import__("subprocess").run(
        [
            sys.executable,
            "-c",
            (
                "from orchestrator_service.app.infrastructure.runtime "
                "import OrchestratorRuntime; print(OrchestratorRuntime.__name__)"
            ),
        ],
        cwd=PLATFORM_ROOT.parent,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "OrchestratorRuntime"


def test_inf_007_uses_production_topic_validation_without_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    calls: list[str] = []

    async def no_reset(*args: object, **kwargs: object) -> None:
        del args, kwargs

    class FakeAdmin:
        async def start(self) -> None:
            return None

        async def list_topics(self) -> set[str]:
            return set()

        async def close(self) -> None:
            return None

    class ProductionManager:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        async def validate_topics(self) -> None:
            calls.append("validate")
            raise RuntimeError("required Kafka topics are missing: m2b.topic")

    monkeypatch.setattr(infrastructure, "_reset_kafka_resources", no_reset)
    monkeypatch.setattr(
        infrastructure,
        "AIOKafkaAdminClient",
        lambda **kwargs: FakeAdmin(),
    )
    monkeypatch.setattr(infrastructure, "KafkaTopicManager", ProductionManager)

    observed = infrastructure._check_inf_007(
        {"kafka_topic": "m2b.topic", "kafka_group": "m2b.group"}
    )

    assert calls == ["validate"]
    assert observed["startup_validation"] == "failed"
    assert "required Kafka topics are missing" in observed["dependency_reason"]


@pytest.mark.parametrize(
    ("case_id", "probe_name"),
    [
        ("INF-008", "pipeline_duplicate"),
        ("INF-009", "consumer_failure_replay"),
        ("INF-010", "outbox_failure"),
        ("INF-011", "publisher_duplicate"),
        ("INF-012", "consumer_commit_exit"),
    ],
)
def test_infrastructure_flow_case_is_wired_to_production_probe(
    case_id: str,
    probe_name: str,
) -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )

    source = inspect.getsource(infrastructure._INFRASTRUCTURE_CHECKERS[case_id])

    assert f'_run_real_flow_probe("{probe_name}", scenario)' in source


def test_infrastructure_flow_cases_do_not_use_in_memory_decisive_evidence() -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    module_source = inspect.getsource(infrastructure)
    flow_source = "\n".join(
        inspect.getsource(function)
        for function in (
            infrastructure._real_pipeline_duplicate,
            infrastructure._real_consumer_failure_replay,
            infrastructure._real_outbox_failure,
            infrastructure._real_publisher_duplicate,
            infrastructure._real_consumer_commit_exit,
        )
    )

    assert "_ROOT_FLOW_PROBE" not in module_source
    assert "class PipelineRepository:" not in flow_source
    assert "class OutboxRepository:" not in flow_source
    assert "class Consumer:" not in flow_source
    assert "class Producer:" not in flow_source


@pytest.mark.parametrize(
    ("case_id", "flow_name"),
    [
        ("INF-008", "pipeline_duplicate"),
        ("INF-009", "consumer_failure_replay"),
        ("INF-010", "outbox_failure"),
        ("INF-011", "publisher_duplicate"),
        ("INF-012", "consumer_commit_exit"),
    ],
)
def test_infrastructure_flow_checker_passes_isolated_scenario_to_real_runner(
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    flow_name: str,
) -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    scenario = {
        "database": "m2b_5_run_1_inf_008_test",
        "kafka_topic": "m2b.run-1.inf-008",
        "kafka_group": "m2b.run-1.inf-008",
    }
    calls: list[tuple[str, object]] = []

    def run_real_flow(name: str, received: object) -> dict[str, object]:
        calls.append((name, received))
        return {"flow": name}

    monkeypatch.setattr(infrastructure, "_run_real_flow_probe", run_real_flow)

    observed = infrastructure._INFRASTRUCTURE_CHECKERS[case_id](scenario)

    assert calls == [(flow_name, scenario)]
    assert observed == {"flow": flow_name}



def test_inf_015_is_wired_to_facerec_production_embedding_filter() -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )

    source = inspect.getsource(infrastructure._check_inf_015)

    assert "_run_production_embedding_probe(scenario)" in source


@pytest.mark.parametrize("case_id", ["INF-014", "INF-015"])
def test_facerec_negative_probes_use_canonical_compose_container_without_conda(
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
) -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    scenario = _infrastructure_probe_scenario(case_id)
    container_id = "f" * 64
    commands: list[list[str]] = []
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("OPERATOR_REGISTRY_TOKEN", "registry-token")

    payload = _facerec_probe_payload(case_id, scenario)

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        del kwargs
        commands.append(command)
        if command[:2] == ["docker", "compose"]:
            return SimpleNamespace(returncode=0, stdout=f"{container_id}\n", stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout=_facerec_result_frame(payload),
            stderr="",
        )

    monkeypatch.setattr(infrastructure.subprocess, "run", fake_run)

    observed = infrastructure._INFRASTRUCTURE_CHECKERS[case_id](scenario)

    assert all(command[0] != "conda" for command in commands)
    assert commands[0] == [
        "docker",
        "compose",
        "-f",
        str(PLATFORM_ROOT / "deploy/docker-compose.operators.yml"),
        "--profile",
        "gpu0",
        "ps",
        "-q",
        "facerec-gpu0",
    ]
    assert commands[1][:3] == ["docker", "exec", container_id]
    assert str(scenario["mongodb_database"]) in commands[1]
    if case_id == "INF-014":
        assert commands[1][-4:-2] == ["m2b_test_invalid", "m2b_test_invalid"]
        assert observed["person_count_after_auth_failure"] == 0
    else:
        assert observed["production_candidate_query"].endswith(
            "get_targets_embeddings"
        )


def test_facerec_container_probes_use_real_mongo_and_production_recognition_wiring() -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )

    auth_source = infrastructure._FACEREC_READINESS_PROBE
    embedding_source = infrastructure._FACEREC_EMBEDDING_PROBE

    assert "AuthFailureDatabase" not in auth_source
    assert "AsyncIOMotorClient" in auth_source
    assert "update_or_create_person" in auth_source
    assert "count_documents" in auth_source
    assert "drop_database" in auth_source
    assert "insert_many" in embedding_source
    assert "get_targets_embeddings" in embedding_source
    assert "find_best_match_embedding" in embedding_source
    assert "drop_database" in embedding_source
    assert auth_source.count(FACEREC_PROBE_RESULT_MARKER) == 1
    assert embedding_source.count(FACEREC_PROBE_RESULT_MARKER) == 1
    assert auth_source.count("print(") == 1
    assert embedding_source.count("print(") == 1


@pytest.mark.parametrize("wrapped", [False, True])
def test_facerec_auth_classifier_accepts_only_code_18_authentication_failure(
    wrapped: bool,
) -> None:
    from pymongo.errors import OperationFailure, ServerSelectionTimeoutError

    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    namespace: dict[str, object] = {}
    exec(infrastructure._FACEREC_MONGO_AUTH_FAILURE_HELPER, namespace)
    classify = namespace["mongodb_authentication_failure_facts"]
    authentication_failure = OperationFailure(
        "Authentication failed.",
        code=18,
        details={
            "ok": 0.0,
            "errmsg": "Authentication failed.",
            "code": 18,
            "codeName": "AuthenticationFailed",
        },
    )
    error = (
        ServerSelectionTimeoutError(
            "Authentication failed.",
            errors={"mongodb:27017": authentication_failure},
        )
        if wrapped
        else authentication_failure
    )

    observed = classify(error)

    assert observed == {
        "authentication_error_type": type(error).__name__,
        "authentication_cause_type": "OperationFailure",
        "authentication_error_code": 18,
        "authentication_error_code_name": "AuthenticationFailed",
        "authentication_error_wrapped": wrapped,
    }


def test_facerec_auth_classifier_rejects_plain_server_selection_timeout() -> None:
    from pymongo.errors import ServerSelectionTimeoutError

    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    namespace: dict[str, object] = {}
    exec(infrastructure._FACEREC_MONGO_AUTH_FAILURE_HELPER, namespace)
    classify = namespace["mongodb_authentication_failure_facts"]

    observed = classify(
        ServerSelectionTimeoutError(
            "No servers found yet",
            errors={"mongodb:27017": TimeoutError("connection timed out")},
        )
    )

    assert observed is None


def test_mongodb_down_probe_decodes_one_strict_marker_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    payload = {
        "ready": False,
        "database_ready": False,
        "production_validator": "FaceRecReadiness",
    }
    monkeypatch.setattr(
        infrastructure.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="INFO FaceRec import completed\n" + _facerec_result_frame(payload) + "\n",
            stderr="",
        ),
    )

    observed = infrastructure._run_production_readiness_probe("mongodb_down")

    assert observed == payload


@pytest.mark.parametrize(
    ("stdout", "error"),
    [
        ("INFO no result\n", "exactly one result frame"),
        (
            _facerec_result_frame({"ready": False})
            + "\n"
            + _facerec_result_frame({"ready": False}),
            "exactly one result frame",
        ),
        (
            FACEREC_PROBE_RESULT_MARKER + '{"ready":false} trailing',
            "result frame is not strict JSON",
        ),
        (FACEREC_PROBE_RESULT_MARKER + "[]", "result frame is not a JSON object"),
        (
            FACEREC_PROBE_RESULT_MARKER + '{"ready":false,"value":NaN}',
            "result frame is not strict JSON",
        ),
    ],
)
def test_mongodb_down_probe_rejects_invalid_result_frames(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    error: str,
) -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    monkeypatch.setattr(
        infrastructure.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=stdout,
            stderr="",
        ),
    )

    with pytest.raises(ValueError, match=error):
        infrastructure._run_production_readiness_probe("mongodb_down")


@pytest.mark.parametrize("case_id", ["INF-014", "INF-015"])
def test_facerec_probe_accepts_logs_before_one_explicit_result_frame(
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
) -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    scenario = _infrastructure_probe_scenario(case_id)
    payload = _facerec_probe_payload(case_id, scenario)
    container_id = "f" * 64
    calls = 0
    monkeypatch.setenv("OPERATOR_REGISTRY_TOKEN", "registry-token")

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        nonlocal calls
        del args, kwargs
        calls += 1
        if calls == 1:
            return SimpleNamespace(returncode=0, stdout=container_id, stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "INFO FaceRec model import completed\n"
                'WARNING [embedding] skip invalid candidate: {"record":"noise"}\n'
                + _facerec_result_frame(payload)
                + "\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(infrastructure.subprocess, "run", fake_run)

    observed = infrastructure._INFRASTRUCTURE_CHECKERS[case_id](scenario)

    assert observed == payload


@pytest.mark.parametrize("case_id", ["INF-014", "INF-015"])
@pytest.mark.parametrize(
    "output_kind",
    [
        "zero_frame",
        "multiple_frames",
        "malformed_frame",
        "non_object_frame",
        "non_standard_constant",
        "forged_log_frame",
    ],
)
def test_facerec_probe_rejects_missing_duplicate_or_malformed_result_frames(
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    output_kind: str,
) -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    scenario = _infrastructure_probe_scenario(case_id)
    payload = _facerec_probe_payload(case_id, scenario)
    valid_frame = _facerec_result_frame(payload)
    outputs = {
        "zero_frame": "INFO import completed\n" + json.dumps(payload),
        "multiple_frames": valid_frame + "\n" + valid_frame,
        "malformed_frame": FACEREC_PROBE_RESULT_MARKER + '{"ready":false} trailing',
        "non_object_frame": FACEREC_PROBE_RESULT_MARKER + "[]",
        "non_standard_constant": FACEREC_PROBE_RESULT_MARKER + '{"value":NaN}',
        "forged_log_frame": (
            'INFO dependency returned {"ready":false}\n'
            + FACEREC_PROBE_RESULT_MARKER
            + '{"forged":true}\n'
            + valid_frame
        ),
    }
    calls = 0
    monkeypatch.setenv("OPERATOR_REGISTRY_TOKEN", "registry-token")

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        nonlocal calls
        del args, kwargs
        calls += 1
        stdout = "f" * 64 if calls == 1 else outputs[output_kind]
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(infrastructure.subprocess, "run", fake_run)

    with pytest.raises(ValueError, match="result frame"):
        infrastructure._INFRASTRUCTURE_CHECKERS[case_id](scenario)


def test_facerec_container_probe_requires_registry_token_before_compose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    commands: list[list[str]] = []
    monkeypatch.delenv("OPERATOR_REGISTRY_TOKEN", raising=False)
    monkeypatch.setattr(
        infrastructure.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command),
    )

    with pytest.raises(ValueError, match="OPERATOR_REGISTRY_TOKEN is required"):
        infrastructure._run_production_embedding_probe(
            _infrastructure_probe_scenario("INF-015")
        )

    assert commands == []


def test_facerec_auth_probe_rejects_invalid_credentials_equal_to_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    commands: list[list[str]] = []
    monkeypatch.setenv("OPERATOR_REGISTRY_TOKEN", "registry-token")
    monkeypatch.setenv("MONGO_ROOT_USERNAME", "m2b_test_invalid")
    monkeypatch.setenv("MONGO_ROOT_PASSWORD", "m2b_test_invalid")
    monkeypatch.setattr(
        infrastructure.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command),
    )

    with pytest.raises(ValueError, match="must differ from admin credentials"):
        infrastructure._run_production_readiness_probe(
            "mongodb_auth",
            _infrastructure_probe_scenario("INF-014"),
        )

    assert commands == []


def test_facerec_embedding_probe_records_bad_candidate_reasons() -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    _require_canonical_facerec_runtime()

    observed = infrastructure._run_production_embedding_probe(
        _infrastructure_probe_scenario("INF-015")
    )

    assert observed["production_validator"] == "filter_candidate_embeddings"
    assert observed["valid_records"] == 0
    assert observed["valid_vectors"] == 0
    assert observed["rejections"] == [
        {"record": "missing", "reason": "embedding_missing"},
        {"record": "wrong-dimension", "reason": "embedding_dimension_invalid"},
    ]
    assert observed["recognition_skipped_bad_records"] is True


def test_postgres_down_probe_derives_readiness_from_both_services() -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )

    observed = infrastructure._run_production_readiness_probe("postgres_down")

    assert observed["control_ready"] is False
    assert observed["orchestrator_ready"] is False
    assert observed["ready"] is any(
        (observed["control_ready"], observed["orchestrator_ready"])
    )
    assert observed["control_validator"] == (
        "ControlReadinessChecker._check_postgresql"
    )
    assert observed["orchestrator_repository"] == "CourseRepository"
    assert observed["postgres_endpoint"].startswith("127.0.0.1:")
    assert "检查资源不可用" not in observed["detail"]


def test_mongodb_auth_probe_executes_person_persistence_with_zero_writes() -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    _require_canonical_facerec_runtime()

    observed = infrastructure._run_production_readiness_probe(
        "mongodb_auth",
        _infrastructure_probe_scenario("INF-014"),
    )

    assert observed["production_persistence_validator"] == (
        "app.services.person.update_or_create_person"
    )
    assert observed["person_lookup_attempts"] == 1
    assert observed["person_write_attempts"] == 0
    assert observed["empty_person_created"] is False
    assert observed["authentication_error_type"] in {
        "OperationFailure",
        "ServerSelectionTimeoutError",
    }
    assert observed["authentication_cause_type"] == "OperationFailure"
    assert observed["authentication_error_code"] == 18
    assert observed["authentication_error_code_name"] == "AuthenticationFailed"
    assert observed["authentication_error_wrapped"] is (
        observed["authentication_error_type"] == "ServerSelectionTimeoutError"
    )
    assert observed["person_count_after_auth_failure"] == 0
    assert "认证失败" in observed["persistence_error"]


@pytest.mark.parametrize(
    "mutation",
    (
        {"rejections": []},
        {"valid_vectors": 1},
        {
            "rejections": [
                {"record": "missing", "reason": "embedding_missing"},
                {"record": "wrong-dimension", "reason": "unexpected"},
            ]
        },
    ),
)
def test_embedding_probe_rejects_incomplete_decisive_evidence(
    monkeypatch: pytest.MonkeyPatch,
    mutation: dict[str, object],
) -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    payload: dict[str, object] = {
        "production_validator": "filter_candidate_embeddings",
        "valid_records": 0,
        "valid_vectors": 0,
        "rejections": [
            {"record": "missing", "reason": "embedding_missing"},
            {
                "record": "wrong-dimension",
                "reason": "embedding_dimension_invalid",
            },
        ],
        "recognition_skipped_bad_records": True,
    }
    payload.update(mutation)
    monkeypatch.setenv("OPERATOR_REGISTRY_TOKEN", "registry-token")
    calls = 0

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        nonlocal calls
        del args, kwargs
        calls += 1
        stdout = "f" * 64 if calls == 1 else _facerec_result_frame(payload)
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(infrastructure.subprocess, "run", fake_run)

    with pytest.raises(ValueError, match="evidence is incomplete"):
        infrastructure._run_production_embedding_probe(
            _infrastructure_probe_scenario("INF-015")
        )


def test_inf_016_uses_production_visual_command_processor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    calls: list[str] = []

    def production_probe() -> dict[str, object]:
        calls.append("message-contract")
        return {
            "contract_rejection": (
                "视觉 Kafka 命令不得携带媒体字段: video_bytes"
            ),
            "media_published": False,
            "production_validator": "VisualCommandProcessor.handle",
        }

    monkeypatch.setattr(
        infrastructure,
        "_run_production_message_contract_probe",
        production_probe,
        raising=False,
    )

    observed = infrastructure._check_inf_016(
        {"component": "inf-016-run-1", "kafka_topic": "m2b.run-1.inf-016"}
    )

    assert calls == ["message-contract"]
    assert observed["production_validator"] == "VisualCommandProcessor.handle"


def test_visual_message_contract_probe_rejects_before_side_effects() -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )

    observed = infrastructure._run_production_message_contract_probe()

    assert observed["production_validator"] == "VisualCommandProcessor.handle"
    assert observed["media_published"] is False
    assert observed["side_effects"] == []
    assert observed["contract_rejection"] == (
        "视觉 Kafka 命令不得携带媒体字段: video_bytes"
    )


def test_reg_011_fails_closed_when_missing_release_is_silently_idempotent() -> None:
    registry = importlib.import_module(
        "scripts.milestone_2b_case_runners.registry"
    )

    class SilentReleaseRegistry:
        def register(self, instance: object) -> object:
            return instance

        def heartbeat(
            self, instance_id: str, *, inflight: int, model_ready: bool
        ) -> object:
            del instance_id, inflight, model_ready
            return object()

        def lease(self, capability: str, ttl_seconds: int) -> object:
            del capability, ttl_seconds
            return SimpleNamespace(lease_id="existing-lease")

        def release(self, lease_id: str) -> None:
            assert lease_id == "missing-lease"

        def renew(self, lease_id: str, ttl_seconds: int) -> object:
            del ttl_seconds
            return SimpleNamespace(lease_id=lease_id)

    with pytest.raises(ValueError, match="没有返回明确错误"):
        registry._check_reg_011(
            SilentReleaseRegistry(),  # type: ignore[arg-type]
            "m2b-instance",
            "http://127.0.0.1:18100",
        )


def test_reg_011_passes_only_after_explicit_error_and_existing_lease_renewal() -> None:
    registry = importlib.import_module(
        "scripts.milestone_2b_case_runners.registry"
    )

    class ExplicitReleaseRegistry:
        def register(self, instance: object) -> object:
            return instance

        def heartbeat(
            self, instance_id: str, *, inflight: int, model_ready: bool
        ) -> object:
            del instance_id, inflight, model_ready
            return object()

        def lease(self, capability: str, ttl_seconds: int) -> object:
            del capability, ttl_seconds
            return SimpleNamespace(lease_id="existing-lease")

        def release(self, lease_id: str) -> None:
            raise registry.CapacityLeaseNotFoundError(lease_id)

        def renew(self, lease_id: str, ttl_seconds: int) -> object:
            del ttl_seconds
            return SimpleNamespace(lease_id=lease_id)

    observed = registry._check_reg_011(
        ExplicitReleaseRegistry(),  # type: ignore[arg-type]
        "m2b-instance",
        "http://127.0.0.1:18100",
    )

    assert observed == {
        "missing_lease_rejection": "CapacityLeaseNotFoundError",
        "existing_lease": "existing-lease",
        "renewed_lease": "existing-lease",
    }


def test_registry_api_rejection_does_not_accept_a_missing_stable_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = importlib.import_module(
        "scripts.milestone_2b_case_runners.registry"
    )
    monkeypatch.setattr(
        registry,
        "_http_json",
        lambda *args, **kwargs: (404, {"detail": "Not Found"}),
    )

    with pytest.raises(ValueError, match="stable route"):
        registry._api_rejection(
            "http://127.0.0.1:18100",
            "/api/operator-instances/register",
            {"declared_capacity": 0},
        )


def test_reg_020_uses_reported_active_work_to_prevent_lease_oversubscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = importlib.import_module(
        "scripts.milestone_2b_case_runners.registry"
    )

    class ActiveWorkRegistry:
        def __init__(self) -> None:
            self.heartbeats: list[int] = []
            self.lease_calls = 0

        def register(self, instance: object) -> object:
            return instance

        def heartbeat(
            self, instance_id: str, *, inflight: int, model_ready: bool
        ) -> object:
            del instance_id, model_ready
            self.heartbeats.append(inflight)
            return SimpleNamespace(inflight=inflight)

        def lease(self, capability: str, ttl_seconds: int) -> object:
            del capability, ttl_seconds
            self.lease_calls += 1
            if self.lease_calls == 2 and self.heartbeats[-1] == 1:
                raise registry.CapacityUnavailableError("teacher_behavior")
            return SimpleNamespace(lease_id=f"lease-{self.lease_calls}")

    fake = ActiveWorkRegistry()
    monkeypatch.setattr(registry.time, "sleep", lambda _: None)

    observed = registry._check_reg_020(
        fake,  # type: ignore[arg-type]
        "m2b-instance",
        "http://127.0.0.1:18100",
    )

    assert fake.heartbeats == [0, 1]
    assert observed == {
        "first_lease": "lease-1",
        "active_inflight": 1,
        "second_lease_rejection": "CapacityUnavailableError",
    }


def test_inf_002_uses_control_readiness_without_direct_psycopg_connection() -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    source = inspect.getsource(infrastructure._check_inf_002)

    assert '_run_production_readiness_probe("postgres_auth")' in source
    assert "psycopg.connect" not in source


def test_isolated_database_cleans_exact_resumable_database_before_create() -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    source = inspect.getsource(infrastructure._isolated_database)

    assert source.index("_drop_isolated_database") < source.index("CREATE DATABASE")


def test_infrastructure_resources_declare_both_run_scoped_databases() -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    context = CaseContext(Path("/tmp/release").resolve(), "run-1", "local")
    case = _foundation_case("INF-015")

    resources = infrastructure._infrastructure_resources(context, case)

    assert ResourceSpec("database", "m2b_5_run_1_inf_015_test") in resources
    assert ResourceSpec(
        "mongodb_database", "m2b_5_run_1_inf_015_mongo_test"
    ) in resources


def test_inf_015_uses_isolated_production_probe_with_exact_mongodb_cleanup() -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    source = inspect.getsource(infrastructure._check_inf_015)

    assert "_run_production_embedding_probe(scenario)" in source
    assert "MongoClient" not in source


def test_inf_009_uses_real_isolated_postgres_and_kafka_flow() -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    checker_source = inspect.getsource(infrastructure._check_inf_009)
    flow_source = inspect.getsource(infrastructure._execute_real_flow_probe)
    replay_source = inspect.getsource(infrastructure._real_consumer_failure_replay)

    assert '_run_real_flow_probe("consumer_failure_replay", scenario)' in checker_source
    assert "_isolated_database(scenario, migrate=True)" in flow_source
    assert "_prepare_kafka_resources(topic, group)" in flow_source
    assert "_reset_kafka_resources(topic, group)" in flow_source
    assert "AioKafkaProducerAdapter" in replay_source
    assert "AioKafkaConsumerAdapter" in replay_source
    assert "_reopened_repository(database)" in replay_source


def test_foundation_catalog_resolves_all_76_explicit_runner_functions() -> None:
    cases = _foundation_cases()
    assert len(cases) == 76
    resolved: list[object] = []
    for case in cases:
        module_name, method_name = case.runner.split(".", 1)
        module = importlib.import_module(
            f"scripts.milestone_2b_case_runners.{module_name}"
        )
        assert "__getattr__" not in vars(module)
        runner = vars(module).get(method_name)
        assert inspect.iscoroutinefunction(runner), case.case_id
        assert runner.__name__ == method_name
        resolved.append(runner)
        spec = module.CASE_SPECS[case.case_id]
        assert spec.title == case.title
        assert spec.expected == case.expected
        assert spec.safety == case.safety
        assert spec.status == "通过"
        assert "反例" in spec.reason
        assert case.expected in spec.reason
    assert len({id(runner) for runner in resolved}) == 76


def test_all_foundation_functions_resolve_with_explicit_async_cleanup() -> None:
    batch = importlib.import_module("scripts.run_milestone_2b_case_batch")

    for case in _foundation_cases():
        runner = batch.resolve_runner(case.runner)
        assert inspect.iscoroutinefunction(runner.cleanup), case.case_id


@pytest.mark.asyncio
async def test_real_resolved_foundation_runner_satisfies_required_cleanup(
    tmp_path: Path,
) -> None:
    batch = importlib.import_module("scripts.run_milestone_2b_case_batch")
    case = _foundation_case("DEP-001")
    release_root = _release_root(tmp_path)
    prefix = "m2b-5-run-1-dep-001-"
    orphan = Path(tempfile.mkdtemp(prefix=prefix))
    unrelated = Path(tempfile.mkdtemp(prefix="m2b-unrelated-"))
    try:
        execution = await batch._run_selected_case(
            case=case,
            runner=batch.resolve_runner(case.runner),
            release_root=release_root,
            release_tag=release_root.parent.name,
            git_sha=release_root.name,
            run_id="run-1",
            target="local",
            semaphore=__import__("asyncio").Semaphore(1),
            require_cleanup=True,
            maintenance_lock=None,
        )

        assert execution.terminal is True
        assert execution.outcome is not None
        assert execution.outcome.status == "通过"
        assert "required runner cleanup" not in execution.outcome.reason
        assert not orphan.exists()
        assert unrelated.is_dir()
    finally:
        if orphan.exists():
            orphan.rmdir()
        unrelated.rmdir()


@pytest.mark.parametrize(
    ("group", "case_id", "expected_kinds"),
    [
        ("deployment", "DEP-001", ("filesystem",)),
        ("gpu", "GPU-001", ("filesystem",)),
        ("registry", "REG-007", ("filesystem", "redis_prefix")),
        (
            "infrastructure",
            "INF-016",
            (
                "filesystem",
                "database",
                "mongodb_database",
                "kafka_topic",
                "kafka_group",
                "redis_prefix",
            ),
        ),
    ],
)
def test_foundation_cleanup_action_is_fixed_to_exact_case_resources(
    group: str, case_id: str, expected_kinds: tuple[str, ...]
) -> None:
    process = importlib.import_module("scripts.milestone_2b_case_runners.process")
    resources = process.foundation_cleanup_resources(group, case_id, "run-1")
    action = process.FoundationCleanupAction(
        group=group,
        case_id=case_id,
        run_id="run-1",
        resources=resources,
    )

    assert tuple(resource.kind for resource in resources) == expected_kinds
    assert CommandSpec(action=action).argv == (
        sys.executable,
        "-m",
        "scripts.milestone_2b_case_runners.cleanup",
        "--group",
        group,
        "--case",
        case_id,
        "--run-id",
        "run-1",
    )
    with pytest.raises(ValueError, match="exact case namespace"):
        process.FoundationCleanupAction(
            group=group,
            case_id=case_id,
            run_id="run-1",
            resources=(*resources[:-1], ResourceSpec("filesystem", "/tmp/shared-")),
        )


@pytest.mark.parametrize("case_id", ["REG-014", "REG-015"])
def test_audited_registry_cases_bind_exact_postgresql_resource_for_check_and_cleanup(
    case_id: str, tmp_path: Path,
) -> None:
    registry = importlib.import_module(
        "scripts.milestone_2b_case_runners.registry"
    )
    process = importlib.import_module("scripts.milestone_2b_case_runners.process")
    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    case = _foundation_case(case_id)
    case_name = case_id.lower()
    expected_database = ResourceSpec(
        "database", f"m2b_5_run_1_{case_name.replace('-', '_')}_test"
    )

    assert registry._registry_resources(context, case) == (
        ResourceSpec("redis_prefix", f"m2b:run-1:{case_name}:registry:"),
        expected_database,
    )
    assert expected_database in process.foundation_cleanup_resources(
        "registry", case_id, "run-1"
    )


@pytest.mark.asyncio
async def test_each_foundation_group_cleanup_uses_typed_action_twice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment = importlib.import_module(
        "scripts.milestone_2b_case_runners.deployment"
    )
    process = importlib.import_module("scripts.milestone_2b_case_runners.process")
    calls: list[object] = []

    async def fake_run_command(**kwargs: object) -> CommandResult:
        action = kwargs["command"].action  # type: ignore[union-attr]
        assert isinstance(action, process.FoundationCleanupAction)
        calls.append(action)
        return CommandResult(
            argv=kwargs["command"].argv,  # type: ignore[union-attr]
            returncode=0,
            stdout=json.dumps(
                {
                    "case_id": action.case_id,
                    "group": action.group,
                    "run_id": action.run_id,
                    "status": "clean",
                    "removed_temp_directories": [],
                    "residual_temp_directories": [],
                    "errors": [],
                }
            ).encode(),
            stderr=b"",
            stdout_truncated=False,
            stderr_truncated=False,
        )

    monkeypatch.setattr(deployment, "run_command", fake_run_command)
    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    selected = ("DEP-001", "GPU-001", "REG-007", "INF-016")
    for case_id in selected:
        case = _foundation_case(case_id)
        module = importlib.import_module(
            f"scripts.milestone_2b_case_runners.{case.runner.split('.', 1)[0]}"
        )
        await module.cleanup(context, case)
        await module.cleanup(context, case)

    assert len(calls) == 8
    for action in calls:
        assert action.resources == process.foundation_cleanup_resources(
            action.group, action.case_id, action.run_id
        )


@pytest.mark.asyncio
async def test_registry_and_infrastructure_cleanup_repeat_exact_backend_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_module = importlib.import_module(
        "scripts.milestone_2b_case_runners.cleanup"
    )
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        cleanup_module,
        "_cleanup_postgresql",
        lambda resource: calls.append(("postgresql", resource.name)),
    )
    monkeypatch.setattr(
        cleanup_module,
        "_cleanup_mongodb",
        lambda resource: calls.append(("mongodb", resource.name)),
    )
    monkeypatch.setattr(
        cleanup_module,
        "_cleanup_redis",
        lambda resource: calls.append(("redis", resource.name)),
    )

    async def reset_kafka(topic: str, group: str) -> None:
        calls.append(("kafka", f"{topic}|{group}"))

    monkeypatch.setattr(cleanup_module, "_reset_kafka_resources", reset_kafka)

    for _ in range(2):
        registry_result = await cleanup_module.cleanup_foundation_resources(
            "registry", "REG-007", "run-1"
        )
        infrastructure_result = await cleanup_module.cleanup_foundation_resources(
            "infrastructure", "INF-016", "run-1"
        )
        assert registry_result["status"] == "clean"
        assert infrastructure_result["status"] == "clean"

    assert calls == [
        ("redis", "m2b:run-1:reg-007:registry:"),
        ("postgresql", "m2b_5_run_1_inf_016_test"),
        ("mongodb", "m2b_5_run_1_inf_016_mongo_test"),
        ("kafka", "m2b.run-1.inf-016|m2b.run-1.inf-016"),
        ("redis", "m2b:run-1:inf-016:"),
        ("redis", "m2b:run-1:reg-007:registry:"),
        ("postgresql", "m2b_5_run_1_inf_016_test"),
        ("mongodb", "m2b_5_run_1_inf_016_mongo_test"),
        ("kafka", "m2b.run-1.inf-016|m2b.run-1.inf-016"),
        ("redis", "m2b:run-1:inf-016:"),
    ]


def test_database_cleanup_rejects_cross_backend_resource_kinds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_module = importlib.import_module(
        "scripts.milestone_2b_case_runners.cleanup"
    )
    connection_attempts: list[str] = []

    monkeypatch.setattr(
        cleanup_module.psycopg,
        "connect",
        lambda *args, **kwargs: connection_attempts.append("postgresql"),
    )
    monkeypatch.setattr(
        cleanup_module,
        "MongoClient",
        lambda *args, **kwargs: connection_attempts.append("mongodb"),
    )
    postgresql = ResourceSpec("database", "m2b_5_run_1_inf_016_test")
    mongodb = ResourceSpec(
        "mongodb_database", "m2b_5_run_1_inf_016_mongo_test"
    )

    with pytest.raises(ValueError, match="PostgreSQL cleanup requires"):
        cleanup_module._cleanup_postgresql(mongodb)
    with pytest.raises(ValueError, match="MongoDB cleanup requires"):
        cleanup_module._cleanup_mongodb(postgresql)

    assert connection_attempts == []


@pytest.mark.parametrize(
    ("group", "case_id"),
    [
        (group, f"{prefix}-{number:03d}")
        for prefix, (group, last) in FOUNDATION_RANGES.items()
        for number in range(1, last + 1)
    ],
)
def test_foundation_action_has_only_fixed_group_and_case_ids(
    group: str, case_id: str
) -> None:
    action = FoundationCheckAction(
        group=group,
        case_id=case_id,
        resources=_action_resources(group, case_id),
    )
    command = CommandSpec(action=action)
    assert command.argv[:3] == (
        sys.executable,
        "-m",
        f"scripts.milestone_2b_case_runners.{group}",
    )
    assert command.argv[3:] == ("--check", case_id, "--input", "/tmp/input.json")


@pytest.mark.parametrize(
    ("group", "case_id"),
    [
        ("missing", "DEP-001"),
        ("deployment", "GPU-001"),
        ("gpu", "GPU-021"),
        ("registry", "REG-000"),
        ("infrastructure", "INF-017"),
    ],
)
def test_foundation_action_rejects_unknown_or_cross_group_checker(
    group: str, case_id: str
) -> None:
    with pytest.raises(ValueError, match="foundation checker"):
        FoundationCheckAction(
            group=group,  # type: ignore[arg-type]
            case_id=case_id,
            resources=(ResourceSpec("filesystem", "/tmp/input.json"),),
        )


def test_foundation_action_does_not_expose_argv_or_environment_override() -> None:
    parameters = inspect.signature(FoundationCheckAction).parameters
    assert set(parameters) == {"group", "case_id", "resources"}
    assert "argv" not in parameters
    assert "environment" not in parameters


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("group", "gpu"),
        ("case_id", "GPU-001"),
        (
            "resources",
            (
                ResourceSpec("filesystem", "/tmp/input.json"),
                ResourceSpec("container", "unexpected"),
            ),
        ),
    ],
)
def test_foundation_action_revalidates_tampered_identity_and_resources(
    field: str, value: object
) -> None:
    action = FoundationCheckAction(
        group="deployment",
        case_id="DEP-001",
        resources=(ResourceSpec("filesystem", "/tmp/input.json"),),
    )
    object.__setattr__(action, field, value)
    with pytest.raises(ValueError, match="foundation checker"):
        CommandSpec(action=action)


@pytest.mark.asyncio
async def test_infrastructure_action_authorizes_all_run_scoped_resource_kinds(
    tmp_path: Path,
) -> None:
    release_root = _release_root(tmp_path)
    context = CaseContext(release_root, "run-1", "local")
    prefix = "m2b-5-run-1-inf-001-"
    with tempfile.TemporaryDirectory(prefix=prefix) as directory:
        os.chmod(directory, 0o700)
        input_path = Path(directory) / "input.json"
        descriptor = os.open(input_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        _write_json(
            input_path,
            {
                "case_id": "INF-001",
                "run_id": "run-1",
                "target": "local",
                "database": "m2b_5_run_1_inf_001_test",
                "mongodb_database": "m2b_5_run_1_inf_001_mongo_test",
                "kafka_topic": "m2b.run-1.inf-001",
                "kafka_group": "m2b.run-1.inf-001",
                "redis_prefix": "m2b:run-1:inf-001:",
            },
        )
        resources = (
            ResourceSpec("filesystem", str(input_path)),
            ResourceSpec("database", "m2b_5_run_1_inf_001_test"),
            ResourceSpec(
                "mongodb_database", "m2b_5_run_1_inf_001_mongo_test"
            ),
            ResourceSpec("kafka_topic", "m2b.run-1.inf-001"),
            ResourceSpec("kafka_group", "m2b.run-1.inf-001"),
            ResourceSpec("redis_prefix", "m2b:run-1:inf-001:"),
        )
        action = FoundationCheckAction(
            group="infrastructure", case_id="INF-001", resources=resources
        )
        async with _case_execution_scope(
            context,
            "isolated_mutation",
            None,
            _foundation_case("INF-001"),
        ):
            validate_command_spec(context=context, command=CommandSpec(action=action))


@pytest.mark.asyncio
async def test_infrastructure_action_rejects_same_run_resources_from_another_case(
    tmp_path: Path,
) -> None:
    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    prefix = "m2b-5-run-1-inf-001-"
    with tempfile.TemporaryDirectory(prefix=prefix) as directory:
        os.chmod(directory, 0o700)
        input_path = Path(directory) / "input.json"
        descriptor = os.open(input_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        resource_names = {
            "database": "m2b_5_run_1_inf_002_test",
            "mongodb_database": "m2b_5_run_1_inf_002_mongo_test",
            "kafka_topic": "m2b.run-1.inf-002",
            "kafka_group": "m2b.run-1.inf-002",
            "redis_prefix": "m2b:run-1:inf-002:",
        }
        _write_json(
            input_path,
            {
                "case_id": "INF-001",
                "run_id": "run-1",
                "target": "local",
                **resource_names,
            },
        )
        action = FoundationCheckAction(
            group="infrastructure",
            case_id="INF-001",
            resources=(
                ResourceSpec("filesystem", str(input_path)),
                ResourceSpec("database", resource_names["database"]),
                ResourceSpec(
                    "mongodb_database", resource_names["mongodb_database"]
                ),
                ResourceSpec("kafka_topic", resource_names["kafka_topic"]),
                ResourceSpec("kafka_group", resource_names["kafka_group"]),
                ResourceSpec("redis_prefix", resource_names["redis_prefix"]),
            ),
        )

        async with _case_execution_scope(
            context,
            "isolated_mutation",
            None,
            _foundation_case("INF-001"),
        ):
            with pytest.raises(ValueError, match="outside current case namespace"):
                validate_command_spec(context=context, command=CommandSpec(action=action))


def _private_foundation_input(
    directory: str,
    document: dict[str, object],
) -> Path:
    input_path = Path(directory) / "input.json"
    descriptor = os.open(input_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    _write_json(input_path, document)
    return input_path


@pytest.mark.asyncio
async def test_deployment_action_accepts_exact_authorized_scratch_directory() -> None:
    process = importlib.import_module("scripts.milestone_2b_case_runners.process")
    context = CaseContext(Path("/tmp/release").resolve(), "run-1", "local")
    prefix = "m2b-5-run-1-dep-013-"
    scratch = Path(tempfile.mkdtemp(prefix=f"{prefix}scratch-"))
    os.chmod(scratch, 0o700)
    try:
        with tempfile.TemporaryDirectory(prefix=prefix) as directory:
            os.chmod(directory, 0o700)
            input_path = _private_foundation_input(
                directory,
                {
                    "case_id": "DEP-013",
                    "run_id": "run-1",
                    "target": "local",
                    "scratch_directory": str(scratch),
                },
            )
            action = FoundationCheckAction(
                group="deployment",
                case_id="DEP-013",
                resources=(
                    ResourceSpec("filesystem", str(input_path)),
                    ResourceSpec("filesystem", str(scratch)),
                ),
            )
            command = CommandSpec(action=action)
            async with _case_execution_scope(
                context,
                "isolated_mutation",
                None,
                _foundation_case("DEP-013"),
            ):
                validate_command_spec(context=context, command=command)
            assert process._materialize_command(command)[0] == "filesystem_mutation"
    finally:
        scratch.rmdir()


@pytest.mark.asyncio
async def test_deployment_action_rejects_undeclared_scratch_directory() -> None:
    prefix = "m2b-5-run-1-dep-013-"
    scratch = Path(tempfile.mkdtemp(prefix=f"{prefix}scratch-"))
    os.chmod(scratch, 0o700)
    try:
        with tempfile.TemporaryDirectory(prefix=prefix) as directory:
            os.chmod(directory, 0o700)
            input_path = _private_foundation_input(
                directory,
                {
                    "case_id": "DEP-013",
                    "run_id": "run-1",
                    "target": "local",
                    "scratch_directory": str(scratch),
                },
            )
            with pytest.raises(ValueError, match="fixed case contract"):
                FoundationCheckAction(
                    group="deployment",
                    case_id="DEP-013",
                    resources=(ResourceSpec("filesystem", str(input_path)),),
                )
    finally:
        scratch.rmdir()


@pytest.mark.asyncio
async def test_deployment_action_rejects_same_run_cross_case_scratch() -> None:
    context = CaseContext(Path("/tmp/release").resolve(), "run-1", "local")
    input_prefix = "m2b-5-run-1-dep-013-"
    scratch = Path(tempfile.mkdtemp(prefix="m2b-5-run-1-dep-014-scratch-"))
    os.chmod(scratch, 0o700)
    try:
        with tempfile.TemporaryDirectory(prefix=input_prefix) as directory:
            os.chmod(directory, 0o700)
            input_path = _private_foundation_input(
                directory,
                {
                    "case_id": "DEP-013",
                    "run_id": "run-1",
                    "target": "local",
                    "scratch_directory": str(scratch),
                },
            )
            action = FoundationCheckAction(
                group="deployment",
                case_id="DEP-013",
                resources=(
                    ResourceSpec("filesystem", str(input_path)),
                    ResourceSpec("filesystem", str(scratch)),
                ),
            )
            async with _case_execution_scope(
                context,
                "isolated_mutation",
                None,
                _foundation_case("DEP-013"),
            ):
                with pytest.raises(ValueError, match="outside current case namespace"):
                    validate_command_spec(
                        context=context,
                        command=CommandSpec(action=action),
                    )
    finally:
        scratch.rmdir()


def test_deployment_checker_rejects_missing_scratch_authorization() -> None:
    deployment = importlib.import_module(
        "scripts.milestone_2b_case_runners.deployment"
    )

    result = deployment.evaluate_scenario(
        "DEP-013",
        {
            "schema_version": 1,
            "case_id": "DEP-013",
            "run_id": "run-1",
            "mode": "controlled_input",
            "mutation": {"case": "DEP-013"},
        },
    )

    assert result["status"] == "失败"
    assert "scratch" in result["reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outside",
    [
        ResourceSpec("database", "shared_test"),
        ResourceSpec("kafka_topic", "shared.foundation"),
        ResourceSpec("kafka_group", "shared.foundation"),
        ResourceSpec("redis_prefix", "shared:foundation:"),
    ],
)
async def test_infrastructure_action_rejects_each_outside_resource_kind(
    outside: ResourceSpec, tmp_path: Path
) -> None:
    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    prefix = "m2b-5-run-1-inf-001-"
    with tempfile.TemporaryDirectory(prefix=prefix) as directory:
        os.chmod(directory, 0o700)
        input_path = Path(directory) / "input.json"
        descriptor = os.open(input_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        action = FoundationCheckAction(
            group="infrastructure",
            case_id="INF-001",
            resources=(ResourceSpec("filesystem", str(input_path)), outside),
        )
        async with _case_execution_scope(context, "isolated_mutation", None):
            with pytest.raises(ValueError, match="outside current run namespace"):
                validate_command_spec(context=context, command=CommandSpec(action=action))


@pytest.mark.asyncio
async def test_foundation_action_rejects_input_outside_current_case_namespace(
    tmp_path: Path,
) -> None:
    release_root = (
        tmp_path
        / "v1.0_260818"
        / ("1" * 40)
    )
    release_root.mkdir(parents=True)
    context = CaseContext(release_root, "run-1", "local")
    action = FoundationCheckAction(
        group="deployment",
        case_id="DEP-001",
        resources=(ResourceSpec("filesystem", str(tmp_path / "outside.json")),),
    )
    async with _case_execution_scope(context, "isolated_mutation", None):
        with pytest.raises(ValueError, match="current case temporary namespace"):
            validate_command_spec(context=context, command=CommandSpec(action=action))


@pytest.mark.asyncio
async def test_foundation_check_action_rejects_a_different_claimed_case(
    tmp_path: Path,
) -> None:
    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    prefix = "m2b-5-run-1-dep-002-"
    with tempfile.TemporaryDirectory(prefix=prefix) as directory:
        os.chmod(directory, 0o700)
        input_path = Path(directory) / "input.json"
        descriptor = os.open(input_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        action = FoundationCheckAction(
            group="deployment",
            case_id="DEP-002",
            resources=(ResourceSpec("filesystem", str(input_path)),),
        )
        async with _case_execution_scope(
            context,
            "isolated_mutation",
            None,
            _foundation_case("DEP-001"),
        ):
            with pytest.raises(ValueError, match="claimed case"):
                validate_command_spec(context=context, command=CommandSpec(action=action))


@pytest.mark.asyncio
async def test_foundation_cleanup_action_rejects_a_different_claimed_case(
    tmp_path: Path,
) -> None:
    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    resources = foundation_cleanup_resources("deployment", "DEP-002", "run-1")
    action = FoundationCleanupAction(
        group="deployment",
        case_id="DEP-002",
        run_id="run-1",
        resources=resources,
    )
    async with _case_execution_scope(
        context,
        "isolated_mutation",
        None,
        _foundation_case("DEP-001"),
    ):
        with pytest.raises(ValueError, match="claimed case"):
            validate_command_spec(context=context, command=CommandSpec(action=action))


@pytest.mark.asyncio
async def test_foundation_input_resources_must_match_the_authorized_action(
    tmp_path: Path,
) -> None:
    infrastructure = importlib.import_module(
        "scripts.milestone_2b_case_runners.infrastructure"
    )
    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    case = _foundation_case("INF-016")
    scenario = infrastructure._infrastructure_scenario(context, case)
    scenario.update(
        {
            "schema_version": 1,
            "case_id": case.case_id,
            "run_id": "run-2",
            "target": context.target,
            "mode": infrastructure.CASE_SPECS[case.case_id].mode,
            **infrastructure._expected_names("run-2", case.case_id),
        }
    )
    prefix = "m2b-5-run-1-inf-016-"
    with tempfile.TemporaryDirectory(prefix=prefix) as directory:
        os.chmod(directory, 0o700)
        input_path = Path(directory) / "input.json"
        input_path.write_text(json.dumps(scenario), encoding="utf-8")
        input_path.chmod(0o600)
        resources = (
            ResourceSpec("filesystem", str(input_path)),
            *infrastructure._infrastructure_resources(context, case),
        )
        action = FoundationCheckAction(
            group="infrastructure",
            case_id=case.case_id,
            resources=resources,
        )
        async with _case_execution_scope(
            context,
            case.safety,
            None,
            case,
        ):
            with pytest.raises(ValueError, match="authorized action"):
                validate_command_spec(context=context, command=CommandSpec(action=action))


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _foundation_cases(), ids=lambda case: case.case_id)
async def test_each_runner_executes_its_typed_scenario_and_asserts_chinese_result(
    case: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment = importlib.import_module(
        "scripts.milestone_2b_case_runners.deployment"
    )
    module_name, method_name = case.runner.split(".", 1)
    module = importlib.import_module(
        f"scripts.milestone_2b_case_runners.{module_name}"
    )
    runner = vars(module)[method_name]
    spec = module.CASE_SPECS[case.case_id]
    captured_inputs: list[Path] = []

    async def fake_run_command(
        *,
        context: CaseContext,
        command: CommandSpec,
        timeout_seconds: float,
        terminate_grace_seconds: float = 2.0,
    ) -> CommandResult:
        del timeout_seconds, terminate_grace_seconds
        assert isinstance(command.action, FoundationCheckAction)
        assert command.action.case_id == case.case_id
        assert command.action.group == module_name
        input_path = Path(command.argv[-1])
        captured_inputs.append(input_path)
        scenario = json.loads(input_path.read_text(encoding="utf-8"))
        assert scenario["case_id"] == case.case_id
        assert scenario["run_id"] == context.run_id
        assert scenario["target"] == context.target
        assert scenario["mode"] in {"controlled_input", "canonical_runtime"}
        assert command.action.resources[0] == ResourceSpec(
            "filesystem", str(input_path)
        )
        return CommandResult(
            argv=command.argv,
            returncode=0,
            stdout=json.dumps(
                {
                    "case_id": case.case_id,
                    "status": spec.status,
                    "reason": spec.reason,
                    "observed": {"checker": "受控测试"},
                },
                ensure_ascii=False,
            ).encode(),
            stderr=b"",
            stdout_truncated=False,
            stderr_truncated=False,
        )

    monkeypatch.setattr(deployment, "run_command", fake_run_command)
    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    async with _case_execution_scope(
        context, case.safety, None, case
    ):
        outcome = await runner(context, case)
    assert outcome.status == "通过"
    assert outcome.reason == spec.reason
    assert len(outcome.evidence) == 1
    assert "反例" in outcome.reason
    assert not captured_inputs[0].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _foundation_cases(), ids=lambda case: case.case_id)
@pytest.mark.parametrize("corruption", ["status", "reason"])
async def test_each_runner_rejects_a_corrupted_case_specific_check(
    case: Any,
    corruption: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment = importlib.import_module(
        "scripts.milestone_2b_case_runners.deployment"
    )
    module_name, method_name = case.runner.split(".", 1)
    module = importlib.import_module(
        f"scripts.milestone_2b_case_runners.{module_name}"
    )
    runner = vars(module)[method_name]
    spec = module.CASE_SPECS[case.case_id]

    async def corrupted_run_command(**kwargs: Any) -> CommandResult:
        command = kwargs["command"]
        payload = {
            "case_id": case.case_id,
            "status": "失败" if corruption == "status" else spec.status,
            "reason": "错误的通用原因" if corruption == "reason" else spec.reason,
            "observed": {"checker": "受控测试"},
        }
        return CommandResult(
            argv=command.argv,
            returncode=0,
            stdout=json.dumps(payload, ensure_ascii=False).encode(),
            stderr=b"",
            stdout_truncated=False,
            stderr_truncated=False,
        )

    monkeypatch.setattr(deployment, "run_command", corrupted_run_command)
    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    async with _case_execution_scope(
        context, case.safety, None, case
    ):
        with pytest.raises(ValueError, match=f"{corruption} does not match"):
            await runner(context, case)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_id", "fact_key"),
    GPU_CANONICAL_CASE_FACTS.items(),
)
async def test_gpu_canonical_runner_validates_health_then_rejects_isolated_mutation(
    case_id: str,
    fact_key: str,
    tmp_path: Path,
) -> None:
    gpu = importlib.import_module("scripts.milestone_2b_case_runners.gpu")
    case = _foundation_case(case_id)
    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    paths = _write_healthy_gpu_evidence(
        context.release_root, gpu._TARGET_CONTAINERS[case_id]
    )
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    }
    if case_id == "GPU-018":
        registration = json.loads(paths[2].read_text(encoding="utf-8"))
        assert "instance_id" not in registration
        assert registration["validated_instances"][0]["instance_id"] == (
            gpu._TARGET_CONTAINERS[case_id]
        )

    with MaintenanceLockGuard(context.release_root) as maintenance_lock:
        async with _case_execution_scope(
            context,
            case.safety,
            maintenance_lock,
            case,
        ):
            outcome = await vars(gpu)[case.runner.split(".", 1)[1]](context, case)

    assert outcome.status == "通过"
    assert outcome.reason == gpu.CASE_SPECS[case_id].reason
    evidence = json.loads(
        (context.release_root / outcome.evidence[0]).read_text(encoding="utf-8")
    )
    checker_result = json.loads(evidence["payload"]["stdout"])
    assert checker_result["observed"]["canonical_pair_valid"] is True
    assert checker_result["observed"][fact_key]
    assert checker_result["observed"]["mutation_case"] == case_id
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    } == before


def test_gpu_utilization_validator_accepts_unavailable_process_metrics(
    tmp_path: Path,
) -> None:
    gpu = importlib.import_module("scripts.milestone_2b_case_runners.gpu")
    release_root = _release_root(tmp_path)
    running_path, stopped_path, registration_path = _write_healthy_gpu_evidence(
        release_root,
        "facerec-gpu2",
    )
    running = json.loads(running_path.read_text(encoding="utf-8"))
    stopped = json.loads(stopped_path.read_text(encoding="utf-8"))
    registration = json.loads(registration_path.read_text(encoding="utf-8"))
    process_metrics = running["synchronous_samples"][0]["processes"][0][
        "gpu_utilization"
    ]
    for field in process_metrics:
        process_metrics[field] = None
    running["utilization"]["target_sm_percent"] = None
    running["compatibility"]["result"]["target_sm_max_percent"] = None
    instance = next(
        item
        for item in load_operator_inventory(
            PLATFORM_ROOT / "deploy/docker-compose.operators.yml"
        ).gpu_instances
        if item.instance_id == "facerec-gpu2"
    )

    gpu._utilization_validator(
        instance,
        running,
        stopped,
        registration,
        release_root.name,
    )
    gpu._compatibility_validator(
        instance,
        running,
        stopped,
        registration,
        release_root.name,
    )


def test_gpu_hardware_validator_accepts_unavailable_temperature_limit(
    tmp_path: Path,
) -> None:
    gpu = importlib.import_module("scripts.milestone_2b_case_runners.gpu")
    release_root = _release_root(tmp_path)
    running_path, stopped_path, registration_path = _write_healthy_gpu_evidence(
        release_root,
        "facerec-gpu2",
    )
    running = json.loads(running_path.read_text(encoding="utf-8"))
    stopped = json.loads(stopped_path.read_text(encoding="utf-8"))
    registration = json.loads(registration_path.read_text(encoding="utf-8"))
    running["synchronous_samples"][0]["hardware"]["temperature_limit_c"] = None
    running["hardware"]["temperature_limit_c"] = None
    instance = next(
        item
        for item in load_operator_inventory(
            PLATFORM_ROOT / "deploy/docker-compose.operators.yml"
        ).gpu_instances
        if item.instance_id == "facerec-gpu2"
    )

    gpu._hardware_validator(
        instance,
        running,
        stopped,
        registration,
        release_root.name,
    )


def test_gpu_015_mutates_canonical_gpu2_compatibility_identity() -> None:
    gpu = importlib.import_module("scripts.milestone_2b_case_runners.gpu")

    source = inspect.getsource(gpu._check_gpu_015)

    assert 'running["compatibility"]["gpu"]["product_name"]' in source
    assert 'running["compatibility"]["gpu"]["compute_capability"]' in source
    assert 'running["gpu"]["physical_index"]' not in source
    assert 'running["gpu"]["physical_uuid"]' not in source


def test_gpu_015_rejects_gpu0_4090_identity_relabelled_as_gpu2(
    tmp_path: Path,
) -> None:
    gpu = importlib.import_module("scripts.milestone_2b_case_runners.gpu")
    release_root = _release_root(tmp_path)
    running_path, stopped_path, _ = _write_healthy_gpu_evidence(
        release_root,
        "facerec-gpu2",
    )
    running = json.loads(running_path.read_text(encoding="utf-8"))
    stopped = json.loads(stopped_path.read_text(encoding="utf-8"))
    instance = next(
        item
        for item in load_operator_inventory(
            PLATFORM_ROOT / "deploy/docker-compose.operators.yml"
        ).gpu_instances
        if item.instance_id == "facerec-gpu2"
    )
    compatibility_gpu = running["compatibility"]["gpu"]
    compatibility_gpu["product_name"] = "NVIDIA GeForce RTX 4090 D"
    compatibility_gpu["compute_capability"] = "8.9"

    with pytest.raises(ValueError, match="GPU2 compatibility requires RTX 3090"):
        gpu._compatibility_validator(
            instance,
            running,
            stopped,
            None,
            release_root.name,
        )


def test_gpu_015_rejects_driver_cuda_without_container_runtime(
    tmp_path: Path,
) -> None:
    gpu = importlib.import_module("scripts.milestone_2b_case_runners.gpu")
    release_root = _release_root(tmp_path)
    running_path, stopped_path, _ = _write_healthy_gpu_evidence(
        release_root,
        "facerec-gpu2",
    )
    running = json.loads(running_path.read_text(encoding="utf-8"))
    stopped = json.loads(stopped_path.read_text(encoding="utf-8"))
    instance = next(
        item
        for item in load_operator_inventory(
            PLATFORM_ROOT / "deploy/docker-compose.operators.yml"
        ).gpu_instances
        if item.instance_id == "facerec-gpu2"
    )
    del running["compatibility"]["gpu"]["container_cuda_runtime_version"]

    with pytest.raises(ValueError, match="compatibility identity is invalid"):
        gpu._compatibility_validator(
            instance,
            running,
            stopped,
            None,
            release_root.name,
        )


def test_gpu_015_rejects_container_runtime_not_backed_by_framework_probe(
    tmp_path: Path,
) -> None:
    gpu = importlib.import_module("scripts.milestone_2b_case_runners.gpu")
    release_root = _release_root(tmp_path)
    running_path, stopped_path, _ = _write_healthy_gpu_evidence(
        release_root,
        "facerec-gpu2",
    )
    running = json.loads(running_path.read_text(encoding="utf-8"))
    stopped = json.loads(stopped_path.read_text(encoding="utf-8"))
    instance = next(
        item
        for item in load_operator_inventory(
            PLATFORM_ROOT / "deploy/docker-compose.operators.yml"
        ).gpu_instances
        if item.instance_id == "facerec-gpu2"
    )
    del running["cuda_probe"]["container_cuda_runtime_version"]

    with pytest.raises(ValueError, match="does not match probe"):
        gpu._compatibility_validator(
            instance,
            running,
            stopped,
            None,
            release_root.name,
        )


@pytest.mark.parametrize(
    ("validator_name", "sample_field"),
    [
        ("_hardware_validator", "hardware"),
        ("_utilization_validator", "gpu_utilization_percent"),
    ],
)
def test_gpu_validators_require_synchronous_production_sample_fields(
    validator_name: str,
    sample_field: str,
    tmp_path: Path,
) -> None:
    gpu = importlib.import_module("scripts.milestone_2b_case_runners.gpu")
    release_root = _release_root(tmp_path)
    running_path, stopped_path, _ = _write_healthy_gpu_evidence(
        release_root,
        "facerec-gpu0",
    )
    running = json.loads(running_path.read_text(encoding="utf-8"))
    stopped = json.loads(stopped_path.read_text(encoding="utf-8"))
    instance = next(
        item
        for item in load_operator_inventory(
            PLATFORM_ROOT / "deploy/docker-compose.operators.yml"
        ).gpu_instances
        if item.instance_id == "facerec-gpu0"
    )
    running["synchronous_samples"][0].pop(sample_field, None)

    with pytest.raises(ValueError, match="synchronous sample"):
        getattr(gpu, validator_name)(
            instance,
            running,
            stopped,
            None,
            release_root.name,
        )


def test_gpu_020_uses_mapped_target_pid_sm_when_whole_card_is_busy(
    tmp_path: Path,
) -> None:
    gpu = importlib.import_module("scripts.milestone_2b_case_runners.gpu")
    release_root = _release_root(tmp_path)
    running_path, stopped_path, _ = _write_healthy_gpu_evidence(
        release_root,
        "facerec-gpu0",
    )
    running = json.loads(running_path.read_text(encoding="utf-8"))
    stopped = json.loads(stopped_path.read_text(encoding="utf-8"))
    instance = next(
        item
        for item in load_operator_inventory(
            PLATFORM_ROOT / "deploy/docker-compose.operators.yml"
        ).gpu_instances
        if item.instance_id == "facerec-gpu0"
    )
    sample = running["synchronous_samples"][0]
    process = sample["processes"][0]
    sample["gpu_utilization_percent"] = 90
    process["cpu_percent"] = 95
    process["gpu_utilization"] = {
        "sm_percent": 0,
        "memory_percent": 0,
        "encoder_percent": 0,
        "decoder_percent": 0,
    }
    running["utilization"] = {
        "cpu_percent": 95,
        "gpu_percent": 90,
        "target_sm_percent": 0,
    }

    with pytest.raises(
        ValueError,
        match="CPU is busy while mapped target PID SM utilization remains zero",
    ):
        gpu._utilization_validator(
            instance,
            running,
            stopped,
            None,
            release_root.name,
        )


def test_gpu_canonical_scenario_uses_only_release_relative_fixed_paths(
    tmp_path: Path,
) -> None:
    gpu = importlib.import_module("scripts.milestone_2b_case_runners.gpu")
    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    case = _foundation_case("GPU-003")

    scenario = gpu._gpu_scenario(context, case)

    assert scenario["release_root"] == str(context.release_root)
    assert scenario["operator_compose"] == "deploy/docker-compose.operators.yml"
    assert scenario["running_evidence"] == (
        "gpu-instances/asr-offline-gpu1.json"
    )
    assert scenario["stopped_evidence"] == (
        "recovery/asr-offline-gpu1-stopped.json"
    )
    assert "registration_evidence" not in scenario

    registration_scenario = gpu._gpu_scenario(
        context, _foundation_case("GPU-018")
    )
    assert registration_scenario["registration_evidence"] == (
        "registration/operator-registration-instance-facerec-gpu0.json"
    )


@pytest.mark.parametrize("unsafe_path", ["../outside.json", "/tmp/outside.json"])
def test_gpu_checker_rejects_release_path_escape(
    unsafe_path: str,
    tmp_path: Path,
) -> None:
    gpu = importlib.import_module("scripts.milestone_2b_case_runners.gpu")
    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    case = _foundation_case("GPU-003")
    _write_healthy_gpu_evidence(context.release_root, "asr-offline-gpu1")
    scenario = gpu._gpu_scenario(context, case)
    scenario.update(
        {
            "schema_version": 1,
            "case_id": case.case_id,
            "run_id": context.run_id,
            "target": context.target,
            "mode": gpu.CASE_SPECS[case.case_id].mode,
        }
    )
    scenario["running_evidence"] = unsafe_path

    result = gpu.evaluate_scenario(case.case_id, scenario)

    assert result["status"] == "失败"
    assert "release root" in result["reason"]


def test_gpu_checker_rejects_symlinked_canonical_evidence(tmp_path: Path) -> None:
    gpu = importlib.import_module("scripts.milestone_2b_case_runners.gpu")
    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    case = _foundation_case("GPU-003")
    paths = _write_healthy_gpu_evidence(context.release_root, "asr-offline-gpu1")
    running_path = paths[0]
    outside = tmp_path / "outside.json"
    outside.write_bytes(running_path.read_bytes())
    running_path.unlink()
    running_path.symlink_to(outside)

    scenario = gpu._gpu_scenario(context, case)
    scenario.update(
        {
            "schema_version": 1,
            "case_id": case.case_id,
            "run_id": context.run_id,
            "target": context.target,
            "mode": gpu.CASE_SPECS[case.case_id].mode,
        }
    )
    result = gpu.evaluate_scenario(case.case_id, scenario)

    assert result["status"] == "失败"
    assert "non-symlink" in result["reason"]
