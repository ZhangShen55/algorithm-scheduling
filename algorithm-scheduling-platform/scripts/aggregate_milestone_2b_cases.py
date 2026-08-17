#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from scripts.milestone_2b_report_contract import (
        CaseRecord,
        Coverage,
        load_report_plan,
        strict_json_loads,
    )
else:
    _contract = importlib.import_module(
        "scripts.milestone_2b_report_contract"
        if __package__
        else "milestone_2b_report_contract"
    )
    CaseRecord = _contract.CaseRecord
    Coverage = _contract.Coverage
    load_report_plan = _contract.load_report_plan
    strict_json_loads = _contract.strict_json_loads

EXPECTED_PROFILES = ("gpu0", "gpu1", "gpu2", "cpu")
EXPECTED_FACEREC_INSTANCES = (
    "facerec-gpu0",
    "facerec-gpu1",
    "facerec-gpu2",
)
CPU_OPERATOR_CODES = {
    "ppt-slice": "ppt_slice",
    "text-analysis": "text_analysis",
}
GPU_ENVIRONMENT_FIELDS = (
    "PLATFORM_GPU_ID",
    "GPU_PROCESS_NAME",
    "NVIDIA_VISIBLE_DEVICES",
    "REQUIRE_GPU",
)
INSTANCE_ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
OPERATOR_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
GIT_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class OperatorInstance:
    service_name: str
    instance_id: str
    operator_code: str
    profile: str
    physical_gpu: int | None
    process_name: str | None


@dataclass(frozen=True)
class OperatorInventory:
    instances: tuple[OperatorInstance, ...]

    @property
    def gpu_instances(self) -> tuple[OperatorInstance, ...]:
        return tuple(
            instance for instance in self.instances if instance.physical_gpu is not None
        )

    @property
    def cpu_instances(self) -> tuple[OperatorInstance, ...]:
        return tuple(
            instance for instance in self.instances if instance.physical_gpu is None
        )


