from __future__ import annotations

import copy
import hashlib
import importlib
import inspect
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
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

    def _write_json(self, relative_path: str, payload: dict[str, Any]) -> Path:
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

    def run_aggregator(self) -> subprocess.CompletedProcess[str]:
        output = self.root / "summary" / "cases.json"
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
                str(output),
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


def _plan_document() -> dict[str, Any]:
    assert PLAN_PATH.is_file(), f"权威 report plan 不存在: {PLAN_PATH}"
    loaded = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _valid_case(case_id: str = "DEP-001") -> dict[str, Any]:
    return {
        "case_id": case_id,
        "source_case_id": "DEP-001",
        "case_kind": "negative",
        "run_id": "declaration",
        "status": "未执行及原因",
        "started_at": "2026-08-17T00:00:00Z",
        "finished_at": "2026-08-17T00:00:00Z",
        "target": "root@192.168.29.11",
        "command": "not-run",
        "evidence": ["negative/DEP-001.json"],
        "reason": REASON,
        "mock": False,
        "release_tag": RELEASE_TAG,
        "git_sha": GIT_SHA,
    }


def _valid_envelope() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "release_tag": RELEASE_TAG,
        "git_sha": GIT_SHA,
        "plan_sha256": PLAN_SHA256,
        "coverage": {
            key: {"expected": 1, "observed": 1, "passed": 1}
            for key in EXPECTED_COVERAGE_KEYS
        },
        "cases": [_valid_case()],
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

    contract.validate_cases_envelope(_valid_envelope())


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


def test_cases_envelope_rejects_duplicate_case_id() -> None:
    contract = _contract_module()
    envelope = _valid_envelope()
    envelope["cases"].append(copy.deepcopy(envelope["cases"][0]))

    with pytest.raises(ValueError, match=r"case_id.*DEP-001"):
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


def test_task2_cli_fails_closed_without_creating_any_output(
    release_tree: ReleaseTree,
) -> None:
    release_tree.write_complete_sources()

    completed = release_tree.run_aggregator()

    assert completed.returncode != 0
    assert "Smoke and declaration coverage" in completed.stderr
    assert not (release_tree.root / "summary" / "cases.json").exists()
    assert not (release_tree.root / "summary").exists()
    assert not (release_tree.root / "negative").exists()
    assert not (release_tree.root / "load").exists()
