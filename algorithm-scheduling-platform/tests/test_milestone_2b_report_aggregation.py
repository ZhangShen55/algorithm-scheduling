from __future__ import annotations

import copy
import hashlib
import importlib
import inspect
import json
import os
import stat
import subprocess
import sys
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, is_dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, get_type_hints

import pytest
import yaml

if TYPE_CHECKING:
    from scripts.milestone_2b_report_contract import CaseRecord, Coverage

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = PLATFORM_ROOT / "deploy" / "milestone-2b-report-plan.json"
COMPOSE_PATH = PLATFORM_ROOT / "deploy" / "docker-compose.operators.yml"
SMOKE_MANIFEST_PATH = PLATFORM_ROOT / "deploy" / "operator-smoke-cases.json"
CONTRACT_PATH = PLATFORM_ROOT / "scripts" / "milestone_2b_report_contract.py"
AGGREGATE_PATH = PLATFORM_ROOT / "scripts" / "aggregate_milestone_2b_cases.py"

REASON = (
    "当前仓库没有该用例的受控目标服务器 runner 与运行证据 schema；"
    "本 release 未执行，现有本地单元测试或正向健康检查不等价于现场执行。"
)
EXPECTED_COUNTS = {
    "DEP": 20,
    "GPU": 20,
    "REG": 20,
    "INF": 16,
    "JOB": 20,
    "FILE": 16,
    "PPT": 15,
    "OCR": 5,
    "KEY": 5,
    "ASR": 18,
    "VIS": 28,
    "ONL": 20,
    "FACE": 14,
    "LOAD": 26,
}
EXPECTED_COVERAGE_KEYS = (
    "registration_full",
    "registration_profiles",
    "registration_recovery",
    "registration_facerec",
    "gpu_running",
    "gpu_stopped",
    "smoke_full",
    "smoke_gpu_trigger",
    "smoke_cpu_instance",
    "negative_declarations",
    "load_declarations",
)
EXPECTED_COVERAGE_AUTHORITY = {
    "registration_full": 1,
    "registration_profiles": 4,
    "registration_recovery": 18,
    "registration_facerec": 1,
    "gpu_running": 18,
    "gpu_stopped": 18,
    "smoke_full": 8,
    "smoke_gpu_trigger": 18,
    "smoke_cpu_instance": 6,
    "negative_declarations": 217,
    "load_declarations": 26,
}
EXPECTED_CASE_FIELDS = (
    "case_id",
    "source_case_id",
    "case_kind",
    "run_id",
    "status",
    "started_at",
    "finished_at",
    "target",
    "command",
    "evidence",
    "reason",
    "mock",
    "release_tag",
    "git_sha",
)
RELEASE_TAG = "v1.0_260817"
GIT_SHA = "a" * 40
PLAN_SHA256 = "b" * 64
EVIDENCE_TIMESTAMP = "2026-08-17T00:00:00+00:00"
DECLARATION_PLACEHOLDER = "NOT_EXECUTED"
DECLARATION_TARGET = "controlled-target-server"


def _contract_module() -> ModuleType:
    assert CONTRACT_PATH.is_file(), f"权威报告合同不存在: {CONTRACT_PATH}"
    return importlib.import_module("scripts.milestone_2b_report_contract")


def _aggregate_module() -> ModuleType:
    return importlib.import_module("scripts.aggregate_milestone_2b_cases")