def _require_object(value: object, context: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{context} must be an object")
    raw = cast(dict[object, object], value)
    if any(type(key) is not str for key in raw):
        raise ValueError(f"{context} contains a non-string field name")
    return cast(dict[str, Any], value)


def _require_list(value: object, context: str) -> list[Any]:
    if type(value) is not list:
        raise ValueError(f"{context} must be a list")
    return value


def _require_string(value: object, context: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{context} must be a non-empty string")
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value):
        raise ValueError(f"{context} contains a control character")
    return value


def _require_nonnegative_int(value: object, context: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{context} must be a non-negative integer")
    return value


def _parse_gpu_id(value: object, context: str) -> int:
    if type(value) is int:
        gpu_id = value
    elif type(value) is str and re.fullmatch(r"[0-9]+", value) is not None:
        gpu_id = int(value)
    else:
        raise ValueError(f"{context} must be a non-negative integer or digit string")
    if gpu_id < 0:
        raise ValueError(f"{context} must be non-negative")
    return gpu_id


def load_operator_inventory(path: Path) -> OperatorInventory:
    try:
        loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"failed to read operator Compose inventory: {path}") from exc
    document = _require_object(loaded, f"operator Compose {path}")
    services = _require_object(document.get("services"), f"{path}: services")
    if len(services) != 24:
        raise ValueError(f"{path}: services must contain exactly 24 operator instances")

    instances: list[OperatorInstance] = []
    seen_instance_ids: set[str] = set()
    for service_name, raw_service in services.items():
        if INSTANCE_ID_PATTERN.fullmatch(service_name) is None:
            raise ValueError(f"{path}: invalid service name: {service_name}")
        service = _require_object(raw_service, f"{path}: services.{service_name}")
        environment = _require_object(
            service.get("environment"),
            f"{path}: services.{service_name}.environment",
        )
        raw_profiles = _require_list(
            service.get("profiles"),
            f"{path}: services.{service_name}.profiles",
        )
        if len(raw_profiles) != 1:
            raise ValueError(f"{path}: {service_name} must have exactly one profile")
        profile = _require_string(raw_profiles[0], f"{path}: {service_name} profile")
        instance_id = _require_string(
            environment.get("PLATFORM_INSTANCE_ID"),
            f"{path}: {service_name}.PLATFORM_INSTANCE_ID",
        )
        if instance_id in seen_instance_ids:
            raise ValueError(f"{path}: duplicate PLATFORM_INSTANCE_ID: {instance_id}")
        if instance_id != service_name:
            raise ValueError(
                f"{path}: service {service_name} does not match "
                f"PLATFORM_INSTANCE_ID {instance_id}"
            )
        seen_instance_ids.add(instance_id)

        if profile in {"gpu0", "gpu1", "gpu2"}:
            physical_gpu = _parse_gpu_id(
                environment.get("PLATFORM_GPU_ID"),
                f"{path}: {service_name}.PLATFORM_GPU_ID",
            )
            expected_gpu = int(profile.removeprefix("gpu"))
            if physical_gpu != expected_gpu:
                raise ValueError(
                    f"{path}: {service_name} profile {profile} does not match "
                    f"PLATFORM_GPU_ID {physical_gpu}"
                )
            process_name = _require_string(
                environment.get("GPU_PROCESS_NAME"),
                f"{path}: {service_name}.GPU_PROCESS_NAME",
            )
            if OPERATOR_CODE_PATTERN.fullmatch(process_name) is None:
                raise ValueError(f"{path}: {service_name} GPU_PROCESS_NAME is invalid")
            suffix = f"-{profile}"
            if not service_name.endswith(suffix):
                raise ValueError(f"{path}: {service_name} does not match profile {profile}")
            operator_code = service_name.removesuffix(suffix).replace("-", "_")
            if process_name != operator_code:
                raise ValueError(
                    f"{path}: {service_name} GPU_PROCESS_NAME {process_name} does not "
                    f"match operator_code {operator_code}"
                )
        elif profile == "cpu":
            present_gpu_fields = [
                field for field in GPU_ENVIRONMENT_FIELDS if field in environment
            ]
            if present_gpu_fields:
                raise ValueError(
                    f"{path}: CPU service {service_name} contains GPU fields "
                    f"{present_gpu_fields}"
                )
            matched_prefixes = [
                prefix
                for prefix in CPU_OPERATOR_CODES
                if re.fullmatch(rf"{re.escape(prefix)}-cpu[0-9]+", service_name)
            ]
            if len(matched_prefixes) != 1:
                raise ValueError(f"{path}: unsupported CPU service: {service_name}")
            operator_code = CPU_OPERATOR_CODES[matched_prefixes[0]]
            physical_gpu = None
            process_name = None
        else:
            raise ValueError(f"{path}: unsupported profile for {service_name}: {profile}")

        instances.append(
            OperatorInstance(
                service_name=service_name,
                instance_id=instance_id,
                operator_code=operator_code,
                profile=profile,
                physical_gpu=physical_gpu,
                process_name=process_name,
            )
        )

    inventory = OperatorInventory(
        instances=tuple(sorted(instances, key=lambda instance: instance.instance_id))
    )
    if len(inventory.instances) != 24:
        raise ValueError(f"{path}: operator inventory must contain exactly 24 instances")
    if len(inventory.gpu_instances) != 18:
        raise ValueError(f"{path}: operator inventory must contain exactly 18 GPU instances")
    if len(inventory.cpu_instances) != 6:
        raise ValueError(f"{path}: operator inventory must contain exactly 6 CPU instances")
    return inventory


def registration_paths(inventory: OperatorInventory) -> dict[str, Path]:
    selected_faces = tuple(
        instance.instance_id
        for instance in inventory.gpu_instances
        if instance.operator_code == "facerec"
    )
    if selected_faces != EXPECTED_FACEREC_INSTANCES:
        raise ValueError(
            "FaceRec inventory must equal " f"{list(EXPECTED_FACEREC_INSTANCES)}"
        )
    face_hash = hashlib.sha256("\n".join(selected_faces).encode()).hexdigest()[:12]
    paths = {
        "full": Path("registration/operator-registration.json"),
        "profile:gpu0": Path(
            "registration/operator-registration-profile-gpu0.json"
        ),
        "profile:gpu1": Path(
            "registration/operator-registration-profile-gpu1.json"
        ),
        "profile:gpu2": Path(
            "registration/operator-registration-profile-gpu2.json"
        ),
        "profile:cpu": Path("registration/operator-registration-profile-cpu.json"),
        "facerec": Path(
            f"registration/operator-registration-instances-{face_hash}.json"
        ),
    }
    paths.update(
        {
            f"recovery:{instance.instance_id}": Path(
                "registration/"
                f"operator-registration-instance-{instance.instance_id}.json"
            )
            for instance in inventory.gpu_instances
        }
    )
    return paths


def _require_release_root(release_root: Path) -> None:
    try:
        metadata = os.lstat(release_root)
    except OSError as exc:
        raise ValueError(f"release root is missing or unreadable: {release_root}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"release root must be a real directory: {release_root}")


def _read_release_text(release_root: Path, relative_path: Path) -> str:
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"release source path escapes release root: {relative_path}")
    if not relative_path.parts:
        raise ValueError("release source path must not be empty")
    _require_release_root(release_root)

    current = release_root
    for part in relative_path.parts[:-1]:
        current /= part
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise ValueError(f"release source directory is missing: {relative_path}") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(
                f"release source directory is unsafe for {relative_path}: {current}"
            )

    candidate = release_root.joinpath(*relative_path.parts)
    try:
        metadata = os.lstat(candidate)
    except OSError as exc:
        raise ValueError(f"required release source is missing: {relative_path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"release source must be a regular non-symlink file: {relative_path}")

    descriptor = -1
    try:
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError(f"release source must be a regular file: {relative_path}")
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ValueError(f"release source changed while opening: {relative_path}")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 65_536):
            chunks.append(chunk)
    except OSError as exc:
        raise ValueError(f"failed to read release source: {relative_path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"release source is not UTF-8: {relative_path}") from exc


def _load_release_json(release_root: Path, relative_path: Path) -> dict[str, Any]:
    text = _read_release_text(release_root, relative_path)
    try:
        loaded = strict_json_loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid JSON release source {relative_path}: {exc}") from exc
    return _require_object(loaded, f"release source {relative_path}")


def _registration_authority(report_plan: dict[str, Any]) -> None:
    registration = _require_object(
        report_plan.get("registration"), "report_plan.registration"
    )
    profiles = tuple(
        _require_string(item, f"report_plan.registration.profiles[{index}]")
        for index, item in enumerate(
            _require_list(
                registration.get("profiles"), "report_plan.registration.profiles"
            )
        )
    )
    if profiles != EXPECTED_PROFILES:
        raise ValueError(
            f"report_plan.registration.profiles must equal {list(EXPECTED_PROFILES)}"
        )
    if registration.get("require_full") is not True:
        raise ValueError("report_plan.registration.require_full must be true")
    if registration.get("require_gpu_recovery_instances") is not True:
        raise ValueError(
            "report_plan.registration.require_gpu_recovery_instances must be true"
        )
    faces = tuple(
        _require_string(
            item, f"report_plan.registration.facerec_instances[{index}]"
        )
        for index, item in enumerate(
            _require_list(
                registration.get("facerec_instances"),
                "report_plan.registration.facerec_instances",
            )
        )
    )
    if faces != EXPECTED_FACEREC_INSTANCES:
        raise ValueError(
            "report_plan.registration.facerec_instances must equal "
            f"{list(EXPECTED_FACEREC_INSTANCES)}"
        )


def _validate_registration(
    payload: dict[str, Any],
    *,
    relative_path: Path,
    selection: dict[str, Any],
    expected_count: int,
    release_tag: str,
    git_sha: str,
) -> tuple[str, str, str, str]:
    context = relative_path.as_posix()
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ValueError(f"{context}: schema_version must equal 1")
    if payload.get("evidence_type") != "operator_registration":
        raise ValueError(f"{context}: evidence_type must be operator_registration")
    if payload.get("mock") is not False:
        raise ValueError(f"{context}: mock must be false")
    if payload.get("release_tag") != release_tag:
        raise ValueError(f"{context}: release_tag does not match current release")
    if payload.get("git_sha") != git_sha:
        raise ValueError(f"{context}: git_sha does not match current release")
    if payload.get("target") != "operator-registry":
        raise ValueError(f"{context}: target must equal operator-registry")
    if payload.get("selection") != selection:
        raise ValueError(f"{context}: selection does not match {selection}")

    status = payload.get("status")
    if status not in {"通过", "失败"}:
        raise ValueError(f"{context}: status must be 通过 or 失败")
    started_at = _require_string(payload.get("started_at"), f"{context}: started_at")
    finished_at = _require_string(
        payload.get("finished_at"), f"{context}: finished_at"
    )
    summary = _require_object(payload.get("summary"), f"{context}: summary")
    if set(summary) != {"expected", "observed", "valid"}:
        raise ValueError(
            f"{context}: summary fields must equal expected, observed, valid"
        )
    expected = _require_nonnegative_int(summary["expected"], f"{context}: expected")
    observed = _require_nonnegative_int(summary["observed"], f"{context}: observed")
    valid = _require_nonnegative_int(summary["valid"], f"{context}: valid")
    if expected != expected_count:
        raise ValueError(
            f"{context}: summary.expected must equal {expected_count}, got {expected}"
        )

    raw_issues = _require_list(payload.get("issues"), f"{context}: issues")
    issues = [
        _require_string(issue, f"{context}: issues[{index}]")
        for index, issue in enumerate(raw_issues)
    ]
    if status == "通过":
        if (expected, observed, valid) != (
            expected_count,
            expected_count,
            expected_count,
        ):
            raise ValueError(
                f"{context}: passing summary must have expected=observed=valid"
            )
        if issues:
            raise ValueError(f"{context}: passing registration must not contain issues")
        reason = "registration evidence passed"
    else:
        if valid > min(expected, observed):
            raise ValueError(
                f"{context}: summary must satisfy valid <= min(expected, observed)"
            )
        if not issues:
            raise ValueError(f"{context}: failed registration must contain issues")
        reason = "; ".join(issues)
    return status, started_at, finished_at, reason


def _case_record(
    *,
    case_id: str,
    case_kind: str,
    run_id: str,
    status: str,
    started_at: str,
    finished_at: str,
    target: str,
    command: str,
    evidence: Path,
    reason: str,
    release_tag: str,
    git_sha: str,
) -> CaseRecord:
    return {
        "case_id": case_id,
        "source_case_id": case_id,
        "case_kind": case_kind,
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "target": target,
        "command": command,
        "evidence": [evidence.as_posix()],
        "reason": reason,
        "mock": False,
        "release_tag": release_tag,
        "git_sha": git_sha,
    }


def _validate_gpu_target(
    payload: dict[str, Any],
    instance: OperatorInstance,
    context: str,
) -> None:
    target = _require_object(payload.get("target"), f"{context}.target")
    if target.get("instance_id") != instance.instance_id:
        raise ValueError(f"{context}.target.instance_id does not match inventory")
    if (
        type(target.get("physical_gpu")) is not int
        or target["physical_gpu"] != instance.physical_gpu
    ):
        raise ValueError(f"{context}.target.physical_gpu does not match inventory")
    if target.get("process_name") != instance.process_name:
        raise ValueError(f"{context}.target.process_name does not match inventory")
    if "container" in target and target["container"] != instance.service_name:
        raise ValueError(f"{context}.target.container does not match inventory")


def _validate_container(
    value: object,
    instance: OperatorInstance,
    context: str,
) -> dict[str, Any]:
    container = _require_object(value, context)
    if container.get("instance_id") != instance.instance_id:
        raise ValueError(f"{context}.instance_id does not match inventory")
    if container.get("name") != instance.service_name:
        raise ValueError(f"{context}.name does not match inventory")
    _require_string(container.get("id"), f"{context}.id")
    if "init_host_pid" in container:
        pid = _require_nonnegative_int(
            container["init_host_pid"], f"{context}.init_host_pid"
        )
        if pid == 0:
            raise ValueError(f"{context}.init_host_pid must be positive")
    return container


def _validate_gpu_identity(
    value: object,
    instance: OperatorInstance,
    context: str,
) -> dict[str, Any]:
    gpu = _require_object(value, context)
    if (
        type(gpu.get("physical_index")) is not int
        or gpu["physical_index"] != instance.physical_gpu
    ):
        raise ValueError(f"{context}.physical_index does not match inventory")
    _require_string(gpu.get("physical_uuid"), f"{context}.physical_uuid")
    if "container_visible" in gpu and gpu["container_visible"] != str(
        instance.physical_gpu
    ):
        raise ValueError(f"{context}.container_visible does not match inventory")
    return gpu


def _activity_run_id(
    value: object,
    instance: OperatorInstance,
    context: str,
) -> str:
    activity = _require_object(value, context)
    if activity.get("instance_id") != instance.instance_id:
        raise ValueError(f"{context}.instance_id does not match inventory")
    if activity.get("operator_code") != instance.operator_code:
        raise ValueError(f"{context}.operator_code does not match inventory")
    return _require_string(activity.get("run_id"), f"{context}.run_id")


def _sample_host_pids(
    value: object,
    instance: OperatorInstance,
    context: str,
) -> set[int]:
    samples = _require_list(value, context)
    host_pids: set[int] = set()
    for sample_index, raw_sample in enumerate(samples):
        sample_context = f"{context}[{sample_index}]"
        sample = _require_object(raw_sample, sample_context)
        if "processes" not in sample:
            continue
        processes = _require_list(sample["processes"], f"{sample_context}.processes")
        sample_host_pids: set[int] = set()
        for process_index, raw_process in enumerate(processes):
            process_context = f"{sample_context}.processes[{process_index}]"
            process = _require_object(raw_process, process_context)
            if (
                "process_name" in process
                and process["process_name"] != instance.process_name
            ):
                raise ValueError(
                    f"{process_context}.process_name does not match inventory"
                )
            if "host_pid" in process:
                host_pid = _require_nonnegative_int(
                    process["host_pid"], f"{process_context}.host_pid"
                )
                if host_pid == 0:
                    raise ValueError(f"{process_context}.host_pid must be positive")
                if host_pid in sample_host_pids:
                    raise ValueError(f"{process_context}.host_pid is duplicate")
                sample_host_pids.add(host_pid)
                host_pids.add(host_pid)
    return host_pids


def _pid_list(value: object, context: str) -> list[int]:
    raw_pids = _require_list(value, context)
    pids = [
        _require_nonnegative_int(pid, f"{context}[{index}]")
        for index, pid in enumerate(raw_pids)
    ]
    if any(pid == 0 for pid in pids):
        raise ValueError(f"{context} must contain only positive PIDs")
    if pids != sorted(set(pids)):
        raise ValueError(f"{context} must contain unique sorted PIDs")
    return pids


def validate_gpu_pair(
    instance: OperatorInstance,
    running: dict[str, Any],
    stopped: dict[str, Any],
    git_sha: str,
) -> None:
    if instance.physical_gpu is None or instance.process_name is None:
        raise ValueError(f"{instance.instance_id} is not a GPU inventory instance")
    if GIT_SHA_PATTERN.fullmatch(git_sha) is None:
        raise ValueError("git_sha must be 40 lowercase hexadecimal characters")

    payloads = (("running", running), ("stopped", stopped))
    for label, payload in payloads:
        context = f"{instance.instance_id}.{label}"
        if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
            raise ValueError(f"{context}.schema_version must equal 1")
        _require_string(payload.get("timestamp"), f"{context}.timestamp")
        commands = _require_list(payload.get("commands"), f"{context}.commands")
        if not commands:
            raise ValueError(f"{context}.commands must not be empty")
        for index, command in enumerate(commands):
            _require_string(command, f"{context}.commands[{index}]")
        expected_mode = "running-inference" if label == "running" else "assert-stopped"
        if payload.get("mode") != expected_mode:
            raise ValueError(f"{context}.mode must equal {expected_mode}")
        _validate_gpu_target(payload, instance, context)
        status_value = payload.get("status")
        if status_value not in {"PASS", "FAIL"}:
            raise ValueError(f"{context}.status must be PASS or FAIL")
        if "release_sha" in payload and payload["release_sha"] != git_sha:
            raise ValueError(f"{context}.release_sha does not match current release")
        if status_value == "FAIL":
            _require_string(payload.get("reason"), f"{context}.reason")

        if "container" in payload:
            _validate_container(payload["container"], instance, f"{context}.container")
        if "gpu" in payload:
            _validate_gpu_identity(payload["gpu"], instance, f"{context}.gpu")
        if "activity" in payload:
            _activity_run_id(payload["activity"], instance, f"{context}.activity")
        if "synchronous_samples" in payload:
            _sample_host_pids(
                payload["synchronous_samples"],
                instance,
                f"{context}.synchronous_samples",
            )

    running_pids: set[int] = set()
    if "synchronous_samples" in running:
        running_pids = _sample_host_pids(
            running["synchronous_samples"],
            instance,
            f"{instance.instance_id}.running.synchronous_samples",
        )
    if running["status"] == "PASS":
        if running.get("release_sha") != git_sha:
            raise ValueError(f"{instance.instance_id}.running PASS requires release_sha")
        _validate_container(
            running.get("container"), instance, f"{instance.instance_id}.running.container"
        )
        _validate_gpu_identity(
            running.get("gpu"), instance, f"{instance.instance_id}.running.gpu"
        )
        _activity_run_id(
            running.get("activity"), instance, f"{instance.instance_id}.running.activity"
        )
        samples = _require_list(
            running.get("synchronous_samples"),
            f"{instance.instance_id}.running.synchronous_samples",
        )
        if not samples:
            raise ValueError(
                f"{instance.instance_id}.running PASS requires synchronous_samples"
            )
        if not running_pids:
            raise ValueError(
                f"{instance.instance_id}.running PASS requires at least one host_pid"
            )

    if "container" in stopped and "container" in running:
        if stopped["container"] != running["container"]:
            raise ValueError(f"{instance.instance_id}.stopped container does not match running")
    if "gpu" in stopped and "gpu" in running:
        if stopped["gpu"] != running["gpu"]:
            raise ValueError(f"{instance.instance_id}.stopped gpu does not match running")
    if "prior_cuda_pids" in stopped:
        prior_pids = _pid_list(
            stopped["prior_cuda_pids"],
            f"{instance.instance_id}.stopped.prior_cuda_pids",
        )
        if prior_pids != sorted(running_pids):
            raise ValueError(
                f"{instance.instance_id}.stopped prior_cuda_pids do not match running"
            )
    else:
        prior_pids = []
    if "remaining_cuda_pids" in stopped:
        remaining_pids = _pid_list(
            stopped["remaining_cuda_pids"],
            f"{instance.instance_id}.stopped.remaining_cuda_pids",
        )
        if not set(remaining_pids).issubset(prior_pids):
            raise ValueError(
                f"{instance.instance_id}.stopped remaining_cuda_pids are not prior PIDs"
            )

    if stopped["status"] == "PASS":
        if stopped.get("release_sha") != git_sha:
            raise ValueError(
                f"{instance.instance_id}.stopped PASS requires release_sha"
            )
        if running["status"] != "PASS":
            raise ValueError(
                f"{instance.instance_id}.stopped PASS requires matching running PASS"
            )
        if not prior_pids:
            raise ValueError(
                f"{instance.instance_id}.stopped PASS requires non-empty "
                "prior_cuda_pids"
            )
        if stopped.get("container") != running.get("container"):
            raise ValueError(f"{instance.instance_id}.stopped container does not match running")
        if stopped.get("gpu") != running.get("gpu"):
            raise ValueError(f"{instance.instance_id}.stopped gpu does not match running")
        if stopped.get("remaining_cuda_pids") != []:
            raise ValueError(
                f"{instance.instance_id}.stopped PASS requires remaining_cuda_pids=[]"
            )


def _commands(payload: dict[str, Any], context: str) -> str:
    return " ; ".join(
        _require_string(command, f"{context}.commands[{index}]")
        for index, command in enumerate(
            _require_list(payload.get("commands"), f"{context}.commands")
        )
    )


def _gpu_reason(payload: dict[str, Any], context: str) -> str:
    if "reason" in payload:
        return _require_string(payload["reason"], f"{context}.reason")
    return "GPU evidence passed"


def collect_registration_gpu_cases(
    *,
    release_root: Path,
    inventory: OperatorInventory,
    report_plan: dict[str, Any],
    release_tag: str,
    git_sha: str,
) -> tuple[list[CaseRecord], dict[str, Coverage]]:
    _require_string(release_tag, "release_tag")
    if GIT_SHA_PATTERN.fullmatch(git_sha) is None:
        raise ValueError("git_sha must be 40 lowercase hexadecimal characters")
    _require_release_root(release_root)
    _registration_authority(report_plan)
    paths = registration_paths(inventory)
    cases: list[CaseRecord] = []
    coverage: dict[str, Coverage] = {}

    registration_groups: list[
        tuple[str, list[tuple[str, str, str, str, dict[str, Any], int, Path]]]
    ] = [
        (
            "registration_full",
            [
                (
                    "REG-FULL",
                    "registration_full",
                    "full",
                    "operator-registry",
                    {"mode": "full", "values": []},
                    len(inventory.instances),
                    paths["full"],
                )
            ],
        ),
        (
            "registration_profiles",
            [
                (
                    f"REG-PROFILE-{profile}",
                    "registration_profile",
                    profile,
                    profile,
                    {"mode": "profile", "values": [profile]},
                    sum(instance.profile == profile for instance in inventory.instances),
                    paths[f"profile:{profile}"],
                )
                for profile in EXPECTED_PROFILES
            ],
        ),
        (
            "registration_facerec",
            [
                (
                    "REG-FACEREC-THREE",
                    "registration_facerec",
                    "facerec-three",
                    "facerec-three",
                    {"mode": "instance", "values": list(EXPECTED_FACEREC_INSTANCES)},
                    3,
                    paths["facerec"],
                )
            ],
        ),
        (
            "registration_recovery",
            [
                (
                    f"REG-RECOVERY-{instance.instance_id}",
                    "registration_recovery",
                    instance.instance_id,
                    instance.instance_id,
                    {"mode": "instance", "values": [instance.instance_id]},
                    1,
                    paths[f"recovery:{instance.instance_id}"],
                )
                for instance in inventory.gpu_instances
            ],
        ),
    ]

    for coverage_key, sources in registration_groups:
        statuses: list[str] = []
        for (
            case_id,
            case_kind,
            run_id,
            target,
            selection,
            expected_count,
            relative_path,
        ) in sources:
            payload = _load_release_json(release_root, relative_path)
            status, started_at, finished_at, reason = _validate_registration(
                payload,
                relative_path=relative_path,
                selection=selection,
                expected_count=expected_count,
                release_tag=release_tag,
                git_sha=git_sha,
            )
            statuses.append(status)
            cases.append(
                _case_record(
                    case_id=case_id,
                    case_kind=case_kind,
                    run_id=run_id,
                    status=status,
                    started_at=started_at,
                    finished_at=finished_at,
                    target=target,
                    command="deploy/scripts/verify-operator-registration",
                    evidence=relative_path,
                    reason=reason,
                    release_tag=release_tag,
                    git_sha=git_sha,
                )
            )
        coverage[coverage_key] = {
            "expected": len(sources),
            "observed": len(statuses),
            "passed": statuses.count("通过"),
        }

    running_statuses: list[str] = []
    stopped_statuses: list[str] = []
    for instance in inventory.gpu_instances:
        running_path = Path(f"gpu-instances/{instance.instance_id}.json")
        stopped_path = Path(f"recovery/{instance.instance_id}-stopped.json")
        running = _load_release_json(release_root, running_path)
        stopped = _load_release_json(release_root, stopped_path)
        validate_gpu_pair(instance, running, stopped, git_sha)
        running_status = "通过" if running["status"] == "PASS" else "失败"
        stopped_status = "通过" if stopped["status"] == "PASS" else "失败"
        running_statuses.append(running_status)
        stopped_statuses.append(stopped_status)
        activity = running.get("activity")
        run_id = (
            _activity_run_id(
                activity, instance, f"{instance.instance_id}.running.activity"
            )
            if activity is not None
            else f"gpu-{instance.instance_id}"
        )
        running_timestamp = _require_string(
            running.get("timestamp"), f"{instance.instance_id}.running.timestamp"
        )
        stopped_timestamp = _require_string(
            stopped.get("timestamp"), f"{instance.instance_id}.stopped.timestamp"
        )
        cases.append(
            _case_record(
                case_id=f"GPU-RUN-{instance.instance_id}",
                case_kind="gpu_running",
                run_id=run_id,
                status=running_status,
                started_at=running_timestamp,
                finished_at=running_timestamp,
                target=instance.instance_id,
                command=_commands(running, f"{instance.instance_id}.running"),
                evidence=running_path,
                reason=_gpu_reason(running, f"{instance.instance_id}.running"),
                release_tag=release_tag,
                git_sha=git_sha,
            )
        )
        cases.append(
            _case_record(
                case_id=f"GPU-STOP-{instance.instance_id}",
                case_kind="gpu_stopped",
                run_id=run_id,
                status=stopped_status,
                started_at=stopped_timestamp,
                finished_at=stopped_timestamp,
                target=instance.instance_id,
                command=_commands(stopped, f"{instance.instance_id}.stopped"),
                evidence=stopped_path,
                reason=_gpu_reason(stopped, f"{instance.instance_id}.stopped"),
                release_tag=release_tag,
                git_sha=git_sha,
            )
        )

    coverage["gpu_running"] = {
        "expected": len(inventory.gpu_instances),
        "observed": len(running_statuses),
        "passed": running_statuses.count("通过"),
    }
    coverage["gpu_stopped"] = {
        "expected": len(inventory.gpu_instances),
        "observed": len(stopped_statuses),
        "passed": stopped_statuses.count("通过"),
    }
    return cases, coverage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="aggregate milestone 2B release evidence",
        allow_abbrev=False,
    )
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--operator-compose", required=True, type=Path)
    parser.add_argument("--smoke-manifest", required=True, type=Path)
    parser.add_argument("--report-plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def _load_smoke_manifest_placeholder(path: Path) -> None:
    try:
        loaded = strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"failed to read smoke manifest: {path}") from exc
    document = _require_object(loaded, f"smoke manifest {path}")
    if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
        raise ValueError(f"smoke manifest {path}: schema_version must equal 1")
    _require_list(document.get("cases"), f"smoke manifest {path}: cases")


def main() -> int:
    arguments = parse_args()
    try:
        inventory = load_operator_inventory(arguments.operator_compose)
        report_plan = load_report_plan(arguments.report_plan)
        _load_smoke_manifest_placeholder(arguments.smoke_manifest)
        release_tag = arguments.release_root.parent.name
        git_sha = arguments.release_root.name
        if arguments.release_root.parent.parent.name != "releases":
            raise ValueError("release root must use releases/<release_tag>/<git_sha>")
        collect_registration_gpu_cases(
            release_root=arguments.release_root,
            inventory=inventory,
            report_plan=report_plan,
            release_tag=release_tag,
            git_sha=git_sha,
        )
        raise ValueError(
            "Smoke and declaration coverage collectors are not implemented; "
            "refusing to publish output"
        )
    except (OSError, ValueError) as exc:
        print(f"milestone 2B aggregation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
