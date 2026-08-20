from __future__ import annotations

import argparse
import ast
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Literal, cast

import psycopg

from scripts.milestone_2b_case_catalog import CaseDefinition

from .base import RUN_ID_PATTERN, CaseContext, CaseOutcome
from .campaign import CampaignCaseRunner
from .evidence import publish_case_evidence
from .process import (
    CommandResult,
    CommandSpec,
    FoundationCheckAction,
    FoundationCleanupAction,
    foundation_cleanup_resources,
    run_command,
)
from .safety import CaseSafety, ResourceSpec

_PLATFORM_ROOT = Path(__file__).resolve().parents[2]
_CONTAINER_ID_PATTERN = re.compile(r"[0-9a-f]{64}")
_CANONICAL_CASES = frozenset(f"LOAD-{number:03d}" for number in range(10, 17))
_ISOLATED_CASES = frozenset(f"LOAD-{number:03d}" for number in range(17, 27))
_LOAD_CASES = _CANONICAL_CASES | _ISOLATED_CASES
_COURSE_FACT_CASES = frozenset({"LOAD-011", "LOAD-012", "LOAD-013", "LOAD-014", "LOAD-016"})
_POSTGRES_DSN = "postgresql://algorithm:algorithm@127.0.0.1:5432/algorithm"
_DATABASE_FACT_TABLES = (
    "course_jobs",
    "course_task_types",
    "task_nodes",
    "node_results",
    "outbox_events",
)
_CAPACITY_SNAPSHOT_URL = "http://127.0.0.1:18100/ops/operator-instances/snapshot"
_CONTROL_URL = "http://127.0.0.1:18100"
_TASK_ID_PATTERN = re.compile(r"m2b-[A-Za-z0-9][A-Za-z0-9._-]{0,127}-load-0(?:11|12|13|14|16)")
_RUNTIME_RECOVERY_START_TIMEOUT_SECONDS = 60.0
_RUNTIME_RECOVERY_TARGET_READY_TIMEOUT_SECONDS = 180.0
_RUNTIME_RECOVERY_FINAL_READY_TIMEOUT_SECONDS = 180.0
_RUNTIME_RECOVERY_OVERHEAD_SECONDS = 30.0
_RUNTIME_RECOVERY_MAX_TIMEOUT_SECONDS = 1200.0
_LOAD_015_LEASE_ACQUIRE_TIMEOUT_SECONDS = 30.0
_LOAD_015_LEASE_RETRY_INTERVAL_SECONDS = 1.0
_LOAD_015_LEASE_CAPABILITY = "recognize"
_LEASE_EVIDENCE_FIELDS = frozenset(
    {
        "instance_id",
        "operator_code",
        "capabilities",
        "lifecycle",
        "model_ready",
        "inflight",
        "reported_inflight",
        "declared_capacity",
        "active_lease_count",
        "capacity_mismatch",
    }
)

for _final_load_number in range(1, 10):
    _final_load_case_id = f"LOAD-{_final_load_number:03d}"
    globals()[f"load_{_final_load_number:03d}"] = CampaignCaseRunner(
        "final", _final_load_case_id
    )

del _final_load_case_id, _final_load_number


@dataclass(frozen=True, slots=True)
class LoadCaseSpec:
    title: str
    expected: str
    safety: CaseSafety
    timeout_seconds: int
    mode: Literal["canonical_runtime", "controlled_input"]

    @property
    def reason(self) -> str:
        prefix = "恢复验证符合预期" if self.mode == "canonical_runtime" else "受控反例符合预期"
        return f"{prefix}：{self.expected}"


@dataclass(frozen=True, slots=True)
class LeaseReleaseResult:
    http_status: int
    status: Literal["RELEASED", "ALREADY_RELEASED"]


def _spec(
    title: str,
    expected: str,
    *,
    safety: CaseSafety,
    timeout_seconds: int,
) -> LoadCaseSpec:
    return LoadCaseSpec(
        title=title,
        expected=expected,
        safety=safety,
        timeout_seconds=timeout_seconds,
        mode=("canonical_runtime" if safety == "canonical_runtime" else "controlled_input"),
    )


CASE_SPECS: Mapping[str, LoadCaseSpec] = {
    "LOAD-010": _spec(
        "实例被 SIGTERM",
        "停止接新任务并按优雅关闭规则退出",
        safety="canonical_runtime",
        timeout_seconds=3600,
    ),
    "LOAD-011": _spec(
        "实例被 SIGKILL",
        "TTL 后离线，未完成任务不得伪报完成",
        safety="canonical_runtime",
        timeout_seconds=3600,
    ),
    "LOAD-012": _spec(
        "orchestrator 重启",
        "Kafka offset 和数据库状态恢复",
        safety="canonical_runtime",
        timeout_seconds=3600,
    ),
    "LOAD-013": _spec(
        "control 重启",
        "PostgreSQL 事实保留，算子重新心跳恢复",
        safety="canonical_runtime",
        timeout_seconds=3600,
    ),
    "LOAD-014": _spec(
        "Kafka 重启",
        "Producer 和 Consumer 重连且消息不丢失",
        safety="canonical_runtime",
        timeout_seconds=3600,
    ),
    "LOAD-015": _spec(
        "Redis 重启",
        "实时租约不伪造恢复，实例重新注册",
        safety="canonical_runtime",
        timeout_seconds=3600,
    ),
    "LOAD-016": _spec(
        "PostgreSQL 重启",
        "服务 readiness 恢复后继续处理持久化任务",
        safety="canonical_runtime",
        timeout_seconds=3600,
    ),
    "LOAD-017": _spec(
        "报告只记录注册响应而未记录真实租约",
        "证据不合格",
        safety="isolated_mutation",
        timeout_seconds=300,
    ),
    "LOAD-018": _spec(
        "报告只记录健康检查而未真实推理",
        "证据不合格",
        safety="isolated_mutation",
        timeout_seconds=300,
    ),
    "LOAD-019": _spec(
        "测试代码直接调用 Repository 完成节点",
        "Harness 必须失败",
        safety="isolated_mutation",
        timeout_seconds=300,
    ),
    "LOAD-020": _spec(
        "反例未执行却标记通过",
        "Harness 或复审失败",
        safety="isolated_mutation",
        timeout_seconds=300,
    ),
    "LOAD-021": _spec(
        "B 级抽样结果无证据路径", "业务验收不合格", safety="isolated_mutation", timeout_seconds=300
    ),
    "LOAD-022": _spec(
        "mock student_count 被写成真实标注",
        "报告验收失败",
        safety="isolated_mutation",
        timeout_seconds=300,
    ),
    "LOAD-023": _spec(
        "测试结束未恢复原运行容器", "交接失败", safety="isolated_mutation", timeout_seconds=300
    ),
    "LOAD-024": _spec(
        "测试临时 Topic、Group、Redis Key 或数据库残留",
        "清理验收失败",
        safety="isolated_mutation",
        timeout_seconds=300,
    ),
    "LOAD-025": _spec(
        "测试进程或 CUDA PID 残留", "清理验收失败", safety="isolated_mutation", timeout_seconds=300
    ),
    "LOAD-026": _spec(
        "测试删除 /data/result",
        "严重清理错误，立即停止",
        safety="isolated_mutation",
        timeout_seconds=300,
    ),
}


@dataclass(frozen=True, slots=True)
class _RuntimeTarget:
    compose_file: str
    compose_project: str
    service: str
    resource_name: str
    readiness_urls: tuple[str, ...]
    instance_id: str | None = None


_RUNTIME_TARGETS: Mapping[str, _RuntimeTarget] = {
    "LOAD-010": _RuntimeTarget(
        "deploy/docker-compose.operators.yml",
        "algorithm-operators",
        "facerec-gpu0",
        "facerec-gpu0",
        ("http://127.0.0.1:18100/ops/readiness",),
        "facerec-gpu0",
    ),
    "LOAD-011": _RuntimeTarget(
        "deploy/docker-compose.operators.yml",
        "algorithm-operators",
        "asr-offline-gpu0",
        "asr-offline-gpu0",
        ("http://127.0.0.1:18100/ops/readiness",),
        "asr-offline-gpu0",
    ),
    "LOAD-012": _RuntimeTarget(
        "deploy/docker-compose.platform.yml",
        "algorithm-scheduling-platform",
        "orchestrator-service",
        "orchestrator-service",
        ("http://127.0.0.1:18101/ops/readiness",),
    ),
    "LOAD-013": _RuntimeTarget(
        "deploy/docker-compose.platform.yml",
        "algorithm-scheduling-platform",
        "control-service",
        "control-service",
        (
            "http://127.0.0.1:18100/ops/readiness",
            "http://127.0.0.1:18101/ops/readiness",
        ),
    ),
    "LOAD-014": _RuntimeTarget(
        "deploy/docker-compose.infrastructure.yml",
        "algorithm-scheduling-platform",
        "kafka",
        "kafka",
        (
            "http://127.0.0.1:18101/ops/readiness",
            "http://127.0.0.1:18102/ready",
        ),
    ),
    "LOAD-015": _RuntimeTarget(
        "deploy/docker-compose.infrastructure.yml",
        "algorithm-scheduling-platform",
        "redis",
        "redis",
        ("http://127.0.0.1:18100/ops/readiness",),
    ),
    "LOAD-016": _RuntimeTarget(
        "deploy/docker-compose.infrastructure.yml",
        "algorithm-scheduling-platform",
        "postgres",
        "postgres",
        ("http://127.0.0.1:18100/ops/readiness", "http://127.0.0.1:18101/ops/readiness"),
    ),
}