class ReleaseTree:
    def __init__(self, tmp_path: Path) -> None:
        self.release_tag = RELEASE_TAG
        self.git_sha = GIT_SHA
        self.root = (
            tmp_path
            / "reports"
            / "milestone-2b"
            / "releases"
            / self.release_tag
            / self.git_sha
        )
        self.compose_path = COMPOSE_PATH
        self.report_plan_path = PLAN_PATH
        self.instances = self._load_compose_instances()
        self.gpu_instances = tuple(
            instance_id
            for instance_id, instance in self.instances.items()
            if instance["profile"] != "cpu"
        )
        self.cpu_instances = tuple(
            instance_id
            for instance_id, instance in self.instances.items()
            if instance["profile"] == "cpu"
        )
        assert len(self.instances) == 24
        assert len(self.gpu_instances) == 18
        assert len(self.cpu_instances) == 6

    def _load_compose_instances(self) -> dict[str, dict[str, Any]]:
        document = yaml.safe_load(self.compose_path.read_text(encoding="utf-8"))
        assert isinstance(document, dict)
        services = document.get("services")
        assert isinstance(services, dict)
        instances: dict[str, dict[str, Any]] = {}
        for service_name, raw_service in services.items():
            assert isinstance(service_name, str)
            assert isinstance(raw_service, dict)
            environment = raw_service.get("environment")
            profiles = raw_service.get("profiles")
            assert isinstance(environment, dict)
            assert isinstance(profiles, list) and len(profiles) == 1
            instance_id = environment.get("PLATFORM_INSTANCE_ID")
            profile = profiles[0]
            assert isinstance(instance_id, str)
            assert isinstance(profile, str)
            assert instance_id == service_name
            process_name = environment.get("GPU_PROCESS_NAME")
            physical_gpu = environment.get("PLATFORM_GPU_ID")
            instances[instance_id] = {
                "service_name": service_name,
                "profile": profile,
                "process_name": process_name,
                "physical_gpu": int(physical_gpu) if physical_gpu is not None else None,
            }
        return dict(sorted(instances.items()))

    def _write_json(self, relative_path: str, payload: object) -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def read_json(self, relative_path: str) -> dict[str, Any]:
        payload = json.loads((self.root / relative_path).read_text(encoding="utf-8"))
        assert isinstance(payload, dict)
        return payload

    def read_value(self, relative_path: str) -> Any:
        return json.loads((self.root / relative_path).read_text(encoding="utf-8"))

    def replace_json(self, relative_path: str, payload: dict[str, Any]) -> None:
        self._write_json(relative_path, payload)

    def _write_registration(
        self,
        relative_path: str,
        *,
        selection: dict[str, Any],
        expected: int,
    ) -> None:
        self._write_json(
            relative_path,
            {
                "schema_version": 1,
                "evidence_type": "operator_registration",
                "status": "通过",
                "mock": False,
                "target": "operator-registry",
                "release_tag": self.release_tag,
                "git_sha": self.git_sha,
                "started_at": EVIDENCE_TIMESTAMP,
                "finished_at": EVIDENCE_TIMESTAMP,
                "control_endpoint": "http://127.0.0.1:18100",
                "selection": selection,
                "summary": {
                    "expected": expected,
                    "observed": expected,
                    "valid": expected,
                },
                "issues": [],
            },
        )

    def write_full_registration(self) -> None:
        self._write_registration(
            "registration/operator-registration.json",
            selection={"mode": "full", "values": []},
            expected=24,
        )

    def write_profile_registrations(self, profiles: tuple[str, ...]) -> None:
        for profile in profiles:
            expected = sum(
                instance["profile"] == profile for instance in self.instances.values()
            )
            self._write_registration(
                f"registration/operator-registration-profile-{profile}.json",
                selection={"mode": "profile", "values": [profile]},
                expected=expected,
            )

    def write_recovery_registrations(self, instance_ids: tuple[str, ...]) -> None:
        for instance_id in instance_ids:
            self._write_registration(
                f"registration/operator-registration-instance-{instance_id}.json",
                selection={"mode": "instance", "values": [instance_id]},
                expected=1,
            )

    def write_facerec_registration(self) -> None:
        instance_ids = tuple(
            instance_id
            for instance_id in self.gpu_instances
            if instance_id.startswith("facerec-")
        )
        assert instance_ids == tuple(sorted(instance_ids))
        assert len(instance_ids) == 3
        digest = hashlib.sha256("\n".join(instance_ids).encode()).hexdigest()[:12]
        assert digest == "784a68323268"
        self._write_registration(
            f"registration/operator-registration-instances-{digest}.json",
            selection={"mode": "instance", "values": list(instance_ids)},
            expected=3,
        )

    def write_all_registrations(self) -> None:
        self.write_full_registration()
        self.write_profile_registrations(("gpu0", "gpu1", "gpu2", "cpu"))
        self.write_recovery_registrations(self.gpu_instances)
        self.write_facerec_registration()

    def write_all_gpu_pairs(self) -> None:
        for index, instance_id in enumerate(self.gpu_instances):
            instance = self.instances[instance_id]
            physical_gpu = instance["physical_gpu"]
            process_name = instance["process_name"]
            assert isinstance(physical_gpu, int)
            assert isinstance(process_name, str)
            host_pid = 20_000 + index
            container = {
                "id": f"container-{instance_id}",
                "name": instance["service_name"],
                "instance_id": instance_id,
                "init_host_pid": 10_000 + index,
            }
            gpu = {
                "physical_index": physical_gpu,
                "physical_uuid": f"GPU-{physical_gpu}",
                "container_visible": str(physical_gpu),
            }
            target = {
                "container": instance["service_name"],
                "instance_id": instance_id,
                "physical_gpu": physical_gpu,
                "process_name": process_name,
            }
            running = {
                "schema_version": 1,
                "timestamp": EVIDENCE_TIMESTAMP,
                "status": "PASS",
                "mode": "running-inference",
                "reason": "GPU instance activity verified",
                "target": target,
                "commands": ["docker inspect <container>", "<trigger>"],
                "release_sha": self.git_sha,
                "container": container,
                "gpu": gpu,
                "activity": {
                    "protocol": "inherited-fd-v1",
                    "operator_code": process_name,
                    "instance_id": instance_id,
                    "target_origin": "http://127.0.0.1:1",
                    "run_id": f"run-{instance_id}",
                    "attempts": [
                        {
                            "attempt": 1,
                            "sample_count": 2,
                            "started_at": EVIDENCE_TIMESTAMP,
                            "finished_at": EVIDENCE_TIMESTAMP,
                        }
                    ],
                },
                "synchronous_samples": [
                    {
                        "timestamp": EVIDENCE_TIMESTAMP,
                        "gpu_memory_mib": 128,
                        "processes": [
                            {
                                "host_pid": host_pid,
                                "container_pid": 42,
                                "process_name": process_name,
                                "used_memory_mib": 64,
                                "mapping": {
                                    "docker_top": True,
                                    "cgroup_full_container_id": True,
                                    "nspid": [host_pid, 42],
                                },
                            }
                        ],
                    },
                    {
                        "timestamp": EVIDENCE_TIMESTAMP,
                        "gpu_memory_mib": 128,
                        "processes": [
                            {
                                "host_pid": host_pid,
                                "container_pid": 42,
                                "process_name": process_name,
                                "used_memory_mib": 64,
                                "mapping": {
                                    "docker_top": True,
                                    "cgroup_full_container_id": True,
                                    "nspid": [host_pid, 42],
                                },
                            }
                        ],
                    },
                ],
            }
            stopped = {
                "schema_version": 1,
                "timestamp": EVIDENCE_TIMESTAMP,
                "status": "PASS",
                "mode": "assert-stopped",
                "reason": "GPU process stopped",
                "target": target,
                "commands": ["docker inspect <container>", "nvidia-smi <query>"],
                "release_sha": self.git_sha,
                "container": container,
                "gpu": gpu,
                "prior_cuda_pids": [host_pid],
                "remaining_cuda_pids": [],
            }
            self._write_json(f"gpu-instances/{instance_id}.json", running)
            self._write_json(f"recovery/{instance_id}-stopped.json", stopped)

    def write_complete_sources(self) -> None:
        self.write_all_registrations()
        self.write_all_gpu_pairs()

    def _smoke_manifest_cases(self) -> list[dict[str, Any]]:
        document = json.loads(SMOKE_MANIFEST_PATH.read_text(encoding="utf-8"))
        assert isinstance(document, dict)
        cases = document["cases"]
        assert isinstance(cases, list)
        assert all(isinstance(case, dict) for case in cases)
        return cases

    def _smoke_case_for_instance(self, instance_id: str) -> dict[str, Any]:
        matches = [
            case
            for case in self._smoke_manifest_cases()
            if instance_id.startswith(case["operator_code"].replace("_", "-") + "-")
        ]
        assert len(matches) == 1
        return matches[0]

    def _write_smoke_case(
        self,
        root: str,
        case: dict[str, Any],
        *,
        target: str,
        status: str,
        mock: bool = False,
    ) -> dict[str, Any]:
        operator_code = case["operator_code"]
        relative = f"{root}/{operator_code}.json"
        evidence_status = {
            "通过": "PASS",
            "失败": "失败",
            "未执行及原因": "未执行及原因",
        }[status]
        reason = {
            "通过": "直接调用响应符合算子合同",
            "失败": "injected smoke failure",
            "未执行及原因": "fixture unavailable",
        }[status]
        evidence: dict[str, Any] = {
            "schema_version": 1,
            "evidence_type": "operator_smoke",
            "operator_code": operator_code,
            "target": target,
            "checks": case["checks"],
            "status": evidence_status,
            "mock": mock,
            "release_tag": self.release_tag,
            "git_sha": self.git_sha,
        }
        if status == "通过":
            evidence["summary"] = {"verified": True}
        else:
            evidence["reason"] = reason
        self._write_json(relative, evidence)
        return {
            "case_id": case["case_id"],
            "status": status,
            "started_at": EVIDENCE_TIMESTAMP,
            "finished_at": EVIDENCE_TIMESTAMP,
            "target": target,
            "command": "deploy/scripts/run-operator-smoke --run-id auto",
            "evidence": [] if status == "未执行及原因" else [relative],
            "reason": reason,
            "mock": mock,
            "release_tag": self.release_tag,
            "git_sha": self.git_sha,
        }

    def write_full_smoke(
        self, statuses: dict[str, str] | None = None
    ) -> None:
        selected_statuses = statuses or {}
        logical_cases = [
            self._write_smoke_case(
                "smoke",
                case,
                target=case["operator_code"],
                status=selected_statuses.get(case["operator_code"], "通过"),
            )
            for case in self._smoke_manifest_cases()
        ]
        self._write_json("smoke/cases.json", logical_cases)

    def write_instance_smoke(
        self,
        instance_id: str,
        run_id: str,
        *,
        status: str = "通过",
        mock: bool = False,
    ) -> None:
        case = self._smoke_case_for_instance(instance_id)
        root = f"smoke/instances/{instance_id}/runs/{run_id}"
        logical = self._write_smoke_case(
            root,
            case,
            target=instance_id,
            status=status,
            mock=mock,
        )
        self._write_json(f"{root}/cases.json", [logical])

    def write_all_instance_smoke(self) -> None:
        for instance_id in self.instances:
            self.write_instance_smoke(instance_id, f"run-{instance_id}")

    def write_complete_smoke_sources(
        self, full_statuses: dict[str, str] | None = None
    ) -> None:
        self.write_full_smoke(full_statuses)
        self.write_all_instance_smoke()

    def collect_smoke(self) -> tuple[list[CaseRecord], dict[str, Coverage]]:
        aggregate = _aggregate_module()
        contract = _contract_module()
        inventory = aggregate.load_operator_inventory(self.compose_path)
        plan = contract.load_report_plan(self.report_plan_path)
        manifest = aggregate.load_smoke_manifest(SMOKE_MANIFEST_PATH)
        return aggregate.collect_smoke_cases(
            release_root=self.root,
            inventory=inventory,
            report_plan=plan,
            smoke_manifest=manifest,
            release_tag=self.release_tag,
            git_sha=self.git_sha,
        )

    def collect_registration_gpu(
        self,
    ) -> tuple[list[CaseRecord], dict[str, Coverage]]:
        aggregate = _aggregate_module()
        contract = _contract_module()
        inventory = aggregate.load_operator_inventory(self.compose_path)
        plan = contract.load_report_plan(self.report_plan_path)
        return aggregate.collect_registration_gpu_cases(
            release_root=self.root,
            inventory=inventory,
            report_plan=plan,
            release_tag=self.release_tag,
            git_sha=self.git_sha,
        )

    def run_aggregator(
        self, output: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        selected_output = output or self.root / "summary" / "cases.json"
        return subprocess.run(
            [
                sys.executable,
                str(AGGREGATE_PATH),
                "--release-root",
                str(self.root),
                "--operator-compose",
                str(self.compose_path),
                "--smoke-manifest",
                str(SMOKE_MANIFEST_PATH),
                "--report-plan",
                str(self.report_plan_path),
                "--output",
                str(selected_output),
            ],
            cwd=PLATFORM_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )


@pytest.fixture
def release_tree(tmp_path: Path) -> ReleaseTree:
    return ReleaseTree(tmp_path)


def _compose_document() -> dict[str, Any]:
    document = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _write_compose(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "docker-compose.operators.yml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _set_nested(document: dict[str, Any], path: tuple[str, ...], value: object) -> None:
    target = document
    for field in path[:-1]:
        nested = target[field]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = value


def _validate_gpu_pair(
    release_tree: ReleaseTree,
    running: dict[str, Any],
    stopped: dict[str, Any],
    instance_id: str = "asr-offline-gpu0",
) -> None:
    aggregate = _aggregate_module()
    inventory = aggregate.load_operator_inventory(COMPOSE_PATH)
    instance = next(
        item for item in inventory.gpu_instances if item.instance_id == instance_id
    )
    aggregate.validate_gpu_pair(instance, running, stopped, release_tree.git_sha)


def _producer_partial_activity(
    *,
    instance_id: str = "asr-offline-gpu0",
    operator_code: str = "asr_offline",
) -> dict[str, Any]:
    return {
        "protocol": "inherited-fd-v1",
        "operator_code": operator_code,
        "instance_id": instance_id,
    }


def _plan_document() -> dict[str, Any]:
    assert PLAN_PATH.is_file(), f"权威 report plan 不存在: {PLAN_PATH}"
    loaded = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _valid_case(case_id: str = "DEP-001") -> dict[str, Any]:
    category = "load" if case_id.startswith("LOAD-") else "negative"
    return {
        "case_id": case_id,
        "source_case_id": case_id,
        "case_kind": "execution_declaration",
        "run_id": DECLARATION_PLACEHOLDER,
        "status": "未执行及原因",
        "started_at": DECLARATION_PLACEHOLDER,
        "finished_at": DECLARATION_PLACEHOLDER,
        "target": DECLARATION_TARGET,
        "command": DECLARATION_PLACEHOLDER,
        "evidence": [f"{category}/cases.json"],
        "reason": REASON,
        "mock": False,
        "release_tag": RELEASE_TAG,
        "git_sha": GIT_SHA,
    }


def _valid_observed_case(
    *,
    case_id: str,
    case_kind: str,
    run_id: str,
    target: str,
    source_case_id: str | None = None,
    status: str = "通过",
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "source_case_id": source_case_id or case_id,
        "case_kind": case_kind,
        "run_id": run_id,
        "status": status,
        "started_at": EVIDENCE_TIMESTAMP,
        "finished_at": EVIDENCE_TIMESTAMP,
        "target": target,
        "command": f"verify {case_kind} {target}",
        "evidence": evidence or [f"evidence/{case_id}.json"],
        "reason": "evidence passed" if status == "通过" else "evidence failed",
        "mock": False,
        "release_tag": RELEASE_TAG,
        "git_sha": GIT_SHA,
    }


def _valid_observed_cases() -> list[dict[str, Any]]:
    gpu_targets = tuple(f"gpu-target-{index:02d}" for index in range(1, 19))
    cpu_targets = tuple(f"cpu-target-{index:02d}" for index in range(1, 7))
    smoke_manifest = json.loads(SMOKE_MANIFEST_PATH.read_text(encoding="utf-8"))
    smoke_authority = tuple(
        (case["case_id"], case["operator_code"])
        for case in smoke_manifest["cases"]
    )
    cases = [
        _valid_observed_case(
            case_id="REG-FULL",
            case_kind="registration_full",
            run_id="full",
            target="operator-registry",
        ),
        *[
            _valid_observed_case(
                case_id=f"REG-PROFILE-{profile}",
                case_kind="registration_profile",
                run_id=profile,
                target=profile,
            )
            for profile in ("gpu0", "gpu1", "gpu2", "cpu")
        ],
        *[
            _valid_observed_case(
                case_id=f"REG-RECOVERY-{index:02d}",
                case_kind="registration_recovery",
                run_id=target,
                target=target,
            )
            for index, target in enumerate(gpu_targets, start=1)
        ],
        _valid_observed_case(
            case_id="REG-FACEREC-THREE",
            case_kind="registration_facerec",
            run_id="facerec-three",
            target="facerec-three",
        ),
    ]
    for index, target in enumerate(gpu_targets, start=1):
        run_id = f"gpu-run-{index:02d}"
        cases.extend(
            (
                _valid_observed_case(
                    case_id=f"GPU-RUN-{index:02d}",
                    case_kind="gpu_running",
                    run_id=run_id,
                    target=target,
                ),
                _valid_observed_case(
                    case_id=f"GPU-STOP-{index:02d}",
                    case_kind="gpu_stopped",
                    run_id=run_id,
                    target=target,
                ),
            )
        )
    cases.extend(
        _valid_observed_case(
            case_id=f"SMOKE-FULL-{source_case_id}",
            source_case_id=source_case_id,
            case_kind="smoke_full",
            run_id="",
            target=operator_code,
            evidence=[f"smoke/{operator_code}.json"],
        )
        for source_case_id, operator_code in smoke_authority
    )
    cases.extend(
        _valid_observed_case(
            case_id=f"SMOKE-GPU-{index:02d}",
            source_case_id=smoke_authority[(index - 1) % len(smoke_authority)][0],
            case_kind="smoke_gpu_trigger",
            run_id=f"gpu-run-{index:02d}",
            target=target,
        )
        for index, target in enumerate(gpu_targets, start=1)
    )
    cases.extend(
        _valid_observed_case(
            case_id=f"SMOKE-CPU-{index:02d}",
            source_case_id=smoke_authority[(index - 1) % len(smoke_authority)][0],
            case_kind="smoke_cpu_instance",
            run_id=f"cpu-run-{index:02d}",
            target=target,
        )
        for index, target in enumerate(cpu_targets, start=1)
    )
    assert len(cases) == 92
    return cases


def _valid_envelope() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "release_tag": RELEASE_TAG,
        "git_sha": GIT_SHA,
        "plan_sha256": PLAN_SHA256,
        "coverage": {
            key: {
                "expected": expected,
                "observed": expected,
                "passed": 0
                if key in {"negative_declarations", "load_declarations"}
                else expected,
            }
            for key, expected in EXPECTED_COVERAGE_AUTHORITY.items()
        },
        "cases": [
            *(
                _valid_case(f"{prefix}-{number:03d}")
                for prefix, count in EXPECTED_COUNTS.items()
                for number in range(1, count + 1)
            ),
            *_valid_observed_cases(),
        ],
    }


def _write_plan(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "report-plan.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def _write_raw_plan(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "report-plan.json"
    path.write_text(text, encoding="utf-8")
    return path


def _remove_key(document: dict[str, Any], key: str) -> None:
    del document[key]


def _add_key(document: dict[str, Any], key: str) -> None:
    document[key] = "unexpected"


def _set_registration_profile(document: dict[str, Any]) -> None:
    document["registration"]["profiles"] = ["gpu0", "gpu1", "cpu"]


def _set_facerec_instance(document: dict[str, Any]) -> None:
    document["registration"]["facerec_instances"][2] = "facerec-cpu"


def _set_registration_bool(document: dict[str, Any]) -> None:
    document["registration"]["require_full"] = 1


def _set_smoke_bool(document: dict[str, Any]) -> None:
    document["smoke"]["require_cpu_instances"] = "true"


def _set_reason(document: dict[str, Any]) -> None:
    document["declarations"]["reason"] = "本地测试已通过"


def _set_range_prefix(document: dict[str, Any], value: object) -> None:
    document["declarations"]["negative"][0]["prefix"] = value


def _set_range_bound(
    document: dict[str, Any], field: str, value: object
) -> None:
    document["declarations"]["negative"][0][field] = value


def _duplicate_prefix(document: dict[str, Any]) -> None:
    document["declarations"]["negative"][1]["prefix"] = "DEP"


def _replace_load_prefix(document: dict[str, Any]) -> None:
    document["declarations"]["load"][0]["prefix"] = "DEP"


def _drop_declaration_range(document: dict[str, Any]) -> None:
    document["declarations"]["negative"].pop()


def test_report_plan_expands_exact_243_design_cases() -> None:
    plan_document = _plan_document()
    contract = _contract_module()

    plan = contract.load_report_plan(PLAN_PATH)
    expanded = contract.expand_declaration_cases(plan)

    assert plan == plan_document
    assert len(expanded) == 243
    assert Counter(case["case_kind"] for case in expanded) == {
        "negative": 217,
        "load": 26,
    }
    assert Counter(
        case["source_case_id"].split("-", maxsplit=1)[0] for case in expanded
    ) == EXPECTED_COUNTS
    assert {case["source_case_id"] for case in expanded} == {
        f"{prefix}-{number:03d}"
        for prefix, count in EXPECTED_COUNTS.items()
        for number in range(1, count + 1)
    }
    assert {case["status"] for case in expanded} == {"未执行及原因"}
    assert {case["reason"] for case in expanded} == {REASON}


def test_cases_envelope_rejects_missing_coverage_key() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    del envelope["coverage"]["registration_full"]

    with pytest.raises(ValueError, match=r"coverage.*registration_full"):
        contract.validate_cases_envelope(envelope)


def test_contract_exports_frozen_constants_and_typed_dicts() -> None:
    contract = _contract_module()

    assert contract.SCHEMA_VERSION == 1
    assert contract.STATUSES == ("通过", "失败", "未执行及原因")
    assert contract.COVERAGE_KEYS == EXPECTED_COVERAGE_KEYS
    assert contract.COVERAGE_EXPECTED == EXPECTED_COVERAGE_AUTHORITY
    assert contract.CASE_FIELDS == EXPECTED_CASE_FIELDS
    assert tuple(contract.Coverage.__annotations__) == ("expected", "observed", "passed")
    assert tuple(contract.CaseRecord.__annotations__) == EXPECTED_CASE_FIELDS
    assert get_type_hints(contract.Coverage) == {
        "expected": int,
        "observed": int,
        "passed": int,
    }
    assert get_type_hints(contract.CaseRecord) == {
        "case_id": str,
        "source_case_id": str,
        "case_kind": str,
        "run_id": str,
        "status": str,
        "started_at": str,
        "finished_at": str,
        "target": str,
        "command": str,
        "evidence": list[str],
        "reason": str,
        "mock": bool,
        "release_tag": str,
        "git_sha": str,
    }


def test_report_plan_freezes_registration_smoke_and_declaration_authority() -> None:
    document = _plan_document()

    assert set(document) == {"schema_version", "registration", "smoke", "declarations"}
    assert document["schema_version"] == 1
    assert document["registration"] == {
        "profiles": ["gpu0", "gpu1", "gpu2", "cpu"],
        "require_full": True,
        "require_gpu_recovery_instances": True,
        "facerec_instances": ["facerec-gpu0", "facerec-gpu1", "facerec-gpu2"],
    }
    assert document["smoke"] == {
        "require_full": True,
        "require_gpu_linked_runs": True,
        "require_cpu_instances": True,
    }
    assert set(document["declarations"]) == {"reason", "negative", "load"}
    assert document["declarations"]["reason"] == REASON


@pytest.mark.parametrize(
    ("container", "mutation"),
    (
        ((), lambda value: _remove_key(value, "smoke")),
        ((), lambda value: _add_key(value, "unknown")),
        (("registration",), lambda value: _remove_key(value, "require_full")),
        (("registration",), lambda value: _add_key(value, "unknown")),
        (("smoke",), lambda value: _remove_key(value, "require_full")),
        (("smoke",), lambda value: _add_key(value, "unknown")),
        (("declarations",), lambda value: _remove_key(value, "reason")),
        (("declarations",), lambda value: _add_key(value, "unknown")),
        (
            ("declarations", "negative", 0),
            lambda value: _remove_key(value, "last"),
        ),
        (
            ("declarations", "load", 0),
            lambda value: _add_key(value, "unknown"),
        ),
    ),
)
def test_report_plan_rejects_missing_or_unknown_nested_fields(
    tmp_path: Path,
    container: tuple[str | int, ...],
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    contract = _contract_module()
    document = copy.deepcopy(_plan_document())
    target: Any = document
    for part in container:
        target = target[part]
    mutation(target)

    with pytest.raises(ValueError):
        contract.load_report_plan(_write_plan(tmp_path, document))


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: value.__setitem__("schema_version", 2),
        lambda value: value.__setitem__("schema_version", True),
        _set_registration_profile,
        _set_facerec_instance,
        _set_registration_bool,
        _set_smoke_bool,
        _set_reason,
    ),
)
def test_report_plan_rejects_noncanonical_fixed_values(
    tmp_path: Path, mutation: Callable[[dict[str, Any]], None]
) -> None:
    contract = _contract_module()
    document = copy.deepcopy(_plan_document())
    mutation(document)

    with pytest.raises(ValueError):
        contract.load_report_plan(_write_plan(tmp_path, document))


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: _set_range_prefix(value, "dep"),
        lambda value: _set_range_prefix(value, "DEP-"),
        lambda value: _set_range_prefix(value, ""),
        lambda value: _set_range_prefix(value, 1),
        lambda value: _set_range_bound(value, "first", True),
        lambda value: _set_range_bound(value, "last", False),
        lambda value: _set_range_bound(value, "first", 0),
        lambda value: _set_range_bound(value, "last", -1),
        lambda value: _set_range_bound(value, "first", 2),
        lambda value: _set_range_bound(value, "last", 21),
        _duplicate_prefix,
        _replace_load_prefix,
        _drop_declaration_range,
    ),
)
def test_report_plan_rejects_invalid_or_noncanonical_ranges(
    mutation: Callable[[dict[str, Any]], None]
) -> None:
    contract = _contract_module()
    document = copy.deepcopy(_plan_document())
    mutation(document)

    with pytest.raises(ValueError):
        contract.expand_declaration_cases(document)


def test_report_plan_loader_rejects_non_object_json(tmp_path: Path) -> None:
    contract = _contract_module()

    with pytest.raises(ValueError, match="object"):
        contract.load_report_plan(_write_plan(tmp_path, []))


def test_report_plan_bytes_loader_uses_strict_utf8_and_json() -> None:
    contract = _contract_module()

    assert contract.load_report_plan_bytes(PLAN_PATH.read_bytes()) == _plan_document()
    with pytest.raises(ValueError, match="UTF-8"):
        contract.load_report_plan_bytes(b"\xff")
    with pytest.raises(ValueError, match="duplicate JSON field: schema_version"):
        contract.load_report_plan_bytes(
            PLAN_PATH.read_bytes().replace(
                b'"schema_version": 1,',
                b'"schema_version": 2, "schema_version": 1,',
                1,
            )
        )


def test_strict_json_loads_exports_typed_reusable_parser() -> None:
    contract = _contract_module()

    assert get_type_hints(contract.strict_json_loads) == {
        "text": str,
        "return": object,
    }
    assert contract.strict_json_loads('{"values": [1, true, null]}') == {
        "values": [1, True, None]
    }


@pytest.mark.parametrize("value", (b"{}", bytearray(b"{}"), 1, None))
def test_strict_json_loads_rejects_non_string_input(value: Any) -> None:
    contract = _contract_module()

    with pytest.raises(ValueError, match="JSON text must be a string"):
        contract.strict_json_loads(value)


@pytest.mark.parametrize("constant", ("NaN", "Infinity", "-Infinity"))
def test_strict_json_loads_rejects_nonstandard_constants(constant: str) -> None:
    contract = _contract_module()

    with pytest.raises(ValueError, match="non-standard JSON constant") as exc_info:
        contract.strict_json_loads(constant)

    assert constant in str(exc_info.value)


@pytest.mark.parametrize("number", ("1e999", "-1e999"))
def test_strict_json_loads_rejects_float_overflow_to_non_finite(
    number: str,
) -> None:
    contract = _contract_module()

    with pytest.raises(ValueError, match="non-finite JSON number") as exc_info:
        contract.strict_json_loads(f'{{"value": {number}}}')

    assert number in str(exc_info.value)


