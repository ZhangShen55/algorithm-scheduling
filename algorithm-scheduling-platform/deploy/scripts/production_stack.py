#!/usr/bin/env python3
"""Persistent production stack lifecycle distinct from the Canonical test controller."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import tempfile
import urllib.request
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deploy.scripts.operator_topology import CURRENT_TOPOLOGY

DEPLOY_ROOT = Path(__file__).resolve().parent.parent
PLATFORM_ROOT = DEPLOY_ROOT.parent
INFRASTRUCTURE_SERVICES = ("postgres", "kafka", "redis", "mongodb")
PLATFORM_SERVICES = (
    "control-service",
    "orchestrator-service",
    "vision-orchestrator-service",
    "online-gateway-service",
)
EXPECTED_SERVICES = (
    *INFRASTRUCTURE_SERVICES,
    *PLATFORM_SERVICES,
    *CURRENT_TOPOLOGY.instance_ids,
)
PLATFORM_PROJECT = "algorithm-scheduling-platform"
OPERATOR_PROJECT = "algorithm-operators"
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
CONTAINER_ID_PATTERN = re.compile(r"[0-9a-f]{64}")
RELEASE_TAG_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
IMAGE_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
REVISION_LABEL = "org.opencontainers.image.revision"
OPERATOR_IMAGE_REPOSITORIES = {
    "ASR_OFFLINE_IMAGE": "seacraft-asr-offline",
    "ASR_ONLINE_IMAGE": "seacraft-asr-online",
    "FACEREC_IMAGE": "algorithm-facerec",
    "OCR_IMAGE": "algorithm-ocr",
    "PPT_SLICE_IMAGE": "algorithm-ppt-slice",
    "SCREEN_DET_IMAGE": "algorithm-screen-det",
    "VBAS_IMAGE": "algorithm-vbas",
}


def _operator_host_ports() -> tuple[int, ...]:
    return tuple(
        entry.host_port(index)
        for entry in CURRENT_TOPOLOGY.operators
        for index in range(entry.instance_count)
    )


REQUIRED_HOST_PORTS = (
    5432,
    9092,
    6379,
    27017,
    18100,
    18101,
    18102,
    18103,
    *_operator_host_ports(),
)


class ProductionStackError(RuntimeError):
    """Raised when persistent stack identity or readiness is not provable."""


@dataclass(frozen=True, slots=True)
class CommandStep:
    name: str
    command: tuple[str, ...]


def _platform_compose(deploy_root: Path) -> tuple[str, ...]:
    return (
        "docker",
        "compose",
        "--project-directory",
        str(deploy_root),
        "-f",
        str(deploy_root / "docker-compose.platform.yml"),
    )


def _operator_compose(deploy_root: Path) -> tuple[str, ...]:
    return (
        "docker",
        "compose",
        "--project-directory",
        str(deploy_root),
        "-f",
        str(deploy_root / "docker-compose.operators.yml"),
    )


def _validate_identity(git_sha: str, release_tag: str) -> None:
    if SHA_PATTERN.fullmatch(git_sha) is None:
        raise ProductionStackError("git_sha 必须是 40 位小写十六进制")
    if RELEASE_TAG_PATTERN.fullmatch(release_tag) is None:
        raise ProductionStackError("release_tag 不是安全的单路径段")


def build_start_plan(
    *,
    deploy_root: Path,
    git_sha: str,
    release_tag: str,
    reports_root: Path,
    wait_timeout_seconds: int = 300,
) -> list[CommandStep]:
    """Build the ordered persistent start plan without Canonical restore semantics."""

    _validate_identity(git_sha, release_tag)
    if wait_timeout_seconds <= 0:
        raise ProductionStackError("wait_timeout_seconds 必须为正整数")
    platform = _platform_compose(deploy_root)
    operators = _operator_compose(deploy_root)
    wait = ("--wait", "--wait-timeout", str(wait_timeout_seconds))
    scripts = deploy_root / "scripts"
    plan = [
        CommandStep(
            "infrastructure",
            (
                *platform,
                "up",
                "-d",
                "--no-build",
                *wait,
                *INFRASTRUCTURE_SERVICES,
            ),
        ),
        CommandStep(
            "migrations",
            (
                str(scripts / "apply-database-migrations"),
                "--git-sha",
                git_sha,
            ),
        ),
        CommandStep(
            "platform",
            (
                *platform,
                "up",
                "-d",
                "--no-build",
                *wait,
                *PLATFORM_SERVICES,
            ),
        ),
        CommandStep(
            "runtime-preflight",
            (str(scripts / "preflight"), "runtime", "--git-sha", git_sha),
        ),
    ]
    for profile in ("gpu0", "gpu1", "gpu2", "cpu"):
        plan.extend(
            (
                CommandStep(
                    profile,
                    (
                        *operators,
                        "--profile",
                        profile,
                        "up",
                        "-d",
                        "--no-build",
                        *wait,
                    ),
                ),
                CommandStep(
                    f"{profile}-readiness",
                    (
                        str(scripts / "preflight"),
                        "operators",
                        "--profile",
                        profile,
                        "--git-sha",
                        git_sha,
                        "--control-url",
                        "http://127.0.0.1:18100",
                        "--release-tag",
                        release_tag,
                        "--reports-root",
                        str(reports_root),
                    ),
                ),
            )
        )
    plan.append(
        CommandStep(
            "operators-full-readiness",
            (
                str(scripts / "preflight"),
                "operators",
                "--full",
                "--git-sha",
                git_sha,
                "--control-url",
                "http://127.0.0.1:18100",
                "--release-tag",
                release_tag,
                "--reports-root",
                str(reports_root),
            ),
        )
    )
    return plan


def _read_json_regular(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ProductionStackError(f"文件不是普通文件: {path}")
    metadata = path.stat()
    if (
        metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o777 != 0o600
    ):
        raise ProductionStackError(f"文件所有权、链接数或权限不安全: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProductionStackError(f"无法解析 JSON: {path}") from error
    if type(document) is not dict:
        raise ProductionStackError(f"JSON 顶层必须是对象: {path}")
    return document


def build_stop_plan(
    authority_ledger: Path,
    *,
    expected_git_sha: str,
) -> list[CommandStep]:
    """Stop only the exact containers recorded by the production authority ledger."""

    if SHA_PATTERN.fullmatch(expected_git_sha) is None:
        raise ProductionStackError("expected_git_sha 必须是完整 Git SHA")
    document = _read_json_regular(authority_ledger)
    if document.get("schema_version") != 1 or document.get("git_sha") != expected_git_sha:
        raise ProductionStackError("常驻栈账本版本或 Git SHA 不一致")
    containers = document.get("containers")
    if type(containers) is not list or len(containers) != len(EXPECTED_SERVICES):
        raise ProductionStackError("常驻栈账本容器数量不完整")
    ids: list[str] = []
    services: list[str] = []
    for item in containers:
        if type(item) is not dict:
            raise ProductionStackError("常驻栈账本容器记录无效")
        container_id = item.get("container_id")
        service = item.get("service")
        compose_project = item.get("compose_project")
        image_id = item.get("image_id")
        if CONTAINER_ID_PATTERN.fullmatch(str(container_id)) is None:
            raise ProductionStackError("常驻栈账本必须使用完整容器 ID")
        if type(service) is not str:
            raise ProductionStackError("常驻栈账本缺少 Compose service")
        if compose_project != _expected_project(service):
            raise ProductionStackError("常驻栈账本 Compose project 不一致")
        if IMAGE_ID_PATTERN.fullmatch(str(image_id)) is None:
            raise ProductionStackError("常驻栈账本必须记录完整镜像 ID")
        ids.append(str(container_id))
        services.append(service)
    if len(ids) != len(set(ids)) or set(services) != set(EXPECTED_SERVICES):
        raise ProductionStackError("常驻栈账本容器或服务身份不唯一")
    return [
        CommandStep(
            "stop-authoritative-containers",
            ("docker", "container", "stop", *ids),
        )
    ]


def _expected_project(service: str) -> str:
    if service in CURRENT_TOPOLOGY.instance_ids:
        return OPERATOR_PROJECT
    return PLATFORM_PROJECT


def _environment(record: dict[str, Any]) -> dict[str, str]:
    values = (record.get("Config") or {}).get("Env") or []
    if type(values) is not list or any(type(value) is not str for value in values):
        raise ProductionStackError("容器环境变量快照无效")
    result: dict[str, str] = {}
    for value in values:
        name, separator, content = value.partition("=")
        if separator and name:
            result[name] = content
    return result


def _validate_device_ownership(
    service: str,
    record: dict[str, Any],
) -> str | None:
    if service not in CURRENT_TOPOLOGY.instance_ids:
        return None
    environment = _environment(record)
    requests = (record.get("HostConfig") or {}).get("DeviceRequests") or []
    if type(requests) is not list:
        raise ProductionStackError(f"GPU DeviceRequests 快照无效: {service}")
    if "-gpu" not in service:
        if environment.get("PLATFORM_GPU_ID") is not None or requests:
            raise ProductionStackError(f"CPU 算子错误声明 GPU: {service}")
        return None
    expected_gpu = service.rsplit("gpu", 1)[1]
    nvidia_requests = [
        request
        for request in requests
        if type(request) is dict and request.get("Driver") == "nvidia"
    ]
    if (
        environment.get("PLATFORM_GPU_ID") != expected_gpu
        or environment.get("NVIDIA_VISIBLE_DEVICES") != expected_gpu
        or len(nvidia_requests) != 1
        or nvidia_requests[0].get("DeviceIDs") != [expected_gpu]
    ):
        raise ProductionStackError(f"GPU 环境或设备请求与实例身份不一致: {service}")
    return expected_gpu


def _port_contract(service: str) -> tuple[int, int, bool]:
    fixed = {
        "postgres": (5432, 5432, False),
        "kafka": (9092, 9092, False),
        "redis": (6379, 6379, False),
        "mongodb": (27017, 27017, False),
        "control-service": (18100, 18100, True),
        "orchestrator-service": (18101, 18101, False),
        "vision-orchestrator-service": (8010, 18102, False),
        "online-gateway-service": (8001, 18103, True),
    }
    if service in fixed:
        return fixed[service]
    for entry in CURRENT_TOPOLOGY.operators:
        for index in range(entry.instance_count):
            if entry.instance_id(index) == service:
                return entry.container_port, entry.host_port(index), False
    raise ProductionStackError(f"没有端口权威: {service}")


def _validate_port_bindings(records: Sequence[dict[str, Any]]) -> dict[int, bool]:
    observed: dict[int, bool] = {}
    for record in records:
        labels = (record.get("Config") or {}).get("Labels") or {}
        service = labels.get("com.docker.compose.service")
        if type(service) is not str:
            raise ProductionStackError("容器缺少 Compose service，无法验证端口")
        container_port, host_port, remote = _port_contract(service)
        ports = (record.get("NetworkSettings") or {}).get("Ports") or {}
        if type(ports) is not dict:
            raise ProductionStackError(f"容器端口快照无效: {service}")
        published: list[dict[str, Any]] = []
        for key, bindings in ports.items():
            if bindings is None:
                continue
            if type(bindings) is not list or any(type(item) is not dict for item in bindings):
                raise ProductionStackError(f"容器端口映射无效: {service}")
            if key != f"{container_port}/tcp":
                raise ProductionStackError(f"容器发布了权威之外的端口: {service}:{key}")
            published.extend(bindings)
        if not published:
            raise ProductionStackError(f"容器缺少必需宿主机端口: {service}")
        allowed_ips = {"0.0.0.0", "::"} if remote else {"127.0.0.1", "::1"}
        if any(
            str(binding.get("HostPort")) != str(host_port)
            or binding.get("HostIp") not in allowed_ips
            for binding in published
        ):
            raise ProductionStackError(f"宿主机端口地址边界不一致: {service}")
        observed[host_port] = True
    if set(observed) != set(REQUIRED_HOST_PORTS):
        raise ProductionStackError("宿主机关键端口集合不完整")
    return observed


def summarize_status(
    container_records: Sequence[dict[str, Any]],
    *,
    expected_git_sha: str,
    shared_directories: dict[str, bool],
    registration_count: int,
    active_lease_count: int,
    critical_ports: dict[int, bool],
    image_revisions: dict[str, str],
) -> dict[str, Any]:
    """Validate and summarize the exact persistent topology."""

    if SHA_PATTERN.fullmatch(expected_git_sha) is None:
        raise ProductionStackError("expected_git_sha 必须是完整 Git SHA")
    observed: dict[str, dict[str, Any]] = {}
    gpu_instances = 0
    cpu_instances = 0
    gpu_assignments: dict[str, list[str]] = {"0": [], "1": [], "2": []}
    for record in container_records:
        container_id = record.get("Id")
        image_id = record.get("Image")
        labels = (record.get("Config") or {}).get("Labels") or {}
        service = labels.get("com.docker.compose.service")
        project = labels.get("com.docker.compose.project")
        state = record.get("State") or {}
        if (
            CONTAINER_ID_PATTERN.fullmatch(str(container_id)) is None
            or type(service) is not str
            or service not in EXPECTED_SERVICES
            or service in observed
            or project != _expected_project(service)
            or IMAGE_ID_PATTERN.fullmatch(str(image_id)) is None
        ):
            raise ProductionStackError("容器 Compose 身份不完整或重复")
        if state.get("Running") is not True:
            raise ProductionStackError(f"容器未运行: {service}")
        if (state.get("Health") or {}).get("Status") != "healthy":
            raise ProductionStackError(f"容器未健康: {service}")
        if (
            service not in INFRASTRUCTURE_SERVICES
            and image_revisions.get(str(image_id)) != expected_git_sha
        ):
            raise ProductionStackError(f"镜像 revision 不一致: {service}")
        if service in CURRENT_TOPOLOGY.instance_ids:
            if "-gpu" in service:
                gpu_instances += 1
                gpu_id = _validate_device_ownership(service, record)
                assert gpu_id is not None
                gpu_assignments[gpu_id].append(service)
            else:
                cpu_instances += 1
                _validate_device_ownership(service, record)
        observed[service] = record
    if set(observed) != set(EXPECTED_SERVICES):
        missing = sorted(set(EXPECTED_SERVICES) - set(observed))
        raise ProductionStackError(f"常驻栈容器集合不完整: {missing}")
    if registration_count != CURRENT_TOPOLOGY.totals["instances"]:
        raise ProductionStackError("算子注册数量不是 21/21")
    if set(shared_directories) != {"/data/course", "/data/result"} or not all(
        shared_directories.values()
    ):
        raise ProductionStackError("共享目录不存在或不可写")
    if set(critical_ports) != set(REQUIRED_HOST_PORTS) or not all(critical_ports.values()):
        raise ProductionStackError("关键端口集合不完整或不可用")
    if type(active_lease_count) is not int or active_lease_count < 0:
        raise ProductionStackError("活跃租约计数无效")
    return {
        "schema_version": 1,
        "status": "PASS",
        "git_sha": expected_git_sha,
        "checked_at": datetime.now(UTC).isoformat(),
        "summary": {
            "infrastructure": len(INFRASTRUCTURE_SERVICES),
            "platform_services": len(PLATFORM_SERVICES),
            "operator_instances": len(CURRENT_TOPOLOGY.instance_ids),
            "gpu_instances": gpu_instances,
            "cpu_instances": cpu_instances,
            "registered_instances": registration_count,
            "active_leases": active_lease_count,
            "gpu_assignments": {
                gpu_id: sorted(services) for gpu_id, services in gpu_assignments.items()
            },
        },
        "critical_ports": {str(key): value for key, value in sorted(critical_ports.items())},
        "shared_directories": dict(sorted(shared_directories.items())),
    }


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        text=True,
        capture_output=True,
        check=False,
        timeout=900,
    )
    if completed.returncode != 0:
        raise ProductionStackError(f"命令失败: {' '.join(command[:4])}")
    return completed


def _compose_ids(deploy_root: Path, operators: bool) -> list[str]:
    base = _operator_compose(deploy_root) if operators else _platform_compose(deploy_root)
    services = (
        CURRENT_TOPOLOGY.instance_ids
        if operators
        else (
            *INFRASTRUCTURE_SERVICES,
            *PLATFORM_SERVICES,
        )
    )
    command = [*base]
    if operators:
        command.extend(("--profile", "*"))
    command.extend(("ps", "--all", "--no-trunc", "-q", *services))
    ids = _run(command).stdout.splitlines()
    if len(ids) != len(services) or any(
        CONTAINER_ID_PATTERN.fullmatch(item) is None for item in ids
    ):
        raise ProductionStackError("Compose 没有返回完整容器集合")
    return ids


def _inspect_records(ids: Sequence[str]) -> list[dict[str, Any]]:
    completed = _run(("docker", "inspect", *ids))
    try:
        records = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ProductionStackError("docker inspect 返回无效 JSON") from error
    if type(records) is not list or len(records) != len(ids):
        raise ProductionStackError("docker inspect 结果不完整")
    return records


def _image_revisions(records: Sequence[dict[str, Any]]) -> dict[str, str]:
    image_ids = sorted(
        {
            str(record.get("Image"))
            for record in records
            if ((record.get("Config") or {}).get("Labels") or {}).get("com.docker.compose.service")
            not in INFRASTRUCTURE_SERVICES
        }
    )
    if not image_ids or any(IMAGE_ID_PATTERN.fullmatch(image_id) is None for image_id in image_ids):
        raise ProductionStackError("运行容器镜像 ID 不完整")
    completed = _run(("docker", "image", "inspect", *image_ids))
    try:
        inspections = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ProductionStackError("docker image inspect 返回无效 JSON") from error
    if type(inspections) is not list or len(inspections) != len(image_ids):
        raise ProductionStackError("运行镜像 inspect 结果不完整")
    result: dict[str, str] = {}
    for raw in inspections:
        if type(raw) is not dict:
            raise ProductionStackError("运行镜像 inspect 记录无效")
        image_id = raw.get("Id")
        revision = ((raw.get("Config") or {}).get("Labels") or {}).get(REVISION_LABEL)
        if (
            IMAGE_ID_PATTERN.fullmatch(str(image_id)) is None
            or SHA_PATTERN.fullmatch(str(revision)) is None
            or image_id in result
        ):
            raise ProductionStackError("运行镜像 revision 证据无效")
        result[str(image_id)] = str(revision)
    if set(result) != set(image_ids):
        raise ProductionStackError("运行镜像 revision 证据不完整")
    return result


def _atomic_replace_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ProductionStackError("状态目录不得是符号链接")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        content = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _collect_stack_records(deploy_root: Path) -> list[dict[str, Any]]:
    ids = _compose_ids(deploy_root, False) + _compose_ids(deploy_root, True)
    if len(ids) != len(set(ids)):
        raise ProductionStackError("Compose 容器 ID 重复")
    return _inspect_records(ids)


def _http_json(url: str) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
            if response.status != 200:
                raise ProductionStackError(f"运维接口返回 HTTP {response.status}")
            return json.loads(response.read())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProductionStackError(f"运维接口不可用: {url}") from error


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def _registry_summary(instances: Any, capacities: Any) -> tuple[int, int]:
    if type(instances) is not list or type(capacities) is not list:
        raise ProductionStackError("算子注册或容量快照格式无效")
    expected = set(CURRENT_TOPOLOGY.instance_ids)
    registered: set[str] = set()
    for item in instances:
        if type(item) is not dict:
            raise ProductionStackError("算子注册记录无效")
        instance_id = item.get("instance_id")
        if (
            instance_id not in expected
            or instance_id in registered
            or item.get("lifecycle") != "ONLINE"
            or item.get("model_ready") is not True
        ):
            raise ProductionStackError("算子注册身份、生命周期或模型状态不符合")
        registered.add(str(instance_id))
    if registered != expected:
        raise ProductionStackError("算子注册集合不是权威 21/21")

    capacity_ids: set[str] = set()
    leases = 0
    for item in capacities:
        if type(item) is not dict:
            raise ProductionStackError("算子容量快照记录无效")
        instance_id = item.get("instance_id")
        active = item.get("active_lease_count")
        if instance_id not in expected or instance_id in capacity_ids or type(active) is not int:
            raise ProductionStackError("容量快照身份或活跃租约计数无效")
        if active < 0:
            raise ProductionStackError("活跃租约计数不得为负")
        capacity_ids.add(str(instance_id))
        leases += active
    if capacity_ids != expected:
        raise ProductionStackError("容量快照集合不是权威 21/21")
    return len(registered), leases


def _runtime_status(
    deploy_root: Path,
    git_sha: str,
    *,
    records: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if records is None:
        records = _collect_stack_records(deploy_root)
    instances = _http_json("http://127.0.0.1:18100/ops/operator-instances")
    capacities = _http_json("http://127.0.0.1:18100/ops/operator-instances/snapshot")
    registration_count, leases = _registry_summary(instances, capacities)
    directories = {
        path: Path(path).is_dir() and os.access(path, os.W_OK)
        for path in ("/data/course", "/data/result")
    }
    _validate_port_bindings(records)
    port_status = {port: _port_open(port) for port in REQUIRED_HOST_PORTS}
    return summarize_status(
        records,
        expected_git_sha=git_sha,
        shared_directories=directories,
        registration_count=registration_count,
        active_lease_count=leases,
        critical_ports=port_status,
        image_revisions=_image_revisions(records),
    )


def _ledger_from_records(
    records: Sequence[dict[str, Any]], git_sha: str, release_tag: str
) -> dict[str, Any]:
    by_service: dict[str, dict[str, str]] = {}
    for record in records:
        labels = (record.get("Config") or {}).get("Labels") or {}
        service = labels.get("com.docker.compose.service")
        project = labels.get("com.docker.compose.project")
        container_id = record.get("Id")
        image_id = record.get("Image")
        if (
            service not in EXPECTED_SERVICES
            or service in by_service
            or project != _expected_project(str(service))
            or CONTAINER_ID_PATTERN.fullmatch(str(container_id)) is None
            or IMAGE_ID_PATTERN.fullmatch(str(image_id)) is None
        ):
            raise ProductionStackError("无法从容器快照建立常驻栈账本")
        by_service[str(service)] = {
            "container_id": str(container_id),
            "service": str(service),
            "compose_project": str(project),
            "image_id": str(image_id),
        }
    if set(by_service) != set(EXPECTED_SERVICES):
        raise ProductionStackError("常驻栈账本服务集合不完整")
    return {
        "schema_version": 1,
        "git_sha": git_sha,
        "release_tag": release_tag,
        "containers": [by_service[service] for service in EXPECTED_SERVICES],
    }


def validate_stop_records(
    authority_ledger: Path,
    records: Sequence[dict[str, Any]],
    *,
    expected_git_sha: str,
) -> None:
    """Reject stop when a recorded container ID no longer has its recorded identity."""

    document = _read_json_regular(authority_ledger)
    if document.get("git_sha") != expected_git_sha:
        raise ProductionStackError("停止前账本 Git SHA 已漂移")
    raw_containers = document.get("containers")
    if type(raw_containers) is not list or len(raw_containers) != len(EXPECTED_SERVICES):
        raise ProductionStackError("停止前账本容器集合不完整")
    expected_by_id = {
        str(item.get("container_id")): item for item in raw_containers if type(item) is dict
    }
    if len(expected_by_id) != len(EXPECTED_SERVICES) or len(records) != len(expected_by_id):
        raise ProductionStackError("停止前 Docker inspect 结果不完整")
    observed_ids: set[str] = set()
    for record in records:
        container_id = str(record.get("Id"))
        expected = expected_by_id.get(container_id)
        labels = (record.get("Config") or {}).get("Labels") or {}
        if (
            expected is None
            or container_id in observed_ids
            or labels.get("com.docker.compose.service") != expected.get("service")
            or labels.get("com.docker.compose.project") != expected.get("compose_project")
            or record.get("Image") != expected.get("image_id")
        ):
            raise ProductionStackError("停止目标的容器、Compose 或镜像身份已漂移")
        observed_ids.add(container_id)
    if observed_ids != set(expected_by_id):
        raise ProductionStackError("停止目标容器集合已漂移")
    revisions = _image_revisions(records)
    for record in records:
        service = ((record.get("Config") or {}).get("Labels") or {}).get(
            "com.docker.compose.service"
        )
        if (
            service not in INFRASTRUCTURE_SERVICES
            and revisions.get(str(record.get("Image"))) != expected_git_sha
        ):
            raise ProductionStackError("停止目标镜像 revision 与账本 SHA 不一致")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="管理七算子三卡正式常驻栈",
        allow_abbrev=False,
    )
    parser.add_argument("command", choices=("start", "status", "stop"))
    parser.add_argument("--deploy-root", type=Path, default=DEPLOY_ROOT)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--release-tag", default="v1.0_260812")
    parser.add_argument("--reports-root", type=Path, default=DEPLOY_ROOT / "reports")
    parser.add_argument("--wait-timeout-seconds", type=int, default=300)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _state_root(
    configured: Path | None,
    *,
    reports_root: Path,
    release_tag: str,
    git_sha: str,
) -> Path:
    if configured is not None:
        return configured
    return (
        reports_root
        / "milestone-2b/releases"
        / release_tag
        / git_sha
        / "production"
    )


def _configure_release_environment(git_sha: str, release_tag: str) -> None:
    """让 Compose 使用 CLI 已核验的 SHA/tag，而不是依赖未导出的 shell 变量。"""

    os.environ["EXPECTED_GIT_SHA"] = git_sha
    for variable, repository in OPERATOR_IMAGE_REPOSITORIES.items():
        os.environ.setdefault(variable, f"{repository}:{release_tag}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _validate_identity(args.git_sha, args.release_tag)
    _configure_release_environment(args.git_sha, args.release_tag)
    state_root = _state_root(
        args.state_root,
        reports_root=args.reports_root,
        release_tag=args.release_tag,
        git_sha=args.git_sha,
    )
    ledger = state_root / "production-stack.json"
    if args.command == "start":
        plan = build_start_plan(
            deploy_root=args.deploy_root,
            git_sha=args.git_sha,
            release_tag=args.release_tag,
            reports_root=args.reports_root,
            wait_timeout_seconds=args.wait_timeout_seconds,
        )
        if args.dry_run:
            print(json.dumps([asdict(step) for step in plan], ensure_ascii=False, indent=2))
            return 0
        for step in plan:
            _run(step.command)
        records = _collect_stack_records(args.deploy_root)
        status = _runtime_status(args.deploy_root, args.git_sha, records=records)
        _atomic_replace_json(ledger, _ledger_from_records(records, args.git_sha, args.release_tag))
    elif args.command == "status":
        status = _runtime_status(args.deploy_root, args.git_sha)
    else:
        plan = build_stop_plan(ledger, expected_git_sha=args.git_sha)
        if args.dry_run:
            print(json.dumps([asdict(step) for step in plan], ensure_ascii=False, indent=2))
            return 0
        stop_ids = plan[0].command[3:]
        validate_stop_records(
            ledger,
            _inspect_records(stop_ids),
            expected_git_sha=args.git_sha,
        )
        for step in plan:
            _run(step.command)
        status = {
            "schema_version": 1,
            "status": "STOPPED",
            "git_sha": args.git_sha,
            "containers_stopped": len(EXPECTED_SERVICES),
            "containers_removed": 0,
            "images_removed": 0,
            "volumes_removed": 0,
        }
    if args.output is not None:
        _atomic_replace_json(args.output, status)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