_LOAD_011_TARGETS = tuple(
    _RuntimeTarget(
        "deploy/docker-compose.operators.yml",
        "algorithm-operators",
        f"asr-offline-gpu{index}",
        f"asr-offline-gpu{index}",
        ("http://127.0.0.1:18100/ops/readiness",),
        f"asr-offline-gpu{index}",
    )
    for index in range(3)
)
_LOAD_016_ORCHESTRATOR_TARGET = _RuntimeTarget(
    "deploy/docker-compose.platform.yml",
    "algorithm-scheduling-platform",
    "orchestrator-service",
    "orchestrator-service",
    ("http://127.0.0.1:18101/ops/readiness",),
)
_RUNTIME_RECOVERY_TARGETS: Mapping[str, tuple[_RuntimeTarget, ...]] = {
    **{
        case_id: (target,)
        for case_id, target in _RUNTIME_TARGETS.items()
    },
    "LOAD-011": _LOAD_011_TARGETS,
    "LOAD-016": (_RUNTIME_TARGETS["LOAD-016"], _LOAD_016_ORCHESTRATOR_TARGET),
}


def _runtime_recovery_targets(case_id: str) -> tuple[_RuntimeTarget, ...]:
    targets = _RUNTIME_RECOVERY_TARGETS.get(case_id)
    if targets is None:
        raise ValueError("runtime recovery receipt is not valid for this load case")
    return targets


def _runtime_recovery_wait_timeout_seconds(case_id: str) -> float:
    target_count = len(_runtime_recovery_targets(case_id))
    return min(
        target_count * _RUNTIME_RECOVERY_TARGET_READY_TIMEOUT_SECONDS
        + _RUNTIME_RECOVERY_FINAL_READY_TIMEOUT_SECONDS,
        _RUNTIME_RECOVERY_MAX_TIMEOUT_SECONDS,
    )


def _runtime_recovery_cleanup_timeout_seconds(case_id: str) -> float:
    target_count = len(_runtime_recovery_targets(case_id))
    return min(
        target_count * _RUNTIME_RECOVERY_START_TIMEOUT_SECONDS
        + _runtime_recovery_wait_timeout_seconds(case_id)
        + _RUNTIME_RECOVERY_OVERHEAD_SECONDS,
        _RUNTIME_RECOVERY_MAX_TIMEOUT_SECONDS,
    )


def _assert_case_contract(case: CaseDefinition, case_id: str) -> LoadCaseSpec:
    spec = CASE_SPECS[case_id]
    actual = (
        case.case_id,
        case.category,
        case.phase,
        case.title,
        case.expected,
        case.runner,
        case.timeout_seconds,
        case.safety,
    )
    expected = (
        case_id,
        "load",
        "deployment",
        spec.title,
        spec.expected,
        f"load.{case_id.lower().replace('-', '_')}",
        spec.timeout_seconds,
        spec.safety,
    )
    if actual != expected:
        raise ValueError(f"{case_id} catalog contract changed")
    return spec


def _write_private_input(path: Path, document: Mapping[str, Any]) -> None:
    payload = (json.dumps(dict(document), ensure_ascii=False, sort_keys=True) + "\n").encode()
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise ValueError("load checker input short write")
            offset += written
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _load_scratch_path(context: CaseContext, case: CaseDefinition) -> Path:
    return Path(tempfile.gettempdir()).resolve(strict=True) / (
        f"m2b-{len(context.run_id)}-{context.run_id}-{case.case_id.lower()}-"
        f"scratch-{os.getpid()}-{id(context):x}"
    )


def _lease_receipt_path(context: CaseContext, case: CaseDefinition) -> Path:
    return _load_scratch_path(context, case) / "lease.json"


def _runtime_recovery_receipt_path(context: CaseContext, case: CaseDefinition) -> Path:
    return _load_scratch_path(context, case) / "runtime-recovery.json"


def _course_task_id(run_id: str, case_id: str) -> str:
    task_id = f"m2b-{run_id}-{case_id.lower()}"
    if _TASK_ID_PATTERN.fullmatch(task_id) is None:
        raise ValueError("load course task_id is outside the current run/case namespace")
    return task_id


def _load_resources(context: CaseContext, case: CaseDefinition) -> tuple[ResourceSpec, ...]:
    if case.case_id in _CANONICAL_CASES:
        runtime_targets = _runtime_recovery_targets(case.case_id)
        resources = [
            ResourceSpec("container", target.resource_name)
            for target in runtime_targets
        ]
        runtime_receipt_path = _runtime_recovery_receipt_path(context, case)
        os.mkdir(runtime_receipt_path.parent, 0o700)
        os.chmod(runtime_receipt_path.parent, 0o700)
        if case.case_id in _COURSE_FACT_CASES:
            resources.append(
                ResourceSpec(
                    "database",
                    f"algorithm:course-task:{_course_task_id(context.run_id, case.case_id)}",
                )
            )
        elif case.case_id == "LOAD-015":
            resources.append(ResourceSpec("redis_prefix", "algorithm:operator-lease:facerec"))
            receipt_path = _lease_receipt_path(context, case)
            resources.append(ResourceSpec("filesystem", str(receipt_path)))
        resources.append(ResourceSpec("filesystem", str(runtime_receipt_path)))
        return tuple(resources)
    scratch = _load_scratch_path(context, case)
    os.mkdir(scratch, 0o700)
    os.chmod(scratch, 0o700)
    return (ResourceSpec("filesystem", str(scratch)),)


def _scenario(context: CaseContext, case: CaseDefinition) -> dict[str, Any]:
    spec = CASE_SPECS[case.case_id]
    document: dict[str, Any] = {
        "schema_version": 1,
        "case_id": case.case_id,
        "run_id": context.run_id,
        "target": context.target,
        "mode": spec.mode,
        "mutation": {"case": case.case_id},
    }
    if case.case_id in _CANONICAL_CASES:
        target = _RUNTIME_TARGETS[case.case_id]
        document.update(
            {
                "container": target.resource_name,
                "compose_file": target.compose_file,
                "compose_project": target.compose_project,
                "service": target.service,
                "release_root": str(context.release_root),
                "runtime_recovery_receipt_path": str(
                    _runtime_recovery_receipt_path(context, case)
                ),
            }
        )
        if case.case_id in _COURSE_FACT_CASES:
            task_id = _course_task_id(context.run_id, case.case_id)
            document["course_task_id"] = task_id
            document["database_scope"] = f"algorithm:course-task:{task_id}"
            if case.case_id == "LOAD-011":
                document["containers"] = [
                    target.resource_name for target in _LOAD_011_TARGETS
                ]
                document["services"] = [target.service for target in _LOAD_011_TARGETS]
            elif case.case_id == "LOAD-016":
                document["support_container"] = (
                    _LOAD_016_ORCHESTRATOR_TARGET.resource_name
                )
                document["support_service"] = _LOAD_016_ORCHESTRATOR_TARGET.service
        elif case.case_id == "LOAD-015":
            document["lease_capability"] = _LOAD_015_LEASE_CAPABILITY
            document["redis_scope"] = "algorithm:operator-lease:facerec"
            document["lease_receipt_path"] = str(_lease_receipt_path(context, case))
    else:
        document["scratch_directory"] = str(_load_scratch_path(context, case))
    return document


def _decode_result(result: CommandResult, case_id: str) -> dict[str, Any]:
    if result.stdout_truncated or result.stderr_truncated:
        raise ValueError(f"{case_id} checker output was truncated")
    try:
        document = json.loads(result.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{case_id} checker stdout is not strict JSON") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{case_id} checker stdout must be an object")
    return cast(dict[str, Any], document)


async def _run(context: CaseContext, case: CaseDefinition, case_id: str) -> CaseOutcome:
    spec = _assert_case_contract(case, case_id)
    scenario = _scenario(context, case)
    prefix = f"m2b-{len(context.run_id)}-{context.run_id}-{case_id.lower()}-"
    with tempfile.TemporaryDirectory(prefix=prefix) as directory:
        os.chmod(directory, 0o700)
        input_path = Path(directory) / "input.json"
        _write_private_input(input_path, scenario)
        resources = (ResourceSpec("filesystem", str(input_path)), *_load_resources(context, case))
        command = CommandSpec(
            action=FoundationCheckAction(group="load", case_id=case_id, resources=resources)
        )
        result = await run_command(
            context=context, command=command, timeout_seconds=spec.timeout_seconds
        )
        evidence_path = publish_case_evidence(
            context=context,
            case=case,
            name="load-check.json",
            payload={
                "mode": spec.mode,
                "resources": [{"kind": item.kind, "name": item.name} for item in resources[1:]],
                "command": [*result.argv[:-1], "<current-case-input>"],
                "returncode": result.returncode,
                "stdout": result.stdout.decode("utf-8", errors="replace"),
                "stderr": result.stderr.decode("utf-8", errors="replace"),
            },
        )
        document = _decode_result(result, case_id)
        if result.returncode != 0:
            raise ValueError(f"{case_id} checker failed closed: {document.get('reason')}")
        if document.get("case_id") != case_id or document.get("status") != "通过":
            raise ValueError(f"{case_id} checker identity or status does not match")
        if document.get("reason") != spec.reason:
            raise ValueError(f"{case_id} checker reason does not match")
        observed = document.get("observed")
        if not isinstance(observed, dict) or not observed:
            raise ValueError(f"{case_id} checker observed facts are missing")
        return CaseOutcome("通过", spec.reason, (evidence_path,))