def test_strict_json_loads_normalizes_excessive_nesting() -> None:
    contract = _contract_module()
    deeply_nested = "[" * 100_000 + "0" + "]" * 100_000

    with pytest.raises(ValueError, match="JSON nesting is too deep") as exc_info:
        contract.strict_json_loads(deeply_nested)

    assert isinstance(exc_info.value.__cause__, RecursionError)


@pytest.mark.parametrize("exception_type", (KeyboardInterrupt, MemoryError))
def test_strict_json_loads_does_not_swallow_fatal_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    exception_type: type[BaseException],
) -> None:
    contract = _contract_module()

    def raise_fatal(*args: object, **kwargs: object) -> None:
        raise exception_type("fatal")

    monkeypatch.setattr(contract.json, "loads", raise_fatal)

    with pytest.raises(exception_type, match="fatal"):
        contract.strict_json_loads("{}")


@pytest.mark.parametrize(
    ("key", "needle", "replacement"),
    (
        (
            "schema_version",
            '{\n  "schema_version": 1,',
            '{\n  "schema_version": 999,\n  "schema_version": 1,',
        ),
        (
            "require_full",
            '    "require_full": true,\n'
            '    "require_gpu_recovery_instances": true,',
            '    "require_full": false,\n'
            '    "require_full": true,\n'
            '    "require_gpu_recovery_instances": true,',
        ),
        (
            "prefix",
            '        "prefix": "DEP",\n        "first": 1,',
            '        "prefix": "EVIL",\n'
            '        "prefix": "DEP",\n'
            '        "first": 1,',
        ),
    ),
)
def test_report_plan_loader_rejects_duplicate_fields_at_any_depth(
    tmp_path: Path, key: str, needle: str, replacement: str
) -> None:
    contract = _contract_module()
    plan_text = PLAN_PATH.read_text(encoding="utf-8")
    assert plan_text.count(needle) == 1
    duplicate_plan = plan_text.replace(needle, replacement, 1)

    with pytest.raises(ValueError, match=rf"duplicate JSON field: {key}\b"):
        contract.load_report_plan(_write_raw_plan(tmp_path, duplicate_plan))


def test_cases_envelope_accepts_strict_valid_document() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()

    contract.validate_cases_envelope(envelope)

    assert len(envelope["cases"]) == 335


def test_cases_envelope_rejects_legacy_single_declaration_partial_document() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["coverage"] = {
        key: {"expected": 1, "observed": 1, "passed": 1}
        for key in EXPECTED_COVERAGE_KEYS
    }
    envelope["cases"] = [_valid_case()]

    with pytest.raises(ValueError, match=r"coverage\..*expected|authority"):
        contract.validate_cases_envelope(envelope)


def test_cases_envelope_rejects_execution_declaration_claiming_pass() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["cases"][0]["status"] = "通过"

    with pytest.raises(ValueError, match="execution_declaration.*status"):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("case_id", "DEP-002"),
        ("source_case_id", "LOAD-001"),
        ("case_kind", "negative"),
        ("run_id", "executed-run"),
        ("started_at", EVIDENCE_TIMESTAMP),
        ("finished_at", EVIDENCE_TIMESTAMP),
        ("target", "root@192.168.29.11"),
        ("command", "pytest"),
        ("evidence", ["negative/DEP-001.json"]),
        ("reason", "unit tests passed"),
        ("mock", True),
    ),
)
def test_cases_envelope_rejects_noncanonical_execution_declaration_fields(
    field: str,
    value: object,
) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["cases"][0][field] = value

    with pytest.raises(ValueError, match=field):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize("action", ("missing", "unknown"))
def test_cases_envelope_rejects_top_level_field_drift(action: str) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    if action == "missing":
        del envelope["plan_sha256"]
    else:
        envelope["unknown"] = "value"

    with pytest.raises(ValueError):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize("schema_version", (True, 0, 2, "1"))
def test_cases_envelope_rejects_wrong_schema_version(schema_version: object) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["schema_version"] = schema_version

    with pytest.raises(ValueError, match="schema_version"):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("release_tag", ""),
        ("release_tag", "release\nname"),
        ("release_tag", 1),
        ("git_sha", "A" * 40),
        ("git_sha", "a" * 39),
        ("git_sha", "g" * 40),
        ("plan_sha256", "B" * 64),
        ("plan_sha256", "b" * 63),
        ("plan_sha256", "z" * 64),
    ),
)
def test_cases_envelope_rejects_invalid_release_or_digest_metadata(
    field: str, value: object
) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope[field] = value

    with pytest.raises(ValueError, match=field):
        contract.validate_cases_envelope(envelope)


def test_cases_envelope_rejects_unknown_coverage_key() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["coverage"]["unexpected"] = {"expected": 0, "observed": 0, "passed": 0}

    with pytest.raises(ValueError, match=r"coverage.*unexpected"):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize("coverage_key", EXPECTED_COVERAGE_KEYS)
def test_cases_envelope_rejects_expected_coverage_authority_drift(
    coverage_key: str,
) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["coverage"][coverage_key]["expected"] += 1

    with pytest.raises(ValueError, match=rf"coverage\.{coverage_key}\.expected"):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize(
    "coverage_key",
    tuple(
        key for key in EXPECTED_COVERAGE_KEYS if key != "smoke_gpu_trigger"
    ),
)
def test_cases_envelope_requires_complete_observation_for_required_sources(
    coverage_key: str,
) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    coverage = envelope["coverage"][coverage_key]
    coverage["observed"] = coverage["expected"] - 1
    coverage["passed"] = min(coverage["passed"], coverage["observed"])

    with pytest.raises(ValueError, match=rf"coverage\.{coverage_key}\.observed"):
        contract.validate_cases_envelope(envelope)


def test_cases_envelope_allows_partial_gpu_trigger_observation() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    running_cases = [
        case for case in envelope["cases"] if case["case_kind"] == "gpu_running"
    ]
    smoke_cases = [
        case
        for case in envelope["cases"]
        if case["case_kind"] == "smoke_gpu_trigger"
    ]
    running_cases[0]["status"] = "失败"
    envelope["cases"].remove(smoke_cases[0])
    running_cases[1]["status"] = "失败"
    smoke_cases[1]["status"] = "失败"
    envelope["coverage"]["gpu_running"]["passed"] = 16
    envelope["coverage"]["smoke_gpu_trigger"] = {
        "expected": 18,
        "observed": 17,
        "passed": 16,
    }

    contract.validate_cases_envelope(envelope)


def test_cases_envelope_allows_real_failures_to_lower_passed_coverage() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    kind_by_coverage = {
        "registration_full": "registration_full",
        "registration_profiles": "registration_profile",
        "registration_recovery": "registration_recovery",
        "registration_facerec": "registration_facerec",
        "gpu_stopped": "gpu_stopped",
        "smoke_full": "smoke_full",
        "smoke_cpu_instance": "smoke_cpu_instance",
    }
    for coverage_key, case_kind in kind_by_coverage.items():
        next(
            case for case in envelope["cases"] if case["case_kind"] == case_kind
        )["status"] = "失败"
        envelope["coverage"][coverage_key]["passed"] -= 1
    running = next(
        case for case in envelope["cases"] if case["case_kind"] == "gpu_running"
    )
    linked = next(
        case
        for case in envelope["cases"]
        if case["case_kind"] == "smoke_gpu_trigger"
        and (case["target"], case["run_id"])
        == (running["target"], running["run_id"])
    )
    running["status"] = "失败"
    linked["status"] = "失败"
    envelope["coverage"]["gpu_running"]["passed"] -= 1
    envelope["coverage"]["smoke_gpu_trigger"]["passed"] -= 1

    contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize(
    ("coverage_key", "case_kind"),
    (
        ("registration_full", "registration_full"),
        ("registration_profiles", "registration_profile"),
        ("registration_recovery", "registration_recovery"),
        ("registration_facerec", "registration_facerec"),
        ("gpu_running", "gpu_running"),
        ("gpu_stopped", "gpu_stopped"),
        ("smoke_gpu_trigger", "smoke_gpu_trigger"),
        ("smoke_cpu_instance", "smoke_cpu_instance"),
    ),
)
def test_cases_envelope_mock_pass_is_observed_but_never_passed(
    coverage_key: str,
    case_kind: str,
) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    case = next(
        item for item in envelope["cases"] if item["case_kind"] == case_kind
    )
    case["mock"] = True

    with pytest.raises(ValueError, match=rf"coverage\.{coverage_key}\.passed"):
        contract.validate_cases_envelope(envelope)

    envelope["coverage"][coverage_key]["passed"] -= 1
    if case_kind == "gpu_running":
        envelope["coverage"]["smoke_gpu_trigger"]["passed"] -= 1
    contract.validate_cases_envelope(envelope)


def test_cases_envelope_linked_gpu_smoke_pass_requires_real_running_case() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    running = next(
        case for case in envelope["cases"] if case["case_kind"] == "gpu_running"
    )
    linked = next(
        case
        for case in envelope["cases"]
        if case["case_kind"] == "smoke_gpu_trigger"
        and (case["target"], case["run_id"])
        == (running["target"], running["run_id"])
    )
    assert linked["mock"] is False
    assert running["status"] == linked["status"] == "通过"
    running["mock"] = True
    envelope["coverage"]["gpu_running"]["passed"] -= 1

    with pytest.raises(ValueError, match=r"coverage\.smoke_gpu_trigger\.passed"):
        contract.validate_cases_envelope(envelope)

    envelope["coverage"]["smoke_gpu_trigger"]["passed"] -= 1
    contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize(
    "coverage_key", ("negative_declarations", "load_declarations")
)
def test_cases_envelope_declaration_coverage_never_reports_passed(
    coverage_key: str,
) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["coverage"][coverage_key]["passed"] = 1

    with pytest.raises(ValueError, match=rf"coverage\.{coverage_key}\.passed"):
        contract.validate_cases_envelope(envelope)


def test_cases_envelope_rejects_one_missing_execution_declaration() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    removed = next(case for case in envelope["cases"] if case["case_id"] == "LOAD-026")
    envelope["cases"].remove(removed)
    assert removed["case_id"] == "LOAD-026"

    with pytest.raises(ValueError, match=r"declaration.*missing.*LOAD-026"):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize(
    "coverage",
    (
        [],
        {"expected": 1, "observed": 1},
        {"expected": 1, "observed": 1, "passed": 1, "extra": 0},
        {"expected": True, "observed": 1, "passed": 1},
        {"expected": 1, "observed": False, "passed": 0},
        {"expected": 1, "observed": 1, "passed": True},
        {"expected": -1, "observed": 0, "passed": 0},
        {"expected": 1, "observed": -1, "passed": 0},
        {"expected": 1, "observed": 0, "passed": -1},
        {"expected": 1.0, "observed": 1, "passed": 1},
        {"expected": 1, "observed": 2, "passed": 1},
        {"expected": 2, "observed": 1, "passed": 2},
    ),
)
def test_cases_envelope_rejects_invalid_coverage_object(coverage: object) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["coverage"]["registration_full"] = coverage

    with pytest.raises(ValueError, match="registration_full"):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize("cases", ({}, (), "cases", None))
def test_cases_envelope_requires_cases_list(cases: object) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["cases"] = cases

    with pytest.raises(ValueError, match="cases"):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize("action", ("missing", "unknown"))
def test_cases_envelope_rejects_case_field_drift(action: str) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    if action == "missing":
        del envelope["cases"][0]["run_id"]
    else:
        envelope["cases"][0]["unknown"] = "value"

    with pytest.raises(ValueError):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize(
    "field",
    (
        "case_id",
        "source_case_id",
        "case_kind",
        "run_id",
        "status",
        "started_at",
        "finished_at",
        "target",
        "command",
        "reason",
        "release_tag",
        "git_sha",
    ),
)
@pytest.mark.parametrize("value", ("", "contains\x00control", 7))
def test_cases_envelope_rejects_invalid_case_strings(field: str, value: object) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["cases"][0][field] = value

    with pytest.raises(ValueError, match=field):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize("status", ("PASS", "成功", "未执行", 1))
def test_cases_envelope_rejects_unknown_status(status: object) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["cases"][0]["status"] = status

    with pytest.raises(ValueError, match="status"):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize(
    "evidence",
    (
        "negative/DEP-001.json",
        ("negative/DEP-001.json",),
        [1],
        [""],
        ["negative/DEP-001.json\n"],
    ),
)
def test_cases_envelope_rejects_invalid_evidence(evidence: object) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["cases"][0]["evidence"] = evidence

    with pytest.raises(ValueError, match="evidence"):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize("mock", (0, 1, "false", None))
def test_cases_envelope_requires_real_bool_mock(mock: object) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["cases"][0]["mock"] = mock

    with pytest.raises(ValueError, match="mock"):
        contract.validate_cases_envelope(envelope)


def test_cases_envelope_rejects_duplicate_execution_declaration_case_id() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["cases"].append(copy.deepcopy(envelope["cases"][0]))

    with pytest.raises(ValueError, match=r"case_id.*DEP-001"):
        contract.validate_cases_envelope(envelope)


def test_cases_envelope_rejects_declaration_evidence_for_wrong_category() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    load_case = next(
        case for case in envelope["cases"] if case["case_id"] == "LOAD-001"
    )
    load_case["evidence"] = ["negative/cases.json"]

    with pytest.raises(ValueError, match=r"evidence.*load/cases\.json"):
        contract.validate_cases_envelope(envelope)


def test_cases_envelope_allows_extra_cpu_instance_smoke_history() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    canonical = next(
        case
        for case in envelope["cases"]
        if case["case_kind"] == "smoke_cpu_instance"
    )
    historical = copy.deepcopy(canonical)
    historical["case_id"] = "SMOKE-CPU-HISTORICAL"
    historical["run_id"] = "historical-failure"
    historical["status"] = "失败"
    historical["reason"] = "historical attempt failed"
    envelope["cases"].append(historical)

    contract.validate_cases_envelope(envelope)


def test_cases_envelope_allows_extra_gpu_instance_smoke_history() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    canonical = next(
        case
        for case in envelope["cases"]
        if case["case_kind"] == "smoke_gpu_trigger"
    )
    historical = copy.deepcopy(canonical)
    historical["case_id"] = "SMOKE-GPU-HISTORICAL"
    historical["run_id"] = "historical-failure"
    historical["status"] = "失败"
    historical["reason"] = "historical attempt failed"
    envelope["cases"].append(historical)

    contract.validate_cases_envelope(envelope)


def test_cases_envelope_rejects_missing_all_real_source_cases() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["cases"] = [
        case
        for case in envelope["cases"]
        if case["case_kind"] == "execution_declaration"
    ]

    with pytest.raises(ValueError, match="registration_full|real source|coverage"):
        contract.validate_cases_envelope(envelope)


def test_cases_envelope_rejects_coverage_passed_drift_from_case_statuses() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["coverage"]["registration_profiles"]["passed"] -= 1

    with pytest.raises(ValueError, match=r"coverage\.registration_profiles\.passed"):
        contract.validate_cases_envelope(envelope)


def test_cases_envelope_rejects_duplicate_gpu_running_target() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    running = [
        case for case in envelope["cases"] if case["case_kind"] == "gpu_running"
    ]
    running[1]["target"] = running[0]["target"]

    with pytest.raises(ValueError, match="gpu_running.*target|GPU target"):
        contract.validate_cases_envelope(envelope)


def test_cases_envelope_rejects_gpu_smoke_linked_to_wrong_run() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    linked = next(
        case
        for case in envelope["cases"]
        if case["case_kind"] == "smoke_gpu_trigger"
    )
    linked["run_id"] = "wrong-run"

    with pytest.raises(ValueError, match="gpu_running|matching|linked"):
        contract.validate_cases_envelope(envelope)


def test_cases_envelope_rejects_unknown_case_kind() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    case = next(
        case for case in envelope["cases"] if case["case_kind"] == "registration_full"
    )
    case["case_kind"] = "registration_unknown"

    with pytest.raises(ValueError, match="case_kind.*unknown|unknown case_kind"):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize("field", ("release_tag", "git_sha"))
