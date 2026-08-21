#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import html
import importlib
import json
import os
import re
import stat
import sys
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, cast

import yaml  # type: ignore[import-untyped]

_PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(_PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLATFORM_ROOT))

from deploy.scripts.operator_topology import CURRENT_TOPOLOGY  # noqa: E402

if TYPE_CHECKING:
    from scripts.milestone_2b_report_contract import (
        SCHEMA_VERSION,
        overall_status,
        strict_json_loads,
        validate_cases_envelope,
        validate_raw_execution_evidence,
    )
else:
    _contract = importlib.import_module(
        "scripts.milestone_2b_report_contract"
        if __package__
        else "milestone_2b_report_contract"
    )
    SCHEMA_VERSION = _contract.SCHEMA_VERSION
    overall_status = _contract.overall_status
    strict_json_loads = _contract.strict_json_loads
    validate_cases_envelope = _contract.validate_cases_envelope
    validate_raw_execution_evidence = _contract.validate_raw_execution_evidence

STATUSES = ("通过", "失败", "未执行及原因")
REPORT_SCHEMA_VERSION = 3
RootIdentity = tuple[int, int]
EXPLICIT_EVIDENCE_TYPES = frozenset(
    {"operator_smoke", "operator_registration", "execution_declaration"}
)
CASE_EVIDENCE_TYPES = {
    "registration_full": "operator_registration",
    "registration_profile": "operator_registration",
    "registration_recovery": "operator_registration",
    "registration_facerec": "operator_registration",
    "gpu_running": "gpu_runtime",
    "gpu_stopped": "gpu_recovery",
    "smoke_full": "operator_smoke",
    "smoke_gpu_trigger": "operator_smoke",
    "smoke_cpu_instance": "operator_smoke",
    "execution_declaration": "execution_declaration",
    "negative_execution": "negative_case_evidence",
    "load_execution": "load_case_evidence",
}
EXECUTION_CASE_KINDS = frozenset({"negative_execution", "load_execution"})
EVIDENCE_STATUS = {
    "operator_smoke": {"PASS": "通过", "失败": "失败", "未执行及原因": "未执行及原因"},
    "operator_registration": {"通过": "通过", "失败": "失败"},
    "gpu_runtime": {"PASS": "通过", "FAIL": "失败"},
    "gpu_recovery": {"PASS": "通过", "FAIL": "失败"},
}
FORBIDDEN_COMMAND = re.compile(
    r"(?i)(repository\s*\.\s*(complete|finish|mark)|complete_node|mark_node_completed|authorization\s*:|bearer\s+|token\s*=)"
)
CONTAINER_ID_PATTERN = re.compile(r"[0-9a-f]{64}")
FORBIDDEN_OPERATOR_ENVIRONMENT_FIELDS = frozenset(
    {
        "PLATFORM_REGISTRATION_ENABLED",
        "PLATFORM_CONTROL_SERVICE_URL",
        "PLATFORM_HEARTBEAT_INTERVAL_SECONDS",
        "PLATFORM_DECLARED_CAPACITY",
        "REQUIRE_GPU",
        "GPU_PROCESS_NAME",
    }
)


@dataclass(frozen=True)
class EvidenceSnapshot:
    relative_path: str
    type: str
    size: int
    sha256: str
    content: bytes
    payload: dict[str, Any]


@dataclass(frozen=True)
class OperatorAuthority:
    service_name: str
    instance_id: str
    operator_code: str
    profile: str
    physical_gpu: int | None
    process_name: str | None


@dataclass(frozen=True)
class SmokeAuthority:
    source_case_id: str
    operator_code: str
    checks: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="汇总里程碑 2B Harness 用例")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def safe_relative(value: str) -> PurePosixPath:
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "." in relative.parts
        or not relative.parts
    ):
        raise ValueError(f"证据路径不安全: {value}")
    return relative