async def cleanup(context: CaseContext, case: CaseDefinition) -> None:
    spec = _assert_case_contract(case, case.case_id)
    resources = foundation_cleanup_resources("load", case.case_id, context.run_id)
    result = await run_command(
        context=context,
        command=CommandSpec(
            action=FoundationCleanupAction(
                group="load", case_id=case.case_id, run_id=context.run_id, resources=resources
            )
        ),
        timeout_seconds=(
            _runtime_recovery_cleanup_timeout_seconds(case.case_id)
            if case.case_id in _CANONICAL_CASES
            else min(spec.timeout_seconds, 30)
        ),
    )
    document = _decode_result(result, case.case_id)
    if result.returncode != 0 or document.get("status") != "clean" or document.get("errors") != []:
        raise ValueError(f"{case.case_id} cleanup failed closed: {document.get('errors')}")
    if document.get("residual_temp_directories") != []:
        raise ValueError(f"{case.case_id} cleanup did not prove no residue")


async def load_010(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "LOAD-010")


async def load_011(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "LOAD-011")


async def load_012(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "LOAD-012")


async def load_013(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "LOAD-013")


async def load_014(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "LOAD-014")


async def load_015(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "LOAD-015")


async def load_016(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "LOAD-016")


async def load_017(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "LOAD-017")


async def load_018(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "LOAD-018")


async def load_019(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "LOAD-019")


async def load_020(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "LOAD-020")


async def load_021(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "LOAD-021")


async def load_022(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "LOAD-022")


async def load_023(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "LOAD-023")


async def load_024(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "LOAD-024")


async def load_025(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "LOAD-025")


async def load_026(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "LOAD-026")


for _case_id in CASE_SPECS:
    globals()[_case_id.lower().replace("-", "_")].cleanup = cleanup


def _command(argv: Sequence[str], *, timeout: float = 30) -> str:
    completed = subprocess.run(
        tuple(argv),
        cwd=_PLATFORM_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"command failed ({completed.returncode}): {detail[:2000]}")
    return completed.stdout


def _resolve_container(target: _RuntimeTarget) -> tuple[str, dict[str, Any]]:
    output = _command(("docker", "compose", "-f", target.compose_file, "ps", "-q", target.service))
    ids = [line.strip() for line in output.splitlines() if line.strip()]
    if len(ids) != 1 or _CONTAINER_ID_PATTERN.fullmatch(ids[0]) is None:
        raise ValueError("missing exactly one canonical Compose container")
    container_id = ids[0]
    inspected = json.loads(_command(("docker", "inspect", container_id)))
    if not isinstance(inspected, list) or len(inspected) != 1 or not isinstance(inspected[0], dict):
        raise ValueError("canonical Docker inspect did not return one object")
    document = cast(dict[str, Any], inspected[0])
    labels = document.get("Config", {}).get("Labels", {})
    if (
        document.get("Id") != container_id
        or not isinstance(labels, dict)
        or labels.get("com.docker.compose.project") != target.compose_project
        or labels.get("com.docker.compose.service") != target.service
    ):
        raise ValueError("canonical Compose container identity mismatch")
    state = document.get("State")
    if not isinstance(state, dict) or state.get("Running") is not True:
        raise ValueError("canonical Compose container is not running before mutation")
    return container_id, document


def _inspect_state(container_id: str) -> dict[str, Any]:
    inspected = json.loads(_command(("docker", "inspect", container_id)))
    if not isinstance(inspected, list) or len(inspected) != 1:
        raise ValueError("Docker inspect lost canonical container")
    state = inspected[0].get("State")
    if not isinstance(state, dict):
        raise ValueError("Docker inspect state is missing")
    return cast(dict[str, Any], state)


def _http_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = response.read()
        if response.status != 200:
            raise ValueError(f"readiness returned HTTP {response.status}")
    return json.loads(payload)