def test_cases_envelope_preserves_release_provenance(field: str) -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["cases"][0][field] = "b" * 40 if field == "git_sha" else "other-release"

    with pytest.raises(ValueError, match=field):
        contract.validate_cases_envelope(envelope)


def test_aggregator_requires_exact_registration_coverage(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    missing = (
        release_tree.root
        / "registration"
        / "operator-registration-profile-gpu2.json"
    )
    missing.unlink()

    with pytest.raises(ValueError, match=missing.name):
        release_tree.collect_registration_gpu()

    assert not (release_tree.root / "summary" / "cases.json").exists()


def test_aggregator_rejects_gpu_target_drift(release_tree: ReleaseTree) -> None:
    release_tree.write_complete_sources()
    evidence = release_tree.root / "gpu-instances" / "asr-offline-gpu0.json"
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    payload["target"]["physical_gpu"] = 2
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="physical_gpu"):
        release_tree.collect_registration_gpu()

    assert not (release_tree.root / "summary" / "cases.json").exists()


def test_aggregator_pairs_stopped_and_recovery_registration(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()

    cases, coverage = release_tree.collect_registration_gpu()

    recovery = [
        case for case in cases if case["case_kind"] == "registration_recovery"
    ]
    assert len(recovery) == 18
    assert {case["target"] for case in recovery} == set(release_tree.gpu_instances)
    assert {case["case_id"] for case in recovery} == {
        f"REG-RECOVERY-{instance_id}"
        for instance_id in release_tree.gpu_instances
    }
    assert coverage["registration_recovery"] == {
        "expected": 18,
        "observed": 18,
        "passed": 18,
    }
    assert not (release_tree.root / "summary" / "cases.json").exists()


def test_operator_inventory_has_exact_frozen_types_and_topology() -> None:
    aggregate = _aggregate_module()

    inventory = aggregate.load_operator_inventory(COMPOSE_PATH)

    assert is_dataclass(aggregate.OperatorInstance)
    assert is_dataclass(aggregate.OperatorInventory)
    assert aggregate.OperatorInstance.__dataclass_params__.frozen is True
    assert aggregate.OperatorInventory.__dataclass_params__.frozen is True
    assert tuple(field.name for field in fields(aggregate.OperatorInstance)) == (
        "service_name",
        "instance_id",
        "operator_code",
        "profile",
        "physical_gpu",
        "process_name",
    )
    assert tuple(field.name for field in fields(aggregate.OperatorInventory)) == (
        "instances",
    )
    assert get_type_hints(aggregate.OperatorInstance) == {
        "service_name": str,
        "instance_id": str,
        "operator_code": str,
        "profile": str,
        "physical_gpu": int | None,
        "process_name": str | None,
    }
    assert get_type_hints(aggregate.OperatorInventory) == {
        "instances": tuple[aggregate.OperatorInstance, ...]
    }
    assert get_type_hints(aggregate.load_operator_inventory) == {
        "path": Path,
        "return": aggregate.OperatorInventory,
    }
    assert len(inventory.instances) == 24
    assert len(inventory.gpu_instances) == 18
    assert len(inventory.cpu_instances) == 6
    assert tuple(item.instance_id for item in inventory.instances) == tuple(
        sorted(item.instance_id for item in inventory.instances)
    )
    assert all(item.operator_code == item.process_name for item in inventory.gpu_instances)
    assert {
        (item.operator_code, item.process_name, item.physical_gpu)
        for item in inventory.cpu_instances
    } == {
        ("ppt_slice", None, None),
        ("text_analysis", None, None),
    }
    with pytest.raises(FrozenInstanceError):
        inventory.instances[0].profile = "cpu"


def test_registration_gpu_collector_has_exact_keyword_only_signature() -> None:
    aggregate = _aggregate_module()
    signature = inspect.signature(aggregate.collect_registration_gpu_cases)

    assert tuple(signature.parameters) == (
        "release_root",
        "inventory",
        "report_plan",
        "release_tag",
        "git_sha",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    hints = get_type_hints(aggregate.collect_registration_gpu_cases)
    assert hints == {
        "release_root": Path,
        "inventory": aggregate.OperatorInventory,
        "report_plan": dict[str, Any],
        "release_tag": str,
        "git_sha": str,
        "return": tuple[
            list[_contract_module().CaseRecord],
            dict[str, _contract_module().Coverage],
        ],
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda document: document.__setitem__("services", []), "services.*object"),
        (
            lambda document: document["services"]["asr-offline-gpu0"].__setitem__(
                "environment", []
            ),
            "environment.*object",
        ),
        (lambda document: document["services"].pop("asr-offline-gpu0"), "24"),
        (
            lambda document: document["services"]["asr-offline-gpu0"].__setitem__(
                "profiles", []
            ),
            "exactly one profile",
        ),
        (
            lambda document: document["services"]["asr-offline-gpu0"][
                "environment"
            ].__setitem__("PLATFORM_INSTANCE_ID", "renamed-gpu0"),
            "does not match",
        ),
        (
            lambda document: document["services"]["asr-online-gpu0"][
                "environment"
            ].__setitem__("PLATFORM_INSTANCE_ID", "asr-offline-gpu0"),
            "duplicate",
        ),
        (
            lambda document: document["services"]["asr-offline-gpu0"][
                "environment"
            ].__setitem__("PLATFORM_GPU_ID", "2"),
            "profile.*PLATFORM_GPU_ID",
        ),
    ),
)
def test_operator_inventory_rejects_structural_drift(
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], object],
    message: str,
) -> None:
    aggregate = _aggregate_module()
    document = _compose_document()
    mutation(document)

    with pytest.raises(ValueError, match=message):
        aggregate.load_operator_inventory(_write_compose(tmp_path, document))


@pytest.mark.parametrize(
    "field",
    ("PLATFORM_GPU_ID", "GPU_PROCESS_NAME", "NVIDIA_VISIBLE_DEVICES", "REQUIRE_GPU"),
)
def test_operator_inventory_rejects_every_gpu_field_on_cpu(
    tmp_path: Path, field: str
) -> None:
    aggregate = _aggregate_module()
    document = _compose_document()
    document["services"]["ppt-slice-cpu0"]["environment"][field] = "0"

    with pytest.raises(ValueError, match=field):
        aggregate.load_operator_inventory(_write_compose(tmp_path, document))


def test_registration_paths_are_canonical_and_face_hash_is_stable() -> None:
    aggregate = _aggregate_module()
    inventory = aggregate.load_operator_inventory(COMPOSE_PATH)

    paths = aggregate.registration_paths(inventory)

    assert paths["full"] == Path("registration/operator-registration.json")
    assert paths["facerec"] == Path(
        "registration/operator-registration-instances-784a68323268.json"
    )
    assert {
        key: path
        for key, path in paths.items()
        if key.startswith("profile:")
    } == {
        f"profile:{profile}": Path(
            f"registration/operator-registration-profile-{profile}.json"
        )
        for profile in ("gpu0", "gpu1", "gpu2", "cpu")
    }
    assert len([key for key in paths if key.startswith("recovery:")]) == 18


