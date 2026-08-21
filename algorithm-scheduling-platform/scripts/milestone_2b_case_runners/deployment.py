from __future__ import annotations

import argparse
import hashlib
import json
import os
import runpy
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from deploy.scripts import (
    deployment_contracts,
    model_asset_transaction,
    preflight_checks,
)
from deploy.scripts.verify_operator_registration import validate_instances
from scripts.milestone_2b_case_catalog import CaseDefinition

from .base import CaseContext, CaseOutcome
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

ScenarioMode = Literal["controlled_input", "canonical_runtime"]
ScenarioBuilder = Callable[[CaseContext, CaseDefinition], dict[str, Any]]
ResourceBuilder = Callable[[CaseContext, CaseDefinition], tuple[ResourceSpec, ...]]
_PLATFORM_ROOT = Path(__file__).resolve().parents[2]
_WRITABLE_DEPLOYMENT_CASES = frozenset(
    {"DEP-013", "DEP-015", "DEP-016", "DEP-019", "DEP-020"}
)


@dataclass(frozen=True, slots=True)
class FoundationCaseSpec:
    title: str
    expected: str
    safety: CaseSafety
    timeout_seconds: int
    mode: ScenarioMode
    status: Literal["通过"] = "通过"

    @property
    def reason(self) -> str:
        return f"反例符合预期：{self.expected}"


def _spec(
    title: str,
    expected: str,
    *,
    safety: CaseSafety = "isolated_mutation",
    timeout_seconds: int = 120,
    mode: ScenarioMode = "controlled_input",
) -> FoundationCaseSpec:
    return FoundationCaseSpec(
        title=title,
        expected=expected,
        safety=safety,
        timeout_seconds=timeout_seconds,
        mode=mode,
    )


CASE_SPECS: Mapping[str, FoundationCaseSpec] = {
    "DEP-001": _spec("x86_64 服务器误用 ARM 镜像", "预检或构建失败，不能启动到业务探针"),
    "DEP-002": _spec("镜像缺少源 commit label", "发布预检失败"),
    "DEP-003": _spec("镜像标签不是小写或不符合 v1.0_YYMMDD", "构建脚本拒绝"),
    "DEP-004": _spec("同一 instance_id 被两个容器使用", "第二个实例不得同时进入可路由状态"),
    "DEP-005": _spec("两个实例占用同一宿主机端口", "Compose 启动失败且指出冲突实例"),
    "DEP-006": _spec("VBas 容器仍监听旧 8881", "健康检查失败，不能注册为可用实例"),
    "DEP-007": _spec("算子以两个 Uvicorn worker 启动", "部署预检失败"),
    "DEP-009": _spec(
        "Compose 只设置 NVIDIA_VISIBLE_DEVICES 而没有 GPU reservation",
        "GPU 预检失败",
    ),
    "DEP-010": _spec("算子镜像缺少注册客户端 wheel", "镜像导入测试失败"),
    "DEP-011": _spec("wheel 版本不是 0.2.0", "镜像构建或运行预检失败"),
    "DEP-012": _spec("模型目录不存在", "对应镜像构建或 readiness 失败"),
    "DEP-013": _spec(
        "模型文件数量或清单哈希与传输前不一致",
        "模型校验失败，不进入构建",
    ),
    "DEP-014": _spec("config.toml 未挂载或路径错误", "服务启动失败并给出配置路径原因"),
    "DEP-015": _spec("/data/course 不可写", "平台部署预检失败"),
    "DEP-016": _spec("/data/result 不可写", "平台部署预检失败"),
    "DEP-017": _spec("根分区可用空间低于 100 GiB", "停止构建、传模和下载"),
    "DEP-018": _spec(
        "服务器已有同名 algorithm-* 容器",
        "停止并要求人工确认，不覆盖未知容器",
    ),
    "DEP-019": _spec("现有业务容器快照不完整", "不允许暂停现有业务容器"),
    "DEP-020": _spec("Deploy Key 无法读取固定提交", "不使用不明来源代码继续部署"),
}