def _post_json_status(url: str, payload: Mapping[str, Any]) -> tuple[int, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode(),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status_code = response.status
            response_payload = response.read()
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        response_payload = exc.read()
    try:
        document = json.loads(response_payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"production API returned non-JSON: {url}") from exc
    return status_code, document


def _post_json(url: str, payload: Mapping[str, Any]) -> Any:
    status_code, document = _post_json_status(url, payload)
    if status_code < 200 or status_code >= 300:
        raise ValueError(f"production API returned HTTP {status_code}: {url}")
    return document


def _wait_until(check: Callable[[], Any], *, timeout: float, label: str) -> Any:
    deadline = time.monotonic() + timeout
    last_error = "not observed"
    while time.monotonic() < deadline:
        try:
            return check()
        except (
            OSError,
            RuntimeError,
            ValueError,
            urllib.error.URLError,
            json.JSONDecodeError,
        ) as exc:
            last_error = str(exc)
            time.sleep(1)
    raise ValueError(f"{label} did not recover: {last_error}")


def _readiness_snapshot(urls: Sequence[str]) -> dict[str, Any]:
    return {url: _http_json(url) for url in urls}


def _operator_instances() -> list[dict[str, Any]]:
    document = _http_json("http://127.0.0.1:18100/ops/operator-instances")
    if not isinstance(document, list) or any(not isinstance(item, dict) for item in document):
        raise ValueError("control operator inventory is not a list of objects")
    return cast(list[dict[str, Any]], document)


def _require_instance(instance_id: str, lifecycle: str) -> dict[str, Any]:
    instance = next(
        (item for item in _operator_instances() if item.get("instance_id") == instance_id), None
    )
    if instance is None or instance.get("lifecycle") != lifecycle:
        raise ValueError(f"operator {instance_id} lifecycle is not {lifecycle}")
    if lifecycle == "ONLINE" and instance.get("model_ready") is not True:
        raise ValueError(f"operator {instance_id} model_ready is not true")
    return instance


def _require_instance_not_routable(instance_id: str) -> dict[str, Any] | None:
    instance = next(
        (item for item in _operator_instances() if item.get("instance_id") == instance_id), None
    )
    if instance is not None and instance.get("lifecycle") != "OFFLINE":
        raise ValueError(f"operator {instance_id} is not offline or absent")
    return instance


def _require_graceful_stop_state(state: Mapping[str, Any]) -> None:
    if (
        state.get("Running") is not False
        or state.get("ExitCode") != 0
        or state.get("OOMKilled") is not False
        or state.get("Error") not in {None, ""}
    ):
        raise ValueError("operator did not exit gracefully after SIGTERM")


def _database_snapshot() -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    with psycopg.connect(_POSTGRES_DSN) as connection:
        with connection.cursor() as cursor:
            for table in _DATABASE_FACT_TABLES:
                cursor.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed allowlist
                row = cursor.fetchone()
                if row is None:
                    raise ValueError(f"PostgreSQL count query returned no row: {table}")
                snapshot[table] = row[0]
            cursor.execute(
                "SELECT status, count(*) FROM task_nodes GROUP BY status ORDER BY status"
            )
            snapshot["task_node_statuses"] = {
                str(status): count for status, count in cursor.fetchall()
            }
    return snapshot


def _empty_course_fact_snapshot(task_id: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "course_jobs": 0,
        "course_task_types": 0,
        "task_nodes": 0,
        "node_results": 0,
        "outbox_events": 0,
        "pending_outbox_events": 0,
        "published_outbox_events": 0,
        "node_attempts": 0,
        "unfinished_operator_nodes": 0,
        "task_node_statuses": {},
    }


def _require_query_row(row: tuple[Any, ...] | None, *, size: int, label: str) -> tuple[Any, ...]:
    if row is None or len(row) != size:
        raise ValueError(f"PostgreSQL scoped query returned no {label} row")
    return row


def _course_fact_snapshot(task_id: str) -> dict[str, Any]:
    if _TASK_ID_PATTERN.fullmatch(task_id) is None:
        raise ValueError("course fact snapshot task_id is not current run/case scoped")
    snapshot = _empty_course_fact_snapshot(task_id)
    with psycopg.connect(_POSTGRES_DSN) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM course_jobs WHERE task_id = %s", (task_id,))
            snapshot["course_jobs"] = _require_query_row(
                cursor.fetchone(), size=1, label="course job count"
            )[0]
            cursor.execute(
                "SELECT count(*) FROM course_task_types WHERE task_id = %s", (task_id,)
            )
            snapshot["course_task_types"] = _require_query_row(
                cursor.fetchone(), size=1, label="course task type count"
            )[0]
            cursor.execute(
                """
                SELECT count(*), COALESCE(sum(n.attempt), 0)
                FROM task_nodes AS n
                JOIN course_task_types AS t ON t.id = n.course_task_type_id
                WHERE t.task_id = %s
                """,
                (task_id,),
            )
            node_count, node_attempts = _require_query_row(
                cursor.fetchone(), size=2, label="task node count"
            )
            snapshot["task_nodes"] = node_count
            snapshot["node_attempts"] = node_attempts
            cursor.execute(
                """
                SELECT count(*)
                FROM task_nodes AS n
                JOIN course_task_types AS t ON t.id = n.course_task_type_id
                WHERE t.task_id = %s
                  AND n.required_capability = 'asr_offline'
                  AND n.status NOT IN (60, 70, 80)
                """,
                (task_id,),
            )
            snapshot["unfinished_operator_nodes"] = _require_query_row(
                cursor.fetchone(), size=1, label="unfinished operator node count"
            )[0]
            cursor.execute(
                """
                SELECT count(*)
                FROM node_results AS r
                JOIN task_nodes AS n ON n.id = r.task_node_id
                JOIN course_task_types AS t ON t.id = n.course_task_type_id
                WHERE t.task_id = %s
                """,
                (task_id,),
            )
            snapshot["node_results"] = _require_query_row(
                cursor.fetchone(), size=1, label="node result count"
            )[0]
            cursor.execute(
                """
                SELECT count(*),
                       count(*) FILTER (WHERE published_at IS NULL),
                       count(*) FILTER (WHERE published_at IS NOT NULL)
                FROM outbox_events
                WHERE payload ->> 'task_id' = %s
                """,
                (task_id,),
            )
            outbox_count, pending_count, published_count = _require_query_row(
                cursor.fetchone(), size=3, label="Outbox count"
            )
            snapshot["outbox_events"] = outbox_count
            snapshot["pending_outbox_events"] = pending_count
            snapshot["published_outbox_events"] = published_count
            cursor.execute(
                """
                SELECT n.status, count(*)
                FROM task_nodes AS n
                JOIN course_task_types AS t ON t.id = n.course_task_type_id
                WHERE t.task_id = %s
                GROUP BY n.status ORDER BY n.status
                """,
                (task_id,),
            )
            snapshot["task_node_statuses"] = {
                str(status): count for status, count in cursor.fetchall()
            }
    return snapshot


def _scenario_task_id(case_id: str, scenario: Mapping[str, Any]) -> str:
    run_id = scenario.get("run_id")
    if not isinstance(run_id, str):
        raise ValueError("load course fact is missing run_id")
    expected = _course_task_id(run_id, case_id)
    if (
        scenario.get("course_task_id") != expected
        or scenario.get("database_scope") != f"algorithm:course-task:{expected}"
    ):
        raise ValueError("load course fact scope does not match current run/case")
    return expected


def _prepare_course_fact(case_id: str, scenario: Mapping[str, Any]) -> dict[str, Any]:
    task_id = _scenario_task_id(case_id, scenario)
    before = _course_fact_snapshot(task_id)
    fact_fields = (
        "course_jobs",
        "course_task_types",
        "task_nodes",
        "node_results",
        "outbox_events",
    )
    if any(before[field] != 0 for field in fact_fields):
        raise ValueError(f"scoped course fact already exists: {task_id}")
    response = _post_json(
        f"{_CONTROL_URL}/api/course-jobs",
        {
            "task_id": task_id,
            "task_types": ["ASR"],
            "teacher_video_path": f"http://127.0.0.1:9/{task_id}.wav",
        },
    )
    if (
        not isinstance(response, dict)
        or response.get("code") != 0
        or not isinstance(response.get("data"), dict)
        or response["data"].get("task_id") != task_id
    ):
        raise ValueError("production control API did not create the scoped course fact")
    after = _course_fact_snapshot(task_id)
    if (
        after["course_jobs"] != 1
        or after["course_task_types"] != 1
        or after["outbox_events"] != 1
    ):
        raise ValueError("production control API course fact is incomplete")
    return after


def _course_cleanup_statements(task_id: str) -> tuple[tuple[str, tuple[str]], ...]:
    if _TASK_ID_PATTERN.fullmatch(task_id) is None:
        raise ValueError("course cleanup task_id is not current run/case scoped")
    return (
        ("DELETE FROM outbox_events WHERE payload ->> 'task_id' = %s", (task_id,)),
        ("DELETE FROM course_task_types WHERE task_id = %s", (task_id,)),
        ("DELETE FROM course_jobs WHERE task_id = %s", (task_id,)),
    )


def _cleanup_course_fact(task_id: str) -> None:
    with psycopg.connect(_POSTGRES_DSN) as connection:
        with connection.cursor() as cursor:
            for statement, parameters in _course_cleanup_statements(task_id):
                cursor.execute(statement, parameters)
    remaining = _course_fact_snapshot(task_id)
    if any(
        remaining[field] != 0
        for field in (
            "course_jobs",
            "course_task_types",
            "task_nodes",
            "node_results",
            "outbox_events",
        )
    ):
        raise ValueError(f"scoped course fact cleanup left residue: {task_id}")


def _database_fact_counts(snapshot: Mapping[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in _DATABASE_FACT_TABLES:
        count = snapshot.get(table)
        if type(count) is not int or count < 0:
            raise ValueError(f"database fact count is invalid: {table}")
        counts[table] = count
    return counts


def _require_database_facts_preserved(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> None:
    before_counts = _database_fact_counts(before)
    after_counts = _database_fact_counts(after)
    regressed = [
        table for table in _DATABASE_FACT_TABLES if after_counts[table] < before_counts[table]
    ]
    if regressed:
        raise ValueError(f"database fact row counts regressed after recovery: {regressed}")


def _active_lease_count(snapshot: Any) -> int:
    if not isinstance(snapshot, list):
        raise ValueError("operator capacity snapshot is not a list")
    total = 0
    for item in snapshot:
        if not isinstance(item, dict):
            raise ValueError("operator capacity snapshot contains a non-object")
        count = item.get("active_lease_count")
        if type(count) is not int or count < 0:
            raise ValueError("operator capacity snapshot has an invalid active lease count")
        total += count
    return total


def _incomplete_nodes(snapshot: Mapping[str, Any]) -> int:
    statuses = snapshot.get("task_node_statuses")
    if not isinstance(statuses, dict):
        return 0
    return sum(int(count) for status, count in statuses.items() if status not in {"60", "70", "80"})


def _kafka_offsets(container_id: str) -> dict[str, int]:
    output = _command(
        (
            "docker",
            "exec",
            container_id,
            "/opt/kafka/bin/kafka-consumer-groups.sh",
            "--bootstrap-server",
            "localhost:9092",
            "--describe",
            "--group",
            "algorithm-orchestrator",
        ),
        timeout=60,
    )
    offsets: dict[str, int] = {}
    for line in output.splitlines():
        fields = line.split()
        if (
            len(fields) >= 5
            and fields[0] == "algorithm-orchestrator"
            and fields[2].isdigit()
            and fields[3].isdigit()
        ):
            offsets[f"{fields[1]}:{fields[2]}"] = int(fields[3])
    return offsets


def _require_offsets_not_regressed(before: Mapping[str, int], after: Mapping[str, int]) -> None:
    if any(key not in after or after[key] < value for key, value in before.items()):
        raise ValueError("Kafka committed offsets regressed after recovery")


def _offsets_advanced(before: Mapping[str, int], after: Mapping[str, int]) -> bool:
    return any(value > before.get(key, 0) for key, value in after.items())


def _require_scoped_recovery_progress(
    case_id: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    before_offsets: Mapping[str, int] | None,
    after_offsets: Mapping[str, int] | None,
) -> None:
    for field in ("course_jobs", "course_task_types", "outbox_events"):
        if type(before.get(field)) is not int or type(after.get(field)) is not int:
            raise ValueError(f"scoped course fact count is invalid: {field}")
        if after[field] < before[field]:
            raise ValueError(f"scoped course fact regressed: {field}")
    if case_id in {"LOAD-012", "LOAD-014"}:
        if before_offsets is None or after_offsets is None:
            raise ValueError("Kafka offset evidence is missing")
        _require_offsets_not_regressed(before_offsets, after_offsets)
        if not _offsets_advanced(before_offsets, after_offsets):
            raise ValueError("Kafka committed offset did not advance for the scoped task")
    if case_id in {"LOAD-012", "LOAD-014", "LOAD-016"}:
        if before.get("pending_outbox_events") != 1:
            raise ValueError(f"{case_id} did not establish one pending Outbox event before restart")
        if after.get("published_outbox_events") != 1:
            raise ValueError(f"{case_id} scoped Outbox event was not published after restart")
        if type(after.get("task_nodes")) is not int or after["task_nodes"] <= 0:
            raise ValueError(f"{case_id} scoped Kafka message did not create a DAG")
        return
    progress_fields = (
        "published_outbox_events",
        "task_nodes",
        "node_results",
        "node_attempts",
    )
    if not any(
        type(before.get(field)) is int
        and type(after.get(field)) is int
        and after[field] > before[field]
        for field in progress_fields
    ) and before.get("task_node_statuses") == after.get("task_node_statuses"):
        raise ValueError(f"{case_id} scoped persisted work did not progress after readiness")


def _require_unfinished_operator_work(
    case_id: str,
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    if case_id != "LOAD-011":
        raise ValueError("unfinished operator work guard is only valid for LOAD-011")
    if type(snapshot.get("task_nodes")) is not int or snapshot["task_nodes"] <= 0:
        raise ValueError("LOAD-011 scoped task has no DAG")
    unfinished = snapshot.get("unfinished_operator_nodes")
    if type(unfinished) is not int or unfinished <= 0:
        raise ValueError("LOAD-011 scoped DAG has no unfinished asr_offline node")
    statuses = snapshot.get("task_node_statuses")
    if not isinstance(statuses, dict) or type(statuses.get("30")) is not int or statuses["30"] <= 0:
        raise ValueError("LOAD-011 scoped DAG has no pending status 30 node")
    return dict(snapshot)


def _require_pending_outbox_without_dag(
    case_id: str,
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    if case_id not in {"LOAD-012", "LOAD-014", "LOAD-016"}:
        raise ValueError("pending scoped work guard is invalid for this case")
    if (
        snapshot.get("pending_outbox_events") != 1
        or snapshot.get("published_outbox_events") != 0
    ):
        raise ValueError(f"{case_id} did not establish one scoped pending Outbox event")
    if snapshot.get("task_nodes") != 0:
        raise ValueError(f"{case_id} created a DAG before the recovery boundary")
    return dict(snapshot)


def _write_lease_receipt(
    scenario: Mapping[str, Any],
    lease: Mapping[str, str],
) -> None:
    run_id = scenario.get("run_id")
    raw_path = scenario.get("lease_receipt_path")
    if not isinstance(run_id, str) or not isinstance(raw_path, str):
        raise ValueError("LOAD-015 lease receipt scope is missing")
    path = Path(raw_path)
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    expected_prefix = f"m2b-{len(run_id)}-{run_id}-load-015-"
    parent = path.parent.resolve(strict=True)
    metadata = os.lstat(parent)
    if (
        path.name != "lease.json"
        or parent.parent != temporary_root
        or not parent.name.startswith(expected_prefix)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError("LOAD-015 lease receipt path is outside the current case scope")
    _write_private_input(
        path,
        {
            "schema_version": 1,
            "case_id": "LOAD-015",
            "run_id": run_id,
            "lease_id": lease["lease_id"],
            "instance_id": lease["instance_id"],
            "capability": lease["capability"],
        },
    )


def _validate_runtime_recovery_receipt_path(
    path: Path,
    case_id: str,
    run_id: str,
) -> None:
    if RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError("runtime recovery receipt run_id is invalid")
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    expected_prefix = f"m2b-{len(run_id)}-{run_id}-{case_id.lower()}-"
    parent = path.parent.resolve(strict=True)
    metadata = os.lstat(path.parent)
    if (
        case_id not in _RUNTIME_RECOVERY_TARGETS
        or not path.is_absolute()
        or path.name != "runtime-recovery.json"
        or parent.parent != temporary_root
        or not path.parent.name.startswith(expected_prefix)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError("runtime recovery receipt path is outside the current case scope")


def _write_runtime_recovery_receipt(
    case_id: str,
    scenario: Mapping[str, Any],
    resolved: Sequence[tuple[_RuntimeTarget, str, dict[str, Any]]],
) -> None:
    run_id = scenario.get("run_id")
    raw_path = scenario.get("runtime_recovery_receipt_path")
    if not isinstance(run_id, str) or not isinstance(raw_path, str):
        raise ValueError("runtime recovery receipt scope is missing")
    path = Path(raw_path)
    _validate_runtime_recovery_receipt_path(path, case_id, run_id)
    expected_targets = _runtime_recovery_targets(case_id)
    if tuple(item[0] for item in resolved) != expected_targets or any(
        _CONTAINER_ID_PATTERN.fullmatch(item[1]) is None for item in resolved
    ):
        raise ValueError("runtime recovery receipt targets are not exact")
    _write_private_input(
        path,
        {
            "schema_version": 1,
            "case_id": case_id,
            "run_id": run_id,
            "containers": [
                {
                    "resource_name": target.resource_name,
                    "container_id": container_id,
                }
                for target, container_id, _ in resolved
            ],
        },
    )
    directory_fd = os.open(
        path.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _runtime_recovery_receipts(
    case_id: str,
    run_id: str,
) -> tuple[tuple[Path, tuple[tuple[str, str], ...]], ...]:
    if RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError("runtime recovery receipt run_id is invalid")
    expected_targets = _runtime_recovery_targets(case_id)
    expected_names = tuple(target.resource_name for target in expected_targets)
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    prefix = f"m2b-{len(run_id)}-{run_id}-{case_id.lower()}-"
    validated: list[tuple[Path, tuple[tuple[str, str], ...]]] = []
    for directory in sorted(temporary_root.iterdir()):
        if not directory.name.startswith(prefix):
            continue
        directory_metadata = os.lstat(directory)
        if (
            not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(directory_metadata.st_mode) != 0o700
        ):
            raise ValueError("runtime recovery receipt directory metadata is unsafe")
        receipt = directory / "runtime-recovery.json"
        try:
            receipt_metadata = os.lstat(receipt)
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISREG(receipt_metadata.st_mode)
            or receipt_metadata.st_uid != os.geteuid()
            or receipt_metadata.st_nlink != 1
            or stat.S_IMODE(receipt_metadata.st_mode) != 0o600
            or receipt_metadata.st_size > 64 * 1024
        ):
            raise ValueError("runtime recovery receipt metadata is unsafe")
        try:
            document = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("runtime recovery receipt is not strict JSON") from exc
        if not isinstance(document, dict) or set(document) != {
            "schema_version",
            "case_id",
            "run_id",
            "containers",
        }:
            raise ValueError("runtime recovery receipt shape is invalid")
        containers = document.get("containers")
        if (
            document.get("schema_version") != 1
            or document.get("case_id") != case_id
            or document.get("run_id") != run_id
            or not isinstance(containers, list)
            or len(containers) != len(expected_names)
        ):
            raise ValueError("runtime recovery receipt identity is invalid")
        entries: list[tuple[str, str]] = []
        for expected_name, entry in zip(expected_names, containers, strict=True):
            if not isinstance(entry, dict) or set(entry) != {
                "resource_name",
                "container_id",
            }:
                raise ValueError("runtime recovery receipt container shape is invalid")
            resource_name = entry.get("resource_name")
            container_id = entry.get("container_id")
            if (
                resource_name != expected_name
                or not isinstance(container_id, str)
                or _CONTAINER_ID_PATTERN.fullmatch(container_id) is None
            ):
                raise ValueError("runtime recovery receipt container identity is invalid")
            entries.append((resource_name, container_id))
        validated.append((receipt, tuple(entries)))
    return tuple(validated)


def _validate_receipted_container(
    target: _RuntimeTarget,
    container_id: str,
) -> dict[str, Any]:
    if _CONTAINER_ID_PATTERN.fullmatch(container_id) is None:
        raise ValueError("receipted container ID is invalid")
    inspected = json.loads(_command(("docker", "inspect", container_id)))
    if not isinstance(inspected, list) or len(inspected) != 1 or not isinstance(
        inspected[0], dict
    ):
        raise ValueError("receipted Docker inspect did not return one object")
    document = inspected[0]
    labels = document.get("Config", {}).get("Labels", {})
    state = document.get("State")
    if (
        document.get("Id") != container_id
        or not isinstance(labels, dict)
        or labels.get("com.docker.compose.project") != target.compose_project
        or labels.get("com.docker.compose.service") != target.service
        or not isinstance(state, dict)
    ):
        raise ValueError("receipted Compose container identity mismatch")
    return cast(dict[str, Any], state)


def _restore_receipted_container(
    target: _RuntimeTarget,
    container_id: str,
    validated_state: Mapping[str, Any],
) -> None:
    if validated_state.get("Running") is not True:
        _command(
            ("docker", "start", container_id),
            timeout=_RUNTIME_RECOVERY_START_TIMEOUT_SECONDS,
        )


def _runtime_recovery_readiness_contract(
    case_id: str,
    targets: Sequence[_RuntimeTarget],
) -> tuple[Callable[[], Any], str]:
    expected_ids = {
        target.instance_id for target in targets if target.instance_id is not None
    }
    if expected_ids:
        return (
            lambda: _require_online_ids(expected_ids),
            "receipt restore operator registrations",
        )
    readiness_urls = tuple(
        dict.fromkeys(url for target in targets for url in target.readiness_urls)
    )
    if not readiness_urls:
        raise ValueError("runtime recovery readiness contract is empty")
    return (
        lambda: _readiness_snapshot(readiness_urls),
        "receipt restore platform readiness",
    )


def _cleanup_runtime_recovery_receipts(case_id: str, run_id: str) -> tuple[str, ...]:
    receipts = _runtime_recovery_receipts(case_id, run_id)
    if not receipts:
        return ()
    canonical_entries = receipts[0][1]
    if any(entries != canonical_entries for _, entries in receipts[1:]):
        raise ValueError("conflicting runtime recovery receipts")
    targets = _runtime_recovery_targets(case_id)
    validated_targets: list[tuple[_RuntimeTarget, str, dict[str, Any]]] = []
    for target, (resource_name, container_id) in zip(
        targets, canonical_entries, strict=True
    ):
        if resource_name != target.resource_name:
            raise ValueError("runtime recovery target ordering changed")
        state = _validate_receipted_container(target, container_id)
        validated_targets.append((target, container_id, state))
    readiness_check, readiness_label = _runtime_recovery_readiness_contract(
        case_id, targets
    )

    for target, container_id, state in validated_targets:
        _restore_receipted_container(target, container_id, state)
    wait_deadline = time.monotonic() + _runtime_recovery_wait_timeout_seconds(
        case_id
    )
    for target, container_id, _ in validated_targets:
        remaining = wait_deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("runtime recovery target readiness budget exhausted")
        _wait_until(
            partial(_require_running_healthy, container_id),
            timeout=min(_RUNTIME_RECOVERY_TARGET_READY_TIMEOUT_SECONDS, remaining),
            label=f"receipt restore {target.service}",
        )
    remaining = wait_deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("runtime recovery final readiness budget exhausted")
    _wait_until(
        readiness_check,
        timeout=min(_RUNTIME_RECOVERY_FINAL_READY_TIMEOUT_SECONDS, remaining),
        label=readiness_label,
    )
    return tuple(str(receipt) for receipt, _ in receipts)


def _cleanup_case_lease_receipts(run_id: str) -> tuple[str, ...]:
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    prefix = f"m2b-{len(run_id)}-{run_id}-load-015-"
    released: list[str] = []
    for directory in sorted(temporary_root.iterdir()):
        if not directory.name.startswith(prefix):
            continue
        directory_metadata = os.lstat(directory)
        if (
            not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(directory_metadata.st_mode) != 0o700
        ):
            raise ValueError("LOAD-015 lease receipt directory metadata is unsafe")
        receipt = directory / "lease.json"
        try:
            receipt_metadata = os.lstat(receipt)
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISREG(receipt_metadata.st_mode)
            or receipt_metadata.st_uid != os.geteuid()
            or receipt_metadata.st_nlink != 1
            or stat.S_IMODE(receipt_metadata.st_mode) != 0o600
        ):
            raise ValueError("LOAD-015 lease receipt metadata is unsafe")
        document = json.loads(receipt.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or set(document) != {
            "schema_version",
            "case_id",
            "run_id",
            "lease_id",
            "instance_id",
            "capability",
        }:
            raise ValueError("LOAD-015 lease receipt shape is invalid")
        lease_id = document.get("lease_id")
        if (
            document.get("schema_version") != 1
            or document.get("case_id") != "LOAD-015"
            or document.get("run_id") != run_id
            or document.get("capability") != _LOAD_015_LEASE_CAPABILITY
            or not isinstance(document.get("instance_id"), str)
            or not document["instance_id"]
            or not isinstance(lease_id, str)
            or not lease_id
        ):
            raise ValueError("LOAD-015 lease receipt identity is invalid")
        _release_case_lease(lease_id)
        released.append(lease_id)
    return tuple(released)


def _sanitized_lease_detail(document: Any) -> str:
    detail = document.get("detail") if isinstance(document, dict) else None
    if not isinstance(detail, str):
        return "unavailable"
    return "<service detail omitted>"


def _sanitized_lease_snapshot(snapshot: Any) -> list[dict[str, Any]] | dict[str, str]:
    if not isinstance(snapshot, list):
        return {"error": "response is not a list"}
    sanitized: list[dict[str, Any]] = []
    for item in snapshot:
        if not isinstance(item, dict):
            return {"error": "response contains a non-object"}
        sanitized.append(
            {field: item[field] for field in _LEASE_EVIDENCE_FIELDS if field in item}
        )
    return sanitized


def _lease_availability_evidence() -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    try:
        evidence["inventory"] = _sanitized_lease_snapshot(_operator_instances())
    except Exception as exc:
        evidence["inventory"] = {"error": type(exc).__name__}
    try:
        evidence["capacity"] = _sanitized_lease_snapshot(
            _http_json(_CAPACITY_SNAPSHOT_URL)
        )
    except Exception as exc:
        evidence["capacity"] = {"error": type(exc).__name__}
    return evidence


def _acquire_case_lease(case_id: str, scenario: Mapping[str, Any]) -> dict[str, str]:
    if (
        case_id != "LOAD-015"
        or scenario.get("lease_capability") != _LOAD_015_LEASE_CAPABILITY
        or scenario.get("redis_scope") != "algorithm:operator-lease:facerec"
    ):
        raise ValueError("LOAD-015 lease scope does not match the fixed contract")
    url = f"{_CONTROL_URL}/internal/operator-instances/lease"
    payload = {"capability": _LOAD_015_LEASE_CAPABILITY, "ttl_seconds": 3600}
    deadline = time.monotonic() + _LOAD_015_LEASE_ACQUIRE_TIMEOUT_SECONDS
    while True:
        status_code, document = _post_json_status(url, payload)
        if 200 <= status_code < 300:
            break
        detail = _sanitized_lease_detail(document)
        if status_code != 503:
            raise ValueError(
                f"production lease API returned HTTP {status_code}: detail={detail}"
            )
        evidence = {
            "status_code": status_code,
            "detail": detail,
            **_lease_availability_evidence(),
        }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError(
                "LOAD-015 lease capacity unavailable after bounded retry: "
                + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
            )
        time.sleep(min(_LOAD_015_LEASE_RETRY_INTERVAL_SECONDS, remaining))
    if not isinstance(document, dict):
        raise ValueError("LOAD-015 lease response is not an object")
    lease = {
        field: document.get(field)
        for field in ("lease_id", "instance_id", "capability")
    }
    if any(not isinstance(value, str) or not value for value in lease.values()):
        raise ValueError("LOAD-015 lease response is incomplete")
    if lease["capability"] != _LOAD_015_LEASE_CAPABILITY:
        raise ValueError("LOAD-015 lease capability does not match")
    return cast(dict[str, str], lease)


def _release_case_lease(lease_id: str) -> LeaseReleaseResult:
    if not lease_id:
        raise ValueError("LOAD-015 lease_id is empty")
    status_code, document = _post_json_status(
        f"{_CONTROL_URL}/internal/operator-instances/release",
        {"lease_id": lease_id},
    )
    if status_code == 404:
        return LeaseReleaseResult(
            http_status=status_code,
            status="ALREADY_RELEASED",
        )
    if status_code != 200:
        raise ValueError(f"LOAD-015 lease release returned HTTP {status_code}")
    if not isinstance(document, dict):
        raise ValueError("LOAD-015 lease release response is not an object")
    release_status = document.get("status")
    if document.get("lease_id") != lease_id or release_status not in {
        "RELEASED",
        "ALREADY_RELEASED",
    }:
        raise ValueError("LOAD-015 lease release response is invalid")
    return LeaseReleaseResult(
        http_status=status_code,
        status=cast(Literal["RELEASED", "ALREADY_RELEASED"], release_status),
    )


def _restart_and_recover(case_id: str, scenario: Mapping[str, Any]) -> dict[str, Any]:
    target = _RUNTIME_TARGETS[case_id]
    required = {
        "container": target.resource_name,
        "compose_file": target.compose_file,
        "compose_project": target.compose_project,
        "service": target.service,
    }
    if any(scenario.get(field) != value for field, value in required.items()):
        raise ValueError("缺少真实恢复事实：canonical target 不完整")
    if case_id == "LOAD-011" and (
        scenario.get("containers")
        != [item.resource_name for item in _LOAD_011_TARGETS]
        or scenario.get("services") != [item.service for item in _LOAD_011_TARGETS]
    ):
        raise ValueError("缺少真实恢复事实：LOAD-011 三实例 target 不完整")
    if case_id == "LOAD-016" and (
        scenario.get("support_container")
        != _LOAD_016_ORCHESTRATOR_TARGET.resource_name
        or scenario.get("support_service") != _LOAD_016_ORCHESTRATOR_TARGET.service
    ):
        raise ValueError("缺少真实恢复事实：LOAD-016 orchestrator target 不完整")
    release_root = scenario.get("release_root")
    if not isinstance(release_root, str) or not Path(release_root).is_absolute():
        raise ValueError("缺少真实恢复事实：release root 不完整")
    mutation_targets = _runtime_recovery_targets(case_id)
    resolved_mutation_targets = tuple(
        (item, *_resolve_container(item)) for item in mutation_targets
    )
    container_id = resolved_mutation_targets[0][1]
    before_container = resolved_mutation_targets[0][2]
    support_container_id: str | None = None
    if case_id == "LOAD-016":
        support_container_id = resolved_mutation_targets[1][1]
    _write_runtime_recovery_receipt(
        case_id, scenario, resolved_mutation_targets
    )
    kafka_id: str | None = None
    before_offsets: dict[str, int] | None = None
    if case_id in {"LOAD-012", "LOAD-014"}:
        kafka_id, _ = _resolve_container(_RUNTIME_TARGETS["LOAD-014"])
        before_offsets = _kafka_offsets(kafka_id)
    course_task_id = (
        _scenario_task_id(case_id, scenario) if case_id in _COURSE_FACT_CASES else None
    )
    course_before = (
        _prepare_course_fact(case_id, scenario)
        if course_task_id is not None
        and case_id not in {"LOAD-011", "LOAD-012", "LOAD-014", "LOAD-016"}
        else None
    )
    before_db = (
        _database_snapshot()
        if case_id in {"LOAD-011", "LOAD-012", "LOAD-013", "LOAD-016"}
        else None
    )
    before_instances = (
        _operator_instances()
        if case_id in {"LOAD-010", "LOAD-011", "LOAD-013", "LOAD-015"}
        else None
    )
    before_active_lease_count: int | None = None
    lease: dict[str, str] | None = None
    lease_release_result: LeaseReleaseResult | None = None
    if case_id == "LOAD-015":
        initial_active = _active_lease_count(_http_json(_CAPACITY_SNAPSHOT_URL))
        if initial_active != 0:
            raise ValueError("LOAD-015 initial active lease count must be zero")
        lease = _acquire_case_lease(case_id, scenario)
        lease_setup_complete = False
        try:
            _write_lease_receipt(scenario, lease)
            before_active_lease_count = _active_lease_count(
                _http_json(_CAPACITY_SNAPSHOT_URL)
            )
            if before_active_lease_count != 1:
                raise ValueError(
                    "缺少真实恢复事实：production lease API 未建立唯一真实租约"
                )
            lease_setup_complete = True
        finally:
            if not lease_setup_complete:
                _release_case_lease(lease["lease_id"])
                lease = None

    restore_container_ids: list[str] = []
    stopped_fact: dict[str, Any] | None = None
    stopped_states: dict[str, dict[str, Any]] = {}
    course_after: dict[str, Any] | None = None
    after_offsets: dict[str, int] | None = None
    try:
        if case_id == "LOAD-011":
            _require_online_ids(
                {
                    item.instance_id
                    for item in _LOAD_011_TARGETS
                    if item.instance_id is not None
                }
            )
        elif target.instance_id is not None:
            _require_instance(target.instance_id, "ONLINE")
        if case_id == "LOAD-010":
            restore_container_ids.append(container_id)
            _command(("docker", "stop", "--time", "30", container_id), timeout=45)
        elif case_id == "LOAD-011":
            for _, current_id, _ in resolved_mutation_targets:
                restore_container_ids.append(current_id)
                _command(
                    ("docker", "kill", "--signal", "KILL", current_id),
                    timeout=15,
                )
            for current_target, current_id, _ in resolved_mutation_targets:
                state = _inspect_state(current_id)
                stopped_states[current_target.service] = state
                if state.get("Running") is not False:
                    raise ValueError(
                        f"LOAD-011 operator did not stop: {current_target.service}"
                    )
            _wait_until(
                lambda: [
                    _require_instance(item.instance_id or "", "OFFLINE")
                    for item in _LOAD_011_TARGETS
                ],
                timeout=30,
                label="all asr_offline instances TTL offline",
            )
            assert course_task_id is not None
            course_before = _prepare_course_fact(case_id, scenario)
            course_before = _wait_until(
                lambda: _require_unfinished_operator_work(
                    case_id, _course_fact_snapshot(course_task_id)
                ),
                timeout=180,
                label="scoped pending asr_offline DAG",
            )
            during = _course_fact_snapshot(course_task_id)
            _require_unfinished_operator_work(case_id, during)
            if any(
                status in {"60", "70", "80"} and int(count) > 0
                for status, count in during["task_node_statuses"].items()
            ):
                raise ValueError("SIGKILL falsely reported scoped unfinished work terminal")
            for _, current_id, _ in resolved_mutation_targets:
                _command(("docker", "start", current_id), timeout=60)
        elif case_id in {"LOAD-012", "LOAD-014"}:
            restore_container_ids.append(container_id)
            _command(("docker", "stop", "--time", "30", container_id), timeout=45)
            assert course_task_id is not None
            course_before = _prepare_course_fact(case_id, scenario)
            _require_pending_outbox_without_dag(case_id, course_before)
            _command(("docker", "start", container_id), timeout=60)
        elif case_id == "LOAD-016":
            assert support_container_id is not None and course_task_id is not None
            restore_container_ids.append(support_container_id)
            _command(
                ("docker", "stop", "--time", "30", support_container_id),
                timeout=45,
            )
            support_stopped = _inspect_state(support_container_id)
            if support_stopped.get("Running") is not False:
                raise ValueError("LOAD-016 orchestrator did not stop")
            course_before = _prepare_course_fact(case_id, scenario)
            _require_pending_outbox_without_dag(case_id, course_before)
            restore_container_ids.append(container_id)
            _command(("docker", "restart", container_id), timeout=120)
            _wait_until(
                lambda: _require_running_healthy(container_id),
                timeout=180,
                label=target.service,
            )
            _wait_until(
                lambda: _readiness_snapshot(target.readiness_urls[:1]),
                timeout=180,
                label="PostgreSQL-backed control readiness",
            )
            _command(("docker", "start", support_container_id), timeout=60)
            _wait_until(
                lambda: _require_running_healthy(support_container_id),
                timeout=180,
                label="orchestrator-service",
            )
        else:
            restore_container_ids.append(container_id)
            _command(("docker", "restart", container_id), timeout=120)

        if case_id == "LOAD-010":
            stopped_fact = _inspect_state(container_id)
            _require_graceful_stop_state(stopped_fact)
            assert target.instance_id is not None
            _wait_until(
                lambda: _require_instance_not_routable(target.instance_id or ""),
                timeout=30,
                label="operator graceful stop",
            )
            _command(("docker", "start", container_id), timeout=60)

        for current_target, current_id, _ in resolved_mutation_targets:
            _wait_until(
                partial(_require_running_healthy, current_id),
                timeout=180,
                label=current_target.service,
            )
        readiness = _wait_until(
            lambda: _readiness_snapshot(target.readiness_urls),
            timeout=180,
            label="service readiness",
        )
        after_db = _database_snapshot() if before_db is not None else None
        after_instances = _operator_instances() if before_instances is not None else None
        if case_id in {"LOAD-012", "LOAD-014", "LOAD-016"}:
            assert course_task_id is not None and course_before is not None

            def require_recovery_progress() -> tuple[dict[str, Any], dict[str, int] | None]:
                candidate = _course_fact_snapshot(course_task_id)
                candidate_offsets = (
                    _kafka_offsets(kafka_id)
                    if kafka_id is not None and before_offsets is not None
                    else None
                )
                _require_scoped_recovery_progress(
                    case_id,
                    course_before,
                    candidate,
                    before_offsets=before_offsets,
                    after_offsets=candidate_offsets,
                )
                return candidate, candidate_offsets

            recovered_progress = _wait_until(
                require_recovery_progress,
                timeout=180,
                label="scoped persisted work",
            )
            course_after, after_offsets = recovered_progress
        elif course_task_id is not None:
            course_after = _course_fact_snapshot(course_task_id)
        if case_id in {"LOAD-012", "LOAD-013"}:
            assert before_db is not None and after_db is not None
            _require_database_facts_preserved(before_db, after_db)
        if case_id == "LOAD-011":
            _wait_until(
                lambda: _require_online_ids(
                    {
                        item.instance_id
                        for item in _LOAD_011_TARGETS
                        if item.instance_id is not None
                    }
                ),
                timeout=180,
                label="all asr_offline registrations",
            )
        elif target.instance_id is not None:
            _wait_until(
                lambda: _require_instance(target.instance_id or "", "ONLINE"),
                timeout=180,
                label="operator registration",
            )
        if case_id in {"LOAD-013", "LOAD-015"}:
            assert before_instances is not None
            expected_ids = {
                str(item.get("instance_id"))
                for item in before_instances
                if item.get("lifecycle") == "ONLINE"
            }
            if not expected_ids:
                raise ValueError("缺少真实恢复事实：重启前无 ONLINE 实例")
            recovered = _wait_until(
                lambda: _require_online_ids(expected_ids), timeout=180, label="operator heartbeats"
            )
            if case_id == "LOAD-015":
                assert lease is not None
                lease_release_result = _release_case_lease(lease["lease_id"])
                if lease_release_result.status != "ALREADY_RELEASED":
                    lease = None
                    raise ValueError("Redis restart incorrectly preserved the old active lease")
                lease = None
                after_active_lease_count = _active_lease_count(
                    _http_json(_CAPACITY_SNAPSHOT_URL)
                )
                if after_active_lease_count != 0:
                    raise ValueError("Redis restart fabricated active lease recovery")
            after_instances = recovered
        return {
            "container_id": container_id,
            "container_ids": [item[1] for item in resolved_mutation_targets],
            "service": target.service,
            "action": "SIGTERM"
            if case_id == "LOAD-010"
            else "SIGKILL"
            if case_id == "LOAD-011"
            else "restart",
            "before_started_at": before_container["State"].get("StartedAt"),
            "stopped_state": stopped_fact,
            "stopped_states": stopped_states,
            "readiness": readiness,
            "before_database": before_db,
            "after_database": after_db,
            "course_task_id": course_task_id,
            "before_course_fact": course_before,
            "after_course_fact": course_after,
            "before_offsets": before_offsets,
            "after_offsets": after_offsets,
            "before_instance_count": len(before_instances or ()),
            "after_instance_count": len(after_instances or ()),
            "before_active_lease_count": before_active_lease_count,
            "lease_release_http_status": (
                lease_release_result.http_status
                if lease_release_result is not None
                else None
            ),
            "lease_release_status": (
                lease_release_result.status if lease_release_result is not None else None
            ),
            "restored_running": True,
        }
    finally:
        cleanup_errors: list[str] = []
        for restore_id in dict.fromkeys(restore_container_ids):
            try:
                state = _inspect_state(restore_id)
                if state.get("Running") is not True:
                    _command(("docker", "start", restore_id), timeout=60)
                _wait_until(
                    partial(_require_running_healthy, restore_id),
                    timeout=180,
                    label="finally restore",
                )
            except Exception as exc:
                cleanup_errors.append(
                    f"canonical runtime restore failed for {restore_id}: {exc}"
                )
        if lease is not None:
            try:
                _release_case_lease(lease["lease_id"])
            except Exception as exc:
                cleanup_errors.append(f"LOAD-015 lease cleanup failed: {exc}")
        if course_task_id is not None:
            try:
                _cleanup_course_fact(course_task_id)
            except Exception as exc:
                cleanup_errors.append(f"scoped course fact cleanup failed: {exc}")
        if cleanup_errors:
            raise ValueError("; ".join(cleanup_errors))


def _require_running_healthy(container_id: str) -> dict[str, Any]:
    state = _inspect_state(container_id)
    health = state.get("Health")
    if state.get("Running") is not True or (
        isinstance(health, dict) and health.get("Status") != "healthy"
    ):
        raise ValueError("container is not running and healthy")
    return state


def _require_online_ids(expected: set[str]) -> list[dict[str, Any]]:
    instances = _operator_instances()
    online = {
        str(item.get("instance_id"))
        for item in instances
        if item.get("lifecycle") == "ONLINE" and item.get("model_ready") is True
    }
    if not expected.issubset(online):
        raise ValueError(f"operator heartbeats are incomplete: {sorted(expected - online)}")
    return instances


def _scratch_directory(case_id: str, scenario: Mapping[str, Any]) -> Path:
    raw = scenario.get("scratch_directory")
    run_id = scenario.get("run_id")
    if not isinstance(raw, str) or not isinstance(run_id, str):
        raise ValueError("隔离变异缺少当前 case 私有目录")
    path = Path(raw)
    expected_prefix = f"m2b-{len(run_id)}-{run_id}-{case_id.lower()}-"
    metadata = os.lstat(path)
    if (
        not path.is_absolute()
        or path.parent.resolve(strict=True) != Path(tempfile.gettempdir()).resolve(strict=True)
        or not path.name.startswith(expected_prefix)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError("隔离变异目录不属于当前 run/case")
    return path


def _require_fields(document: Mapping[str, Any], fields: Sequence[str], label: str) -> None:
    missing = [field for field in fields if not document.get(field)]
    if missing:
        raise ValueError(f"{label} missing required facts: {missing}")


def _validate_controlled_bad_evidence(case_id: str, document: Mapping[str, Any]) -> None:
    if case_id == "LOAD-017":
        _require_fields(
            document,
            ("registration_response", "lease_request", "lease_response", "lease_release"),
            "real lease evidence",
        )
    elif case_id == "LOAD-018":
        _require_fields(
            document,
            ("health_response", "inference_request", "inference_response", "instance_id"),
            "real inference evidence",
        )
    elif case_id == "LOAD-019":
        source = document.get("test_source")
        if not isinstance(source, str):
            raise ValueError("test source is missing")
        tree = ast.parse(source)
        forbidden = {"complete_node", "update_task_type_state", "complete_task"}
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        if calls & forbidden:
            raise ValueError("test directly calls Repository completion method")
    elif case_id == "LOAD-020":
        if document.get("status") == "通过" and document.get("executed") is not True:
            raise ValueError("unexecuted negative case cannot be marked passed")
    elif case_id == "LOAD-021":
        paths = document.get("evidence_paths")
        if (
            not isinstance(paths, list)
            or not paths
            or any(not isinstance(path, str) or not path for path in paths)
        ):
            raise ValueError("B-level sample has no evidence path")
    elif case_id == "LOAD-022":
        if document.get("student_count_source") == "mock" or document.get("mock") is True:
            raise ValueError("mock student_count cannot be reported as real annotation")
    elif case_id == "LOAD-023":
        original = document.get("original_running")
        restored = document.get("restored_running")
        if not isinstance(original, list) or restored != original:
            raise ValueError("original running containers were not exactly restored")
    elif case_id == "LOAD-024":
        residue = document.get("residue")
        if not isinstance(residue, dict) or any(value for value in residue.values()):
            raise ValueError("isolated Topic/Group/Redis/database cleanup residue remains")
    elif case_id == "LOAD-025":
        if document.get("process_ids") or document.get("cuda_pids"):
            raise ValueError("test process or CUDA PID residue remains")
    elif case_id == "LOAD-026":
        targets = document.get("delete_targets")
        if not isinstance(targets, list):
            raise ValueError("cleanup delete targets are missing")
        if any(
            Path(target) == Path("/data/result") or Path("/data/result") in Path(target).parents
            for target in targets
            if isinstance(target, str)
        ):
            raise ValueError("cleanup must never delete /data/result")
    else:
        raise ValueError("isolated load checker is not registered")


_CONTROLLED_MUTATIONS: Mapping[str, Mapping[str, Any]] = {
    "LOAD-017": {"registration_response": {"instance_id": "x"}},
    "LOAD-018": {"health_response": {"status": "ok"}},
    "LOAD-019": {"test_source": "repository.complete_node(1, {})"},
    "LOAD-020": {"status": "通过", "executed": False},
    "LOAD-021": {"sample_status": "B", "evidence_paths": []},
    "LOAD-022": {"student_count": 30, "student_count_source": "mock", "mock": True},
    "LOAD-023": {"original_running": ["ocr-v6-amd"], "restored_running": []},
    "LOAD-024": {
        "residue": {"topics": ["m2b.run.case"], "groups": [], "redis_keys": [], "databases": []}
    },
    "LOAD-025": {"process_ids": [12345], "cuda_pids": [12345]},
    "LOAD-026": {"delete_targets": ["/data/result/task-1"]},
}


def _controlled_rejection(case_id: str, scenario: Mapping[str, Any]) -> dict[str, Any]:
    scratch = _scratch_directory(case_id, scenario)
    mutation = dict(_CONTROLLED_MUTATIONS[case_id])
    mutation_path = scratch / "mutation.json"
    _write_private_input(mutation_path, mutation)
    try:
        _validate_controlled_bad_evidence(case_id, mutation)
    except (SyntaxError, ValueError) as exc:
        return {
            "mutation_file": mutation_path.name,
            "mutation_directory": scratch.name,
            "validator": "_validate_controlled_bad_evidence",
            "rejection_detail": str(exc),
            "canonical_evidence_modified": False,
            "result_directory_deleted": False,
        }
    raise ValueError("controlled bad evidence was accepted")


def evaluate_scenario(case_id: str, scenario: Mapping[str, Any]) -> dict[str, Any]:
    spec = CASE_SPECS.get(case_id)
    mutation = scenario.get("mutation")
    if (
        spec is None
        or case_id not in _LOAD_CASES
        or scenario.get("schema_version") != 1
        or scenario.get("case_id") != case_id
        or scenario.get("mode") != spec.mode
        or not isinstance(mutation, dict)
        or mutation.get("case") != case_id
    ):
        return {
            "case_id": case_id,
            "status": "失败",
            "reason": "load 输入与固定 checker 不匹配",
            "observed": {"input_valid": False},
        }
    try:
        observed = (
            _restart_and_recover(case_id, scenario)
            if case_id in _CANONICAL_CASES
            else _controlled_rejection(case_id, scenario)
        )
    except (
        OSError,
        RuntimeError,
        ValueError,
        psycopg.Error,
        subprocess.SubprocessError,
        urllib.error.URLError,
    ) as exc:
        prefix = (
            "缺少真实恢复事实" if case_id in _CANONICAL_CASES else "隔离变异或清理合同未得到验证"
        )
        return {
            "case_id": case_id,
            "status": "失败",
            "reason": f"{prefix}：{exc}",
            "observed": {"detail": str(exc)},
        }
    return {"case_id": case_id, "status": "通过", "reason": spec.reason, "observed": observed}


def checker_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--check", required=True, choices=sorted(CASE_SPECS))
    parser.add_argument("--input", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        document = json.loads(arguments.input.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("checker input must be a JSON object")
        result = evaluate_scenario(arguments.check, document)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        result = {
            "case_id": arguments.check,
            "status": "失败",
            "reason": f"load checker 输入失败：{exc}",
            "observed": {"input_valid": False},
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "通过" else 1


if __name__ == "__main__":
    raise SystemExit(checker_main())