def test_complete_registration_gpu_sources_generate_exact_cases(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()

    cases, coverage = release_tree.collect_registration_gpu()

    assert len(cases) == 60
    assert Counter(case["case_kind"] for case in cases) == {
        "registration_full": 1,
        "registration_profile": 4,
        "registration_facerec": 1,
        "registration_recovery": 18,
        "gpu_running": 18,
        "gpu_stopped": 18,
    }
    assert coverage == {
        "registration_full": {"expected": 1, "observed": 1, "passed": 1},
        "registration_profiles": {"expected": 4, "observed": 4, "passed": 4},
        "registration_facerec": {"expected": 1, "observed": 1, "passed": 1},
        "registration_recovery": {"expected": 18, "observed": 18, "passed": 18},
        "gpu_running": {"expected": 18, "observed": 18, "passed": 18},
        "gpu_stopped": {"expected": 18, "observed": 18, "passed": 18},
    }
    gpu_cases = [case for case in cases if case["case_kind"].startswith("gpu_")]
    assert all(
        case["started_at"] == case["finished_at"] == EVIDENCE_TIMESTAMP
        for case in gpu_cases
    )
    assert not (release_tree.root / "summary" / "cases.json").exists()


@pytest.mark.parametrize(
    ("path", "value", "message"),
    (
        (("schema_version",), True, "schema_version"),
        (("evidence_type",), "operator_smoke", "evidence_type"),
        (("mock",), True, "mock"),
        (("release_tag",), "other-release", "release_tag"),
        (("git_sha",), "b" * 40, "git_sha"),
        (("target",), "other-registry", "target"),
        (("selection", "values"), ["gpu0"], "selection"),
        (("summary", "expected"), 23, "summary.expected"),
        (("summary", "observed"), 23, "expected=observed=valid"),
        (("status",), "PASS", "status"),
    ),
)
def test_registration_evidence_rejects_contract_drift(
    release_tree: ReleaseTree,
    path: tuple[str, ...],
    value: object,
    message: str,
) -> None:
    release_tree.write_complete_sources()
    relative = "registration/operator-registration.json"
    payload = release_tree.read_json(relative)
    _set_nested(payload, path, value)
    release_tree.replace_json(relative, payload)

    with pytest.raises(ValueError, match=message):
        release_tree.collect_registration_gpu()


def test_failed_registration_is_observed_but_not_passed(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    instance_id = release_tree.gpu_instances[0]
    relative = f"registration/operator-registration-instance-{instance_id}.json"
    payload = release_tree.read_json(relative)
    payload["status"] = "失败"
    payload["summary"]["valid"] = 0
    payload["issues"] = ["instance did not recover"]
    release_tree.replace_json(relative, payload)

    cases, coverage = release_tree.collect_registration_gpu()

    recovery = next(
        case for case in cases if case["case_id"] == f"REG-RECOVERY-{instance_id}"
    )
    assert recovery["status"] == "失败"
    assert recovery["reason"] == "instance did not recover"
    assert coverage["registration_recovery"] == {
        "expected": 18,
        "observed": 18,
        "passed": 17,
    }


@pytest.mark.parametrize("payload_observed", (23, 25))
def test_failed_registration_preserves_observed_count_drift(
    release_tree: ReleaseTree,
    payload_observed: int,
) -> None:
    release_tree.write_complete_sources()
    relative = "registration/operator-registration.json"
    payload = release_tree.read_json(relative)
    payload["status"] = "失败"
    payload["summary"] = {
        "expected": 24,
        "observed": payload_observed,
        "valid": 0,
    }
    payload["issues"] = ["registration topology drift"]
    release_tree.replace_json(relative, payload)

    cases, coverage = release_tree.collect_registration_gpu()

    case = next(item for item in cases if item["case_id"] == "REG-FULL")
    assert case["status"] == "失败"
    assert coverage["registration_full"] == {
        "expected": 1,
        "observed": 1,
        "passed": 0,
    }


def test_failed_registration_requires_nonempty_issues(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    relative = "registration/operator-registration.json"
    payload = release_tree.read_json(relative)
    payload["status"] = "失败"
    payload["summary"]["valid"] = 0
    payload["issues"] = []
    release_tree.replace_json(relative, payload)

    with pytest.raises(ValueError, match="issues"):
        release_tree.collect_registration_gpu()


@pytest.mark.parametrize("status", ([], {}))
def test_registration_rejects_non_string_status_as_value_error(
    release_tree: ReleaseTree,
    status: object,
) -> None:
    release_tree.write_complete_sources()
    relative = "registration/operator-registration.json"
    payload = release_tree.read_json(relative)
    payload["status"] = status
    release_tree.replace_json(relative, payload)

    with pytest.raises(ValueError, match="status"):
        release_tree.collect_registration_gpu()


@pytest.mark.parametrize(
    ("relative", "raw_text", "message"),
    (
        (
            "registration/operator-registration.json",
            '{"schema_version":1,"schema_version":1}',
            "duplicate JSON field",
        ),
        (
            "gpu-instances/asr-offline-gpu0.json",
            '{"schema_version":1,"value":NaN}',
            "non-standard JSON constant",
        ),
        (
            "gpu-instances/asr-offline-gpu0.json",
            '{"schema_version":1,"value":1e999}',
            "non-finite JSON number",
        ),
        ("registration/operator-registration.json", "[]", "must be an object"),
    ),
)
def test_release_evidence_uses_strict_json_parser(
    release_tree: ReleaseTree,
    relative: str,
    raw_text: str,
    message: str,
) -> None:
    release_tree.write_complete_sources()
    (release_tree.root / relative).write_text(raw_text, encoding="utf-8")

    with pytest.raises(ValueError, match=message) as exc_info:
        release_tree.collect_registration_gpu()

    assert Path(relative).name in str(exc_info.value)


@pytest.mark.parametrize("source_kind", ("symlink", "directory"))
def test_release_evidence_rejects_non_regular_sources(
    release_tree: ReleaseTree, source_kind: str
) -> None:
    release_tree.write_complete_sources()
    source = release_tree.root / "registration" / "operator-registration.json"
    source.unlink()
    if source_kind == "symlink":
        outside = release_tree.root.parent / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        source.symlink_to(outside)
    else:
        source.mkdir()

    with pytest.raises(ValueError, match="regular non-symlink file"):
        release_tree.collect_registration_gpu()

    assert not (release_tree.root / "summary" / "cases.json").exists()


def test_release_evidence_rejects_path_outside_release_root(
    release_tree: ReleaseTree,
) -> None:
    release_tree.root.mkdir(parents=True)
    aggregate = _aggregate_module()

    with pytest.raises(ValueError, match="escapes release root"):
        aggregate._read_release_text(release_tree.root, Path("../outside.json"))


def test_release_evidence_rejects_parent_replacement_during_open(
    release_tree: ReleaseTree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_tree.write_full_smoke()
    aggregate = _aggregate_module()
    smoke_root = release_tree.root / "smoke"
    original_smoke_root = release_tree.root / "smoke-original"
    outside_smoke_root = release_tree.root.parent / "outside-smoke"
    outside_smoke_root.mkdir()
    os.link(
        smoke_root / "cases.json",
        outside_smoke_root / "cases.json",
    )
    real_open = os.open
    replaced = False

    def replace_parent_then_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes] | int,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal replaced
        old_style_open = (
            dir_fd is None
            and not isinstance(path, int)
            and Path(path) == smoke_root / "cases.json"
        )
        dir_fd_open = dir_fd is not None and path == "smoke"
        if not replaced and (old_style_open or dir_fd_open):
            smoke_root.rename(original_smoke_root)
            smoke_root.symlink_to(outside_smoke_root, target_is_directory=True)
            replaced = True
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(aggregate.os, "open", replace_parent_then_open)

    with pytest.raises(ValueError, match="release source|unsafe|changed"):
        aggregate._read_release_text(release_tree.root, Path("smoke/cases.json"))

    assert replaced


def _open_descriptor_count() -> int | None:
    for directory in (Path("/proc/self/fd"), Path("/dev/fd")):
        try:
            return len(os.listdir(directory))
        except OSError:
            continue
    return None


def test_release_evidence_rejection_does_not_leak_file_descriptors(
    release_tree: ReleaseTree,
) -> None:
    unsafe_parent = release_tree.root / "smoke" / "unsafe"
    unsafe_parent.parent.mkdir(parents=True)
    outside = release_tree.root.parent / "outside"
    outside.mkdir()
    unsafe_parent.symlink_to(outside, target_is_directory=True)
    aggregate = _aggregate_module()
    before = _open_descriptor_count()
    if before is None:
        pytest.skip("the platform does not expose an fd directory")

    for _ in range(100):
        with pytest.raises(ValueError, match="release source|unsafe|missing"):
            aggregate._read_release_text(
                release_tree.root, Path("smoke/unsafe/cases.json")
            )

    assert _open_descriptor_count() == before


@pytest.mark.parametrize(
    ("side", "path", "value", "message"),
    (
        ("running", ("schema_version",), True, "schema_version"),
        ("running", ("timestamp",), "", "timestamp"),
        ("running", ("commands",), [], "commands"),
        ("running", ("mode",), "assert-stopped", "mode"),
        ("stopped", ("mode",), "running-inference", "mode"),
        ("running", ("target", "instance_id"), "ocr-gpu0", "instance_id"),
        ("stopped", ("target", "physical_gpu"), 2, "physical_gpu"),
        ("running", ("target", "process_name"), "ocr", "process_name"),
        ("running", ("status",), "通过", "status"),
        ("running", ("release_sha",), "b" * 40, "release_sha"),
    ),
)
def test_gpu_pair_rejects_core_contract_drift(
    release_tree: ReleaseTree,
    side: str,
    path: tuple[str, ...],
    value: object,
    message: str,
) -> None:
    release_tree.write_all_gpu_pairs()
    running = release_tree.read_json("gpu-instances/asr-offline-gpu0.json")
    stopped = release_tree.read_json("recovery/asr-offline-gpu0-stopped.json")
    target = running if side == "running" else stopped
    _set_nested(target, path, value)

    with pytest.raises(ValueError, match=message):
        _validate_gpu_pair(release_tree, running, stopped)


@pytest.mark.parametrize(
    "field", ("release_sha", "container", "gpu", "activity", "synchronous_samples")
)
def test_running_pass_requires_complete_runtime_identity(
    release_tree: ReleaseTree, field: str
) -> None:
    release_tree.write_all_gpu_pairs()
    running = release_tree.read_json("gpu-instances/asr-offline-gpu0.json")
    stopped = release_tree.read_json("recovery/asr-offline-gpu0-stopped.json")
    del running[field]

    with pytest.raises(ValueError, match=field):
        _validate_gpu_pair(release_tree, running, stopped)


def test_running_pass_requires_at_least_one_mapped_host_pid(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_all_gpu_pairs()
    running = release_tree.read_json("gpu-instances/asr-offline-gpu0.json")
    stopped = release_tree.read_json("recovery/asr-offline-gpu0-stopped.json")
    for sample in running["synchronous_samples"]:
        sample["processes"] = []

    with pytest.raises(ValueError, match="host_pid"):
        _validate_gpu_pair(release_tree, running, stopped)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda process: process.pop("host_pid"), "host_pid"),
        (lambda process: process.pop("process_name"), "process_name"),
        (lambda process: process.pop("mapping"), "mapping"),
        (
            lambda process: process["mapping"].__setitem__("docker_top", False),
            "docker_top",
        ),
        (
            lambda process: process["mapping"].__setitem__(
                "cgroup_full_container_id", False
            ),
            "cgroup_full_container_id",
        ),
        (
            lambda process: process["mapping"].__setitem__("docker_top", 1),
            "docker_top",
        ),
        (
            lambda process: process["mapping"].__setitem__(
                "cgroup_full_container_id", "true"
            ),
            "cgroup_full_container_id",
        ),
        (lambda process: process["mapping"].pop("nspid"), "nspid"),
        (
            lambda process: process["mapping"].__setitem__("nspid", [20_001, 42]),
            "nspid",
        ),
        (lambda process: process.pop("container_pid"), "container_pid"),
        (lambda process: process.__setitem__("container_pid", 43), "container_pid"),
    ),
)
def test_running_pid_requires_proven_container_mapping(
    release_tree: ReleaseTree,
    mutation: Callable[[dict[str, Any]], object],
    message: str,
) -> None:
    release_tree.write_all_gpu_pairs()
    running = release_tree.read_json("gpu-instances/asr-offline-gpu0.json")
    stopped = release_tree.read_json("recovery/asr-offline-gpu0-stopped.json")
    process = running["synchronous_samples"][0]["processes"][0]
    mutation(process)

    with pytest.raises(ValueError, match=message):
        _validate_gpu_pair(release_tree, running, stopped)


def test_stopped_pass_requires_explicit_prior_cuda_pids(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_all_gpu_pairs()
    running = release_tree.read_json("gpu-instances/asr-offline-gpu0.json")
    stopped = release_tree.read_json("recovery/asr-offline-gpu0-stopped.json")
    del stopped["prior_cuda_pids"]

    with pytest.raises(ValueError, match="prior_cuda_pids"):
        _validate_gpu_pair(release_tree, running, stopped)


@pytest.mark.parametrize(
    ("side", "status"),
    (("running", []), ("running", {}), ("stopped", []), ("stopped", {})),
)
def test_gpu_rejects_non_string_status_as_value_error(
    release_tree: ReleaseTree,
    side: str,
    status: object,
) -> None:
    release_tree.write_all_gpu_pairs()
    running = release_tree.read_json("gpu-instances/asr-offline-gpu0.json")
    stopped = release_tree.read_json("recovery/asr-offline-gpu0-stopped.json")
    target = running if side == "running" else stopped
    target["status"] = status

    with pytest.raises(ValueError, match="status"):
        _validate_gpu_pair(release_tree, running, stopped)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    (
        (("release_sha",), None, "release_sha"),
        (("container", "id"), "different-container", "container"),
        (("gpu", "physical_uuid"), "GPU-DIFFERENT", "gpu"),
        (("prior_cuda_pids",), [99_999], "prior_cuda_pids"),
        (("remaining_cuda_pids",), [20_000], "remaining_cuda_pids"),
    ),
)
def test_stopped_pass_requires_matching_running_identity_and_no_pids(
    release_tree: ReleaseTree,
    path: tuple[str, ...],
    value: object,
    message: str,
) -> None:
    release_tree.write_all_gpu_pairs()
    running = release_tree.read_json("gpu-instances/asr-offline-gpu0.json")
    stopped = release_tree.read_json("recovery/asr-offline-gpu0-stopped.json")
    if value is None:
        del stopped[path[0]]
    else:
        _set_nested(stopped, path, value)

    with pytest.raises(ValueError, match=message):
        _validate_gpu_pair(release_tree, running, stopped)


def test_running_fail_accepts_producer_activity_without_run_id_and_is_observed(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    instance_id = "asr-offline-gpu0"
    running_relative = f"gpu-instances/{instance_id}.json"
    stopped_relative = f"recovery/{instance_id}-stopped.json"
    running = release_tree.read_json(running_relative)
    stopped = release_tree.read_json(stopped_relative)

    running["status"] = "FAIL"
    running["reason"] = "activity protocol failed before run_id"
    running["activity"] = _producer_partial_activity()
    for field in ("release_sha", "container", "gpu", "synchronous_samples"):
        running.pop(field)
    stopped["status"] = "FAIL"
    stopped["reason"] = "prior running evidence did not pass"
    for field in (
        "release_sha",
        "container",
        "gpu",
        "prior_cuda_pids",
        "remaining_cuda_pids",
    ):
        stopped.pop(field)

    _validate_gpu_pair(release_tree, running, stopped)
    release_tree.replace_json(running_relative, running)
    release_tree.replace_json(stopped_relative, stopped)

    cases, coverage = release_tree.collect_registration_gpu()

    gpu_cases = {
        case["case_id"]: case
        for case in cases
        if case["case_id"]
        in {f"GPU-RUN-{instance_id}", f"GPU-STOP-{instance_id}"}
    }
    assert gpu_cases[f"GPU-RUN-{instance_id}"]["status"] == "失败"
    assert gpu_cases[f"GPU-RUN-{instance_id}"]["run_id"] == f"gpu-{instance_id}"
    assert gpu_cases[f"GPU-STOP-{instance_id}"]["status"] == "失败"
    assert gpu_cases[f"GPU-STOP-{instance_id}"]["run_id"] == f"gpu-{instance_id}"
    assert coverage["gpu_running"] == {
        "expected": 18,
        "observed": 18,
        "passed": 17,
    }
    assert coverage["gpu_stopped"] == {
        "expected": 18,
        "observed": 18,
        "passed": 17,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("instance_id", "ocr-gpu0"),
        ("operator_code", "ocr"),
    ),
)
def test_running_fail_partial_activity_rejects_identity_drift(
    release_tree: ReleaseTree,
    field: str,
    value: str,
) -> None:
    release_tree.write_all_gpu_pairs()
    running = release_tree.read_json("gpu-instances/asr-offline-gpu0.json")
    stopped = release_tree.read_json("recovery/asr-offline-gpu0-stopped.json")
    running["status"] = "FAIL"
    running["reason"] = "activity protocol failed"
    running["activity"] = _producer_partial_activity()
    running["activity"][field] = value
    stopped["status"] = "FAIL"
    stopped["reason"] = "stopped check failed"

    with pytest.raises(ValueError, match=field):
        _validate_gpu_pair(release_tree, running, stopped)


@pytest.mark.parametrize("run_id", ("", [], "run\x00id"))
def test_running_fail_partial_activity_rejects_invalid_present_run_id(
    release_tree: ReleaseTree,
    run_id: object,
) -> None:
    release_tree.write_all_gpu_pairs()
    running = release_tree.read_json("gpu-instances/asr-offline-gpu0.json")
    stopped = release_tree.read_json("recovery/asr-offline-gpu0-stopped.json")
    running["status"] = "FAIL"
    running["reason"] = "activity protocol failed"
    running["activity"] = _producer_partial_activity()
    running["activity"]["run_id"] = run_id
    stopped["status"] = "FAIL"
    stopped["reason"] = "stopped check failed"

    with pytest.raises(ValueError, match="run_id"):
        _validate_gpu_pair(release_tree, running, stopped)


def test_running_pass_requires_activity_run_id(release_tree: ReleaseTree) -> None:
    release_tree.write_all_gpu_pairs()
    running = release_tree.read_json("gpu-instances/asr-offline-gpu0.json")
    stopped = release_tree.read_json("recovery/asr-offline-gpu0-stopped.json")
    running["activity"].pop("run_id")

    with pytest.raises(ValueError, match="run_id"):
        _validate_gpu_pair(release_tree, running, stopped)


def test_gpu_fail_evidence_may_omit_unavailable_runtime_fields(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    instance_id = "asr-offline-gpu0"
    for relative in (
        f"gpu-instances/{instance_id}.json",
        f"recovery/{instance_id}-stopped.json",
    ):
        payload = release_tree.read_json(relative)
        payload["status"] = "FAIL"
        payload["reason"] = "runtime evidence unavailable"
        for field in (
            "release_sha",
            "container",
            "gpu",
            "activity",
            "synchronous_samples",
            "prior_cuda_pids",
            "remaining_cuda_pids",
        ):
            payload.pop(field, None)
        release_tree.replace_json(relative, payload)

    cases, coverage = release_tree.collect_registration_gpu()

    target_cases = [
        case
        for case in cases
        if case["case_id"]
        in {f"GPU-RUN-{instance_id}", f"GPU-STOP-{instance_id}"}
    ]
    assert {case["status"] for case in target_cases} == {"失败"}
    assert coverage["gpu_running"] == {
        "expected": 18,
        "observed": 18,
        "passed": 17,
    }
    assert coverage["gpu_stopped"] == {
        "expected": 18,
        "observed": 18,
        "passed": 17,
    }


def test_gpu_fail_rejects_missing_reason_and_present_identity_drift(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_all_gpu_pairs()
    running = release_tree.read_json("gpu-instances/asr-offline-gpu0.json")
    stopped = release_tree.read_json("recovery/asr-offline-gpu0-stopped.json")
    running["status"] = "FAIL"
    running["reason"] = ""
    with pytest.raises(ValueError, match="reason"):
        _validate_gpu_pair(release_tree, running, stopped)

    running["reason"] = "failed"
    running["container"]["instance_id"] = "ocr-gpu0"
    stopped["status"] = "FAIL"
    stopped["reason"] = "stopped check failed"
    with pytest.raises(ValueError, match="container.instance_id"):
        _validate_gpu_pair(release_tree, running, stopped)


def test_task4_cli_publishes_complete_deterministic_envelope(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()

    completed = release_tree.run_aggregator()

    assert completed.returncode == 0, completed.stderr
    output = release_tree.root / "summary" / "cases.json"
    first_bytes = output.read_bytes()
    envelope = json.loads(first_bytes)
    _contract_module().validate_cases_envelope(envelope)
    assert set(envelope) == {
        "schema_version",
        "release_tag",
        "git_sha",
        "plan_sha256",
        "coverage",
        "cases",
    }
    assert envelope["schema_version"] == 1
    assert envelope["release_tag"] == release_tree.release_tag
    assert envelope["git_sha"] == release_tree.git_sha
    assert envelope["plan_sha256"] == hashlib.sha256(
        release_tree.report_plan_path.read_bytes()
    ).hexdigest()
    assert envelope["coverage"] == {
        "registration_full": {"expected": 1, "observed": 1, "passed": 1},
        "registration_profiles": {"expected": 4, "observed": 4, "passed": 4},
        "registration_recovery": {
            "expected": 18,
            "observed": 18,
            "passed": 18,
        },
        "registration_facerec": {"expected": 1, "observed": 1, "passed": 1},
        "gpu_running": {"expected": 18, "observed": 18, "passed": 18},
        "gpu_stopped": {"expected": 18, "observed": 18, "passed": 18},
        "smoke_full": {"expected": 8, "observed": 8, "passed": 8},
        "smoke_gpu_trigger": {
            "expected": 18,
            "observed": 18,
            "passed": 18,
        },
        "smoke_cpu_instance": {"expected": 6, "observed": 6, "passed": 6},
        "negative_declarations": {
            "expected": 217,
            "observed": 217,
            "passed": 0,
        },
        "load_declarations": {"expected": 26, "observed": 26, "passed": 0},
    }
    assert len(envelope["cases"]) == 335

    def sort_key(case: dict[str, Any]) -> tuple[int, str, str, str]:
        kind = case["case_kind"]
        evidence = case["evidence"]
        if kind.startswith("registration_"):
            group = 0
        elif kind == "gpu_running":
            group = 1
        elif kind == "gpu_stopped":
            group = 2
        elif kind == "smoke_full":
            group = 3
        elif kind in {"smoke_gpu_trigger", "smoke_cpu_instance"}:
            group = 4
        elif evidence == ["negative/cases.json"]:
            group = 5
        elif evidence == ["load/cases.json"]:
            group = 6
        else:
            raise AssertionError(f"unknown case source: {case}")
        return group, case["target"], case["run_id"], case["case_id"]

    assert envelope["cases"] == sorted(envelope["cases"], key=sort_key)
    assert len({case["case_id"] for case in envelope["cases"]}) == 335

    rerun = release_tree.run_aggregator()
    assert rerun.returncode == 0, rerun.stderr
    assert output.read_bytes() == first_bytes


def test_task3_cli_validates_gpu_smoke_link_before_failing_closed(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    running_path = "gpu-instances/asr-offline-gpu0.json"
    running = release_tree.read_json(running_path)
    running["activity"]["run_id"] = "missing-run"
    release_tree.replace_json(running_path, running)

    completed = release_tree.run_aggregator()

    assert completed.returncode != 0
    assert "missing-run" in completed.stderr
    assert "Declaration coverage" not in completed.stderr
    assert not (release_tree.root / "summary").exists()
    assert not (release_tree.root / "negative").exists()
    assert not (release_tree.root / "load").exists()


def _smoke_manifest_document() -> dict[str, Any]:
    loaded = json.loads(SMOKE_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _write_smoke_manifest(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "operator-smoke-cases.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def test_smoke_manifest_loader_matches_the_real_compose_operator_set() -> None:
    aggregate = _aggregate_module()
    manifest = aggregate.load_smoke_manifest(SMOKE_MANIFEST_PATH)
    inventory = aggregate.load_operator_inventory(COMPOSE_PATH)

    assert len(manifest) == 8
    assert {case["operator_code"] for case in manifest} == {
        instance.operator_code for instance in inventory.instances
    }
    assert manifest == _smoke_manifest_document()["cases"]


@pytest.mark.parametrize(
    "mutation",
    (
        lambda document: document.__setitem__("schema_version", True),
        lambda document: document.__setitem__("unknown", "value"),
        lambda document: document.pop("cases"),
        lambda document: document["cases"].pop(),
        lambda document: document["cases"][0].__setitem__("case_id", 7),
        lambda document: document["cases"][0].__setitem__("operator_code", False),
        lambda document: document["cases"][0].__setitem__("fixtures", "fixture"),
        lambda document: document["cases"][0].__setitem__("checks", [True]),
        lambda document: document["cases"][0]["fixtures"].append("bad\x00fixture"),
        lambda document: document["cases"][0]["checks"].append(
            document["cases"][0]["checks"][0]
        ),
        lambda document: document["cases"][1].__setitem__(
            "case_id", document["cases"][0]["case_id"]
        ),
        lambda document: document["cases"][1].__setitem__(
            "operator_code", document["cases"][0]["operator_code"]
        ),
        lambda document: document["cases"][0].__setitem__(
            "operator_code", "unknown_operator"
        ),
        lambda document: document["cases"][0].__setitem__("unknown", "value"),
    ),
)
def test_smoke_manifest_loader_rejects_structural_drift(
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], object],
) -> None:
    document = _smoke_manifest_document()
    mutation(document)

    with pytest.raises(ValueError):
        _aggregate_module().load_smoke_manifest(_write_smoke_manifest(tmp_path, document))


def test_smoke_manifest_loader_rejects_duplicate_json_fields(tmp_path: Path) -> None:
    raw = SMOKE_MANIFEST_PATH.read_text(encoding="utf-8").replace(
        '"schema_version": 1,',
        '"schema_version": 1, "schema_version": 1,',
        1,
    )
    path = tmp_path / "duplicate-smoke-manifest.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        _aggregate_module().load_smoke_manifest(path)


def test_instance_smoke_case_id_is_scoped_and_content_addressed() -> None:
    aggregate = _aggregate_module()
    run_id = "retry-20260817"
    source_case_id = "INF-PPT-SLICE"
    digest = hashlib.sha256(f"{run_id}\0{source_case_id}".encode()).hexdigest()[:12]

    assert aggregate.instance_smoke_case_id(
        "cpu", "ppt-slice-cpu0", run_id, source_case_id
    ) == f"SMOKE-CPU-ppt-slice-cpu0-{digest}"
    assert aggregate.instance_smoke_case_id(
        "gpu", "ppt-slice-cpu0", run_id, source_case_id
    ) == f"SMOKE-GPU-ppt-slice-cpu0-{digest}"


@pytest.mark.parametrize("scope", ("", "GPU", "full", 1, True))
def test_instance_smoke_case_id_rejects_unknown_scope(scope: object) -> None:
    with pytest.raises(ValueError, match="scope"):
        _aggregate_module().instance_smoke_case_id(
            scope, "ppt-slice-cpu0", "run-1", "INF-PPT-SLICE"
        )


def _release_file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_full_smoke_collects_exact_manifest_cases_without_writing(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    manifest = _smoke_manifest_document()["cases"]
    before = _release_file_snapshot(release_tree.root)

    cases, coverage = release_tree.collect_smoke()

    full_cases = [case for case in cases if case["case_kind"] == "smoke_full"]
    assert {case["case_id"] for case in full_cases} == {
        f"SMOKE-FULL-{source['case_id']}" for source in manifest
    }
    assert {case["source_case_id"] for case in full_cases} == {
        source["case_id"] for source in manifest
    }
    assert all(case["run_id"] == "" for case in full_cases)
    assert all(case["status"] == "通过" for case in full_cases)
    assert {
        tuple(case["evidence"])
        for case in full_cases
    } == {
        (f"smoke/{source['operator_code']}.json",) for source in manifest
    }
    assert coverage["smoke_full"] == {"expected": 8, "observed": 8, "passed": 8}
    assert _release_file_snapshot(release_tree.root) == before
    assert not (release_tree.root / "summary").exists()
    assert not (release_tree.root / "negative").exists()
    assert not (release_tree.root / "load").exists()


def test_full_smoke_preserves_failed_and_unexecuted_evidence(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources(
        {"ocr": "失败", "vbas": "未执行及原因"}
    )

    cases, coverage = release_tree.collect_smoke()

    full_by_source = {
        case["source_case_id"]: case
        for case in cases
        if case["case_kind"] == "smoke_full"
    }
    assert full_by_source["INF-OCR"]["status"] == "失败"
    assert full_by_source["INF-VBAS"]["status"] == "未执行及原因"
    assert full_by_source["INF-VBAS"]["evidence"] == ["smoke/vbas.json"]
    assert coverage["smoke_full"] == {"expected": 8, "observed": 8, "passed": 6}


def test_full_smoke_rejects_partial_cases_as_full(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    logical_cases = release_tree.read_value("smoke/cases.json")
    assert isinstance(logical_cases, list)
    logical_cases.pop()
    release_tree._write_json("smoke/cases.json", logical_cases)

    with pytest.raises(ValueError, match="8|manifest|cases"):
        release_tree.collect_smoke()

    assert not (release_tree.root / "summary").exists()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("status", "成功", "status"),
        ("operator_code", "vbas", "operator_code"),
        ("target", "ocr-gpu0", "target"),
        ("checks", ["different_check"], "checks"),
        ("mock", True, "mock"),
        ("release_tag", "different-release", "release_tag"),
        ("git_sha", "b" * 40, "git_sha"),
    ),
)
def test_full_smoke_rejects_evidence_drift(
    release_tree: ReleaseTree,
    field: str,
    value: object,
    message: str,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    evidence = release_tree.read_json("smoke/ocr.json")
    evidence[field] = value
    release_tree.replace_json("smoke/ocr.json", evidence)

    with pytest.raises(ValueError, match=message):
        release_tree.collect_smoke()


def test_smoke_case_ids_are_unique_and_cpu_retry_history_is_preserved(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    cpu_instance = release_tree.cpu_instances[0]
    release_tree.write_instance_smoke(cpu_instance, "retry-failed", status="失败")

    cases, coverage = release_tree.collect_smoke()

    assert len(cases) == 8 + 18 + 6 + 1
    assert len({case["case_id"] for case in cases}) == len(cases)
    cpu_cases = [
        case
        for case in cases
        if case["case_kind"] == "smoke_cpu_instance"
        and case["target"] == cpu_instance
    ]
    assert {case["run_id"] for case in cpu_cases} == {
        f"run-{cpu_instance}",
        "retry-failed",
    }
    assert {case["status"] for case in cpu_cases} == {"通过", "失败"}
    assert coverage["smoke_gpu_trigger"] == {
        "expected": 18,
        "observed": 18,
        "passed": 18,
    }
    assert coverage["smoke_cpu_instance"] == {
        "expected": 6,
        "observed": 6,
        "passed": 6,
    }


def test_gpu_running_activity_missing_run_fails_without_summary(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    running_path = "gpu-instances/asr-offline-gpu0.json"
    running = release_tree.read_json(running_path)
    running["activity"]["run_id"] = "missing-run"
    release_tree.replace_json(running_path, running)

    with pytest.raises(ValueError, match="missing-run|run_id|run"):
        release_tree.collect_smoke()

    assert not (release_tree.root / "summary").exists()


def test_cpu_instance_with_only_failed_run_is_observed_but_not_passed(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    cpu_instance = release_tree.cpu_instances[0]
    release_tree.write_instance_smoke(
        cpu_instance, f"run-{cpu_instance}", status="失败"
    )

    cases, coverage = release_tree.collect_smoke()

    failed = [
        case
        for case in cases
        if case["case_kind"] == "smoke_cpu_instance"
        and case["target"] == cpu_instance
    ]
    assert len(failed) == 1
    assert failed[0]["status"] == "失败"
    assert coverage["smoke_cpu_instance"] == {
        "expected": 6,
        "observed": 6,
        "passed": 5,
    }


def test_gpu_running_fail_with_run_id_links_the_exact_failed_run(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    instance_id = "asr-offline-gpu0"
    linked_run = f"run-{instance_id}"
    release_tree.write_instance_smoke(instance_id, linked_run, status="失败")
    release_tree.write_instance_smoke(instance_id, "historical-pass")
    running_path = f"gpu-instances/{instance_id}.json"
    running = release_tree.read_json(running_path)
    running["status"] = "FAIL"
    running["reason"] = "trigger failed after run_id"
    release_tree.replace_json(running_path, running)

    cases, coverage = release_tree.collect_smoke()

    instance_cases = [
        case
        for case in cases
        if case["case_kind"] == "smoke_gpu_trigger" and case["target"] == instance_id
    ]
    assert {(case["run_id"], case["status"]) for case in instance_cases} == {
        (linked_run, "失败"),
        ("historical-pass", "通过"),
    }
    assert coverage["smoke_gpu_trigger"] == {
        "expected": 18,
        "observed": 18,
        "passed": 17,
    }


def test_gpu_running_fail_without_run_id_does_not_use_unlinked_history(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    instance_id = "asr-offline-gpu0"
    running_path = f"gpu-instances/{instance_id}.json"
    running = release_tree.read_json(running_path)
    running["status"] = "FAIL"
    running["reason"] = "trigger failed before run_id"
    running["activity"].pop("run_id")
    release_tree.replace_json(running_path, running)

    cases, coverage = release_tree.collect_smoke()

    historical = [
        case
        for case in cases
        if case["case_kind"] == "smoke_gpu_trigger" and case["target"] == instance_id
    ]
    assert len(historical) == 1
    assert historical[0]["status"] == "通过"
    assert coverage["smoke_gpu_trigger"] == {
        "expected": 18,
        "observed": 17,
        "passed": 17,
    }


def test_gpu_running_fail_with_run_id_rejects_a_passing_linked_run(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    instance_id = "asr-offline-gpu0"
    running_path = f"gpu-instances/{instance_id}.json"
    running = release_tree.read_json(running_path)
    running["status"] = "FAIL"
    running["reason"] = "trigger failed after run_id"
    release_tree.replace_json(running_path, running)

    with pytest.raises(ValueError, match="FAIL|failed|失败|status"):
        release_tree.collect_smoke()


def test_cpu_instance_without_any_run_fails_closed(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    instance_id = release_tree.cpu_instances[0]
    instance_root = release_tree.root / "smoke" / "instances" / instance_id
    run_root = instance_root / "runs" / f"run-{instance_id}"
    for source in tuple(run_root.iterdir()):
        source.unlink()
    run_root.rmdir()
    (instance_root / "runs").rmdir()
    instance_root.rmdir()

    with pytest.raises(ValueError, match=instance_id):
        release_tree.collect_smoke()


def test_instance_smoke_rejects_wrong_compose_target(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    instance_id = release_tree.cpu_instances[0]
    run_id = f"run-{instance_id}"
    root = f"smoke/instances/{instance_id}/runs/{run_id}"
    logical_cases = release_tree.read_value(f"{root}/cases.json")
    assert isinstance(logical_cases, list)
    logical_cases[0]["target"] = release_tree.cpu_instances[1]
    release_tree._write_json(f"{root}/cases.json", logical_cases)
    operator_code = release_tree._smoke_case_for_instance(instance_id)["operator_code"]
    evidence = release_tree.read_json(f"{root}/{operator_code}.json")
    evidence["target"] = release_tree.cpu_instances[1]
    release_tree.replace_json(f"{root}/{operator_code}.json", evidence)

    with pytest.raises(ValueError, match="target"):
        release_tree.collect_smoke()


def test_cpu_instance_rejects_mock_only_run(release_tree: ReleaseTree) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    instance_id = release_tree.cpu_instances[0]
    release_tree.write_instance_smoke(
        instance_id, f"run-{instance_id}", mock=True
    )

    with pytest.raises(ValueError, match="mock"):
        release_tree.collect_smoke()


def test_instance_smoke_rejects_unknown_compose_instance(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    unknown = release_tree.root / "smoke" / "instances" / "unknown-gpu0" / "runs"
    unknown.mkdir(parents=True)

    with pytest.raises(ValueError, match="unknown-gpu0|unknown instance"):
        release_tree.collect_smoke()


def test_instance_smoke_rejects_instances_replacement_during_scan(
    release_tree: ReleaseTree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    aggregate = _aggregate_module()
    instances_root = release_tree.root / "smoke" / "instances"
    original_metadata = os.stat(instances_root, follow_symlinks=False)
    moved_instances_root = release_tree.root.parent / "original-instances"
    outside_instances_root = release_tree.root.parent / "outside-instances"
    outside_instances_root.mkdir()
    real_listdir = os.listdir
    replaced = False

    def replace_instances_then_list(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes] | int,
    ) -> list[str]:
        nonlocal replaced
        if isinstance(path, int):
            opened = os.fstat(path)
            scans_instances = (opened.st_dev, opened.st_ino) == (
                original_metadata.st_dev,
                original_metadata.st_ino,
            )
        else:
            scans_instances = Path(path) == instances_root
        if not replaced and scans_instances:
            instances_root.rename(moved_instances_root)
            instances_root.symlink_to(outside_instances_root, target_is_directory=True)
            replaced = True
        return real_listdir(path)

    monkeypatch.setattr(aggregate.os, "listdir", replace_instances_then_list)

    with pytest.raises(ValueError, match="release (source )?directory"):
        release_tree.collect_smoke()

    assert replaced


@pytest.mark.parametrize("source_name", ("extra.json", "latest"))
def test_instance_smoke_rejects_extra_run_source(
    release_tree: ReleaseTree,
    source_name: str,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    instance_id = release_tree.cpu_instances[0]
    run_id = f"run-{instance_id}"
    extra = (
        release_tree.root
        / "smoke"
        / "instances"
        / instance_id
        / "runs"
        / run_id
        / source_name
    )
    if source_name.endswith(".json"):
        extra.write_text("{}", encoding="utf-8")
    else:
        extra.mkdir()

    with pytest.raises(ValueError, match="extra|canonical|source"):
        release_tree.collect_smoke()


def test_instance_smoke_rejects_missing_operator_evidence(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    instance_id = release_tree.cpu_instances[0]
    run_id = f"run-{instance_id}"
    operator_code = release_tree._smoke_case_for_instance(instance_id)["operator_code"]
    evidence = (
        release_tree.root
        / "smoke"
        / "instances"
        / instance_id
        / "runs"
        / run_id
        / f"{operator_code}.json"
    )
    evidence.unlink()

    with pytest.raises(ValueError, match="missing|source"):
        release_tree.collect_smoke()


def test_instance_smoke_rejects_unsafe_run_id(release_tree: ReleaseTree) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    instance_id = release_tree.cpu_instances[0]
    runs_root = release_tree.root / "smoke" / "instances" / instance_id / "runs"
    (runs_root / f"run-{instance_id}").rename(runs_root / "unsafe run")

    with pytest.raises(ValueError, match="run_id|run ID|unsafe"):
        release_tree.collect_smoke()


def test_instance_smoke_rejects_symlink_source(release_tree: ReleaseTree) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    instance_id = release_tree.cpu_instances[0]
    run_id = f"run-{instance_id}"
    run_root = release_tree.root / "smoke" / "instances" / instance_id / "runs" / run_id
    operator_code = release_tree._smoke_case_for_instance(instance_id)["operator_code"]
    evidence = run_root / f"{operator_code}.json"
    evidence.unlink()
    evidence.symlink_to(run_root / "cases.json")

    with pytest.raises(ValueError, match="symlink|regular|source"):
        release_tree.collect_smoke()


def test_instance_smoke_rejects_generated_case_id_collision(
    release_tree: ReleaseTree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    aggregate = _aggregate_module()
    monkeypatch.setattr(
        aggregate,
        "instance_smoke_case_id",
        lambda *_: "SMOKE-INSTANCE-COLLISION",
    )

    with pytest.raises(ValueError, match="collision|duplicate|unique"):
        release_tree.collect_smoke()


def _canonical_full_smoke_envelope() -> dict[str, Any]:
    envelope = _valid_envelope()
    index = next(
        index
        for index, case in enumerate(envelope["cases"])
        if case["case_id"] == "SMOKE-FULL-INF-OCR"
    )
    envelope["cases"].insert(0, envelope["cases"].pop(index))
    return envelope


def test_cases_envelope_allows_empty_run_id_only_for_full_smoke() -> None:
    contract = _contract_module()

    contract.validate_cases_envelope(_canonical_full_smoke_envelope())


def test_cases_envelope_rejects_nonempty_full_smoke_run_id() -> None:
    contract = _contract_module()
    envelope = _canonical_full_smoke_envelope()
    envelope["cases"][0]["run_id"] = "registration"

    with pytest.raises(ValueError, match="run_id"):
        contract.validate_cases_envelope(envelope)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("target", "operator-registry"),
        ("evidence", ["registration/operator-registration.json"]),
        ("mock", True),
    ),
)
def test_cases_envelope_rejects_noncanonical_full_smoke_authority_fields(
    field: str,
    value: object,
) -> None:
    contract = _contract_module()
    envelope = _canonical_full_smoke_envelope()
    envelope["cases"][0][field] = value

    with pytest.raises(ValueError, match=field):
        contract.validate_cases_envelope(envelope)


def test_cases_envelope_rejects_relabelled_declaration_with_empty_run_id() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    case = envelope["cases"][0]
    case["case_kind"] = "smoke_full"
    case["run_id"] = ""

    with pytest.raises(ValueError, match="source_case_id|canonical|Smoke"):
        contract.validate_cases_envelope(envelope)


def test_cases_envelope_rejects_unknown_full_smoke_source() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    case = envelope["cases"][0]
    case["case_id"] = "SMOKE-FULL-INF-UNKNOWN"
    case["source_case_id"] = "INF-UNKNOWN"
    case["case_kind"] = "smoke_full"
    case["run_id"] = ""

    with pytest.raises(ValueError, match="source_case_id|canonical|Smoke"):
        contract.validate_cases_envelope(envelope)


def test_cases_envelope_rejects_wrong_full_smoke_case_id() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    case = envelope["cases"][0]
    case["case_id"] = "SMOKE-FULL-INF-ASR-ONLINE"
    case["source_case_id"] = "INF-OCR"
    case["case_kind"] = "smoke_full"
    case["run_id"] = ""

    with pytest.raises(ValueError, match="case_id|canonical|Smoke"):
        contract.validate_cases_envelope(envelope)


def test_declarations_materialize_exact_batches_and_never_pass(
    release_tree: ReleaseTree,
) -> None:
    aggregate = _aggregate_module()
    contract = _contract_module()
    release_tree.root.mkdir(parents=True)
    report_plan = contract.load_report_plan(release_tree.report_plan_path)
    expected = contract.expand_declaration_cases(report_plan)

    cases, coverage = aggregate.materialize_declaration_cases(
        release_root=release_tree.root,
        report_plan=report_plan,
        release_tag=release_tree.release_tag,
        git_sha=release_tree.git_sha,
    )

    expected_by_category = {
        category: [case for case in expected if case["case_kind"] == category]
        for category in ("negative", "load")
    }
    for category, expected_cases in expected_by_category.items():
        declaration = release_tree.read_json(f"{category}/cases.json")
        assert set(declaration) == {
            "schema_version",
            "evidence_type",
            "category",
            "status",
            "mock",
            "release_tag",
            "git_sha",
            "reason",
            "cases",
        }
        assert declaration == {
            "schema_version": 1,
            "evidence_type": "execution_declaration",
            "category": category,
            "status": "NOT_EXECUTED",
            "mock": False,
            "release_tag": release_tree.release_tag,
            "git_sha": release_tree.git_sha,
            "reason": REASON,
            "cases": [
                {"case_id": case["case_id"], "status": "NOT_EXECUTED"}
                for case in expected_cases
            ],
        }

    assert len(cases) == 243
    assert {case["case_id"] for case in cases} == {
        case["case_id"] for case in expected
    }
    assert all(
        case["case_id"] == case["source_case_id"]
        and case["case_kind"] == "execution_declaration"
        and case["status"] == "未执行及原因"
        and case["run_id"] == DECLARATION_PLACEHOLDER
        and case["started_at"] == DECLARATION_PLACEHOLDER
        and case["finished_at"] == DECLARATION_PLACEHOLDER
        and case["target"] == DECLARATION_TARGET
        and case["command"] == DECLARATION_PLACEHOLDER
        and case["reason"] == REASON
        and case["mock"] is False
        and case["evidence"]
        == [
            "load/cases.json"
            if case["case_id"].startswith("LOAD-")
            else "negative/cases.json"
        ]
        for case in cases
    )
    assert coverage == {
        "negative_declarations": {
            "expected": 217,
            "observed": 217,
            "passed": 0,
        },
        "load_declarations": {"expected": 26, "observed": 26, "passed": 0},
    }


def test_existing_declaration_changed_to_pass_is_rejected(
    release_tree: ReleaseTree,
) -> None:
    aggregate = _aggregate_module()
    contract = _contract_module()
    release_tree.root.mkdir(parents=True)
    report_plan = contract.load_report_plan(release_tree.report_plan_path)
    arguments = {
        "release_root": release_tree.root,
        "report_plan": report_plan,
        "release_tag": release_tree.release_tag,
        "git_sha": release_tree.git_sha,
    }
    aggregate.materialize_declaration_cases(**arguments)
    declaration = release_tree.read_json("negative/cases.json")
    declaration["cases"][0]["status"] = "PASS"
    release_tree.replace_json("negative/cases.json", declaration)

    with pytest.raises(
        ValueError,
        match=r"negative/cases\.json.*cases\[0\]\.status",
    ):
        aggregate.materialize_declaration_cases(**arguments)


@pytest.mark.parametrize("scope", ("document", "case"))
def test_existing_declaration_rejects_unknown_fields(
    release_tree: ReleaseTree,
    scope: str,
) -> None:
    aggregate = _aggregate_module()
    contract = _contract_module()
    release_tree.root.mkdir(parents=True)
    report_plan = contract.load_report_plan(release_tree.report_plan_path)
    arguments = {
        "release_root": release_tree.root,
        "report_plan": report_plan,
        "release_tag": release_tree.release_tag,
        "git_sha": release_tree.git_sha,
    }
    aggregate.materialize_declaration_cases(**arguments)
    declaration = release_tree.read_json("negative/cases.json")
    if scope == "document":
        declaration["unknown"] = "value"
    else:
        declaration["cases"][0]["unknown"] = "value"
    release_tree.replace_json("negative/cases.json", declaration)

    with pytest.raises(ValueError, match=r"negative/cases\.json.*unknown"):
        aggregate.materialize_declaration_cases(**arguments)


def test_publish_json_once_uses_durable_hard_link_and_preserves_conflicts(
    release_tree: ReleaseTree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate = _aggregate_module()
    release_tree.root.mkdir(parents=True)
    document = {"schema_version": 1, "value": "first"}
    original_link = aggregate.os.link
    original_fsync = aggregate.os.fsync
    link_calls: list[tuple[object, object]] = []
    fsync_types: list[int] = []

    def observed_link(source: object, destination: object, **kwargs: object) -> None:
        link_calls.append((source, destination))
        original_link(source, destination, **kwargs)

    def observed_fsync(descriptor: int) -> None:
        fsync_types.append(stat.S_IFMT(os.fstat(descriptor).st_mode))
        original_fsync(descriptor)

    monkeypatch.setattr(aggregate.os, "link", observed_link)
    monkeypatch.setattr(aggregate.os, "fsync", observed_fsync)

    aggregate.publish_json_once(
        release_root=release_tree.root,
        relative_path=Path("summary/cases.json"),
        document=document,
    )

    output = release_tree.root / "summary" / "cases.json"
    original_bytes = output.read_bytes()
    assert link_calls
    assert stat.S_IFREG in fsync_types
    assert stat.S_IFDIR in fsync_types
    assert stat.S_IMODE(os.lstat(output).st_mode) == 0o600
    assert {entry.name for entry in output.parent.iterdir()} == {"cases.json"}

    aggregate.publish_json_once(
        release_root=release_tree.root,
        relative_path=Path("summary/cases.json"),
        document=document,
    )
    assert output.read_bytes() == original_bytes

    with pytest.raises(ValueError, match="different bytes|conflict"):
        aggregate.publish_json_once(
            release_root=release_tree.root,
            relative_path=Path("summary/cases.json"),
            document={"schema_version": 1, "value": "second"},
        )
    assert output.read_bytes() == original_bytes
    assert {entry.name for entry in output.parent.iterdir()} == {"cases.json"}


@pytest.mark.parametrize("same_content", (True, False))
def test_publish_json_once_is_concurrent_create_if_absent(
    release_tree: ReleaseTree,
    monkeypatch: pytest.MonkeyPatch,
    same_content: bool,
) -> None:
    aggregate = _aggregate_module()
    release_tree.root.mkdir(parents=True)
    documents = (
        {"schema_version": 1, "value": "first"},
        {
            "schema_version": 1,
            "value": "first" if same_content else "second",
        },
    )
    link_barrier = threading.Barrier(2)
    original_link = aggregate.os.link

    def synchronized_link(source: object, destination: object, **kwargs: object) -> None:
        link_barrier.wait(timeout=5)
        original_link(source, destination, **kwargs)

    monkeypatch.setattr(aggregate.os, "link", synchronized_link)

    def publish(document: dict[str, object]) -> str | None:
        try:
            aggregate.publish_json_once(
                release_root=release_tree.root,
                relative_path=Path("summary/cases.json"),
                document=document,
            )
        except ValueError as exc:
            return str(exc)
        return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(publish, documents))

    output = release_tree.root / "summary" / "cases.json"
    if same_content:
        assert results == (None, None)
        assert json.loads(output.read_bytes()) == documents[0]
    else:
        assert sum(result is None for result in results) == 1
        winner = results.index(None)
        assert json.loads(output.read_bytes()) == documents[winner]
        assert "different bytes" in results[1 - winner]
    assert stat.S_IMODE(os.lstat(output).st_mode) == 0o600
    assert {entry.name for entry in output.parent.iterdir()} == {"cases.json"}


def test_publish_json_once_keeps_temp_descriptor_open_through_link(
    release_tree: ReleaseTree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate = _aggregate_module()
    release_tree.root.mkdir(parents=True)
    summary = release_tree.root / "summary"
    original_close = aggregate.os.close
    original_create_temp = aggregate._create_publication_temp
    temp_descriptor: int | None = None
    replaced_during_close = False

    def observed_create_temp(
        parent_descriptor: int, final_name: str
    ) -> tuple[int, str]:
        nonlocal temp_descriptor
        result = original_create_temp(parent_descriptor, final_name)
        temp_descriptor = result[0]
        return result

    def replacing_close(descriptor: int) -> None:
        nonlocal replaced_during_close
        metadata = aggregate.os.fstat(descriptor)
        original_close(descriptor)
        if (
            replaced_during_close
            or descriptor != temp_descriptor
            or not stat.S_ISREG(metadata.st_mode)
        ):
            return
        temps = tuple(summary.glob(".cases.json.*.tmp")) if summary.exists() else ()
        if not temps:
            return
        assert len(temps) == 1
        temps[0].unlink()
        temps[0].write_bytes(b'{"attacker":true}\n')
        temps[0].chmod(0o600)
        replaced_during_close = True

    monkeypatch.setattr(aggregate, "_create_publication_temp", observed_create_temp)
    monkeypatch.setattr(aggregate.os, "close", replacing_close)

    aggregate.publish_json_once(
        release_root=release_tree.root,
        relative_path=Path("summary/cases.json"),
        document={"schema_version": 1},
    )

    output = summary / "cases.json"
    assert replaced_during_close is False
    assert json.loads(output.read_bytes()) == {"schema_version": 1}


def test_publish_json_once_preserves_wrong_inode_link(
    release_tree: ReleaseTree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate = _aggregate_module()
    release_tree.root.mkdir(parents=True)
    summary = release_tree.root / "summary"
    summary.mkdir(mode=0o700)
    attacker = summary / ".attacker"
    attacker.write_bytes(b'{"attacker":true}\n')
    attacker.chmod(0o600)
    original_link = aggregate.os.link

    def wrong_inode_link(
        source: object, destination: object, **kwargs: object
    ) -> None:
        original_link(attacker.name, destination, **kwargs)

    monkeypatch.setattr(aggregate.os, "link", wrong_inode_link)

    output = summary / "cases.json"
    with pytest.raises(ValueError, match="inode|publication|published"):
        aggregate.publish_json_once(
            release_root=release_tree.root,
            relative_path=Path("summary/cases.json"),
            document={"schema_version": 1},
        )

    assert output.read_bytes() == b'{"attacker":true}\n'
    assert (os.lstat(output).st_dev, os.lstat(output).st_ino) == (
        os.lstat(attacker).st_dev,
        os.lstat(attacker).st_ino,
    )
    assert attacker.read_bytes() == b'{"attacker":true}\n'


def test_publish_json_once_preserves_rebound_temp_name_and_wrong_final(
    release_tree: ReleaseTree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate = _aggregate_module()
    release_tree.root.mkdir(parents=True)
    summary = release_tree.root / "summary"
    original_link = aggregate.os.link
    rebound_name: str | None = None

    def rebound_link(source: object, destination: object, **kwargs: object) -> None:
        nonlocal rebound_name
        assert isinstance(source, str)
        parent_descriptor = kwargs["src_dir_fd"]
        assert isinstance(parent_descriptor, int)
        aggregate.os.rename(
            source,
            f"{source}.original",
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        descriptor = aggregate.os.open(
            source,
            aggregate.os.O_WRONLY | aggregate.os.O_CREAT | aggregate.os.O_EXCL,
            0o600,
            dir_fd=parent_descriptor,
        )
        try:
            aggregate.os.write(descriptor, b'{"rebound":true}\n')
            aggregate.os.fsync(descriptor)
        finally:
            aggregate.os.close(descriptor)
        rebound_name = source
        original_link(source, destination, **kwargs)

    monkeypatch.setattr(aggregate.os, "link", rebound_link)

    output = summary / "cases.json"
    with pytest.raises(ValueError, match="inode|publication|published"):
        aggregate.publish_json_once(
            release_root=release_tree.root,
            relative_path=Path("summary/cases.json"),
            document={"schema_version": 1},
        )

    assert rebound_name is not None
    rebound = summary / rebound_name
    assert output.read_bytes() == b'{"rebound":true}\n'
    assert rebound.read_bytes() == b'{"rebound":true}\n'
    assert (os.lstat(output).st_dev, os.lstat(output).st_ino) == (
        os.lstat(rebound).st_dev,
        os.lstat(rebound).st_ino,
    )


def test_publish_json_once_rejects_rebound_temp_name_before_link(
    release_tree: ReleaseTree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate = _aggregate_module()
    release_tree.root.mkdir(parents=True)
    summary = release_tree.root / "summary"
    original_fsync = aggregate.os.fsync
    rebound_name: str | None = None

    def rebound_after_fsync(descriptor: int) -> None:
        nonlocal rebound_name
        original_fsync(descriptor)
        if rebound_name is not None or not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return
        temps = tuple(summary.glob(".cases.json.*.tmp"))
        if not temps:
            return
        assert len(temps) == 1
        temp = temps[0]
        temp.rename(temp.with_name(f"{temp.name}.original"))
        temp.write_bytes(b'{"rebound":true}\n')
        temp.chmod(0o600)
        rebound_name = temp.name

    monkeypatch.setattr(aggregate.os, "fsync", rebound_after_fsync)

    output = summary / "cases.json"
    with pytest.raises(ValueError, match="temp name changed|temp.*link"):
        aggregate.publish_json_once(
            release_root=release_tree.root,
            relative_path=Path("summary/cases.json"),
            document={"schema_version": 1},
        )

    assert not output.exists()
    assert rebound_name is not None
    assert (summary / rebound_name).read_bytes() == b'{"rebound":true}\n'


def test_publish_json_once_preserves_third_party_before_first_final_stat(
    release_tree: ReleaseTree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate = _aggregate_module()
    release_tree.root.mkdir(parents=True)
    summary = release_tree.root / "summary"
    summary.mkdir(mode=0o700)
    third_party = summary / ".third-party"
    third_party.write_bytes(b'{"third_party":true}\n')
    third_party.chmod(0o600)
    original_link = aggregate.os.link
    original_unlink = aggregate.os.unlink

    def replace_after_link(
        source: object, destination: object, **kwargs: object
    ) -> None:
        original_link(source, destination, **kwargs)
        parent_descriptor = kwargs["dst_dir_fd"]
        assert isinstance(parent_descriptor, int)
        assert isinstance(destination, str)
        original_unlink(destination, dir_fd=parent_descriptor)
        original_link(
            third_party.name,
            destination,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
            follow_symlinks=False,
        )

    monkeypatch.setattr(aggregate.os, "link", replace_after_link)

    output = summary / "cases.json"
    with pytest.raises(ValueError, match="inode|publication|published"):
        aggregate.publish_json_once(
            release_root=release_tree.root,
            relative_path=Path("summary/cases.json"),
            document={"schema_version": 1},
        )

    assert output.read_bytes() == b'{"third_party":true}\n'
    assert third_party.read_bytes() == b'{"third_party":true}\n'
    assert (os.lstat(output).st_dev, os.lstat(output).st_ino) == (
        os.lstat(third_party).st_dev,
        os.lstat(third_party).st_ino,
    )


@pytest.mark.parametrize("mode", (0o720, 0o702))
def test_publish_json_once_rejects_group_or_other_writable_parent(
    release_tree: ReleaseTree,
    mode: int,
) -> None:
    aggregate = _aggregate_module()
    release_tree.root.mkdir(parents=True)
    summary = release_tree.root / "summary"
    summary.mkdir()
    summary.chmod(mode)

    with pytest.raises(ValueError, match="writable|mode|publication parent"):
        aggregate.publish_json_once(
            release_root=release_tree.root,
            relative_path=Path("summary/cases.json"),
            document={"schema_version": 1},
        )

    assert not (summary / "cases.json").exists()


def test_publish_json_once_rejects_parent_not_owned_by_current_uid(
    release_tree: ReleaseTree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate = _aggregate_module()
    release_tree.root.mkdir(parents=True)
    summary = release_tree.root / "summary"
    summary.mkdir(mode=0o700)
    current_uid = os.getuid()
    monkeypatch.setattr(aggregate.os, "getuid", lambda: current_uid + 1)

    with pytest.raises(ValueError, match="owner|UID|publication parent"):
        aggregate.publish_json_once(
            release_root=release_tree.root,
            relative_path=Path("summary/cases.json"),
            document={"schema_version": 1},
        )

    assert not (summary / "cases.json").exists()


@pytest.mark.parametrize("mode", (0o700, 0o755))
def test_publish_json_once_accepts_secure_parent_modes(
    release_tree: ReleaseTree,
    mode: int,
) -> None:
    aggregate = _aggregate_module()
    release_tree.root.mkdir(parents=True)
    summary = release_tree.root / "summary"
    summary.mkdir()
    summary.chmod(mode)

    aggregate.publish_json_once(
        release_root=release_tree.root,
        relative_path=Path("summary/cases.json"),
        document={"schema_version": 1},
    )

    assert json.loads((summary / "cases.json").read_bytes()) == {"schema_version": 1}


def test_publish_json_once_rechecks_opened_parent_mode(
    release_tree: ReleaseTree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate = _aggregate_module()
    release_tree.root.mkdir(parents=True)
    summary = release_tree.root / "summary"
    summary.mkdir(mode=0o700)
    original_open = aggregate.os.open

    def changing_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        descriptor = original_open(path, flags, *args, **kwargs)
        if path == "summary" and kwargs.get("dir_fd") is not None:
            summary.chmod(0o777)
        return descriptor

    monkeypatch.setattr(aggregate.os, "open", changing_open)

    with pytest.raises(ValueError, match="writable|mode|publication parent"):
        aggregate.publish_json_once(
            release_root=release_tree.root,
            relative_path=Path("summary/cases.json"),
            document={"schema_version": 1},
        )

    assert not (summary / "cases.json").exists()


def test_publish_json_once_rolls_back_own_final_when_parent_becomes_writable(
    release_tree: ReleaseTree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate = _aggregate_module()
    release_tree.root.mkdir(parents=True)
    summary = release_tree.root / "summary"
    original_read = aggregate._read_existing_publication

    def loosen_parent_after_read(
        parent_descriptor: int, name: str, **kwargs: object
    ) -> bytes:
        content = original_read(parent_descriptor, name, **kwargs)
        if kwargs.get("expected_inode") is not None:
            summary.chmod(0o777)
        return content

    monkeypatch.setattr(
        aggregate, "_read_existing_publication", loosen_parent_after_read
    )

    output = summary / "cases.json"
    with pytest.raises(ValueError, match="writable|mode|publication parent"):
        aggregate.publish_json_once(
            release_root=release_tree.root,
            relative_path=Path("summary/cases.json"),
            document={"schema_version": 1},
        )

    assert not output.exists()


def test_publish_json_once_preserves_third_party_when_parent_becomes_writable(
    release_tree: ReleaseTree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate = _aggregate_module()
    release_tree.root.mkdir(parents=True)
    summary = release_tree.root / "summary"
    original_read = aggregate._read_existing_publication
    original_link = aggregate.os.link
    third_party = release_tree.root / "third-party.json"
    third_party.write_bytes(b'{"third_party":true}\n')
    third_party.chmod(0o600)

    def replace_final_after_read(
        parent_descriptor: int, name: str, **kwargs: object
    ) -> bytes:
        content = original_read(parent_descriptor, name, **kwargs)
        if kwargs.get("expected_inode") is not None:
            aggregate.os.unlink(name, dir_fd=parent_descriptor)
            original_link(
                third_party,
                name,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            summary.chmod(0o777)
        return content

    monkeypatch.setattr(
        aggregate, "_read_existing_publication", replace_final_after_read
    )

    output = summary / "cases.json"
    with pytest.raises(ValueError, match="writable|mode|publication parent"):
        aggregate.publish_json_once(
            release_root=release_tree.root,
            relative_path=Path("summary/cases.json"),
            document={"schema_version": 1},
        )

    assert output.read_bytes() == b'{"third_party":true}\n'
    assert (os.lstat(output).st_dev, os.lstat(output).st_ino) == (
        os.lstat(third_party).st_dev,
        os.lstat(third_party).st_ino,
    )


def test_publish_json_once_rolls_back_own_final_when_release_root_is_rebound(
    release_tree: ReleaseTree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate = _aggregate_module()
    release_tree.root.mkdir(parents=True)
    displaced_root = release_tree.root.with_name(f"{release_tree.root.name}.displaced")
    original_read = aggregate._read_existing_publication
    rebound = False

    def rebind_release_root_after_read(
        parent_descriptor: int, name: str, **kwargs: object
    ) -> bytes:
        nonlocal rebound
        content = original_read(parent_descriptor, name, **kwargs)
        if kwargs.get("expected_inode") is not None:
            release_tree.root.rename(displaced_root)
            release_tree.root.mkdir(mode=0o700)
            rebound = True
        return content

    monkeypatch.setattr(
        aggregate, "_read_existing_publication", rebind_release_root_after_read
    )

    with pytest.raises(ValueError, match=r"release root.*changed|changed.*release root"):
        aggregate.publish_json_once(
            release_root=release_tree.root,
            relative_path=Path("summary/cases.json"),
            document={"schema_version": 1},
        )

    assert rebound is True
    assert not (displaced_root / "summary" / "cases.json").exists()
    assert not (release_tree.root / "summary" / "cases.json").exists()


def test_publish_json_once_preserves_third_party_when_release_root_is_rebound(
    release_tree: ReleaseTree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate = _aggregate_module()
    release_tree.root.mkdir(parents=True)
    summary = release_tree.root / "summary"
    summary.mkdir(mode=0o700)
    old_third_party = summary / ".third-party"
    old_third_party.write_bytes(b'{"old_third_party":true}\n')
    old_third_party.chmod(0o600)
    displaced_root = release_tree.root.with_name(f"{release_tree.root.name}.displaced")
    replacement_root = release_tree.root.with_name(f"{release_tree.root.name}.replacement")
    replacement_summary = replacement_root / "summary"
    replacement_summary.mkdir(parents=True, mode=0o700)
    replacement_final = replacement_summary / "cases.json"
    replacement_final.write_bytes(b'{"new_third_party":true}\n')
    replacement_final.chmod(0o600)
    replacement_inode = (os.lstat(replacement_final).st_dev, os.lstat(replacement_final).st_ino)
    original_read = aggregate._read_existing_publication
    original_link = aggregate.os.link
    rebound = False

    def replace_final_and_release_root_after_read(
        parent_descriptor: int, name: str, **kwargs: object
    ) -> bytes:
        nonlocal rebound
        content = original_read(parent_descriptor, name, **kwargs)
        if kwargs.get("expected_inode") is not None:
            aggregate.os.unlink(name, dir_fd=parent_descriptor)
            original_link(
                old_third_party.name,
                name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            release_tree.root.rename(displaced_root)
            replacement_root.rename(release_tree.root)
            rebound = True
        return content

    monkeypatch.setattr(
        aggregate,
        "_read_existing_publication",
        replace_final_and_release_root_after_read,
    )

    with pytest.raises(ValueError, match=r"release root.*changed|changed.*release root"):
        aggregate.publish_json_once(
            release_root=release_tree.root,
            relative_path=Path("summary/cases.json"),
            document={"schema_version": 1},
        )

    assert rebound is True
    displaced_final = displaced_root / "summary" / "cases.json"
    displaced_third_party = displaced_root / "summary" / ".third-party"
    assert displaced_final.read_bytes() == b'{"old_third_party":true}\n'
    assert (os.lstat(displaced_final).st_dev, os.lstat(displaced_final).st_ino) == (
        os.lstat(displaced_third_party).st_dev,
        os.lstat(displaced_third_party).st_ino,
    )
    rebound_final = release_tree.root / "summary" / "cases.json"
    assert rebound_final.read_bytes() == b'{"new_third_party":true}\n'
    assert (os.lstat(rebound_final).st_dev, os.lstat(rebound_final).st_ino) == (
        replacement_inode
    )


@pytest.mark.parametrize(
    "unsafe_kind",
    ("parent_symlink", "parent_file", "final_symlink", "final_directory"),
)
def test_publish_json_once_rejects_unsafe_parent_or_final_binding(
    release_tree: ReleaseTree,
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    aggregate = _aggregate_module()
    release_tree.root.mkdir(parents=True)
    summary = release_tree.root / "summary"
    outside = tmp_path / "outside"
    outside.mkdir()
    if unsafe_kind == "parent_symlink":
        summary.symlink_to(outside, target_is_directory=True)
    elif unsafe_kind == "parent_file":
        summary.write_text("not a directory", encoding="utf-8")
    else:
        summary.mkdir()
        final = summary / "cases.json"
        if unsafe_kind == "final_symlink":
            target = outside / "cases.json"
            target.write_text("outside", encoding="utf-8")
            final.symlink_to(target)
        else:
            final.mkdir()

    with pytest.raises(ValueError, match="directory|regular|publication"):
        aggregate.publish_json_once(
            release_root=release_tree.root,
            relative_path=Path("summary/cases.json"),
            document={"schema_version": 1},
        )

    if unsafe_kind == "parent_symlink":
        assert tuple(outside.iterdir()) == ()
    elif unsafe_kind == "final_symlink":
        assert (outside / "cases.json").read_bytes() == b"outside"


def test_task4_cli_rejects_wrong_output_without_summary(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    wrong_output = release_tree.root / "summary" / "other.json"

    completed = release_tree.run_aggregator(wrong_output)

    assert completed.returncode == 1
    assert "--output" in completed.stderr
    assert not (release_tree.root / "summary").exists()
    assert not (release_tree.root / "negative").exists()
    assert not (release_tree.root / "load").exists()


def test_task4_cli_rejects_changed_declaration_after_summary_is_removed(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    assert release_tree.run_aggregator().returncode == 0
    output = release_tree.root / "summary" / "cases.json"
    output.unlink()
    declaration = release_tree.read_json("negative/cases.json")
    declaration["cases"][0]["status"] = "PASS"
    release_tree.replace_json("negative/cases.json", declaration)

    completed = release_tree.run_aggregator()

    assert completed.returncode == 1
    assert "negative/cases.json.cases[0].status" in completed.stderr
    assert not output.exists()


def test_task4_cli_late_instance_run_conflicts_without_replacing_summary(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    assert release_tree.run_aggregator().returncode == 0
    output = release_tree.root / "summary" / "cases.json"
    original_bytes = output.read_bytes()
    instance_id = release_tree.cpu_instances[0]
    release_tree.write_instance_smoke(instance_id, "late-run")

    completed = release_tree.run_aggregator()

    assert completed.returncode == 1
    assert "summary/cases.json" in completed.stderr
    assert "different bytes" in completed.stderr
    assert output.read_bytes() == original_bytes
    assert {entry.name for entry in output.parent.iterdir()} == {"cases.json"}


def test_task4_cli_missing_real_source_publishes_nothing(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    missing = release_tree.root / "registration" / "operator-registration.json"
    missing.unlink()

    completed = release_tree.run_aggregator()

    assert completed.returncode == 1
    assert missing.name in completed.stderr
    assert not (release_tree.root / "summary").exists()
    assert not (release_tree.root / "negative").exists()
    assert not (release_tree.root / "load").exists()


def test_task4_cli_hashes_raw_report_plan_bytes(
    release_tree: ReleaseTree,
    tmp_path: Path,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    raw_plan = PLAN_PATH.read_bytes() + b" \n"
    report_plan = tmp_path / "report-plan-with-trailing-space.json"
    report_plan.write_bytes(raw_plan)
    release_tree.report_plan_path = report_plan

    completed = release_tree.run_aggregator()

    assert completed.returncode == 0, completed.stderr
    envelope = release_tree.read_json("summary/cases.json")
    assert envelope["plan_sha256"] == hashlib.sha256(raw_plan).hexdigest()


def test_task4_cli_parses_and_hashes_one_report_plan_byte_snapshot(
    release_tree: ReleaseTree,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    report_plan = tmp_path / "changing-report-plan.json"
    report_plan.write_bytes(PLAN_PATH.read_bytes())
    parsed_text = PLAN_PATH.read_text(encoding="utf-8")
    hashed_bytes = PLAN_PATH.read_bytes() + b" \n"
    reads: list[str] = []
    original_read_text = Path.read_text
    original_read_bytes = Path.read_bytes

    def changing_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path == report_plan:
            reads.append("text")
            return parsed_text
        return original_read_text(path, *args, **kwargs)

    def changing_read_bytes(path: Path) -> bytes:
        if path == report_plan:
            reads.append("bytes")
            return hashed_bytes
        return original_read_bytes(path)

    aggregate = _aggregate_module()
    monkeypatch.setattr(Path, "read_text", changing_read_text)
    monkeypatch.setattr(Path, "read_bytes", changing_read_bytes)
    monkeypatch.setattr(
        aggregate,
        "parse_args",
        lambda: aggregate.argparse.Namespace(
            release_root=release_tree.root,
            operator_compose=release_tree.compose_path,
            smoke_manifest=SMOKE_MANIFEST_PATH,
            report_plan=report_plan,
            output=release_tree.root / "summary" / "cases.json",
        ),
    )

    assert aggregate.main() == 0
    envelope = release_tree.read_json("summary/cases.json")
    assert reads == ["bytes"]
    assert envelope["plan_sha256"] == hashlib.sha256(hashed_bytes).hexdigest()


def test_task4_cli_preserves_real_failure_in_final_envelope(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()
    release_tree.write_complete_smoke_sources()
    instance_id = release_tree.gpu_instances[0]
    relative = f"registration/operator-registration-instance-{instance_id}.json"
    registration = release_tree.read_json(relative)
    registration["status"] = "失败"
    registration["summary"]["valid"] = 0
    registration["issues"] = ["instance recovery failed"]
    release_tree.replace_json(relative, registration)

    completed = release_tree.run_aggregator()

    assert completed.returncode == 0, completed.stderr
    envelope = release_tree.read_json("summary/cases.json")
    failed = next(
        case
        for case in envelope["cases"]
        if case["case_id"] == f"REG-RECOVERY-{instance_id}"
    )
    assert failed["status"] == "失败"
    assert failed["reason"] == "instance recovery failed"
    assert envelope["coverage"]["registration_recovery"] == {
        "expected": 18,
        "observed": 18,
        "passed": 17,
    }