def _write_private_input(path: Path, document: Mapping[str, Any]) -> None:
    payload = (
        json.dumps(
            dict(document),
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise ValueError("foundation checker input short write")
            offset += written
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _assert_case_contract(
    *,
    case: CaseDefinition,
    case_id: str,
    group: str,
    spec: FoundationCaseSpec,
) -> None:
    expected_runner = f"{group}.{case_id.lower().replace('-', '_')}"
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
        "negative",
        "deployment",
        spec.title,
        spec.expected,
        expected_runner,
        spec.timeout_seconds,
        spec.safety,
    )
    if actual != expected:
        raise ValueError(f"{case_id} catalog contract changed")


def _decode_checker_result(result: CommandResult, case_id: str) -> dict[str, Any]:
    if result.stdout_truncated or result.stderr_truncated:
        raise ValueError(f"{case_id} checker output was truncated")
    try:
        document = json.loads(result.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{case_id} checker stdout is not strict JSON") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{case_id} checker stdout must be a JSON object")
    return cast(dict[str, Any], document)


async def run_foundation_case(
    *,
    context: CaseContext,
    case: CaseDefinition,
    case_id: str,
    group: str,
    spec: FoundationCaseSpec,
    scenario_builder: ScenarioBuilder,
    resource_builder: ResourceBuilder,
) -> CaseOutcome:
    _assert_case_contract(case=case, case_id=case_id, group=group, spec=spec)
    scenario = scenario_builder(context, case)
    required = {
        "schema_version": 1,
        "case_id": case_id,
        "run_id": context.run_id,
        "target": context.target,
        "mode": spec.mode,
    }
    if not isinstance(scenario, dict):
        raise ValueError(f"{case_id} scenario builder must return an object")
    scenario_document = {**scenario, **required}
    prefix = f"m2b-{len(context.run_id)}-{context.run_id}-{case_id.lower()}-"
    with tempfile.TemporaryDirectory(prefix=prefix) as directory:
        os.chmod(directory, 0o700)
        input_path = Path(directory) / "input.json"
        _write_private_input(input_path, scenario_document)
        resources = (
            ResourceSpec("filesystem", str(input_path)),
            *resource_builder(context, case),
        )
        command = CommandSpec(
            action=FoundationCheckAction(
                group=cast(Any, group),
                case_id=case_id,
                resources=resources,
            )
        )
        result = await run_command(
            context=context,
            command=command,
            timeout_seconds=spec.timeout_seconds,
        )
        command_display = [*result.argv[:-1], "<current-case-input>"]
        evidence_path = publish_case_evidence(
            context=context,
            case=case,
            name="foundation-check.json",
            payload={
                "group": group,
                "mode": spec.mode,
                "resources": [
                    {"kind": resource.kind, "name": resource.name}
                    for resource in resources[1:]
                ],
                "command": command_display,
                "returncode": result.returncode,
                "stdout": result.stdout.decode("utf-8", errors="replace"),
                "stderr": result.stderr.decode("utf-8", errors="replace"),
            },
        )
        document = _decode_checker_result(result, case_id)
        if result.returncode != 0:
            reason = document.get("reason")
            raise ValueError(f"{case_id} checker failed closed: {reason}")
        if document.get("case_id") != case_id:
            raise ValueError(f"{case_id} checker case_id does not match")
        if document.get("status") != spec.status:
            raise ValueError(f"{case_id} checker status does not match")
        if document.get("reason") != spec.reason:
            raise ValueError(f"{case_id} checker reason does not match")
        observed = document.get("observed")
        if not isinstance(observed, dict) or not observed:
            raise ValueError(f"{case_id} checker observed facts are missing")
        return CaseOutcome(
            status=spec.status,
            reason=spec.reason,
            evidence=(evidence_path,),
        )


async def run_foundation_cleanup(
    *,
    context: CaseContext,
    case: CaseDefinition,
    group: str,
    spec: FoundationCaseSpec,
) -> None:
    case_id = case.case_id
    _assert_case_contract(case=case, case_id=case_id, group=group, spec=spec)
    resources = foundation_cleanup_resources(
        cast(Any, group), case_id, context.run_id
    )
    result = await run_command(
        context=context,
        command=CommandSpec(
            action=FoundationCleanupAction(
                group=cast(Any, group),
                case_id=case_id,
                run_id=context.run_id,
                resources=resources,
            )
        ),
        timeout_seconds=min(spec.timeout_seconds, 30),
    )
    document = _decode_checker_result(result, case_id)
    if result.returncode != 0:
        raise ValueError(f"{case_id} cleanup failed closed: {document.get('errors')}")
    expected_identity = (case_id, group, context.run_id)
    actual_identity = (
        document.get("case_id"),
        document.get("group"),
        document.get("run_id"),
    )
    if actual_identity != expected_identity:
        raise ValueError(f"{case_id} cleanup identity does not match")
    if (
        document.get("status") != "clean"
        or document.get("errors") != []
        or document.get("residual_temp_directories") != []
    ):
        raise ValueError(f"{case_id} cleanup did not prove no residue")


def _deployment_scenario(
    context: CaseContext, case: CaseDefinition
) -> dict[str, Any]:
    scenario = {
        "mutation": {"case": case.case_id},
        "platform_compose": "deploy/docker-compose.platform.yml",
        "operator_compose": "deploy/docker-compose.operators.yml",
        "build_context_checker": "deploy/scripts/verify-operator-build-contexts",
        "preflight_checker": "deploy/scripts/preflight_checks.py",
    }
    if case.case_id in _WRITABLE_DEPLOYMENT_CASES:
        scenario["scratch_directory"] = str(
            _deployment_scratch_path(context, case)
        )
    return scenario


def _deployment_scratch_path(
    context: CaseContext, case: CaseDefinition
) -> Path:
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    prefix = (
        f"m2b-{len(context.run_id)}-{context.run_id}-"
        f"{case.case_id.lower()}-scratch-"
    )
    return temporary_root / f"{prefix}{os.getpid()}-{id(context):x}"


def _deployment_resources(
    context: CaseContext, case: CaseDefinition
) -> tuple[ResourceSpec, ...]:
    if case.case_id not in _WRITABLE_DEPLOYMENT_CASES:
        return ()
    scratch = _deployment_scratch_path(context, case)
    os.mkdir(scratch, 0o700)
    os.chmod(scratch, 0o700)
    return (ResourceSpec("filesystem", str(scratch)),)


async def _run(
    context: CaseContext, case: CaseDefinition, case_id: str
) -> CaseOutcome:
    return await run_foundation_case(
        context=context,
        case=case,
        case_id=case_id,
        group="deployment",
        spec=CASE_SPECS[case_id],
        scenario_builder=_deployment_scenario,
        resource_builder=_deployment_resources,
    )


async def dep_001(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-001")


async def dep_002(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-002")


async def dep_003(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-003")


async def dep_004(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-004")


async def dep_005(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-005")


async def dep_006(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-006")


async def dep_007(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-007")


async def dep_009(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-009")


async def dep_010(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-010")


async def dep_011(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-011")


async def dep_012(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-012")


async def dep_013(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-013")


async def dep_014(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-014")


async def dep_015(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-015")


async def dep_016(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-016")


async def dep_017(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-017")


async def dep_018(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-018")


async def dep_019(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-019")


async def dep_020(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "DEP-020")


async def cleanup(context: CaseContext, case: CaseDefinition) -> None:
    spec = CASE_SPECS.get(case.case_id)
    if spec is None:
        raise ValueError("deployment cleanup case is not registered")
    await run_foundation_cleanup(
        context=context,
        case=case,
        group="deployment",
        spec=spec,
    )


for _case_id in CASE_SPECS:
    globals()[_case_id.lower().replace("-", "_")].cleanup = cleanup


def _expect_error(
    check: Callable[[], object],
    *,
    exception: type[Exception],
    fragment: str,
) -> dict[str, Any]:
    try:
        check()
    except exception as exc:
        detail = str(exc)
        if fragment not in detail:
            raise ValueError(
                f"checker reason does not contain required detail: {fragment}"
            ) from exc
        return {"rejected": True, "detail": detail}
    raise ValueError(f"controlled mutation was not rejected: {fragment}")


def _check_dep_001() -> dict[str, Any]:
    return _expect_error(
        lambda: deployment_contracts.validate_release_architecture(
            "x86_64", ["arm64"]
        ),
        exception=deployment_contracts.DeploymentContractError,
        fragment="image architecture",
    )


def _check_dep_002() -> dict[str, Any]:
    image_id = "sha256:" + "1" * 64
    return _expect_error(
        lambda: preflight_checks.validate_image_revisions(
            [{"Id": image_id, "Config": {"Labels": {}}}],
            expected_image_ids=[image_id],
            expected_git_sha="2" * 40,
        ),
        exception=preflight_checks.PreflightError,
        fragment="image revision label is missing",
    )


def _check_dep_003() -> dict[str, Any]:
    command = [str(_PLATFORM_ROOT / "deploy/scripts/build-images"), "V1.0_260818"]
    completed = subprocess.run(
        command,
        cwd=_PLATFORM_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    if completed.returncode == 0 or "image tag must match" not in completed.stderr:
        raise ValueError("生产构建脚本没有拒绝非法镜像标签")
    return {
        "command": command,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
    }


def _check_dep_004() -> dict[str, Any]:
    instance_id = "m2b-controlled-duplicate"
    contract = {
        instance_id: {
            "operator_code": "vbas",
            "capabilities": {"teacher_behavior"},
            "service_url": "http://vbas:8981",
            "declared_capacity": 1,
            "gpu": "0",
        }
    }
    row = {
        "instance_id": instance_id,
        "operator_code": "vbas",
        "capabilities": ["teacher_behavior"],
        "service_url": "http://vbas:8981",
        "lifecycle": "ONLINE",
        "model_ready": True,
        "declared_capacity": 1,
        "inflight": 0,
        "last_heartbeat_at": "2026-08-18T00:00:00+00:00",
        "labels": {"gpu": "0"},
    }
    issues, observed = validate_instances([row, dict(row)], contract)
    if not any(issue == f"重复实例: {instance_id}" for issue in issues):
        raise ValueError("重复 instance_id 未被注册验证器识别")
    return {
        "issues": issues,
        "validator_observed_instance_ids": sorted(observed),
        "validator_observed_count": len(observed),
    }


def _check_dep_005() -> dict[str, Any]:
    services = {
        "first": {"ports": [{"published": 18000, "target": 8000, "protocol": "tcp"}]},
        "second": {"ports": [{"published": 18000, "target": 8001, "protocol": "tcp"}]},
    }
    return _expect_error(
        lambda: preflight_checks._published_ports(services, "operator"),
        exception=preflight_checks.PreflightError,
        fragment="duplicate published port 18000",
    )


def _check_dep_006() -> dict[str, Any]:
    services = {
        "vbas-gpu0": {
            "ports": [
                {
                    "published": 18981,
                    "target": 8881,
                    "protocol": "tcp",
                    "host_ip": "127.0.0.1",
                }
            ]
        }
    }
    return _expect_error(
        lambda: preflight_checks._validate_port_contract(
            services,
            "operator",
            {"vbas-gpu0": (18981, 8981, "tcp", "127.0.0.1")},
        ),
        exception=preflight_checks.PreflightError,
        fragment="operator Compose port mapping is not canonical",
    )


def _operator_service(
    service_name: str,
    *,
    workers: str = "1",
) -> dict[str, Any]:
    config_path = (
        "/workspace/config.toml"
        if service_name.startswith("vbas-")
        else "/app/config.toml"
    )
    return {
        "environment": {
            "CONFIG_PATH": config_path,
            "UVICORN_WORKERS": workers,
        },
        "volumes": [
            {
                "type": "bind",
                "source": "/release/config/operator.toml",
                "target": config_path,
                "read_only": True,
            }
        ],
    }


def _check_dep_007() -> dict[str, Any]:
    return _expect_error(
        lambda: deployment_contracts.validate_operator_service_contracts(
            {"vbas-gpu0": _operator_service("vbas-gpu0", workers="2")}
        ),
        exception=deployment_contracts.DeploymentContractError,
        fragment="exactly one Uvicorn worker",
    )


def _check_dep_009() -> dict[str, Any]:
    service = {"deploy": {}, "environment": {"NVIDIA_VISIBLE_DEVICES": "0"}}
    return _expect_error(
        lambda: preflight_checks._validate_gpu_service(
            "vbas-gpu0",
            service,
            {"PLATFORM_GPU_ID": "0", "NVIDIA_VISIBLE_DEVICES": "0"},
            "gpu0",
        ),
        exception=preflight_checks.PreflightError,
        fragment="GPU reservation must contain one device",
    )


def _check_dep_010() -> dict[str, Any]:
    dockerfile = "COPY app /service/app\n"
    return _expect_error(
        lambda: deployment_contracts.validate_registry_wheel_dockerfile(
            dockerfile, "operator/Dockerfile"
        ),
        exception=deployment_contracts.DeploymentContractError,
        fragment="registry client wheel",
    )


def _check_dep_011() -> dict[str, Any]:
    dockerfile = (
        "COPY wheel/algorithm_operator_registry_client-0.1.0-py3-none-any.whl "
        "/tmp/client.whl\n"
        "RUN python -m pip install --no-deps /tmp/client.whl\n"
    )
    return _expect_error(
        lambda: deployment_contracts.validate_registry_wheel_dockerfile(
            dockerfile, "operator/Dockerfile"
        ),
        exception=deployment_contracts.DeploymentContractError,
        fragment="registry client wheel",
    )


def _check_dep_012() -> dict[str, Any]:
    missing = Path(tempfile.gettempdir()) / "m2b-model-root-does-not-exist"
    if missing.exists() or missing.is_symlink():
        raise ValueError("受控缺失模型路径意外存在")
    observed = _expect_error(
        lambda: model_asset_transaction._actual_files(missing),
        exception=model_asset_transaction.AssetError,
        fragment="missing or non-directory model root",
    )
    return {"model_root": str(missing), **observed}


def _check_dep_013(scratch_directory: Path) -> dict[str, Any]:
    payload = b"changed-model"
    expected = hashlib.sha256(b"expected-model").hexdigest()
    (scratch_directory / "model.bin").write_bytes(payload)
    return _expect_error(
        lambda: model_asset_transaction._verify_tree(
            scratch_directory,
            {"model.bin": (len(payload), expected)},
        ),
        exception=model_asset_transaction.AssetError,
        fragment="model file hash mismatch",
    )


def _check_dep_014() -> dict[str, Any]:
    service = _operator_service("text-analysis-cpu0")
    service["environment"]["CONFIG_PATH"] = "/wrong/config.toml"
    return _expect_error(
        lambda: deployment_contracts.validate_operator_service_contracts(
            {"text-analysis-cpu0": service}
        ),
        exception=deployment_contracts.DeploymentContractError,
        fragment="CONFIG_PATH",
    )


def _check_dep_015(scratch_directory: Path) -> dict[str, Any]:
    os.chmod(scratch_directory, 0o500)
    try:
        return _expect_error(
            lambda: deployment_contracts.validate_writable_directory(
                scratch_directory
            ),
            exception=deployment_contracts.DeploymentContractError,
            fragment="not writable",
        )
    finally:
        os.chmod(scratch_directory, 0o700)


def _check_dep_016(scratch_directory: Path) -> dict[str, Any]:
    os.chmod(scratch_directory, 0o500)
    try:
        return _expect_error(
            lambda: deployment_contracts.validate_writable_directory(
                scratch_directory
            ),
            exception=deployment_contracts.DeploymentContractError,
            fragment="not writable",
        )
    finally:
        os.chmod(scratch_directory, 0o700)


def _check_dep_017() -> dict[str, Any]:
    return _expect_error(
        lambda: deployment_contracts.validate_root_disk(99 * 1024 * 1024, 100),
        exception=deployment_contracts.DeploymentContractError,
        fragment="root disk",
    )


def _check_dep_018() -> dict[str, Any]:
    containers = [
        {"Name": "/algorithm-control-service", "Config": {"Labels": {}}}
    ]
    return _expect_error(
        lambda: deployment_contracts.validate_existing_algorithm_containers(
            containers, set()
        ),
        exception=deployment_contracts.DeploymentContractError,
        fragment="unknown algorithm container",
    )


_container_protection_namespace = runpy.run_path(
    str(_PLATFORM_ROOT / "deploy/scripts/container-protection.py")
)
_production_snapshot_validator = cast(
    Callable[[str, Path], list[dict[str, Any]]],
    _container_protection_namespace["validate_snapshot"],
)


def _check_dep_019(scratch_directory: Path) -> dict[str, Any]:
    snapshot = scratch_directory / "snapshot.jsonl"
    snapshot.write_text(
        json.dumps({"container_id": "a" * 64, "name": "existing"}) + "\n",
        encoding="utf-8",
    )
    try:
        _production_snapshot_validator("container-protection", snapshot)
    except SystemExit as exc:
        detail = str(exc)
        if "incomplete snapshot line 1" not in detail:
            raise ValueError(
                "checker reason does not contain required snapshot detail"
            ) from exc
        return {"rejection": type(exc).__name__, "detail": detail}
    raise ValueError("不完整快照未被生产校验器拒绝")


def _check_dep_020(scratch_directory: Path) -> dict[str, Any]:
    fake_bin = scratch_directory / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/bin/sh\nexit 23\n", encoding="utf-8")
    fake_git.chmod(0o755)
    identity = scratch_directory / "deploy-key"
    identity.write_text("test-only-key", encoding="utf-8")
    identity.chmod(0o600)
    destination = scratch_directory / "release"
    command = [
        str(_PLATFORM_ROOT / "deploy/scripts/checkout-release"),
        "--repository",
        "git@example.invalid:team/repository.git",
        "--git-sha",
        "1" * 40,
        "--destination",
        str(destination),
        "--identity-file",
        str(identity),
    ]
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    completed = subprocess.run(
        command,
        cwd=_PLATFORM_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
        env=environment,
    )
    if completed.returncode == 0 or "fixed commit checkout failed" not in completed.stderr:
        raise ValueError("checkout-release wrapper did not fail closed")
    partial_paths = list(scratch_directory.glob(".checkout-release-*"))
    destination_exists = destination.exists()
    if destination_exists or partial_paths:
        raise ValueError("checkout-release wrapper left a partial destination")
    return {
        "wrapper_returncode": completed.returncode,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
        "scope": "checkout wrapper fail-closed only",
        "destination_exists": destination_exists,
        "partial_checkout_count": len(partial_paths),
    }


_DEPLOYMENT_CHECKERS: Mapping[str, Callable[..., dict[str, Any]]] = {
    "DEP-001": _check_dep_001,
    "DEP-002": _check_dep_002,
    "DEP-003": _check_dep_003,
    "DEP-004": _check_dep_004,
    "DEP-005": _check_dep_005,
    "DEP-006": _check_dep_006,
    "DEP-007": _check_dep_007,
    "DEP-009": _check_dep_009,
    "DEP-010": _check_dep_010,
    "DEP-011": _check_dep_011,
    "DEP-012": _check_dep_012,
    "DEP-013": _check_dep_013,
    "DEP-014": _check_dep_014,
    "DEP-015": _check_dep_015,
    "DEP-016": _check_dep_016,
    "DEP-017": _check_dep_017,
    "DEP-018": _check_dep_018,
    "DEP-019": _check_dep_019,
    "DEP-020": _check_dep_020,
}


def _authorized_scratch_directory(
    case_id: str,
    scenario: Mapping[str, Any],
) -> Path | None:
    value = scenario.get("scratch_directory")
    if case_id not in _WRITABLE_DEPLOYMENT_CASES:
        if value is not None:
            raise ValueError("read-only deployment checker declared scratch authorization")
        return None
    run_id = scenario.get("run_id")
    if not isinstance(run_id, str) or not isinstance(value, str):
        raise ValueError("deployment scratch authorization is missing")
    path = Path(value)
    expected_prefix = (
        f"m2b-{len(run_id)}-{run_id}-{case_id.lower()}-scratch-"
    )
    try:
        temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
        parent = path.parent.resolve(strict=True)
        metadata = os.lstat(path)
    except OSError as exc:
        raise ValueError("deployment scratch authorization is invalid") from exc
    if (
        not path.is_absolute()
        or ".." in path.parts
        or parent != temporary_root
        or not path.name.startswith(expected_prefix)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError("deployment scratch authorization is invalid")
    if any(path.iterdir()):
        raise ValueError("deployment scratch directory is not empty")
    return path


def evaluate_scenario(case_id: str, scenario: Mapping[str, Any]) -> dict[str, Any]:
    spec = CASE_SPECS.get(case_id)
    checker = _DEPLOYMENT_CHECKERS.get(case_id)
    if spec is None or checker is None:
        return {
            "case_id": case_id,
            "status": "失败",
            "reason": "未知部署 checker",
            "observed": {"registered": False},
        }
    mutation = scenario.get("mutation")
    if (
        scenario.get("schema_version") != 1
        or scenario.get("case_id") != case_id
        or scenario.get("mode") != spec.mode
        or not isinstance(mutation, dict)
        or mutation.get("case") != case_id
    ):
        return {
            "case_id": case_id,
            "status": "失败",
            "reason": "部署受控输入与 checker 不匹配",
            "observed": {"input_valid": False},
        }
    try:
        scratch_directory = _authorized_scratch_directory(case_id, scenario)
        observed = (
            checker(scratch_directory)
            if scratch_directory is not None
            else checker()
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "case_id": case_id,
            "status": "失败",
            "reason": f"部署 checker 未观察到目标状态：{exc}",
            "observed": {"checker": checker.__name__, "detail": str(exc)},
        }
    return {
        "case_id": case_id,
        "status": spec.status,
        "reason": spec.reason,
        "observed": {"checker": checker.__name__, **observed},
    }


def _parse_checker_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--check", required=True, choices=sorted(CASE_SPECS))
    parser.add_argument("--input", required=True, type=Path)
    return parser.parse_args(argv)


def checker_main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_checker_args(argv)
    try:
        document = json.loads(arguments.input.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("checker input must be a JSON object")
        result = evaluate_scenario(arguments.check, document)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        result = {
            "case_id": arguments.check,
            "status": "失败",
            "reason": f"部署 checker 输入失败：{exc}",
            "observed": {"input_valid": False},
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "通过" else 1


if __name__ == "__main__":
    raise SystemExit(checker_main())