def _require_object(value: object, context: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{context} 必须是对象")
    raw = cast(dict[object, object], value)
    if any(type(key) is not str for key in raw):
        raise ValueError(f"{context} 包含非字符串字段名")
    return cast(dict[str, Any], value)


def _require_list(value: object, context: str) -> list[Any]:
    if type(value) is not list:
        raise ValueError(f"{context} 必须是数组")
    return value


def _require_string(value: object, context: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{context} 必须是非空字符串")
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value):
        raise ValueError(f"{context} 包含控制字符")
    return value


def _require_nonnegative_int(value: object, context: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{context} 必须是非负整数")
    return value


def _same_filesystem_object(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _require_snapshot_metadata(metadata: os.stat_result, relative_path: str) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"release JSON 必须是普通文件: {relative_path}")
    if metadata.st_uid != os.geteuid():
        raise ValueError(f"release JSON 必须由当前 UID 所有: {relative_path}")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ValueError(f"release JSON 权限必须是 0600: {relative_path}")
    if metadata.st_nlink != 1:
        raise ValueError(f"release JSON 必须只有一个硬链接: {relative_path}")


def _metadata_snapshot(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def release_root_identity(release_root: Path) -> RootIdentity:
    try:
        metadata = os.lstat(release_root)
    except OSError as exc:
        raise ValueError(f"无法检查 release root: {release_root}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"release root 必须是真实目录: {release_root}")
    return metadata.st_dev, metadata.st_ino


@contextmanager
def _release_directory_descriptor(
    release_root: Path,
    relative_parent: PurePosixPath,
    expected_root_identity: RootIdentity,
) -> Iterator[int]:
    if relative_parent.is_absolute() or ".." in relative_parent.parts:
        raise ValueError(f"release 目录路径不安全: {relative_parent}")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        with ExitStack() as descriptors:
            named_root = os.lstat(release_root)
            if stat.S_ISLNK(named_root.st_mode) or not stat.S_ISDIR(named_root.st_mode):
                raise ValueError(f"release root 必须是真实目录: {release_root}")
            if (named_root.st_dev, named_root.st_ino) != expected_root_identity:
                raise ValueError("release root 与首次 root 锚点不一致")
            current_descriptor = os.open(release_root, directory_flags)
            descriptors.callback(os.close, current_descriptor)
            opened_root = os.fstat(current_descriptor)
            if not stat.S_ISDIR(opened_root.st_mode) or not _same_filesystem_object(
                named_root, opened_root
            ):
                raise ValueError(f"release root 打开期间发生变化: {release_root}")
            if (opened_root.st_dev, opened_root.st_ino) != expected_root_identity:
                raise ValueError("打开的 release root 与首次 root 锚点不一致")
            bindings: list[tuple[int, str, int]] = []
            for part in relative_parent.parts:
                named = os.stat(part, dir_fd=current_descriptor, follow_symlinks=False)
                if stat.S_ISLNK(named.st_mode) or not stat.S_ISDIR(named.st_mode):
                    raise ValueError(f"release JSON 父目录不安全: {relative_parent}")
                opened_descriptor = os.open(
                    part,
                    directory_flags,
                    dir_fd=current_descriptor,
                )
                descriptors.callback(os.close, opened_descriptor)
                opened = os.fstat(opened_descriptor)
                if not stat.S_ISDIR(opened.st_mode) or not _same_filesystem_object(
                    named, opened
                ):
                    raise ValueError(f"release JSON 父目录打开期间发生变化: {relative_parent}")
                bindings.append((current_descriptor, part, opened_descriptor))
                current_descriptor = opened_descriptor
            yield current_descriptor
            for parent_descriptor, part, opened_descriptor in bindings:
                named_after = os.stat(
                    part,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                opened_after = os.fstat(opened_descriptor)
                if (
                    stat.S_ISLNK(named_after.st_mode)
                    or not stat.S_ISDIR(named_after.st_mode)
                    or not _same_filesystem_object(named_after, opened_after)
                ):
                    raise ValueError(
                        f"release JSON 父目录读取期间发生变化: {relative_parent}"
                    )
            named_root_after = os.lstat(release_root)
            if (
                stat.S_ISLNK(named_root_after.st_mode)
                or not stat.S_ISDIR(named_root_after.st_mode)
                or not _same_filesystem_object(named_root_after, opened_root)
                or (named_root_after.st_dev, named_root_after.st_ino)
                != expected_root_identity
            ):
                raise ValueError(f"release root 读取期间发生变化: {release_root}")
    except OSError as exc:
        raise ValueError(f"无法安全访问 release 目录: {relative_parent}") from exc


def _classify_evidence(relative_path: PurePosixPath, payload: dict[str, Any]) -> str:
    if (
        len(relative_path.parts) >= 4
        and relative_path.parts[0] in {"negative", "load"}
        and relative_path.parts[1] == "evidence"
        and relative_path.suffix == ".json"
    ):
        validate_raw_execution_evidence(
            payload,
            relative_path.as_posix(),
            expected_case_id=relative_path.parts[2],
        )
        return f"{relative_path.parts[0]}_case_evidence"
    explicit = payload.get("evidence_type")
    if explicit is not None:
        if type(explicit) is not str or explicit not in EXPLICIT_EVIDENCE_TYPES:
            raise ValueError(f"证据类型不受支持: {relative_path}")
        return explicit
    if relative_path.parts[0] == "gpu-instances" and payload.get(
        "mode"
    ) == "running-inference":
        return "gpu_runtime"
    if relative_path.parts[0] == "recovery" and payload.get("mode") == "assert-stopped":
        return "gpu_recovery"
    raise ValueError(f"无法按固定规则识别证据类型: {relative_path}")


def _snapshot_release_json(
    release_root: Path,
    relative_path_value: str,
    *,
    snapshot_type: str | None = None,
    expected_root_identity: RootIdentity,
) -> EvidenceSnapshot:
    relative_path = safe_relative(relative_path_value)
    parent = PurePosixPath(*relative_path.parts[:-1])
    source_name = relative_path.parts[-1]
    descriptor = -1
    with _release_directory_descriptor(
        release_root, parent, expected_root_identity
    ) as parent_descriptor:
        try:
            named = os.stat(
                source_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if stat.S_ISLNK(named.st_mode):
                raise ValueError(f"release JSON 不得是软链接: {relative_path}")
            _require_snapshot_metadata(named, relative_path.as_posix())
            descriptor = os.open(
                source_name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_descriptor,
            )
            opened = os.fstat(descriptor)
            _require_snapshot_metadata(opened, relative_path.as_posix())
            if not _same_filesystem_object(named, opened):
                raise ValueError(f"release JSON 打开期间发生变化: {relative_path}")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 65_536):
                chunks.append(chunk)
            opened_after = os.fstat(descriptor)
            named_after = os.stat(
                source_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            _require_snapshot_metadata(opened_after, relative_path.as_posix())
            _require_snapshot_metadata(named_after, relative_path.as_posix())
            if (
                not _same_filesystem_object(named_after, opened_after)
                or _metadata_snapshot(opened) != _metadata_snapshot(opened_after)
                or _metadata_snapshot(named_after) != _metadata_snapshot(opened_after)
            ):
                raise ValueError(f"release JSON 读取期间发生变化: {relative_path}")
        except OSError as exc:
            raise ValueError(f"无法安全读取 release JSON: {relative_path}") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    content = b"".join(chunks)
    if len(content) != opened.st_size:
        raise ValueError(f"release JSON 字节数与文件大小不一致: {relative_path}")
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError(f"release JSON 不是 UTF-8: {relative_path}") from exc
    try:
        parsed = strict_json_loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"release JSON 不是严格 JSON: {relative_path}: {exc}") from exc
    payload = _require_object(parsed, f"release JSON {relative_path}")
    evidence_type = snapshot_type or _classify_evidence(relative_path, payload)
    return EvidenceSnapshot(
        relative_path=relative_path.as_posix(),
        type=evidence_type,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        content=content,
        payload=payload,
    )


def read_evidence_snapshot(
    release_root: Path,
    relative_path: str,
    *,
    expected_root_identity: RootIdentity,
) -> EvidenceSnapshot:
    return _snapshot_release_json(
        release_root,
        relative_path,
        expected_root_identity=expected_root_identity,
    )


def _load_smoke_authority(project_root: Path) -> tuple[
    dict[str, SmokeAuthority], dict[str, SmokeAuthority]
]:
    path = project_root / "deploy/operator-smoke-cases.json"
    try:
        parsed = strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"无法读取 Smoke authority: {path}") from exc
    document = _require_object(parsed, "Smoke authority")
    if document.get("schema_version") != 1:
        raise ValueError("Smoke authority schema_version 必须是 1")
    by_source: dict[str, SmokeAuthority] = {}
    by_operator: dict[str, SmokeAuthority] = {}
    for index, raw_case in enumerate(_require_list(document.get("cases"), "Smoke authority.cases")):
        case = _require_object(raw_case, f"Smoke authority.cases[{index}]")
        source_case_id = _require_string(
            case.get("case_id"), f"Smoke authority.cases[{index}].case_id"
        )
        operator_code = _require_string(
            case.get("operator_code"), f"Smoke authority.cases[{index}].operator_code"
        )
        checks = tuple(
            _require_string(check, f"Smoke authority.cases[{index}].checks[{check_index}]")
            for check_index, check in enumerate(
                _require_list(case.get("checks"), f"Smoke authority.cases[{index}].checks")
            )
        )
        if not checks:
            raise ValueError(f"Smoke authority {source_case_id} checks 不能为空")
        authority = SmokeAuthority(source_case_id, operator_code, checks)
        if source_case_id in by_source or operator_code in by_operator:
            raise ValueError("Smoke authority 包含重复 case/operator")
        by_source[source_case_id] = authority
        by_operator[operator_code] = authority
    if len(by_source) != CURRENT_TOPOLOGY.totals["operator_smoke_types"]:
        raise ValueError("Smoke authority 必须包含 7 个算子")
    return by_source, by_operator


def _load_operator_authority(
    project_root: Path, smoke_by_operator: Mapping[str, SmokeAuthority]
) -> dict[str, OperatorAuthority]:
    path = project_root / "deploy/docker-compose.operators.yml"
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"无法读取 Compose authority: {path}") from exc
    document = _require_object(parsed, "Compose authority")
    services = _require_object(document.get("services"), "Compose authority.services")
    authorities: dict[str, OperatorAuthority] = {}
    for raw_service_name, raw_service in services.items():
        service_name = _require_string(raw_service_name, "Compose service name")
        service = _require_object(raw_service, f"Compose service {service_name}")
        environment = _require_object(
            service.get("environment"), f"Compose service {service_name}.environment"
        )
        present_forbidden_fields = sorted(
            FORBIDDEN_OPERATOR_ENVIRONMENT_FIELDS.intersection(environment)
        )
        if present_forbidden_fields:
            raise ValueError(
                f"Compose service {service_name} 仍包含已移除环境字段: "
                f"{present_forbidden_fields}"
            )
        instance_id = _require_string(
            environment.get("PLATFORM_INSTANCE_ID"),
            f"Compose service {service_name}.PLATFORM_INSTANCE_ID",
        )
        if instance_id != service_name:
            raise ValueError(f"Compose service/instance 不一致: {service_name}")
        profiles = _require_list(
            service.get("profiles"), f"Compose service {service_name}.profiles"
        )
        if len(profiles) != 1:
            raise ValueError(f"Compose service {service_name} 必须有一个 profile")
        profile = _require_string(profiles[0], f"Compose service {service_name}.profile")
        matching_operators = [
            operator_code
            for operator_code in smoke_by_operator
            if instance_id.startswith(operator_code.replace("_", "-") + "-")
        ]
        if len(matching_operators) != 1:
            raise ValueError(f"Compose service {service_name} 无法映射唯一算子")
        operator_code = matching_operators[0]
        raw_gpu = environment.get("PLATFORM_GPU_ID")
        if raw_gpu is None:
            physical_gpu = None
            process_name = None
        else:
            if type(raw_gpu) is not str or not raw_gpu.isdigit():
                raise ValueError(f"Compose service {service_name} GPU ID 无效")
            physical_gpu = int(raw_gpu)
            process_name = operator_code
        authorities[instance_id] = OperatorAuthority(
            service_name=service_name,
            instance_id=instance_id,
            operator_code=operator_code,
            profile=profile,
            physical_gpu=physical_gpu,
            process_name=process_name,
        )
    if len(authorities) != CURRENT_TOPOLOGY.totals["instances"]:
        raise ValueError("Compose authority 必须包含 21 个算子实例")
    return authorities


def _require_schema_v1(payload: Mapping[str, Any], context: str) -> None:
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ValueError(f"{context}.schema_version 必须是 1")


def _validate_gpu_common_v1(payload: Mapping[str, Any], context: str) -> None:
    _require_string(payload.get("timestamp"), f"{context}.timestamp")
    commands = _require_list(payload.get("commands"), f"{context}.commands")
    if not commands:
        raise ValueError(f"{context}.commands 不能为空")
    for index, command in enumerate(commands):
        _require_string(command, f"{context}.commands[{index}]")


def _mapped_evidence_status(snapshot: EvidenceSnapshot) -> str:
    if snapshot.type == "execution_declaration":
        return "未执行及原因"
    mapping = EVIDENCE_STATUS.get(snapshot.type)
    if mapping is None:
        raise ValueError(f"证据类型不受支持: {snapshot.relative_path}")
    raw_status = snapshot.payload.get("status")
    if type(raw_status) is not str or raw_status not in mapping:
        raise ValueError(f"{snapshot.relative_path}.status 不受支持: {raw_status}")
    return mapping[raw_status]


def _require_case_status_match(
    case: Mapping[str, Any], snapshot: EvidenceSnapshot
) -> None:
    evidence_status = _mapped_evidence_status(snapshot)
    if case.get("status") != evidence_status:
        raise ValueError(
            f"用例与证据 status 不一致: {case.get('case_id')} / {snapshot.relative_path}"
        )


def _validate_smoke_evidence(
    case: Mapping[str, Any],
    snapshot: EvidenceSnapshot,
    *,
    release_tag: str,
    git_sha: str,
    authorities: Mapping[str, OperatorAuthority],
    smoke_by_source: Mapping[str, SmokeAuthority],
) -> None:
    payload = snapshot.payload
    context = snapshot.relative_path
    _require_schema_v1(payload, context)
    if payload.get("evidence_type") != "operator_smoke":
        raise ValueError(f"{context}.evidence_type 必须是 operator_smoke")
    source_case_id = _require_string(case.get("source_case_id"), "case.source_case_id")
    smoke = smoke_by_source.get(source_case_id)
    if smoke is None:
        raise ValueError(f"{context}.operator_code 没有对应 Smoke authority")
    case_kind = _require_string(case.get("case_kind"), "case.case_kind")
    target = _require_string(case.get("target"), "case.target")
    if case_kind == "smoke_full":
        expected_operator = target
        if target != smoke.operator_code:
            raise ValueError(f"{context}.target 与 Smoke authority 不匹配")
        expected_path = f"smoke/{expected_operator}.json"
    else:
        instance = authorities.get(target)
        if instance is None:
            raise ValueError(f"{context}.target 不是 Compose 算子实例")
        if case_kind == "smoke_cpu_instance":
            if instance.physical_gpu is not None:
                raise ValueError(f"{context}.target 必须是 Compose CPU 实例")
        elif case_kind == "smoke_gpu_trigger":
            if instance.physical_gpu is None:
                raise ValueError(f"{context}.target 必须是 Compose GPU 实例")
        else:
            raise ValueError(f"{context} 不是受支持的实例 Smoke 用例")
        expected_operator = instance.operator_code
        if expected_operator != smoke.operator_code:
            raise ValueError(f"{context}.operator_code 与 Compose 算子实例不匹配")
        run_id = _require_string(case.get("run_id"), "Smoke case.run_id")
        expected_path = (
            f"smoke/instances/{target}/runs/{run_id}/{expected_operator}.json"
        )
    if snapshot.relative_path != expected_path:
        raise ValueError(f"{context} 不是用例要求的规范 Smoke 证据路径")
    if payload.get("operator_code") != expected_operator:
        raise ValueError(f"{context}.operator_code 与用例不匹配")
    if payload.get("target") != target:
        raise ValueError(f"{context}.target 与用例不匹配")
    raw_checks = _require_list(payload.get("checks"), f"{context}.checks")
    checks = tuple(
        _require_string(check, f"{context}.checks[{index}]")
        for index, check in enumerate(raw_checks)
    )
    if checks != smoke.checks:
        raise ValueError(f"{context}.checks 与 Smoke authority 不匹配")
    if type(payload.get("mock")) is not bool or payload["mock"] != case.get("mock"):
        raise ValueError(f"{context}.mock 与用例不匹配")
    if payload.get("release_tag") != release_tag or payload.get("release_tag") != case.get(
        "release_tag"
    ):
        raise ValueError(f"{context}.release_tag 与当前 release 不匹配")
    if payload.get("git_sha") != git_sha or payload.get("git_sha") != case.get("git_sha"):
        raise ValueError(f"{context}.git_sha 与当前 release 不匹配")
    _require_case_status_match(case, snapshot)
    status = _mapped_evidence_status(snapshot)
    if status == "通过":
        summary = _require_object(payload.get("summary"), f"{context}.summary")
        if not summary:
            raise ValueError(f"{context}.summary 不能为空")
        attempts = _require_list(summary.get("attempts"), f"{context}.summary.attempts")
        if not attempts:
            raise ValueError(f"{context}.summary.attempts 不能为空")
        for index, attempt in enumerate(attempts):
            if not _require_object(attempt, f"{context}.summary.attempts[{index}]"):
                raise ValueError(f"{context}.summary.attempts[{index}] 不能为空")
    elif status in {"失败", "未执行及原因"}:
        _require_string(payload.get("reason"), f"{context}.reason")


def _registration_expected(
    case: Mapping[str, Any], authorities: Mapping[str, OperatorAuthority]
) -> tuple[dict[str, Any], int, str]:
    case_kind = _require_string(case.get("case_kind"), "registration case_kind")
    target = _require_string(case.get("target"), "registration target")
    if case_kind == "registration_full":
        return (
            {"mode": "full", "values": []},
            len(authorities),
            "registration/operator-registration.json",
        )
    if case_kind == "registration_profile":
        expected = sum(authority.profile == target for authority in authorities.values())
        if expected == 0:
            raise ValueError(f"registration profile 不在 Compose authority 中: {target}")
        return (
            {"mode": "profile", "values": [target]},
            expected,
            f"registration/operator-registration-profile-{target}.json",
        )
    if case_kind == "registration_recovery":
        authority = authorities.get(target)
        if authority is None or authority.physical_gpu is None:
            raise ValueError(f"registration recovery 目标不是 GPU 实例: {target}")
        return (
            {"mode": "instance", "values": [target]},
            1,
            f"registration/operator-registration-instance-{target}.json",
        )
    if case_kind == "registration_facerec":
        instances = sorted(
            authority.instance_id
            for authority in authorities.values()
            if authority.operator_code == "facerec" and authority.physical_gpu is not None
        )
        if instances != ["facerec-gpu0", "facerec-gpu1", "facerec-gpu2"]:
            raise ValueError("Compose facerec authority 不符合三实例合同")
        digest = hashlib.sha256("\n".join(instances).encode("utf-8")).hexdigest()[:12]
        return (
            {"mode": "instance", "values": instances},
            len(instances),
            f"registration/operator-registration-instances-{digest}.json",
        )
    raise ValueError(f"未知 registration case_kind: {case_kind}")


def _validate_registration_evidence(
    case: Mapping[str, Any],
    snapshot: EvidenceSnapshot,
    *,
    release_tag: str,
    git_sha: str,
    authorities: Mapping[str, OperatorAuthority],
) -> None:
    payload = snapshot.payload
    context = snapshot.relative_path
    _require_schema_v1(payload, context)
    if payload.get("evidence_type") != "operator_registration":
        raise ValueError(f"{context}.evidence_type 必须是 operator_registration")
    if payload.get("mock") is not False:
        raise ValueError(f"{context}.mock 必须是 false")
    if payload.get("release_tag") != release_tag or payload.get("git_sha") != git_sha:
        raise ValueError(f"{context} 的 release/SHA 不匹配")
    if payload.get("target") != "operator-registry":
        raise ValueError(f"{context}.target 必须是 operator-registry")
    expected_selection, expected_count, expected_path = _registration_expected(
        case, authorities
    )
    if snapshot.relative_path != expected_path:
        raise ValueError(f"{context} 不是用例要求的规范 registration 证据路径")
    if payload.get("selection") != expected_selection:
        raise ValueError(f"{context}.selection 与权威选择不匹配")
    summary = _require_object(payload.get("summary"), f"{context}.summary")
    if set(summary) != {"expected", "observed", "valid"}:
        raise ValueError(f"{context}.summary 字段不完整")
    expected = _require_nonnegative_int(summary["expected"], f"{context}.summary.expected")
    observed = _require_nonnegative_int(summary["observed"], f"{context}.summary.observed")
    valid = _require_nonnegative_int(summary["valid"], f"{context}.summary.valid")
    if expected != expected_count:
        raise ValueError(f"{context}.summary.expected 与权威 expected 不匹配")
    raw_issues = _require_list(payload.get("issues"), f"{context}.issues")
    issues = [
        _require_string(issue, f"{context}.issues[{index}]")
        for index, issue in enumerate(raw_issues)
    ]
    _require_case_status_match(case, snapshot)
    if _mapped_evidence_status(snapshot) == "通过":
        if (expected, observed, valid) != (expected_count, expected_count, expected_count):
            raise ValueError(f"{context}.summary 通过时三值必须等于权威 expected")
        if issues:
            raise ValueError(f"{context}.issues 通过时必须为空")
    else:
        if valid > min(expected, observed):
            raise ValueError(f"{context}.summary 必须满足 valid <= min(expected, observed)")
        if not issues:
            raise ValueError(f"{context}.issues 失败时不能为空")


def _gpu_target(
    payload: Mapping[str, Any], authority: OperatorAuthority, context: str
) -> None:
    target = _require_object(payload.get("target"), f"{context}.target")
    if target.get("instance_id") != authority.instance_id:
        raise ValueError(f"{context}.target.instance_id 与 Compose 不匹配")
    if type(target.get("physical_gpu")) is not int or target.get(
        "physical_gpu"
    ) != authority.physical_gpu:
        raise ValueError(f"{context}.target.physical_gpu 与 Compose 不匹配")
    if target.get("process_name") != authority.process_name:
        raise ValueError(f"{context}.target.process_name 与 Compose 不匹配")
    target_container = _require_string(
        target.get("container"), f"{context}.target.container"
    )
    if target_container == authority.service_name:
        return
    if CONTAINER_ID_PATTERN.fullmatch(target_container) is None:
        raise ValueError(f"{context}.target.container 与 Compose 不匹配")
    if "container" not in payload and payload.get("status") == "FAIL":
        return
    nested = _require_object(payload.get("container"), f"{context}.container")
    if nested.get("id") != target_container:
        raise ValueError(f"{context}.target.container 与 container.id 不匹配")


def _gpu_container(
    value: object, authority: OperatorAuthority, context: str
) -> dict[str, Any]:
    container = _require_object(value, context)
    if container.get("instance_id") != authority.instance_id:
        raise ValueError(f"{context}.instance_id 与 Compose 不匹配")
    container_id = _require_string(container.get("id"), f"{context}.id")
    if CONTAINER_ID_PATTERN.fullmatch(container_id) is None:
        raise ValueError(f"{context}.id 必须是 64 位小写十六进制 Docker ID")
    container_name = _require_string(container.get("name"), f"{context}.name")
    if container_name not in {authority.service_name, container_id}:
        raise ValueError(f"{context}.name 与 Compose 或 container.id 不匹配")
    if "init_host_pid" in container:
        pid = _require_nonnegative_int(container["init_host_pid"], f"{context}.init_host_pid")
        if pid == 0:
            raise ValueError(f"{context}.init_host_pid 必须是正整数")
    return container


def _gpu_identity(
    value: object, authority: OperatorAuthority, context: str
) -> dict[str, Any]:
    gpu = _require_object(value, context)
    if type(gpu.get("physical_index")) is not int or gpu.get(
        "physical_index"
    ) != authority.physical_gpu:
        raise ValueError(f"{context}.physical_index 与 Compose 不匹配")
    _require_string(gpu.get("physical_uuid"), f"{context}.physical_uuid")
    if "container_visible" in gpu and gpu["container_visible"] != str(
        authority.physical_gpu
    ):
        raise ValueError(f"{context}.container_visible 与 Compose 不匹配")
    return gpu


def _gpu_activity(
    value: object,
    authority: OperatorAuthority,
    case: Mapping[str, Any],
    context: str,
    *,
    require_run_id: bool,
) -> dict[str, Any]:
    activity = _require_object(value, context)
    if not activity:
        raise ValueError(f"{context} 不能为空")
    if activity.get("instance_id") != authority.instance_id:
        raise ValueError(f"{context}.instance_id 与 Compose 不匹配")
    if activity.get("operator_code") != authority.operator_code:
        raise ValueError(f"{context}.operator_code 与 Compose 不匹配")
    if "run_id" in activity:
        run_id = _require_string(activity["run_id"], f"{context}.run_id")
        if run_id != case.get("run_id"):
            raise ValueError(f"{context}.run_id 与用例不匹配")
    elif require_run_id:
        raise ValueError(f"{context}.run_id 不能为空")
    return activity


def _gpu_sample_pids(
    value: object, authority: OperatorAuthority, context: str
) -> set[int]:
    samples = _require_list(value, context)
    host_pids: set[int] = set()
    for sample_index, raw_sample in enumerate(samples):
        sample = _require_object(raw_sample, f"{context}[{sample_index}]")
        processes = _require_list(
            sample.get("processes"), f"{context}[{sample_index}].processes"
        )
        for process_index, raw_process in enumerate(processes):
            process_context = f"{context}[{sample_index}].processes[{process_index}]"
            process = _require_object(raw_process, process_context)
            if process.get("process_name") != authority.process_name:
                raise ValueError(f"{process_context}.process_name 与 Compose 不匹配")
            host_pid = _require_nonnegative_int(
                process.get("host_pid"), f"{process_context}.host_pid"
            )
            if host_pid == 0:
                raise ValueError(f"{process_context}.host_pid 必须是正整数")
            container_pid = _require_nonnegative_int(
                process.get("container_pid"), f"{process_context}.container_pid"
            )
            if container_pid == 0:
                raise ValueError(f"{process_context}.container_pid 必须是正整数")
            mapping = _require_object(
                process.get("mapping"), f"{process_context}.mapping"
            )
            for field in ("docker_top", "cgroup_full_container_id"):
                if type(mapping.get(field)) is not bool or mapping[field] is not True:
                    raise ValueError(f"{process_context}.mapping.{field} 必须是 true")
            namespace_pids = [
                _require_nonnegative_int(
                    raw_pid,
                    f"{process_context}.mapping.nspid[{namespace_index}]",
                )
                for namespace_index, raw_pid in enumerate(
                    _require_list(
                        mapping.get("nspid"), f"{process_context}.mapping.nspid"
                    )
                )
            ]
            if (
                not namespace_pids
                or any(pid == 0 for pid in namespace_pids)
                or namespace_pids[0] != host_pid
                or namespace_pids[-1] != container_pid
            ):
                raise ValueError(
                    f"{process_context}.mapping.nspid 与 host/container PID 不匹配"
                )
            host_pids.add(host_pid)
    return host_pids


def _pid_list(value: object, context: str) -> list[int]:
    pids = [
        _require_nonnegative_int(pid, f"{context}[{index}]")
        for index, pid in enumerate(_require_list(value, context))
    ]
    if any(pid == 0 for pid in pids) or pids != sorted(set(pids)):
        raise ValueError(f"{context} 必须是唯一、有序的正整数列表")
    return pids


def _validate_gpu_runtime(
    case: Mapping[str, Any],
    snapshot: EvidenceSnapshot,
    *,
    git_sha: str,
    authorities: Mapping[str, OperatorAuthority],
) -> set[int]:
    payload = snapshot.payload
    context = snapshot.relative_path
    _require_schema_v1(payload, context)
    _validate_gpu_common_v1(payload, context)
    target = _require_string(case.get("target"), "GPU runtime case.target")
    authority = authorities.get(target)
    if authority is None or authority.physical_gpu is None:
        raise ValueError(f"GPU runtime 目标不在 Compose GPU authority: {target}")
    if payload.get("mode") != "running-inference":
        raise ValueError(f"{context}.mode 必须是 running-inference")
    if snapshot.relative_path != f"gpu-instances/{target}.json":
        raise ValueError(f"{context} 不是用例要求的规范 GPU runtime 证据路径")
    _gpu_target(payload, authority, context)
    _require_case_status_match(case, snapshot)
    status = _mapped_evidence_status(snapshot)
    if "release_sha" in payload and payload["release_sha"] != git_sha:
        raise ValueError(f"{context}.release_sha 与当前 release 不匹配")
    container = (
        _gpu_container(payload["container"], authority, f"{context}.container")
        if "container" in payload
        else None
    )
    gpu = (
        _gpu_identity(payload["gpu"], authority, f"{context}.gpu")
        if "gpu" in payload
        else None
    )
    activity = (
        _gpu_activity(
            payload["activity"],
            authority,
            case,
            f"{context}.activity",
            require_run_id=status == "通过",
        )
        if "activity" in payload
        else None
    )
    host_pids = (
        _gpu_sample_pids(
            payload["synchronous_samples"],
            authority,
            f"{context}.synchronous_samples",
        )
        if "synchronous_samples" in payload
        else set()
    )
    if status == "通过":
        if payload.get("release_sha") != git_sha:
            raise ValueError(f"{context}.release_sha 通过时必须匹配当前 release")
        if container is None or gpu is None or activity is None:
            raise ValueError(f"{context} 通过时缺少 container/gpu/activity")
        samples = _require_list(
            payload.get("synchronous_samples"), f"{context}.synchronous_samples"
        )
        if not samples or not host_pids:
            raise ValueError(f"{context}.synchronous_samples 通过时不能为空")
    else:
        _require_string(payload.get("reason"), f"{context}.reason")
    return host_pids


def _validate_gpu_recovery(
    case: Mapping[str, Any],
    snapshot: EvidenceSnapshot,
    runtime: EvidenceSnapshot,
    *,
    git_sha: str,
    authorities: Mapping[str, OperatorAuthority],
    runtime_pids: set[int],
) -> None:
    payload = snapshot.payload
    running = runtime.payload
    context = snapshot.relative_path
    _require_schema_v1(payload, context)
    _validate_gpu_common_v1(payload, context)
    target = _require_string(case.get("target"), "GPU recovery case.target")
    authority = authorities.get(target)
    if authority is None or authority.physical_gpu is None:
        raise ValueError(f"GPU recovery 目标不在 Compose GPU authority: {target}")
    if payload.get("mode") != "assert-stopped":
        raise ValueError(f"{context}.mode 必须是 assert-stopped")
    if snapshot.relative_path != f"recovery/{target}-stopped.json":
        raise ValueError(f"{context} 不是用例要求的规范 GPU recovery 证据路径")
    _gpu_target(payload, authority, context)
    running_target = _require_object(running.get("target"), f"{runtime.relative_path}.target")
    stopped_target = _require_object(payload.get("target"), f"{context}.target")
    if stopped_target["container"] != running_target["container"]:
        raise ValueError(f"{context}.target.container 与对应 runtime 不匹配")
    _require_case_status_match(case, snapshot)
    status = _mapped_evidence_status(snapshot)
    if "release_sha" in payload and payload["release_sha"] != git_sha:
        raise ValueError(f"{context}.release_sha 与当前 release 不匹配")
    container = (
        _gpu_container(payload["container"], authority, f"{context}.container")
        if "container" in payload
        else None
    )
    gpu = (
        _gpu_identity(payload["gpu"], authority, f"{context}.gpu")
        if "gpu" in payload
        else None
    )
    prior_pids = (
        _pid_list(payload["prior_cuda_pids"], f"{context}.prior_cuda_pids")
        if "prior_cuda_pids" in payload
        else None
    )
    remaining_pids = (
        _pid_list(payload["remaining_cuda_pids"], f"{context}.remaining_cuda_pids")
        if "remaining_cuda_pids" in payload
        else None
    )
    if container is not None and "container" in running and container != running["container"]:
        raise ValueError(f"{context}.container 与对应 runtime 不匹配")
    if gpu is not None and "gpu" in running and gpu != running["gpu"]:
        raise ValueError(f"{context}.gpu 与对应 runtime 不匹配")
    if prior_pids is not None and prior_pids != sorted(runtime_pids):
        raise ValueError(f"{context}.prior_cuda_pids 与对应 runtime 不匹配")
    if remaining_pids is not None and not set(remaining_pids).issubset(runtime_pids):
        raise ValueError(f"{context}.remaining_cuda_pids 不属于对应 runtime PID")
    if status == "通过":
        if _mapped_evidence_status(runtime) != "通过":
            raise ValueError(f"{context} 通过要求对应 runtime 通过")
        if payload.get("release_sha") != git_sha:
            raise ValueError(f"{context}.release_sha 通过时必须匹配当前 release")
        if container is None or gpu is None or not prior_pids:
            raise ValueError(f"{context} 通过时缺少实例/container/GPU/prior PID")
        if remaining_pids != []:
            raise ValueError(f"{context}.remaining_cuda_pids 通过时必须是 []")
    else:
        _require_string(payload.get("reason"), f"{context}.reason")


def _validate_declaration_evidence(
    case: Mapping[str, Any],
    snapshot: EvidenceSnapshot,
    *,
    release_tag: str,
    git_sha: str,
    expected_case_ids: set[str],
) -> None:
    payload = snapshot.payload
    context = snapshot.relative_path
    if (
        type(payload.get("schema_version")) is not int
        or payload["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError(
            f"{context}.schema_version 必须是 {SCHEMA_VERSION}"
        )
    if payload.get("evidence_type") != "execution_declaration":
        raise ValueError(f"{context}.evidence_type 必须是 execution_declaration")
    if case.get("status") != "未执行及原因":
        raise ValueError(f"{context} 声明用例只能是未执行及原因")
    if payload.get("mock") is not False:
        raise ValueError(f"{context}.mock 必须是 false")
    expected_category = {
        "negative/cases.json": "negative",
        "load/cases.json": "load",
    }.get(snapshot.relative_path)
    if expected_category is None:
        raise ValueError(f"{context} 不是规范声明证据路径")
    if payload.get("category") != expected_category:
        raise ValueError(f"{context}.category 与规范路径不匹配")
    if payload.get("status") != "NOT_EXECUTED":
        raise ValueError(f"{context}.status 只能是 NOT_EXECUTED")
    if payload.get("release_tag") != release_tag or payload.get("git_sha") != git_sha:
        raise ValueError(f"{context} 的 release/SHA 不匹配")
    reason = _require_string(payload.get("reason"), f"{context}.reason")
    if reason != case.get("reason"):
        raise ValueError(f"{context}.reason 与用例不匹配")
    batch = _require_list(payload.get("cases"), f"{context}.cases")
    batch_case_ids: list[str] = []
    for index, raw_item in enumerate(batch):
        item = _require_object(raw_item, f"{context}.cases[{index}]")
        item_case_id = _require_string(item.get("case_id"), f"{context}.cases[{index}].case_id")
        item_status = _require_string(item.get("status"), f"{context}.cases[{index}].status")
        if item_status != "NOT_EXECUTED":
            raise ValueError(f"{context}.cases[{index}] 只能声明未执行")
        batch_case_ids.append(item_case_id)
    if len(batch_case_ids) != len(set(batch_case_ids)):
        raise ValueError(f"{context}.cases 包含重复 case_id")
    actual_case_ids = set(batch_case_ids)
    if actual_case_ids != expected_case_ids:
        raise ValueError(
            f"{context}.cases 与 source/envelope 声明 ID 不一致: "
            f"missing={sorted(expected_case_ids - actual_case_ids)}, "
            f"unknown={sorted(actual_case_ids - expected_case_ids)}"
        )


def _validate_execution_evidence(
    case: Mapping[str, Any],
    snapshot: EvidenceSnapshot,
    *,
    release_tag: str,
    git_sha: str,
) -> None:
    case_id = _require_string(case.get("case_id"), "execution.case_id")
    case_kind = _require_string(case.get("case_kind"), f"{case_id}.case_kind")
    category = {
        "negative_execution": "negative",
        "load_execution": "load",
    }.get(case_kind)
    if category is None:
        raise ValueError(f"{case_id}.case_kind 不是执行证据用例")
    expected_prefix = f"{category}/evidence/{case_id}/"
    if (
        not snapshot.relative_path.startswith(expected_prefix)
        or not snapshot.relative_path.endswith(".json")
    ):
        raise ValueError(f"{snapshot.relative_path} 不属于用例 {case_id} 的原始证据")
    if snapshot.type != f"{category}_case_evidence":
        raise ValueError(f"{snapshot.relative_path} 执行证据类型与类别不匹配")
    validate_raw_execution_evidence(
        snapshot.payload,
        snapshot.relative_path,
        expected_case_id=case_id,
    )
    if (
        snapshot.payload.get("release_tag") != release_tag
        or snapshot.payload.get("git_sha") != git_sha
    ):
        raise ValueError(f"{snapshot.relative_path} 的 release/SHA 不匹配")


def _validate_case_basics(case: Mapping[str, Any]) -> None:
    case_id = _require_string(case.get("case_id"), "case.case_id")
    status = _require_string(case.get("status"), f"{case_id}.status")
    command = _require_string(case.get("command"), f"{case_id}.command")
    evidence = _require_list(case.get("evidence"), f"{case_id}.evidence")
    if status in {"通过", "失败"} and not evidence:
        raise ValueError(f"{case_id} 的通过/失败用例必须包含 evidence")
    if status == "失败" and not command:
        raise ValueError(f"{case_id} 的失败用例必须包含 command")
    if status == "未执行及原因":
        _require_string(case.get("reason"), f"{case_id}.reason")
    if FORBIDDEN_COMMAND.search(command):
        raise ValueError(f"用例命令包含仓储完成捷径或敏感 token: {case_id}")


def _collect_evidence_snapshots(
    release_root: Path,
    cases: Sequence[Mapping[str, Any]],
    *,
    expected_root_identity: RootIdentity,
) -> dict[str, EvidenceSnapshot]:
    owners: dict[str, list[Mapping[str, Any]]] = {}
    for case in cases:
        _validate_case_basics(case)
        case_id = _require_string(case.get("case_id"), "case.case_id")
        paths = [
            _require_string(path, f"{case_id}.evidence[{index}]")
            for index, path in enumerate(
                _require_list(case.get("evidence"), f"{case_id}.evidence")
            )
        ]
        case_kind = _require_string(case.get("case_kind"), f"{case_id}.case_kind")
        if case_kind in EXECUTION_CASE_KINDS:
            if not paths:
                raise ValueError(f"{case_id}.evidence 必须包含原始执行证据")
        elif len(paths) != 1:
            raise ValueError(f"{case_id}.evidence 必须恰好包含一个规范路径")
        for path in paths:
            safe_relative(path)
            owners.setdefault(path, []).append(case)
    for relative_path, path_owners in owners.items():
        if len(path_owners) > 1 and any(
            owner.get("case_kind") != "execution_declaration" for owner in path_owners
        ):
            raise ValueError(f"同一执行证据路径不得支持两个用例: {relative_path}")
    snapshots: dict[str, EvidenceSnapshot] = {}
    for relative_path in sorted(owners):
        snapshots[relative_path] = _snapshot_release_json(
            release_root,
            relative_path,
            expected_root_identity=expected_root_identity,
        )
    return snapshots


def validate_release_envelope(
    envelope: dict[str, Any],
    release_root: Path,
    *,
    expected_root_identity: RootIdentity,
) -> tuple[list[dict[str, Any]], list[EvidenceSnapshot], str, str]:
    validate_cases_envelope(envelope)
    release_tag = _require_string(envelope.get("release_tag"), "release_tag")
    git_sha = _require_string(envelope.get("git_sha"), "git_sha")
    if (
        release_root.name != git_sha
        or release_root.parent.name != release_tag
        or release_root.parent.parent.name != "releases"
    ):
        raise ValueError("用例 release/SHA 与 releases 归档目录不匹配")
    cases = [
        _require_object(raw_case, f"cases[{index}]")
        for index, raw_case in enumerate(_require_list(envelope.get("cases"), "cases"))
    ]
    declaration_ids_by_path: dict[str, set[str]] = {
        "negative/cases.json": set(),
        "load/cases.json": set(),
    }
    for case in cases:
        if case.get("case_kind") != "execution_declaration":
            continue
        path = _require_string(
            _require_list(case.get("evidence"), f"{case['case_id']}.evidence")[0],
            f"{case['case_id']}.evidence[0]",
        )
        declaration_ids_by_path[path].add(
            _require_string(case.get("source_case_id"), f"{case['case_id']}.source_case_id")
        )
    snapshots = _collect_evidence_snapshots(
        release_root,
        cases,
        expected_root_identity=expected_root_identity,
    )
    project_root = Path(__file__).resolve().parents[1]
    smoke_by_source, smoke_by_operator = _load_smoke_authority(project_root)
    authorities = _load_operator_authority(project_root, smoke_by_operator)
    runtime_by_target: dict[str, EvidenceSnapshot] = {}
    runtime_pids_by_target: dict[str, set[int]] = {}
    for case in cases:
        case_kind = _require_string(case.get("case_kind"), f"{case['case_id']}.case_kind")
        case_paths = [
            _require_string(path, f"{case['case_id']}.evidence[{index}]")
            for index, path in enumerate(
                _require_list(case["evidence"], f"{case['case_id']}.evidence")
            )
        ]
        expected_type = CASE_EVIDENCE_TYPES.get(case_kind)
        if case_kind in EXECUTION_CASE_KINDS:
            for path in case_paths:
                snapshot = snapshots[path]
                if snapshot.type != expected_type:
                    raise ValueError(
                        f"{snapshot.relative_path} 证据类型 {snapshot.type} "
                        f"与 {case_kind} 不匹配"
                    )
                _validate_execution_evidence(
                    case,
                    snapshot,
                    release_tag=release_tag,
                    git_sha=git_sha,
                )
            continue
        path = _require_string(
            case_paths[0],
            f"{case['case_id']}.evidence[0]",
        )
        snapshot = snapshots[path]
        if snapshot.type != expected_type:
            raise ValueError(
                f"{snapshot.relative_path} 证据类型 {snapshot.type} 与 {case_kind} 不匹配"
            )
        if snapshot.type == "operator_smoke":
            _validate_smoke_evidence(
                case,
                snapshot,
                release_tag=release_tag,
                git_sha=git_sha,
                authorities=authorities,
                smoke_by_source=smoke_by_source,
            )
        elif snapshot.type == "operator_registration":
            _validate_registration_evidence(
                case,
                snapshot,
                release_tag=release_tag,
                git_sha=git_sha,
                authorities=authorities,
            )
        elif snapshot.type == "gpu_runtime":
            target = _require_string(case.get("target"), "GPU runtime target")
            runtime_by_target[target] = snapshot
            runtime_pids_by_target[target] = _validate_gpu_runtime(
                case,
                snapshot,
                git_sha=git_sha,
                authorities=authorities,
            )
        elif snapshot.type == "execution_declaration":
            _validate_declaration_evidence(
                case,
                snapshot,
                release_tag=release_tag,
                git_sha=git_sha,
                expected_case_ids=declaration_ids_by_path[path],
            )
    for case in cases:
        if case.get("case_kind") != "gpu_stopped":
            continue
        target = _require_string(case.get("target"), "GPU recovery target")
        path = _require_string(
            _require_list(case["evidence"], f"{case['case_id']}.evidence")[0],
            f"{case['case_id']}.evidence[0]",
        )
        runtime = runtime_by_target.get(target)
        if runtime is None:
            raise ValueError(f"GPU recovery 缺少对应 runtime: {target}")
        _validate_gpu_recovery(
            case,
            snapshots[path],
            runtime,
            git_sha=git_sha,
            authorities=authorities,
            runtime_pids=runtime_pids_by_target[target],
        )
    evidence_snapshots = sorted(
        snapshots.values(),
        key=lambda snapshot: snapshot.relative_path,
    )
    return cases, evidence_snapshots, release_tag, git_sha


REPORT_OUTPUT_NAMES = ("report.json", "report.md")
REPORT_JOURNAL_NAME = ".report-transaction.journal"
_LOWER_HEX_32 = re.compile(r"[0-9a-f]{32}\Z")
_LOWER_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")


def _stat_at(parent_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        offset += os.write(descriptor, content[offset:])


def _require_private_regular(
    metadata: os.stat_result,
    context: str,
    *,
    allowed_links: frozenset[int] = frozenset({1}),
) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{context} 必须是普通文件")
    if metadata.st_uid != os.geteuid():
        raise ValueError(f"{context} 必须由当前 UID 所有")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ValueError(f"{context} 权限必须是 0600")
    if metadata.st_nlink not in allowed_links:
        raise ValueError(f"{context} 硬链接数不安全")


def _read_private_at(
    parent_fd: int,
    name: str,
    context: str,
    *,
    allowed_links: frozenset[int] = frozenset({1}),
) -> tuple[bytes, os.stat_result] | None:
    named = _stat_at(parent_fd, name)
    if named is None:
        return None
    _require_private_regular(named, context, allowed_links=allowed_links)
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        _require_private_regular(opened, context, allowed_links=allowed_links)
        if not _same_filesystem_object(named, opened):
            raise ValueError(f"{context} 打开期间发生变化")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 65_536):
            chunks.append(chunk)
        opened_after = os.fstat(descriptor)
        named_after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        _require_private_regular(opened_after, context, allowed_links=allowed_links)
        _require_private_regular(named_after, context, allowed_links=allowed_links)
        if (
            _metadata_snapshot(opened) != _metadata_snapshot(opened_after)
            or _metadata_snapshot(named_after) != _metadata_snapshot(opened_after)
        ):
            raise ValueError(f"{context} 读取期间发生变化")
    except OSError as exc:
        raise ValueError(f"无法安全读取{context}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    content = b"".join(chunks)
    if len(content) != opened.st_size:
        raise ValueError(f"{context} 字节数与文件大小不一致")
    return content, opened_after


def _create_private_at(
    parent_fd: int, name: str, content: bytes
) -> os.stat_result:
    descriptor = -1
    created_metadata: os.stat_result | None = None
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, content)
        os.fsync(descriptor)
        created_metadata = os.fstat(descriptor)
        _require_private_regular(created_metadata, f"事务文件 {name}")
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        _require_private_regular(named, f"事务文件 {name}")
        if (
            not _same_filesystem_object(created_metadata, named)
            or _metadata_snapshot(created_metadata) != _metadata_snapshot(named)
        ):
            raise ValueError(f"事务文件创建期间发生变化: {name}")
    except OSError as exc:
        raise ValueError(f"无法安全创建事务文件: {name}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    os.fsync(parent_fd)
    if created_metadata is None:
        raise ValueError(f"事务文件缺少创建快照: {name}")
    return created_metadata


def _require_bound_private_at(
    parent_fd: int,
    name: str,
    expected_content: bytes,
    expected_metadata: os.stat_result,
    context: str,
    *,
    allowed_links: frozenset[int] = frozenset({1}),
) -> os.stat_result:
    snapshot = _read_private_at(
        parent_fd,
        name,
        context,
        allowed_links=allowed_links,
    )
    if snapshot is None:
        raise ValueError(f"{context} 不存在")
    content, metadata = snapshot
    if content != expected_content:
        raise ValueError(f"{context} 内容摘要冲突")
    if not _same_filesystem_object(metadata, expected_metadata):
        raise ValueError(f"{context} inode 与事务快照不一致")
    return metadata


def _verified_unlink_at(
    parent_fd: int,
    name: str,
    expected_content: bytes,
    expected_metadata: os.stat_result,
    context: str,
    *,
    allowed_links: frozenset[int] = frozenset({1}),
) -> None:
    validated_metadata = _require_bound_private_at(
        parent_fd,
        name,
        expected_content,
        expected_metadata,
        context,
        allowed_links=allowed_links,
    )
    named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    _require_private_regular(named, context, allowed_links=allowed_links)
    if _metadata_snapshot(named) != _metadata_snapshot(validated_metadata):
        raise ValueError(f"{context} 删除前 metadata 发生变化")
    os.unlink(name, dir_fd=parent_fd)
    os.fsync(parent_fd)


def _journal_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _validate_journal(
    content: bytes,
    expected_outputs: Mapping[str, tuple[bytes, str]],
) -> tuple[dict[str, Any], str]:
    try:
        parsed = strict_json_loads(content.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("报告事务 journal 不合法") from exc
    payload = _require_object(parsed, "报告事务 journal")
    if set(payload) != {
        "schema_version",
        "transaction_id",
        "state",
        "outputs",
        "temporaries",
    }:
        raise ValueError("报告事务 journal 不合法")
    transaction_id = payload.get("transaction_id")
    if (
        type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 1
        or type(transaction_id) is not str
        or _LOWER_HEX_32.fullmatch(transaction_id) is None
        or payload.get("state") != "publishing"
        or type(payload.get("state")) is not str
    ):
        raise ValueError("报告事务 journal 不合法")
    output_states = _require_object(payload.get("outputs"), "报告事务 journal.outputs")
    if set(output_states) != set(REPORT_OUTPUT_NAMES):
        raise ValueError("报告事务 journal 不合法")
    for name in REPORT_OUTPUT_NAMES:
        output_state = _require_object(
            output_states.get(name), f"报告事务 journal.outputs.{name}"
        )
        if set(output_state) != {"sha256", "published"}:
            raise ValueError("报告事务 journal 不合法")
        digest = output_state.get("sha256")
        if (
            type(digest) is not str
            or _LOWER_HEX_64.fullmatch(digest) is None
            or digest != expected_outputs[name][1]
            or type(output_state.get("published")) is not bool
        ):
            raise ValueError("报告事务 journal 不合法")
    temporaries = _require_object(
        payload.get("temporaries"), "报告事务 journal.temporaries"
    )
    if not set(temporaries).issubset(REPORT_OUTPUT_NAMES):
        raise ValueError("报告事务 journal 不合法")
    for name, temporary_name in temporaries.items():
        if (
            type(temporary_name) is not str
            or temporary_name != f".{name}.{transaction_id}.tmp"
        ):
            raise ValueError("报告事务 journal 不合法")
    return payload, transaction_id


def _read_and_validate_journal(
    summary_fd: int,
    expected_outputs: Mapping[str, tuple[bytes, str]],
) -> tuple[dict[str, Any], str, bytes, os.stat_result] | None:
    try:
        snapshot = _read_private_at(
            summary_fd, REPORT_JOURNAL_NAME, "报告事务 journal"
        )
    except ValueError as exc:
        raise ValueError("报告事务 journal 不合法") from exc
    if snapshot is None:
        return None
    content, metadata = snapshot
    payload, transaction_id = _validate_journal(content, expected_outputs)
    return payload, transaction_id, content, metadata


def _validate_transaction_temporary(
    summary_fd: int,
    name: str,
    expected_content: bytes,
    terminal_metadata: os.stat_result | None,
) -> os.stat_result | None:
    snapshot = _read_private_at(
        summary_fd,
        name,
        "报告事务临时文件",
        allowed_links=frozenset({1, 2}),
    )
    if snapshot is None:
        return None
    content, metadata = snapshot
    if content != expected_content:
        raise ValueError("报告事务临时文件摘要冲突")
    if metadata.st_nlink == 2 and (
        terminal_metadata is None
        or not _same_filesystem_object(metadata, terminal_metadata)
    ):
        raise ValueError("报告事务临时文件硬链接状态不安全")
    return metadata


def _terminal_states(
    summary_fd: int,
    expected_outputs: Mapping[str, tuple[bytes, str]],
    *,
    recovery: bool,
) -> dict[str, os.stat_result | None]:
    states: dict[str, os.stat_result | None] = {}
    allowed_links = frozenset({1, 2}) if recovery else frozenset({1})
    for name in REPORT_OUTPUT_NAMES:
        try:
            snapshot = _read_private_at(
                summary_fd,
                name,
                f"报告输出 {name}",
                allowed_links=allowed_links,
            )
        except ValueError as exc:
            raise ValueError(f"拒绝覆盖不安全报告输出: {name}") from exc
        if snapshot is None:
            states[name] = None
            continue
        content, metadata = snapshot
        if hashlib.sha256(content).hexdigest() != expected_outputs[name][1]:
            raise ValueError(f"拒绝覆盖报告输出摘要冲突: {name}")
        states[name] = metadata
    return states


def _require_complete_terminal_pair(
    summary_fd: int,
    expected_outputs: Mapping[str, tuple[bytes, str]],
) -> None:
    terminal_states = _terminal_states(
        summary_fd, expected_outputs, recovery=False
    )
    if not all(state is not None for state in terminal_states.values()):
        raise ValueError("报告双文件终态不完整，保留事务 journal")


def _publish_from_temporary(
    summary_fd: int,
    temporary_name: str,
    output_name: str,
    expected_content: bytes,
    expected_metadata: os.stat_result,
) -> os.stat_result:
    _require_bound_private_at(
        summary_fd,
        temporary_name,
        expected_content,
        expected_metadata,
        "报告事务发布临时文件",
    )
    try:
        os.link(
            temporary_name,
            output_name,
            src_dir_fd=summary_fd,
            dst_dir_fd=summary_fd,
            follow_symlinks=False,
        )
    except FileExistsError as exc:
        raise ValueError(f"拒绝覆盖已存在报告输出: {output_name}") from exc
    except OSError as exc:
        raise ValueError(f"无法安全发布报告输出: {output_name}") from exc
    os.fsync(summary_fd)
    try:
        terminal_metadata = _require_bound_private_at(
            summary_fd,
            output_name,
            expected_content,
            expected_metadata,
            f"报告发布终态 {output_name}",
            allowed_links=frozenset({2}),
        )
        _require_bound_private_at(
            summary_fd,
            temporary_name,
            expected_content,
            expected_metadata,
            "报告事务发布临时文件",
            allowed_links=frozenset({2}),
        )
    except ValueError as exc:
        raise ValueError(f"报告发布后终态不安全: {output_name}") from exc
    return terminal_metadata


def _recover_transaction(
    summary_fd: int,
    expected_outputs: Mapping[str, tuple[bytes, str]],
    journal: tuple[dict[str, Any], str, bytes, os.stat_result],
) -> None:
    payload, transaction_id, journal_content, journal_metadata = journal
    terminal_states = _terminal_states(summary_fd, expected_outputs, recovery=True)
    temporary_names = {
        name: f".{name}.{transaction_id}.tmp" for name in REPORT_OUTPUT_NAMES
    }
    temporary_states = {
        name: _validate_transaction_temporary(
            summary_fd,
            temporary_names[name],
            expected_outputs[name][0],
            terminal_states[name],
        )
        for name in REPORT_OUTPUT_NAMES
    }
    for name in REPORT_OUTPUT_NAMES:
        terminal_metadata = terminal_states[name]
        temporary_metadata = temporary_states[name]
        if terminal_metadata is not None and terminal_metadata.st_nlink == 2 and (
            temporary_metadata is None
            or not _same_filesystem_object(terminal_metadata, temporary_metadata)
        ):
            raise ValueError("报告终态硬链接状态不安全")
    journal_temporary = f"{REPORT_JOURNAL_NAME}.{transaction_id}.tmp"
    journal_temporary_snapshot = _read_private_at(
        summary_fd, journal_temporary, "报告事务 journal 临时文件"
    )
    if journal_temporary_snapshot is not None:
        temporary_content, journal_temporary_metadata = journal_temporary_snapshot
        replacement_payload, replacement_id = _validate_journal(
            temporary_content, expected_outputs
        )
        if replacement_id != transaction_id or replacement_payload != payload:
            raise ValueError("报告事务 journal 临时文件不合法")
    for name in REPORT_OUTPUT_NAMES:
        if terminal_states[name] is not None:
            continue
        temporary_name = temporary_names[name]
        if temporary_states[name] is None:
            temporary_states[name] = _create_private_at(
                summary_fd, temporary_name, expected_outputs[name][0]
            )
        temporary_metadata = temporary_states[name]
        if temporary_metadata is None:
            raise ValueError("报告事务临时文件缺少安全快照")
        terminal_states[name] = _publish_from_temporary(
            summary_fd,
            temporary_name,
            name,
            expected_outputs[name][0],
            temporary_metadata,
        )
    for name in REPORT_OUTPUT_NAMES:
        temporary_name = temporary_names[name]
        temporary_metadata = temporary_states[name]
        if temporary_metadata is not None:
            _verified_unlink_at(
                summary_fd,
                temporary_name,
                expected_outputs[name][0],
                temporary_metadata,
                "报告事务临时文件",
                allowed_links=frozenset({1, 2}),
            )
    if journal_temporary_snapshot is not None:
        _verified_unlink_at(
            summary_fd,
            journal_temporary,
            temporary_content,
            journal_temporary_metadata,
            "报告事务 journal 临时文件",
        )
    _require_complete_terminal_pair(summary_fd, expected_outputs)
    _verified_unlink_at(
        summary_fd,
        REPORT_JOURNAL_NAME,
        journal_content,
        journal_metadata,
        "报告事务 journal",
    )


def _write_initial_journal(
    summary_fd: int,
    payload: Mapping[str, object],
) -> tuple[bytes, os.stat_result]:
    content = _journal_bytes(payload)
    metadata = _create_private_at(summary_fd, REPORT_JOURNAL_NAME, content)
    return content, metadata


def _replace_journal_for_crash_injection(
    summary_fd: int,
    transaction_id: str,
    content: bytes,
    journal_metadata: os.stat_result,
) -> os.stat_result:
    temporary_name = f"{REPORT_JOURNAL_NAME}.{transaction_id}.tmp"
    temporary_metadata = _create_private_at(summary_fd, temporary_name, content)
    if os.getenv("REPORT_TRANSACTION_CRASH_DURING_JOURNAL_REPLACE"):
        os._exit(87)
    _require_bound_private_at(
        summary_fd,
        REPORT_JOURNAL_NAME,
        content,
        journal_metadata,
        "报告事务 journal",
    )
    _require_bound_private_at(
        summary_fd,
        temporary_name,
        content,
        temporary_metadata,
        "报告事务 journal 临时文件",
    )
    try:
        os.rename(
            temporary_name,
            REPORT_JOURNAL_NAME,
            src_dir_fd=summary_fd,
            dst_dir_fd=summary_fd,
        )
    except OSError as exc:
        raise ValueError("无法安全替换报告事务 journal") from exc
    os.fsync(summary_fd)
    return _require_bound_private_at(
        summary_fd,
        REPORT_JOURNAL_NAME,
        content,
        temporary_metadata,
        "报告事务 journal",
    )


def publish_report_transaction(
    release_root: Path,
    outputs: tuple[tuple[Path, bytes], tuple[Path, bytes]],
    *,
    expected_root_identity: RootIdentity,
) -> None:
    if len(outputs) != len(REPORT_OUTPUT_NAMES):
        raise ValueError("报告事务必须精确包含 JSON 与 Markdown")
    expected_paths = {
        name: release_root / "summary" / name for name in REPORT_OUTPUT_NAMES
    }
    output_by_name: dict[str, tuple[bytes, str]] = {}
    for path, content in outputs:
        if path.name not in expected_paths or path != expected_paths[path.name]:
            raise ValueError("报告输出必须位于当前 release 的 summary 目录")
        if path.name in output_by_name:
            raise ValueError("报告事务包含重复输出")
        output_by_name[path.name] = (content, hashlib.sha256(content).hexdigest())
    if set(output_by_name) != set(REPORT_OUTPUT_NAMES):
        raise ValueError("报告事务必须精确包含 JSON 与 Markdown")
    with _release_directory_descriptor(
        release_root,
        PurePosixPath("summary"),
        expected_root_identity,
    ) as summary_fd:
        fcntl.flock(summary_fd, fcntl.LOCK_EX)
        journal = _read_and_validate_journal(summary_fd, output_by_name)
        if journal is not None:
            _recover_transaction(summary_fd, output_by_name, journal)
        terminal_states = _terminal_states(
            summary_fd, output_by_name, recovery=False
        )
        if all(state is not None for state in terminal_states.values()):
            return
        if any(state is not None for state in terminal_states.values()):
            raise ValueError("报告双文件状态不一致，拒绝补写半套报告")
        transaction_id = uuid.uuid4().hex
        temporary_names = {
            name: f".{name}.{transaction_id}.tmp" for name in REPORT_OUTPUT_NAMES
        }
        payload: dict[str, object] = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "state": "publishing",
            "outputs": {
                name: {"sha256": output_by_name[name][1], "published": False}
                for name in REPORT_OUTPUT_NAMES
            },
            "temporaries": temporary_names,
        }
        journal_content, journal_metadata = _write_initial_journal(
            summary_fd, payload
        )
        journal_metadata = _replace_journal_for_crash_injection(
            summary_fd,
            transaction_id,
            journal_content,
            journal_metadata,
        )
        for index, name in enumerate(REPORT_OUTPUT_NAMES):
            temporary_name = temporary_names[name]
            temporary_metadata = _create_private_at(
                summary_fd, temporary_name, output_by_name[name][0]
            )
            _publish_from_temporary(
                summary_fd,
                temporary_name,
                name,
                output_by_name[name][0],
                temporary_metadata,
            )
            if index == 0 and os.getenv("REPORT_TRANSACTION_CRASH_AFTER_FIRST_RENAME"):
                os._exit(86)
            if index == 0 and os.getenv("REPORT_TRANSACTION_FAIL_AFTER_FIRST_RENAME"):
                raise RuntimeError("注入第一文件发布后的事务失败")
            if index == 1 and os.getenv("REPORT_TRANSACTION_CRASH_AFTER_SECOND_RENAME"):
                os._exit(88)
            _verified_unlink_at(
                summary_fd,
                temporary_name,
                output_by_name[name][0],
                temporary_metadata,
                "报告事务临时文件",
                allowed_links=frozenset({2}),
            )
        _require_complete_terminal_pair(summary_fd, output_by_name)
        _verified_unlink_at(
            summary_fd,
            REPORT_JOURNAL_NAME,
            journal_content,
            journal_metadata,
            "报告事务 journal",
        )


def _require_canonical_cli_paths(arguments: argparse.Namespace) -> None:
    summary = arguments.release_root / "summary"
    expected = {
        "--input": summary / "cases.json",
        "--output-json": summary / "report.json",
        "--output-markdown": summary / "report.md",
    }
    actual = {
        "--input": arguments.input,
        "--output-json": arguments.output_json,
        "--output-markdown": arguments.output_markdown,
    }
    for option, expected_path in expected.items():
        if os.fsencode(actual[option]) != os.fsencode(expected_path):
            raise ValueError(f"{option} 必须精确等于 {expected_path}")


def render(
    envelope: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    evidence_snapshots: Sequence[EvidenceSnapshot],
    conclusion: str,
    cases_snapshot: EvidenceSnapshot,
) -> tuple[dict[str, Any], str]:
    release_tag = _require_string(envelope.get("release_tag"), "release_tag")
    git_sha = _require_string(envelope.get("git_sha"), "git_sha")
    real_cases = [case for case in cases if case.get("mock") is False]
    counts = {
        status: sum(case.get("status") == status for case in real_cases)
        for status in STATUSES
    }
    evidence_index = {
        snapshot.relative_path: {
            "type": snapshot.type,
            "bytes": snapshot.size,
            "sha256": snapshot.sha256,
        }
        for snapshot in evidence_snapshots
    }
    document = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "cases_schema_version": envelope["schema_version"],
        "release_tag": release_tag,
        "git_sha": git_sha,
        "plan_sha256": envelope["plan_sha256"],
        "cases_input": {
            "bytes": cases_snapshot.size,
            "sha256": cases_snapshot.sha256,
        },
        "overall_status": conclusion,
        "coverage": envelope["coverage"],
        "counts": counts,
        "evidence_index": evidence_index,
        "cases": list(cases),
    }
    lines = [
        "# 里程碑 2B 验证报告",
        "",
        "## 验收结论",
        "",
        f"**{conclusion}**",
        "",
        f"- Release：`{release_tag}`",
        f"- Git SHA：`{git_sha}`",
        f"- Cases schema：`{envelope['schema_version']}`",
        "",
        "## 真实验证",
        "",
        f"通过 {counts['通过']}，失败 {counts['失败']}，未执行 {counts['未执行及原因']}。",
        "",
        "## 覆盖率",
        "",
        "| 覆盖项 | 期望 | 已观察 | 已通过 |",
        "|---|---:|---:|---:|",
    ]
    coverage = _require_object(envelope.get("coverage"), "coverage")
    for key in sorted(coverage):
        item = _require_object(coverage[key], f"coverage.{key}")
        lines.append(
            f"| {html.escape(key)} | {item['expected']} | "
            f"{item['observed']} | {item['passed']} |"
        )
    lines.extend(
        [
            "",
            "## 用例明细",
            "",
            "| 用例 | 类型 | 状态 | 目标 | 原因 |",
            "|---|---|---|---|---|",
        ]
    )
    for case in cases:
        kind = "Mock" if case["mock"] else "真实"
        cells = [
            str(case["case_id"]),
            kind,
            str(case["status"]),
            str(case["target"]),
            str(case["reason"]),
        ]
        escaped = [
            html.escape(cell, quote=True)
            .replace("|", "\\|")
            .replace("\r", " ")
            .replace("\n", " ")
            for cell in cells
        ]
        lines.append("| " + " | ".join(escaped) + " |")
    return document, "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    try:
        _require_canonical_cli_paths(args)
        root_identity = release_root_identity(args.release_root)
        cases_snapshot = _snapshot_release_json(
            args.release_root,
            "summary/cases.json",
            snapshot_type="cases_envelope",
            expected_root_identity=root_identity,
        )
        envelope = cases_snapshot.payload
        cases, evidence_snapshots, _release_tag, _git_sha = validate_release_envelope(
            envelope,
            args.release_root,
            expected_root_identity=root_identity,
        )
        conclusion = overall_status(cast(Sequence[Mapping[str, object]], cases))
        document, markdown = render(
            envelope,
            cases,
            evidence_snapshots,
            conclusion,
            cases_snapshot,
        )
        outputs = (
            (
                args.output_json,
                (
                    json.dumps(
                        document,
                        allow_nan=False,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8"),
            ),
            (args.output_markdown, markdown.encode("utf-8")),
        )
        if release_root_identity(args.release_root) != root_identity:
            raise ValueError("release root 与最终 root 锚点不一致")
        publish_report_transaction(
            args.release_root,
            outputs,
            expected_root_identity=root_identity,
        )
        return 0 if conclusion == "通过" else 3
    except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
        print(f"报告生成失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
