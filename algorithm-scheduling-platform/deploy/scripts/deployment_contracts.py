#!/usr/bin/env python3
"""Fail-closed deployment contracts shared by canonical release entrypoints."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .operator_topology import CURRENT_TOPOLOGY  # type: ignore[import-not-found]
elif __package__:
    from .operator_topology import CURRENT_TOPOLOGY
else:
    from operator_topology import CURRENT_TOPOLOGY

RELEASE_TAG_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+_[0-9]{6}$")
REGISTRY_WHEEL = "algorithm_operator_registry_client-0.2.0-py3-none-any.whl"
REGISTRY_WHEEL_PATTERN = re.compile(
    r"algorithm_operator_registry_client-[0-9]+(?:\.[0-9]+){2}-py3-none-any\.whl"
)
CONFIG_TARGETS = {
    entry.service_prefix: entry.config_target for entry in CURRENT_TOPOLOGY.operators
}
OPERATOR_CAPACITIES = {
    entry.service_prefix: entry.declared_capacity for entry in CURRENT_TOPOLOGY.operators
}
GPU_OPERATORS = frozenset(
    entry.service_prefix
    for entry in CURRENT_TOPOLOGY.operators
    if entry.device_kind == "gpu"
)
FORBIDDEN_OPERATOR_ENVIRONMENT = frozenset(
    {
        "PLATFORM_REGISTRATION_ENABLED",
        "PLATFORM_CONTROL_SERVICE_URL",
        "PLATFORM_HEARTBEAT_INTERVAL_SECONDS",
        "PLATFORM_DECLARED_CAPACITY",
        "REQUIRE_GPU",
        "GPU_PROCESS_NAME",
    }
)
INFRASTRUCTURE_IDENTITIES = {
    ("algorithm-scheduling-platform", service)
    for service in ("postgres", "kafka", "redis", "mongodb")
}
RETIRED_STOPPED_OPERATOR_SERVICES = frozenset(
    {"text-analysis-cpu0", "text-analysis-cpu1", "text-analysis-cpu2"}
)
CONTAINER_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class DeploymentContractError(ValueError):
    """Raised when release input violates a production deployment contract."""


def validate_release_architecture(
    host_architecture: str, image_architectures: Sequence[str]
) -> None:
    host = host_architecture.strip().lower()
    if host not in {"x86_64", "amd64"}:
        raise DeploymentContractError(
            f"release host architecture must be x86_64: {host_architecture!r}"
        )
    for architecture in image_architectures:
        normalized = architecture.strip().lower()
        if normalized not in {"x86_64", "amd64"}:
            raise DeploymentContractError(
                "release image architecture must be linux/amd64 on the x86_64 host: "
                f"{architecture!r}"
            )


def validate_release_tag(tag: str) -> str:
    if RELEASE_TAG_PATTERN.fullmatch(tag) is None:
        raise DeploymentContractError(
            "release tag must match lowercase v<major>.<minor>_YYMMDD"
        )
    return tag


def _environment(service_name: str, service: Mapping[str, Any]) -> dict[str, str]:
    value = service.get("environment")
    if isinstance(value, Mapping):
        environment: dict[str, str] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not isinstance(item, (str, int, float, bool)):
                raise DeploymentContractError(
                    f"operator environment is invalid: {service_name}"
                )
            environment[key] = str(item)
        return environment
    if isinstance(value, list):
        environment = {}
        for item in value:
            if not isinstance(item, str) or "=" not in item:
                raise DeploymentContractError(
                    f"operator environment is invalid: {service_name}"
                )
            key, _, item_value = item.partition("=")
            environment[key] = item_value
        return environment
    raise DeploymentContractError(f"operator environment is missing: {service_name}")


def _expected_config_target(service_name: str) -> str:
    for operator_name, target in CONFIG_TARGETS.items():
        if service_name.startswith(f"{operator_name}-"):
            return cast(str, target)
    raise DeploymentContractError(f"unknown operator service: {service_name}")


def _operator_name(service_name: str) -> str:
    for operator_name in CONFIG_TARGETS:
        if service_name.startswith(f"{operator_name}-"):
            return cast(str, operator_name)
    raise DeploymentContractError(f"unknown operator service: {service_name}")


def _command_tokens(value: Any, service_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            return shlex.split(value)
        except ValueError as error:
            raise DeploymentContractError(
                f"operator command is invalid: {service_name}"
            ) from error
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise DeploymentContractError(f"operator command is invalid: {service_name}")


def _validate_worker_tokens(service_name: str, tokens: Sequence[str]) -> None:
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--workers":
            if index + 1 >= len(tokens) or tokens[index + 1] != "1":
                raise DeploymentContractError(
                    f"{service_name} requires exactly one Uvicorn worker"
                )
            index += 2
            continue
        if token.startswith("--workers=") and token.partition("=")[2] != "1":
            raise DeploymentContractError(
                f"{service_name} requires exactly one Uvicorn worker"
            )
        index += 1


def validate_operator_service_contracts(
    services: Mapping[str, Mapping[str, Any]],
) -> None:
    if not services:
        raise DeploymentContractError("operator services are empty")
    for service_name, service in services.items():
        if not isinstance(service_name, str) or not isinstance(service, Mapping):
            raise DeploymentContractError("operator service document is invalid")
        environment = _environment(service_name, service)
        forbidden = FORBIDDEN_OPERATOR_ENVIRONMENT & environment.keys()
        if forbidden:
            raise DeploymentContractError(
                f"{service_name} contains TOML-owned environment: "
                + ", ".join(sorted(forbidden))
            )
        if environment.get("UVICORN_WORKERS") != "1":
            raise DeploymentContractError(
                f"{service_name} requires exactly one Uvicorn worker"
            )
        for field in ("entrypoint", "command"):
            _validate_worker_tokens(
                service_name, _command_tokens(service.get(field), service_name)
            )
        _validate_worker_tokens(
            service_name,
            _command_tokens(environment.get("UVICORN_EXTRA"), service_name),
        )

        expected_target = _expected_config_target(service_name)
        config_path = environment.get("CONFIG_PATH")
        if config_path != expected_target:
            raise DeploymentContractError(
                f"{service_name} CONFIG_PATH must be {expected_target}"
            )
        volumes = service.get("volumes")
        if not isinstance(volumes, list):
            raise DeploymentContractError(
                f"{service_name} must define exactly one config bind"
            )
        config_mounts = [
            mount
            for mount in volumes
            if isinstance(mount, Mapping)
            and (
                str(mount.get("target", "")).lower().endswith(".toml")
                or str(mount.get("source", "")).lower().endswith(".toml")
            )
        ]
        if len(config_mounts) != 1:
            raise DeploymentContractError(
                f"{service_name} must define exactly one config bind"
            )
        mount = config_mounts[0]
        if mount.get("type") != "bind" or mount.get("target") != config_path:
            raise DeploymentContractError(
                f"{service_name} CONFIG_PATH must match its config bind target"
            )
        source = mount.get("source")
        if not isinstance(source, str) or not source.lower().endswith(".toml"):
            raise DeploymentContractError(
                f"{service_name} config bind source must be a TOML file"
            )
        if mount.get("read_only") is not True:
            raise DeploymentContractError(
                f"{service_name} config bind must be read-only"
            )


def validate_operator_toml_contract(service_name: str, config_path: Path) -> None:
    operator_name = _operator_name(service_name)
    try:
        import tomllib
    except ModuleNotFoundError as error:
        raise DeploymentContractError(
            "Python 3.11+ is required for operator TOML validation"
        ) from error
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise DeploymentContractError(
            f"{service_name} cannot read mounted TOML: {config_path}"
        ) from error
    platform = config.get("platform")
    runtime = config.get("runtime")
    if not isinstance(platform, Mapping) or not isinstance(runtime, Mapping):
        raise DeploymentContractError(
            f"{service_name} mounted TOML requires [platform] and [runtime]"
        )
    expected_platform = {
        "registration_enabled": True,
        "control_service_url": "http://control-service:18100",
        "heartbeat_interval_seconds": 5,
        "max_concurrent_requests": OPERATOR_CAPACITIES[operator_name],
    }
    for field, expected in expected_platform.items():
        actual = platform.get(field)
        if type(actual) is not type(expected) or actual != expected:
            raise DeploymentContractError(
                f"{service_name} platform.{field} must be {expected!r}"
            )
    expected_gpu = operator_name in GPU_OPERATORS
    if (
        type(runtime.get("require_gpu")) is not bool
        or runtime.get("require_gpu") is not expected_gpu
    ):
        raise DeploymentContractError(
            f"{service_name} runtime.require_gpu must be {expected_gpu!r}"
        )
    if operator_name == "ocr":
        ocr = config.get("ocr")
        if not isinstance(ocr, Mapping) or ocr.get("image_max_bytes") != 52_428_800:
            raise DeploymentContractError(
                f"{service_name} ocr.image_max_bytes must be 52428800"
            )
    if operator_name == "vbas":
        tias = config.get("TIAS")
        if not isinstance(tias, Mapping):
            raise DeploymentContractError(f"{service_name} mounted TOML requires [TIAS]")
        if tias.get("MaxConcurrentBatches") != 1024:
            raise DeploymentContractError(
                f"{service_name} TIAS.MaxConcurrentBatches must be 1024"
            )
        if tias.get("MaxQueueSize") != 0:
            raise DeploymentContractError(
                f"{service_name} TIAS.MaxQueueSize must be 0"
            )
    if operator_name == "ppt-slice":
        task = config.get("task")
        if isinstance(task, Mapping) and "max_concurrent_tasks" in task:
            raise DeploymentContractError(
                f"{service_name} must not define task.max_concurrent_tasks"
            )


def validate_operator_config_mounts(
    services: Mapping[str, Mapping[str, Any]],
    *,
    compose_directory: Path,
) -> None:
    for service_name, service in services.items():
        volumes = service.get("volumes")
        if not isinstance(volumes, list):
            raise DeploymentContractError(f"{service_name} volumes are invalid")
        config_mount = next(
            (
                mount
                for mount in volumes
                if isinstance(mount, Mapping)
                and str(mount.get("target")) == _expected_config_target(service_name)
            ),
            None,
        )
        if config_mount is None:
            raise DeploymentContractError(f"{service_name} config bind is missing")
        source = Path(str(config_mount["source"]))
        if not source.is_absolute():
            source = compose_directory / source
        validate_operator_toml_contract(service_name, source.resolve())


def validate_registry_wheel_dockerfile(source: str, label: str) -> None:
    wheels = set(REGISTRY_WHEEL_PATTERN.findall(source))
    if wheels != {REGISTRY_WHEEL}:
        detail = ", ".join(sorted(wheels)) if wheels else "missing"
        raise DeploymentContractError(
            f"{label} registry client wheel must be exactly {REGISTRY_WHEEL}: {detail}"
        )


def validate_writable_directory(path: Path) -> None:
    directory = Path(path)
    try:
        metadata = directory.lstat()
    except OSError as error:
        raise DeploymentContractError(
            f"required directory is missing or inaccessible: {directory}"
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise DeploymentContractError(
            f"required directory is not a real directory: {directory}"
        )
    if metadata.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH) == 0:
        raise DeploymentContractError(f"required directory is not writable: {directory}")

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    probe_name = f".preflight-write-probe-{secrets.token_hex(16)}"
    probe_created = False
    try:
        descriptor = os.open(directory, directory_flags)
        probe_descriptor = os.open(
            probe_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=descriptor,
        )
        probe_created = True
        try:
            os.write(probe_descriptor, b"probe")
            os.fsync(probe_descriptor)
        finally:
            os.close(probe_descriptor)
        os.unlink(probe_name, dir_fd=descriptor)
        probe_created = False
        os.fsync(descriptor)
    except OSError as error:
        raise DeploymentContractError(f"required directory is not writable: {directory}") from error
    finally:
        if probe_created and descriptor >= 0:
            try:
                os.unlink(probe_name, dir_fd=descriptor)
            except OSError:
                pass
        if descriptor >= 0:
            os.close(descriptor)


def validate_root_disk(available_kib: int, minimum_gib: int) -> None:
    if isinstance(available_kib, bool) or available_kib < 0:
        raise DeploymentContractError("root disk available KiB must be a non-negative integer")
    if isinstance(minimum_gib, bool) or minimum_gib < 0:
        raise DeploymentContractError("root disk minimum GiB must be a non-negative integer")
    minimum_kib = minimum_gib * 1024 * 1024
    if available_kib < minimum_kib:
        raise DeploymentContractError(
            f"root disk has {available_kib} KiB free; {minimum_gib} GiB required"
        )


def validate_logging_root(path: Path, *, minimum_free_gib: int = 1) -> None:
    """仅在启用宿主机日志挂载时创建并校验独立日志根目录。"""
    directory = Path(path)
    if not directory.is_absolute():
        raise DeploymentContractError("logging root must be an absolute path")
    current = Path(directory.anchor)
    for component in directory.relative_to(directory.anchor).parts:
        current /= component
        if current.is_symlink():
            raise DeploymentContractError(f"logging root contains a symlink: {current}")
        if current.exists() and not current.is_dir():
            raise DeploymentContractError(f"logging root component is not a directory: {current}")
        if not current.exists():
            current.mkdir(mode=0o750)
    validate_writable_directory(directory)
    metadata = directory.stat()
    if metadata.st_uid != os.geteuid():
        raise DeploymentContractError(
            f"logging root is not owned by the preflight identity: {directory}"
        )
    if minimum_free_gib < 0:
        raise DeploymentContractError("logging root minimum free GiB must be non-negative")
    available_kib = shutil.disk_usage(directory).free // 1024
    validate_root_disk(available_kib, minimum_free_gib)


def validate_existing_algorithm_containers(
    containers: Iterable[Mapping[str, Any]],
    allowed_identities: set[tuple[str, str]],
) -> None:
    unknown: list[str] = []
    for container in containers:
        name_value = container.get("Name")
        if not isinstance(name_value, str):
            raise DeploymentContractError("container inspection is missing Name")
        name = name_value.removeprefix("/")
        if not name.startswith("algorithm-"):
            continue
        config = container.get("Config")
        labels = config.get("Labels") if isinstance(config, Mapping) else None
        project = labels.get("com.docker.compose.project") if isinstance(labels, Mapping) else None
        service = labels.get("com.docker.compose.service") if isinstance(labels, Mapping) else None
        identity = (project, service)
        if _retired_stopped_operator_service(container) is not None:
            # 退役实例只作为已停止的旧 release 资产保留，不能放宽当前运行身份集合。
            continue
        if (
            not all(isinstance(value, str) for value in identity)
            or identity not in allowed_identities
        ):
            unknown.append(name)
    if unknown:
        raise DeploymentContractError(
            "unknown algorithm container requires manual confirmation: "
            + ", ".join(sorted(unknown))
        )


def _retired_stopped_operator_service(
    container: Mapping[str, Any],
) -> str | None:
    name_value = container.get("Name")
    config = container.get("Config")
    labels = config.get("Labels") if isinstance(config, Mapping) else None
    project = labels.get("com.docker.compose.project") if isinstance(labels, Mapping) else None
    service = labels.get("com.docker.compose.service") if isinstance(labels, Mapping) else None
    state = container.get("State")
    if not isinstance(service, str) or service not in RETIRED_STOPPED_OPERATOR_SERVICES:
        return None
    if (
        project != "algorithm-operators"
        or name_value != f"/algorithm-operators-{service}-1"
        or not isinstance(state, Mapping)
        or state.get("Status") != "exited"
        or state.get("Running") is not False
    ):
        return None
    return service


def _validate_sorted_container_ids(container_ids: Sequence[str], label: str) -> None:
    if list(container_ids) != sorted(set(container_ids)):
        raise DeploymentContractError(
            f"继承算子账本必须按字节序排序且 ID 唯一: {label}"
        )
    invalid = [value for value in container_ids if CONTAINER_ID_PATTERN.fullmatch(value) is None]
    if invalid:
        raise DeploymentContractError(f"继承算子账本包含无效容器 ID: {label}")


def project_inherited_operator_ledgers(
    baseline_ids: Sequence[str],
    new_ids: Sequence[str],
    containers: Sequence[Mapping[str, Any]],
    allowed_services: set[str],
) -> tuple[list[str], list[str]]:
    """将完整旧拓扑账本严格投影到当前算子集合，不改写来源账本。"""
    _validate_sorted_container_ids(baseline_ids, "baseline")
    _validate_sorted_container_ids(new_ids, "new")
    if set(baseline_ids) & set(new_ids):
        raise DeploymentContractError("继承算子账本的 baseline 与 new 不得重叠")
    if not allowed_services or RETIRED_STOPPED_OPERATOR_SERVICES & allowed_services:
        raise DeploymentContractError("当前算子 allowlist 无效")

    expected_ids = [*baseline_ids, *new_ids]
    by_id: dict[str, Mapping[str, Any]] = {}
    for container in containers:
        container_id = container.get("Id")
        if not isinstance(container_id, str) or container_id in by_id:
            raise DeploymentContractError("继承算子账本的 Docker inspect 结果无效")
        by_id[container_id] = container
    if set(by_id) != set(expected_ids):
        raise DeploymentContractError("继承算子账本与 Docker inspect 结果不一致")

    retired_services: set[str] = set()

    def project(container_ids: Sequence[str]) -> list[str]:
        projected: list[str] = []
        for container_id in container_ids:
            container = by_id[container_id]
            config = container.get("Config")
            labels = config.get("Labels") if isinstance(config, Mapping) else None
            project_name = (
                labels.get("com.docker.compose.project")
                if isinstance(labels, Mapping)
                else None
            )
            service = (
                labels.get("com.docker.compose.service")
                if isinstance(labels, Mapping)
                else None
            )
            if project_name == "algorithm-operators" and service in allowed_services:
                projected.append(container_id)
                continue
            retired_service = _retired_stopped_operator_service(container)
            if retired_service is None or retired_service in retired_services:
                raise DeploymentContractError(
                    f"继承算子账本包含非当前或不可信容器: {container_id}"
                )
            retired_services.add(retired_service)
        return projected

    projected_baseline = project(baseline_ids)
    projected_new = project(new_ids)
    if retired_services and retired_services != RETIRED_STOPPED_OPERATOR_SERVICES:
        missing = sorted(RETIRED_STOPPED_OPERATOR_SERVICES - retired_services)
        raise DeploymentContractError(
            "继承算子账本的退役实例集合不完整: " + ", ".join(missing)
        )
    return projected_baseline, projected_new


def _read_strict_line_file(path: Path, label: str) -> list[str]:
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise DeploymentContractError(f"{label} 必须是非 symlink 普通文件")
        values = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise DeploymentContractError(f"无法读取 {label}: {path}") from error
    return values


def _inspect_containers(container_ids: Sequence[str]) -> list[Mapping[str, Any]]:
    if not container_ids:
        return []
    try:
        completed = subprocess.run(
            ["docker", "inspect", *container_ids],
            check=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        parsed = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise DeploymentContractError("无法核验继承算子账本中的容器") from error
    if not isinstance(parsed, list) or any(not isinstance(item, Mapping) for item in parsed):
        raise DeploymentContractError("继承算子账本的 Docker inspect 结果无效")
    return parsed


def _write_projected_ledger(path: Path, container_ids: Sequence[str]) -> None:
    flags = os.O_WRONLY | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
        ):
            raise DeploymentContractError(f"投影算子账本输出文件不可信: {path}")
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write("".join(f"{container_id}\n" for container_id in container_ids))
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise DeploymentContractError(f"无法写入投影算子账本: {path}") from error
    finally:
        if "descriptor" in locals() and descriptor >= 0:
            os.close(descriptor)


def compose_identities(documents: Iterable[Mapping[str, Any]]) -> set[tuple[str, str]]:
    identities = set(INFRASTRUCTURE_IDENTITIES)
    for document in documents:
        project = document.get("name")
        services = document.get("services")
        if not isinstance(project, str) or not isinstance(services, Mapping):
            raise DeploymentContractError("Compose document is missing name or services")
        for service_name in services:
            if not isinstance(service_name, str):
                raise DeploymentContractError("Compose service name is invalid")
            identities.add((project, service_name))
    return identities


def _validate_identity_file(path: Path) -> Path:
    identity = path.expanduser()
    try:
        metadata = identity.lstat()
    except OSError as error:
        raise DeploymentContractError(f"Deploy Key is unavailable: {identity}") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
    ):
        raise DeploymentContractError(
            "Deploy Key must be a current-UID-owned 0600 regular file with one link"
        )
    return identity.resolve(strict=True)


def checkout_release(
    *,
    repository: str,
    git_sha: str,
    destination: Path,
    identity_file: Path,
) -> None:
    if not repository or repository.startswith("-") or any(
        character.isspace() or ord(character) < 32 for character in repository
    ):
        raise DeploymentContractError("repository URL is invalid")
    if re.fullmatch(r"[0-9a-f]{40}", git_sha) is None:
        raise DeploymentContractError("Git SHA must be a full lowercase 40-character revision")
    identity = _validate_identity_file(identity_file)
    target = destination.expanduser()
    if target.exists() or target.is_symlink():
        raise DeploymentContractError(f"release destination already exists: {target}")
    parent = target.parent.resolve(strict=True)
    if not parent.is_dir():
        raise DeploymentContractError(f"release destination parent is not a directory: {parent}")

    temporary = Path(tempfile.mkdtemp(prefix=".checkout-release-", dir=parent))
    temporary.chmod(0o700)
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_SSH_COMMAND"] = " ".join(
        [
            "ssh",
            "-F",
            "/dev/null",
            "-oBatchMode=yes",
            "-oIdentitiesOnly=yes",
            "-oStrictHostKeyChecking=yes",
            "-i",
            shlex.quote(str(identity)),
        ]
    )
    try:
        commands = (
            ["git", "init", "--quiet", str(temporary)],
            ["git", "-C", str(temporary), "remote", "add", "origin", repository],
            [
                "git",
                "-C",
                str(temporary),
                "fetch",
                "--quiet",
                "--depth=1",
                "origin",
                git_sha,
            ],
            ["git", "-C", str(temporary), "checkout", "--quiet", "--detach", "FETCH_HEAD"],
        )
        for command in commands:
            subprocess.run(
                command,
                check=True,
                stdin=subprocess.DEVNULL,
                timeout=120,
                env=environment,
            )
        completed = subprocess.run(
            ["git", "-C", str(temporary), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
            env=environment,
        )
        if completed.stdout.strip() != git_sha:
            raise DeploymentContractError("fixed commit checkout resolved to a different revision")
        os.rename(temporary, target)
    except (OSError, subprocess.SubprocessError) as error:
        raise DeploymentContractError(
            f"fixed commit checkout failed for {git_sha}"
        ) from error
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _load_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeploymentContractError(f"cannot read strict JSON: {path}") from error


def _document(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DeploymentContractError(f"{label} must be a JSON object")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deployment-contracts")
    subparsers = parser.add_subparsers(dest="command", required=True)
    architecture = subparsers.add_parser("architecture")
    architecture.add_argument("--host", required=True)
    architecture.add_argument("--image-architecture", action="append", default=[])
    release_tag = subparsers.add_parser("release-tag")
    release_tag.add_argument("tag")
    operator_compose = subparsers.add_parser("operator-compose")
    operator_compose.add_argument("document")
    operator_compose_yaml = subparsers.add_parser("operator-compose-yaml")
    operator_compose_yaml.add_argument("document")
    writable = subparsers.add_parser("writable-directory")
    writable.add_argument("paths", nargs="+")
    root_disk = subparsers.add_parser("root-disk")
    root_disk.add_argument("--available-kib", required=True, type=int)
    root_disk.add_argument("--minimum-gib", required=True, type=int)
    logging_root = subparsers.add_parser("logging-root")
    logging_root.add_argument("path")
    logging_root.add_argument("--minimum-free-gib", type=int, default=1)
    containers = subparsers.add_parser("existing-containers")
    containers.add_argument("container_document")
    containers.add_argument("compose_documents", nargs="+")
    inherited_ledgers = subparsers.add_parser("project-inherited-operator-ledgers")
    inherited_ledgers.add_argument("--allowlist", required=True)
    inherited_ledgers.add_argument("--baseline", required=True)
    inherited_ledgers.add_argument("--new", required=True)
    inherited_ledgers.add_argument("--projected-baseline", required=True)
    inherited_ledgers.add_argument("--projected-new", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    if arguments.command == "architecture":
        validate_release_architecture(arguments.host, arguments.image_architecture)
    elif arguments.command == "release-tag":
        validate_release_tag(arguments.tag)
    elif arguments.command == "operator-compose":
        document = _document(_load_json(arguments.document), "operator Compose")
        services = document.get("services")
        if not isinstance(services, Mapping):
            raise DeploymentContractError("operator Compose services are invalid")
        validate_operator_service_contracts(services)
        validate_operator_config_mounts(
            services,
            compose_directory=Path(arguments.document).resolve().parent,
        )
    elif arguments.command == "operator-compose-yaml":
        try:
            import yaml  # type: ignore[import-untyped]

            parsed = yaml.safe_load(Path(arguments.document).read_text(encoding="utf-8"))
        except ModuleNotFoundError as error:
            raise DeploymentContractError(
                "PyYAML is required for operator Compose validation"
            ) from error
        except (OSError, yaml.YAMLError) as error:
            raise DeploymentContractError("cannot read operator Compose YAML") from error
        document = _document(parsed, "operator Compose")
        services = document.get("services")
        if not isinstance(services, Mapping):
            raise DeploymentContractError("operator Compose services are invalid")
        validate_operator_service_contracts(services)
        validate_operator_config_mounts(
            services,
            compose_directory=Path(arguments.document).resolve().parent,
        )
    elif arguments.command == "writable-directory":
        for path in arguments.paths:
            validate_writable_directory(Path(path))
    elif arguments.command == "root-disk":
        validate_root_disk(arguments.available_kib, arguments.minimum_gib)
    elif arguments.command == "logging-root":
        validate_logging_root(
            Path(arguments.path), minimum_free_gib=arguments.minimum_free_gib
        )
    elif arguments.command == "existing-containers":
        containers = _load_json(arguments.container_document)
        if not isinstance(containers, list):
            raise DeploymentContractError("container inspection must be a JSON array")
        if not containers:
            return 0
        compose_documents = [
            _document(_load_json(path), "Compose document")
            for path in arguments.compose_documents
        ]
        validate_existing_algorithm_containers(
            containers, compose_identities(compose_documents)
        )
    elif arguments.command == "project-inherited-operator-ledgers":
        allowed_services = _read_strict_line_file(
            Path(arguments.allowlist), "当前算子 allowlist"
        )
        if allowed_services != sorted(set(allowed_services)):
            raise DeploymentContractError("当前算子 allowlist 必须排序且唯一")
        baseline_ids = _read_strict_line_file(
            Path(arguments.baseline), "previous baseline 账本"
        )
        new_ids = _read_strict_line_file(Path(arguments.new), "previous new 账本")
        inspected = _inspect_containers([*baseline_ids, *new_ids])
        projected_baseline, projected_new = project_inherited_operator_ledgers(
            baseline_ids,
            new_ids,
            inspected,
            set(allowed_services),
        )
        _write_projected_ledger(
            Path(arguments.projected_baseline), projected_baseline
        )
        _write_projected_ledger(Path(arguments.projected_new), projected_new)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeploymentContractError as error:
        print(f"deployment-contracts: FAIL: {error}", file=sys.stderr)
        raise SystemExit(1) from error
