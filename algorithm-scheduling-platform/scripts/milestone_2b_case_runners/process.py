from __future__ import annotations

import asyncio
import json
import math
import os
import signal
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from . import safety as safety_module
from .base import RUN_ID_PATTERN, CaseContext
from .safety import (
    CommandOperation,
    CommandTaskTerminationError,
    ResourceKind,
    ResourceSpec,
    register_command_task,
    unregister_command_task,
    validate_command_authority,
)

ReadKind = Literal["filesystem_file", "docker_container"]
MutationKind = Literal[
    "docker_remove",
    "database_drop",
    "redis_delete_prefix",
    "kafka_delete_topic",
    "kafka_delete_group",
]
FoundationGroup = Literal[
    "deployment",
    "gpu",
    "registry",
    "infrastructure",
    "load",
]

_FOUNDATION_CASE_RANGES = {
    "deployment": ("DEP", 20),
    "gpu": ("GPU", 20),
    "registry": ("REG", 20),
    "infrastructure": ("INF", 16),
    "load": ("LOAD", 26),
}
_FOUNDATION_RESOURCE_KINDS = {
    "deployment": frozenset({"filesystem"}),
    "gpu": frozenset({"filesystem", "container"}),
    "registry": frozenset({"filesystem", "redis_prefix", "database"}),
    "infrastructure": frozenset(
        {
            "filesystem",
            "database",
            "mongodb_database",
            "kafka_topic",
            "kafka_group",
            "redis_prefix",
        }
    ),
    "load": frozenset({"filesystem", "container", "database", "redis_prefix"}),
}
_REGISTRY_DATABASE_CASES = frozenset({"REG-014", "REG-015"})
_WRITABLE_DEPLOYMENT_CASES = frozenset(
    {"DEP-013", "DEP-015", "DEP-016", "DEP-019", "DEP-020"}
)
_LOAD_CASES = frozenset(f"LOAD-{number:03d}" for number in range(10, 27))
_CANONICAL_LOAD_CASES = frozenset(
    f"LOAD-{number:03d}" for number in range(10, 17)
)
_LOAD_COURSE_FACT_CASES = frozenset(
    {"LOAD-011", "LOAD-012", "LOAD-013", "LOAD-014", "LOAD-016"}
)
_LOAD_RUNTIME_CONTAINERS: dict[str, tuple[str, ...]] = {
    "LOAD-010": ("facerec-gpu0",),
    "LOAD-011": ("asr-offline-gpu0", "asr-offline-gpu1", "asr-offline-gpu2"),
    "LOAD-012": ("orchestrator-service",),
    "LOAD-013": ("control-service",),
    "LOAD-014": ("kafka",),
    "LOAD-015": ("redis",),
    "LOAD-016": ("postgres", "orchestrator-service"),
}

_MUTATION_AUTHORITY: dict[
    MutationKind, tuple[CommandOperation, ResourceKind]
] = {
    "docker_remove": ("docker_mutation", "container"),
    "database_drop": ("database_mutation", "database"),
    "redis_delete_prefix": ("redis_mutation", "redis_prefix"),
    "kafka_delete_topic": ("kafka_mutation", "kafka_topic"),
    "kafka_delete_group": ("kafka_mutation", "kafka_group"),
}

MaterializedCommand = tuple[
    CommandOperation,
    tuple[ResourceSpec, ...],
    tuple[str, ...],
]
POST_KILL_DRAIN_TIMEOUT_SECONDS = 2.0
COMPLETION_CANCEL_GRACE_SECONDS = 0.1
SUPERVISOR_REGISTRATION_CLEANUP_GRACE_SECONDS = 0.1
DEFAULT_COMMAND_TERMINATE_GRACE_SECONDS = 2.0
MAX_COMMAND_TERMINATE_GRACE_SECONDS = 30.0
_SUPERVISOR_PROCESS_FD: int | None = None


def _load_checker_resource_kinds(case_id: str) -> tuple[ResourceKind, ...]:
    container_names = _LOAD_RUNTIME_CONTAINERS.get(case_id)
    if container_names is None:
        return ("filesystem",)
    kinds: list[ResourceKind] = ["container"] * len(container_names)
    if case_id in _LOAD_COURSE_FACT_CASES:
        kinds.append("database")
    elif case_id == "LOAD-015":
        kinds.extend(("redis_prefix", "filesystem"))
    kinds.append("filesystem")
    return tuple(kinds)


def _configure_process_supervisor(descriptor: int | None) -> None:
    global _SUPERVISOR_PROCESS_FD
    if descriptor is not None:
        if type(descriptor) is not int or descriptor < 0:
            raise ValueError("process supervisor descriptor must be non-negative")
        try:
            os.fstat(descriptor)
        except OSError as exc:
            raise ValueError("process supervisor descriptor is not open") from exc
    _SUPERVISOR_PROCESS_FD = descriptor


def _notify_process_supervisor(event: str, process_group_id: int) -> None:
    descriptor = _SUPERVISOR_PROCESS_FD
    if descriptor is None:
        return
    if event not in {"+", "-"} or process_group_id <= 0:
        raise ProcessCleanupError("invalid process supervisor event")
    payload = f"{event}{process_group_id}\n".encode("ascii")
    while True:
        try:
            written = os.write(descriptor, payload)
            break
        except InterruptedError:
            continue
        except OSError as exc:
            raise ProcessCleanupError(
                "failed to notify process supervisor"
            ) from exc
    if written != len(payload):
        raise ProcessCleanupError("partial process supervisor notification")


@dataclass(frozen=True, slots=True)
class ReadAction:
    kind: ReadKind
    resource: ResourceSpec

    def __post_init__(self) -> None:
        if type(self.resource) is not ResourceSpec:
            raise ValueError("read action resource type must be ResourceSpec")
        if (
            type(self.kind) is not str
            or self.kind not in {"filesystem_file", "docker_container"}
        ):
            raise ValueError(f"unsupported read action: {self.kind}")
        expected = "filesystem" if self.kind == "filesystem_file" else "container"
        if self.resource.kind != expected:
            raise ValueError(f"{self.kind} requires a {expected} resource")


@dataclass(frozen=True, slots=True)
class MutationAction:
    kind: MutationKind
    resource: ResourceSpec

    def __post_init__(self) -> None:
        if type(self.resource) is not ResourceSpec:
            raise ValueError("mutation action resource type must be ResourceSpec")
        if type(self.kind) is not str:
            raise ValueError("mutation action kind must be a plain string")
        authority = _MUTATION_AUTHORITY.get(self.kind)
        if authority is None or self.resource.kind != authority[1]:
            raise ValueError(f"resource kind does not match action {self.kind}")

@dataclass(frozen=True, slots=True)
class OutputProbeAction:
    stdout_bytes: int
    stderr_bytes: int

    def __post_init__(self) -> None:
        if (
            type(self.stdout_bytes) is not int
            or type(self.stderr_bytes) is not int
            or self.stdout_bytes < 0
            or self.stderr_bytes < 0
        ):
            raise ValueError("probe output sizes must be non-negative")


_PROCESS_GROUP_PROBE_SOURCE = """
import json
import os
import signal
import subprocess
import sys
import time

spawn_child = sys.argv[1] == "1"
parent_exits = sys.argv[2] == "1"
ignore_sigterm = sys.argv[3] == "1"
if ignore_sigterm:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
document = {"pid": os.getpid(), "parent": os.getpid()}
if spawn_child:
    child_source = "import time; time.sleep(60)"
    if ignore_sigterm:
        child_source = (
            "import signal,time;"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN);time.sleep(60)"
        )
    child = subprocess.Popen([sys.executable, "-c", child_source])
    document["child"] = child.pid
print(json.dumps(document), flush=True)
if not parent_exits:
    time.sleep(60)
"""


@dataclass(frozen=True, slots=True)
class ProcessGroupProbeAction:
    spawn_child: bool
    parent_exits: bool
    ignore_sigterm: bool

    def __post_init__(self) -> None:
        if any(
            type(value) is not bool
            for value in (self.spawn_child, self.parent_exits, self.ignore_sigterm)
        ):
            raise ValueError("process probe flags must be booleans")


@dataclass(frozen=True, slots=True)
class FoundationCheckAction:
    group: FoundationGroup
    case_id: str
    resources: tuple[ResourceSpec, ...]

    def __post_init__(self) -> None:
        if type(self.group) is not str or self.group not in _FOUNDATION_CASE_RANGES:
            raise ValueError("foundation checker group is not registered")
        prefix, last = _FOUNDATION_CASE_RANGES[self.group]
        valid_case_ids = (
            _LOAD_CASES
            if self.group == "load"
            else {f"{prefix}-{number:03d}" for number in range(1, last + 1)}
        )
        if type(self.case_id) is not str or self.case_id not in valid_case_ids:
            raise ValueError("foundation checker case does not match its group")
        if type(self.resources) is not tuple or not self.resources:
            raise ValueError("foundation checker resources must be a non-empty tuple")
        if any(type(resource) is not ResourceSpec for resource in self.resources):
            raise ValueError("foundation checker resources must use ResourceSpec")
        if self.resources[0].kind != "filesystem":
            raise ValueError("foundation checker first resource must be its input file")
        allowed_kinds = _FOUNDATION_RESOURCE_KINDS[self.group]
        if any(resource.kind not in allowed_kinds for resource in self.resources):
            raise ValueError("foundation checker resource kind is not allowed")
        mutable_resources = self.resources[1:]
        if self.group not in {"deployment", "load"} and any(
            resource.kind == "filesystem" for resource in mutable_resources
        ):
            raise ValueError("foundation checker accepts exactly one input file")
        if self.group == "deployment":
            expected_kinds = (
                ("filesystem",)
                if self.case_id in _WRITABLE_DEPLOYMENT_CASES
                else ()
            )
            if tuple(resource.kind for resource in mutable_resources) != expected_kinds:
                raise ValueError(
                    "deployment checker resources do not match the fixed case contract"
                )
        if self.group == "gpu" and (
            not mutable_resources
            or any(resource.kind != "container" for resource in mutable_resources)
        ):
            raise ValueError("GPU checker requires an enumerated container resource")
        if self.group == "registry":
            expected_kinds = (
                ("redis_prefix", "database")
                if self.case_id in _REGISTRY_DATABASE_CASES
                else ("redis_prefix",)
            )
            if tuple(resource.kind for resource in mutable_resources) != expected_kinds:
                raise ValueError(
                    "registry checker resources do not match the fixed case contract"
                )
        if self.group == "infrastructure" and not mutable_resources:
            raise ValueError("infrastructure checker requires isolated resources")
        if self.group == "load":
            expected_kinds = _load_checker_resource_kinds(self.case_id)
            if tuple(resource.kind for resource in mutable_resources) != expected_kinds:
                raise ValueError(
                    "load checker resources do not match the fixed case contract"
                )


def foundation_cleanup_resources(
    group: FoundationGroup, case_id: str, run_id: str
) -> tuple[ResourceSpec, ...]:
    if type(group) is not str or group not in _FOUNDATION_CASE_RANGES:
        raise ValueError("foundation cleanup group is not registered")
    prefix, last = _FOUNDATION_CASE_RANGES[group]
    valid_case_ids = (
        _LOAD_CASES
        if group == "load"
        else {f"{prefix}-{number:03d}" for number in range(1, last + 1)}
    )
    if type(case_id) is not str or case_id not in valid_case_ids:
        if group == "load":
            raise ValueError("load cleanup case is not registered")
        raise ValueError("foundation cleanup case does not match its group")
    if type(run_id) is not str or RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError("foundation cleanup run_id is invalid")
    case_name = case_id.lower()
    safe_run = run_id.replace("-", "_")
    safe_case = case_name.replace("-", "_")
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    resources = [
        ResourceSpec(
            "filesystem",
            str(temporary_root / f"m2b-{len(run_id)}-{run_id}-{case_name}-"),
        )
    ]
    if group == "registry":
        resources.append(
            ResourceSpec("redis_prefix", f"m2b:{run_id}:{case_name}:registry:")
        )
        if case_id in _REGISTRY_DATABASE_CASES:
            resources.append(
                ResourceSpec(
                    "database",
                    f"m2b_{len(run_id)}_{safe_run}_{safe_case}_test",
                )
            )
    elif group == "infrastructure":
        topic = f"m2b.{run_id}.{case_name}"
        resources.extend(
            (
                ResourceSpec(
                    "database",
                    f"m2b_{len(run_id)}_{safe_run}_{safe_case}_test",
                ),
                ResourceSpec(
                    "mongodb_database",
                    f"m2b_{len(run_id)}_{safe_run}_{safe_case}_mongo_test",
                ),
                ResourceSpec("kafka_topic", topic),
                ResourceSpec("kafka_group", topic),
                ResourceSpec("redis_prefix", f"m2b:{run_id}:{case_name}:"),
            )
        )
    elif group == "load":
        if case_id in _LOAD_COURSE_FACT_CASES:
            resources.append(
                ResourceSpec(
                    "database",
                    f"algorithm:course-task:m2b-{run_id}-{case_name}",
                )
            )
        resources.extend(
            ResourceSpec("container", name)
            for name in _LOAD_RUNTIME_CONTAINERS.get(case_id, ())
        )
    return tuple(resources)


@dataclass(frozen=True, slots=True)
class FoundationCleanupAction:
    group: FoundationGroup
    case_id: str
    run_id: str
    resources: tuple[ResourceSpec, ...]

    def __post_init__(self) -> None:
        expected = foundation_cleanup_resources(self.group, self.case_id, self.run_id)
        if type(self.resources) is not tuple or self.resources != expected:
            raise ValueError("foundation cleanup resources do not match the exact case namespace")
        if any(type(resource) is not ResourceSpec for resource in self.resources):
            raise ValueError("foundation cleanup resources must use ResourceSpec")


CommandAction = (
    ReadAction
    | MutationAction
    | OutputProbeAction
    | ProcessGroupProbeAction
    | FoundationCheckAction
    | FoundationCleanupAction
)


def _snapshot_resource(resource: ResourceSpec, action_label: str) -> ResourceSpec:
    if type(resource) is not ResourceSpec:
        raise ValueError(f"{action_label} resource type must be ResourceSpec")
    kind = resource.kind
    name = resource.name
    if type(kind) is not str or type(name) is not str:
        raise ValueError(f"{action_label} resource fields must be plain strings")
    return ResourceSpec(kind, name)


def _materialize_action(action: CommandAction) -> MaterializedCommand:
    if type(action) is ReadAction:
        read_action = action
        read_kind = read_action.kind
        if type(read_kind) is not str:
            raise ValueError("read action kind must be a plain string")
        resource_snapshot = _snapshot_resource(read_action.resource, "read action")
        expected = "filesystem" if read_kind == "filesystem_file" else "container"
        if read_kind not in {"filesystem_file", "docker_container"}:
            raise ValueError(f"unsupported read action: {read_kind}")
        if resource_snapshot.kind != expected:
            raise ValueError(f"{read_kind} requires a {expected} resource")
        read_argv: tuple[str, ...]
        if read_kind == "filesystem_file":
            read_argv = ("cat", "--", resource_snapshot.name)
        else:
            read_argv = ("docker", "inspect", "--", resource_snapshot.name)
        return "read", (resource_snapshot,), read_argv

    if type(action) is MutationAction:
        mutation_action = action
        mutation_kind = mutation_action.kind
        if type(mutation_kind) is not str:
            raise ValueError("mutation action kind must be a plain string")
        resource_snapshot = _snapshot_resource(
            mutation_action.resource, "mutation action"
        )
        authority = _MUTATION_AUTHORITY.get(mutation_kind)
        if authority is None or resource_snapshot.kind != authority[1]:
            raise ValueError(
                f"resource kind does not match action {mutation_kind}"
            )
        operation, _ = authority
        name = resource_snapshot.name
        mutation_argv: tuple[str, ...]
        if mutation_kind == "docker_remove":
            mutation_argv = ("docker", "rm", "--", name)
        elif mutation_kind == "database_drop":
            mutation_argv = ("dropdb", "--if-exists", name)
        elif mutation_kind == "redis_delete_prefix":
            mutation_argv = ("milestone-2b-delete-redis-prefix", "--prefix", name)
        elif mutation_kind == "kafka_delete_topic":
            mutation_argv = ("kafka-topics", "--delete", "--topic", name)
        else:
            mutation_argv = (
                "kafka-consumer-groups",
                "--delete",
                "--group",
                name,
            )
        return operation, (resource_snapshot,), mutation_argv

    if type(action) is OutputProbeAction:
        output_action = action
        stdout_bytes = output_action.stdout_bytes
        stderr_bytes = output_action.stderr_bytes
        if (
            type(stdout_bytes) is not int
            or type(stderr_bytes) is not int
            or stdout_bytes < 0
            or stderr_bytes < 0
        ):
            raise ValueError("probe output sizes must be non-negative")
        source = (
            "import sys;"
            "sys.stdout.write('x'*int(sys.argv[1]));"
            "sys.stderr.write('y'*int(sys.argv[2]))"
        )
        return (
            "read",
            (),
            (
                sys.executable,
                "-c",
                source,
                str(stdout_bytes),
                str(stderr_bytes),
            ),
        )

    if type(action) is ProcessGroupProbeAction:
        process_action = action
        flags = (
            process_action.spawn_child,
            process_action.parent_exits,
            process_action.ignore_sigterm,
        )
        if any(type(value) is not bool for value in flags):
            raise ValueError("process probe flags must be booleans")
        return (
            "read",
            (),
            (
                sys.executable,
                "-c",
                _PROCESS_GROUP_PROBE_SOURCE,
                *("1" if value else "0" for value in flags),
            ),
        )

    if type(action) is FoundationCheckAction:
        foundation_action = action
        group = foundation_action.group
        case_id = foundation_action.case_id
        resources = foundation_action.resources
        if (
            type(group) is not str
            or group not in _FOUNDATION_CASE_RANGES
            or type(case_id) is not str
        ):
            raise ValueError("foundation checker identity is invalid")
        prefix, last = _FOUNDATION_CASE_RANGES[group]
        valid_case_ids = (
            _LOAD_CASES
            if group == "load"
            else {f"{prefix}-{number:03d}" for number in range(1, last + 1)}
        )
        if case_id not in valid_case_ids:
            raise ValueError("foundation checker case does not match its group")
        if type(resources) is not tuple or not resources:
            raise ValueError("foundation checker resources are invalid")
        resource_snapshots = tuple(
            _snapshot_resource(resource, "foundation checker")
            for resource in resources
        )
        validated_action = FoundationCheckAction(
            group=cast(FoundationGroup, group),
            case_id=case_id,
            resources=resource_snapshots,
        )
        input_resource = validated_action.resources[0]
        mutable_resources = validated_action.resources[1:]
        foundation_operation: CommandOperation
        if group == "registry":
            foundation_operation = (
                "database_mutation"
                if case_id in _REGISTRY_DATABASE_CASES
                else "redis_mutation"
            )
        elif group == "infrastructure":
            foundation_operation = "database_mutation"
        elif group == "load":
            foundation_operation = (
                "docker_mutation"
                if case_id in _CANONICAL_LOAD_CASES
                else "filesystem_mutation"
            )
        elif group == "deployment" and case_id in _WRITABLE_DEPLOYMENT_CASES:
            foundation_operation = "filesystem_mutation"
        else:
            foundation_operation = "read"
        return (
            foundation_operation,
            mutable_resources,
            (
                sys.executable,
                "-m",
                f"scripts.milestone_2b_case_runners.{group}",
                "--check",
                case_id,
                "--input",
                input_resource.name,
            ),
        )

    if type(action) is FoundationCleanupAction:
        cleanup_action = action
        group = cleanup_action.group
        case_id = cleanup_action.case_id
        run_id = cleanup_action.run_id
        resources = cleanup_action.resources
        resource_snapshots = tuple(
            _snapshot_resource(resource, "foundation cleanup")
            for resource in resources
        )
        validated_action = FoundationCleanupAction(
            group=group,
            case_id=case_id,
            run_id=run_id,
            resources=resource_snapshots,
        )
        return (
            "filesystem_mutation",
            validated_action.resources,
            (
                sys.executable,
                "-m",
                "scripts.milestone_2b_case_runners.cleanup",
                "--group",
                group,
                "--case",
                case_id,
                "--run-id",
                run_id,
            ),
        )

    raise ValueError("command action type is not registered")


@dataclass(frozen=True, slots=True)
class CommandSpec:
    action: CommandAction
    max_output_bytes: int = 65_536

    def __post_init__(self) -> None:
        if type(self.action) not in {
            ReadAction,
            MutationAction,
            OutputProbeAction,
            ProcessGroupProbeAction,
            FoundationCheckAction,
            FoundationCleanupAction,
        }:
            raise ValueError("command action type is not registered")
        if type(self.max_output_bytes) is not int or self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        _materialize_action(self.action)

    @property
    def argv(self) -> tuple[str, ...]:
        return _materialize_action(self.action)[2]


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool


class CaseCommandTimeout(TimeoutError):
    def __init__(self, message: str, result: CommandResult) -> None:
        super().__init__(message)
        self.result = result


class ProcessCleanupError(CommandTaskTerminationError):
    pass


def mutation_kind_for_resource(resource_kind: str) -> MutationKind:
    mapping: dict[str, MutationKind] = {
        "container": "docker_remove",
        "database": "database_drop",
        "redis_prefix": "redis_delete_prefix",
        "kafka_topic": "kafka_delete_topic",
        "kafka_group": "kafka_delete_group",
    }
    try:
        return mapping[resource_kind]
    except KeyError as exc:
        raise ValueError(f"unsupported resource kind: {resource_kind}") from exc


def validate_command_spec(*, context: CaseContext, command: CommandSpec) -> None:
    operation, resources, argv, _ = _materialize_command(command)
    claimed_case_id = _foundation_action_case_id(command.action)
    capability = validate_command_authority(
        context=context,
        operation=operation,
        resources=resources,
        claimed_case_id=claimed_case_id,
    )
    _validate_foundation_claimed_case(
        command=command,
        claimed_case_id=capability.authority_case_id,
    )
    _validate_foundation_input(context=context, command=command, argv=argv)
    _validate_foundation_cleanup(context=context, command=command, argv=argv)


def _materialize_command(
    command: CommandSpec,
) -> tuple[CommandOperation, tuple[ResourceSpec, ...], tuple[str, ...], int]:
    if type(command) is not CommandSpec:
        raise ValueError("command type must be CommandSpec")
    max_output_bytes = command.max_output_bytes
    if type(max_output_bytes) is not int or max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive")
    operation, resources, argv = _materialize_action(command.action)
    return operation, resources, argv, max_output_bytes


async def run_command(
    *,
    context: CaseContext,
    command: CommandSpec,
    timeout_seconds: float,
    terminate_grace_seconds: float = DEFAULT_COMMAND_TERMINATE_GRACE_SECONDS,
) -> CommandResult:
    _require_positive_finite_seconds(timeout_seconds, "command timeout")
    termination_timeout_seconds = command_termination_timeout_seconds(
        terminate_grace_seconds
    )
    operation, resources, argv, max_output_bytes = _materialize_command(command)
    claimed_case_id = _foundation_action_case_id(command.action)
    capability = validate_command_authority(
        context=context,
        operation=operation,
        resources=resources,
        claimed_case_id=claimed_case_id,
    )
    _validate_foundation_claimed_case(
        command=command,
        claimed_case_id=capability.authority_case_id,
    )
    _validate_foundation_input(context=context, command=command, argv=argv)
    _validate_foundation_cleanup(context=context, command=command, argv=argv)
    command_task = asyncio.create_task(
        _run_argv(
            argv=argv,
            max_output_bytes=max_output_bytes,
            timeout_seconds=timeout_seconds,
            terminate_grace_seconds=terminate_grace_seconds,
        )
    )
    register_command_task(
        capability,
        command_task,
        termination_timeout_seconds,
    )
    try:
        return await command_task
    finally:
        unregister_command_task(capability, command_task)


def _validate_foundation_claimed_case(
    *,
    command: CommandSpec,
    claimed_case_id: str | None,
) -> None:
    action = command.action
    if type(action) not in {FoundationCheckAction, FoundationCleanupAction}:
        return
    foundation_action = cast(FoundationCheckAction | FoundationCleanupAction, action)
    if claimed_case_id is not None and foundation_action.case_id != claimed_case_id:
        raise ValueError("foundation action does not match the claimed case")


def _foundation_action_case_id(action: CommandAction) -> str | None:
    if type(action) not in {FoundationCheckAction, FoundationCleanupAction}:
        return None
    foundation_action = cast(FoundationCheckAction | FoundationCleanupAction, action)
    return foundation_action.case_id


def _validate_foundation_input(
    *,
    context: CaseContext,
    command: CommandSpec,
    argv: tuple[str, ...],
) -> None:
    if type(command.action) is not FoundationCheckAction:
        return
    if len(argv) != 7 or argv[1] != "-m" or argv[3] != "--check" or argv[5] != "--input":
        raise ValueError("foundation checker command shape changed")
    group = argv[2].removeprefix("scripts.milestone_2b_case_runners.")
    case_id = argv[4]
    input_path = Path(argv[6])
    expected_prefix = (
        f"m2b-{len(context.run_id)}-{context.run_id}-"
        f"{case_id.lower()}-"
    )
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    if (
        not input_path.is_absolute()
        or ".." in input_path.parts
        or input_path.name != "input.json"
        or not input_path.parent.name.startswith(expected_prefix)
    ):
        raise ValueError("foundation checker input is outside current case temporary namespace")
    try:
        parent = input_path.parent.resolve(strict=True)
        parent_metadata = os.lstat(input_path.parent)
        input_metadata = os.lstat(input_path)
    except OSError as exc:
        raise ValueError("foundation checker input is not a real temporary file") from exc
    if parent.parent != temporary_root or input_path.parent.is_symlink():
        raise ValueError("foundation checker input is outside current case temporary namespace")
    if (
        not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(parent_metadata.st_mode) != 0o700
    ):
        raise ValueError("foundation checker temporary directory is not private")
    if (
        not stat.S_ISREG(input_metadata.st_mode)
        or input_metadata.st_uid != os.geteuid()
        or input_metadata.st_nlink != 1
        or stat.S_IMODE(input_metadata.st_mode) != 0o600
    ):
        raise ValueError("foundation checker input file metadata is unsafe")
    _validate_foundation_document(
        context=context,
        action=command.action,
        input_path=input_path,
        input_metadata=input_metadata,
    )
    expected_module = f"scripts.milestone_2b_case_runners.{group}"
    expected_group = _FOUNDATION_CASE_RANGES.get(group)
    if argv[2] != expected_module or expected_group is None or not case_id.startswith(
        f"{expected_group[0]}-"
    ):
        raise ValueError("foundation checker command identity changed")


def _validate_foundation_document(
    *,
    context: CaseContext,
    action: FoundationCheckAction,
    input_path: Path,
    input_metadata: os.stat_result,
) -> None:
    if input_metadata.st_size > 1024 * 1024:
        raise ValueError("foundation input does not match the authorized action")
    try:
        document = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("foundation input does not match the authorized action") from exc
    if not isinstance(document, dict):
        raise ValueError("foundation input does not match the authorized action")
    identity = {
        "case_id": action.case_id,
        "run_id": context.run_id,
        "target": context.target,
    }
    if any(document.get(field) != value for field, value in identity.items()):
        raise ValueError("foundation input does not match the authorized action")

    mutable_resources = action.resources[1:]
    bindings: tuple[tuple[str, ResourceSpec], ...]
    if action.group == "deployment":
        if action.case_id in _WRITABLE_DEPLOYMENT_CASES:
            if tuple(resource.kind for resource in mutable_resources) != (
                "filesystem",
            ):
                raise ValueError(
                    "foundation input does not match the authorized action"
                )
            bindings = (("scratch_directory", mutable_resources[0]),)
        elif not mutable_resources:
            bindings = ()
        else:
            raise ValueError("foundation input does not match the authorized action")
    elif action.group == "gpu" and len(mutable_resources) == 1:
        bindings = (("container", mutable_resources[0]),)
    elif action.group == "registry" and tuple(
        resource.kind for resource in mutable_resources
    ) == (
        ("redis_prefix", "database")
        if action.case_id in _REGISTRY_DATABASE_CASES
        else ("redis_prefix",)
    ):
        registry_fields = (
            ("redis_prefix", "database")
            if action.case_id in _REGISTRY_DATABASE_CASES
            else ("redis_prefix",)
        )
        bindings = tuple(zip(registry_fields, mutable_resources, strict=True))
    elif action.group == "infrastructure" and tuple(
        resource.kind for resource in mutable_resources
    ) == (
        "database",
        "mongodb_database",
        "kafka_topic",
        "kafka_group",
        "redis_prefix",
    ):
        infrastructure_fields = (
            "database",
            "mongodb_database",
            "kafka_topic",
            "kafka_group",
            "redis_prefix",
        )
        bindings = tuple(zip(infrastructure_fields, mutable_resources, strict=True))
    elif action.group == "load":
        expected_kinds = _load_checker_resource_kinds(action.case_id)
        if tuple(resource.kind for resource in mutable_resources) != expected_kinds:
            raise ValueError("foundation input does not match the authorized action")
        if action.case_id in _CANONICAL_LOAD_CASES and document.get(
            "runtime_recovery_receipt_path"
        ) != mutable_resources[-1].name:
            raise ValueError("foundation input does not match the authorized action")
        if action.case_id == "LOAD-011":
            if document.get("containers") != [
                resource.name for resource in mutable_resources[:3]
            ] or document.get("database_scope") != mutable_resources[3].name:
                raise ValueError("foundation input does not match the authorized action")
            bindings = ()
        elif action.case_id == "LOAD-016":
            fields = (
                "container",
                "support_container",
                "database_scope",
            )
            bindings = tuple(zip(fields, mutable_resources[:-1], strict=True))
        else:
            other_fields = (
                ("container", "database_scope")
                if action.case_id in _LOAD_COURSE_FACT_CASES
                else ("container", "redis_scope", "lease_receipt_path")
                if action.case_id == "LOAD-015"
                else ("container",)
                if action.case_id in _CANONICAL_LOAD_CASES
                else ("scratch_directory",)
            )
            bound_resources = (
                mutable_resources[:-1]
                if action.case_id in _CANONICAL_LOAD_CASES
                else mutable_resources
            )
            bindings = tuple(zip(other_fields, bound_resources, strict=True))
    else:
        raise ValueError("foundation input does not match the authorized action")
    if any(document.get(field) != resource.name for field, resource in bindings):
        raise ValueError("foundation input does not match the authorized action")


def _validate_foundation_cleanup(
    *,
    context: CaseContext,
    command: CommandSpec,
    argv: tuple[str, ...],
) -> None:
    if type(command.action) is not FoundationCleanupAction:
        return
    if (
        len(argv) != 9
        or argv[1:4]
        != (
            "-m",
            "scripts.milestone_2b_case_runners.cleanup",
            "--group",
        )
        or argv[5] != "--case"
        or argv[7] != "--run-id"
    ):
        raise ValueError("foundation cleanup command shape changed")
    if argv[8] != context.run_id:
        raise ValueError("foundation cleanup run_id changed after capability binding")
    expected = foundation_cleanup_resources(
        cast(FoundationGroup, argv[4]), argv[6], argv[8]
    )
    if command.action.resources != expected:
        raise ValueError("foundation cleanup resources changed after action validation")


async def _run_argv(
    *,
    argv: tuple[str, ...],
    max_output_bytes: int,
    timeout_seconds: float,
    terminate_grace_seconds: float,
) -> CommandResult:
    if not argv or any(not argument or "\0" in argument for argument in argv):
        raise ValueError("command argv must contain non-empty arguments")
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive")
    _require_positive_finite_seconds(timeout_seconds, "command timeout")
    _require_positive_finite_seconds(
        terminate_grace_seconds, "command termination grace"
    )
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    completion_task = asyncio.create_task(
        _collect_command_output(process, max_output_bytes)
    )
    supervisor_registered = False
    process_group_termination_confirmed = False
    try:
        try:
            _notify_process_supervisor("+", process.pid)
        except ProcessCleanupError:
            await _finish_process_group_termination(
                process,
                completion_task,
                SUPERVISOR_REGISTRATION_CLEANUP_GRACE_SECONDS,
            )
            raise
        supervisor_registered = True
        try:
            collected = await asyncio.wait_for(
                asyncio.shield(completion_task),
                timeout=timeout_seconds,
            )
            if not await _wait_for_process_group_exit(
                process.pid,
                asyncio.get_running_loop().time(),
            ):
                collected = await _finish_process_group_termination(
                    process,
                    completion_task,
                    terminate_grace_seconds,
                )
            process_group_termination_confirmed = True
            returncode, stdout_result, stderr_result = collected
        except TimeoutError as exc:
            collected = await _finish_process_group_termination(
                process,
                completion_task,
                terminate_grace_seconds,
            )
            process_group_termination_confirmed = True
            raise CaseCommandTimeout(
                f"command timed out after {timeout_seconds} seconds",
                _command_result(argv, *collected),
            ) from exc
        except asyncio.CancelledError:
            await _finish_process_group_termination(
                process,
                completion_task,
                terminate_grace_seconds,
            )
            process_group_termination_confirmed = True
            raise
    finally:
        if not completion_task.done():
            completion_task.cancel()
        if (
            supervisor_registered
            and process_group_termination_confirmed
        ):
            _notify_process_supervisor("-", process.pid)
    return _command_result(argv, returncode, stdout_result, stderr_result)


async def _finish_process_group_termination(
    process: asyncio.subprocess.Process,
    completion_task: asyncio.Task[
        tuple[int, tuple[bytes, bool], tuple[bytes, bool]]
    ],
    grace_seconds: float,
) -> tuple[int, tuple[bytes, bool], tuple[bytes, bool]]:
    termination_task = asyncio.create_task(
        _terminate_process_group(process, completion_task, grace_seconds)
    )
    cancellation_seen = False
    while not termination_task.done():
        try:
            await asyncio.shield(termination_task)
        except asyncio.CancelledError:
            cancellation_seen = True
    result = termination_task.result()
    if cancellation_seen:
        raise asyncio.CancelledError
    return result


def _require_positive_finite_seconds(value: float, label: str) -> None:
    if type(value) not in {int, float}:
        raise ValueError(f"{label} must be a plain finite number")
    if value <= 0 or (type(value) is float and not math.isfinite(value)):
        raise ValueError(f"{label} must be positive and finite")


def command_termination_timeout_seconds(terminate_grace_seconds: float) -> float:
    _require_positive_finite_seconds(
        terminate_grace_seconds,
        "command termination grace",
    )
    if terminate_grace_seconds > MAX_COMMAND_TERMINATE_GRACE_SECONDS:
        raise ValueError(
            "command termination grace must not exceed "
            f"{MAX_COMMAND_TERMINATE_GRACE_SECONDS:g} seconds"
        )
    return (
        terminate_grace_seconds
        + POST_KILL_DRAIN_TIMEOUT_SECONDS
        + COMPLETION_CANCEL_GRACE_SECONDS
    )


def maximum_command_termination_budget_seconds() -> float:
    return (
        command_termination_timeout_seconds(MAX_COMMAND_TERMINATE_GRACE_SECONDS)
        + safety_module.COMMAND_TASK_TERMINATION_MARGIN_SECONDS
    )


def _command_result(
    argv: tuple[str, ...],
    returncode: int,
    stdout_result: tuple[bytes, bool],
    stderr_result: tuple[bytes, bool],
) -> CommandResult:
    stdout, stdout_truncated = stdout_result
    stderr, stderr_truncated = stderr_result
    return CommandResult(
        argv=argv,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )


async def _collect_command_output(
    process: asyncio.subprocess.Process,
    limit: int,
) -> tuple[int, tuple[bytes, bool], tuple[bytes, bool]]:
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_task = asyncio.create_task(_read_bounded(process.stdout, limit))
    stderr_task = asyncio.create_task(_read_bounded(process.stderr, limit))
    try:
        returncode, stdout_result, stderr_result = await asyncio.gather(
            process.wait(),
            stdout_task,
            stderr_task,
        )
        return returncode, stdout_result, stderr_result
    finally:
        for task in (stdout_task, stderr_task):
            if not task.done():
                task.cancel()


async def _read_bounded(
    stream: asyncio.StreamReader, limit: int
) -> tuple[bytes, bool]:
    retained = bytearray()
    truncated = False
    while chunk := await stream.read(65_536):
        remaining = limit - len(retained)
        if remaining > 0:
            retained.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True
    return bytes(retained), truncated


async def _terminate_process_group(
    process: asyncio.subprocess.Process,
    completion_task: asyncio.Task[
        tuple[int, tuple[bytes, bool], tuple[bytes, bool]]
    ],
    grace_seconds: float,
) -> tuple[int, tuple[bytes, bool], tuple[bytes, bool]]:
    loop = asyncio.get_running_loop()
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    terminate_deadline = loop.time() + grace_seconds
    collected: tuple[int, tuple[bytes, bool], tuple[bytes, bool]] | None = None
    try:
        collected = await _await_completion_before(
            completion_task,
            terminate_deadline,
        )
    except TimeoutError:
        pass
    if collected is not None and await _wait_for_process_group_exit(
        process.pid, terminate_deadline
    ):
        return collected

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    kill_deadline = loop.time() + POST_KILL_DRAIN_TIMEOUT_SECONDS
    if collected is None:
        try:
            collected = await _await_completion_before(
                completion_task,
                kill_deadline,
            )
        except TimeoutError as exc:
            await _cancel_completion_task(completion_task)
            raise ProcessCleanupError(
                "process output/reap did not finish after SIGKILL"
            ) from exc
    if not await _wait_for_process_group_exit(process.pid, kill_deadline):
        if not completion_task.done():
            await _cancel_completion_task(completion_task)
        raise ProcessCleanupError("process group remained after SIGKILL")
    return collected


async def _await_completion_before(
    completion_task: asyncio.Task[
        tuple[int, tuple[bytes, bool], tuple[bytes, bool]]
    ],
    deadline: float,
) -> tuple[int, tuple[bytes, bool], tuple[bytes, bool]]:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError
    return await asyncio.wait_for(
        asyncio.shield(completion_task),
        timeout=remaining,
    )


async def _wait_for_process_group_exit(
    process_group_id: int,
    deadline: float,
) -> bool:
    while True:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            # Darwin can transiently report EPERM after the last process exits
            # but before the process group becomes unobservable as ESRCH.
            pass
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.01, remaining))


async def _cancel_completion_task(task: asyncio.Task[object]) -> None:
    task.cancel()
    done, _ = await asyncio.wait(
        (task,),
        timeout=COMPLETION_CANCEL_GRACE_SECONDS,
    )
    if task in done:
        _consume_task_result(task)
    else:
        task.add_done_callback(_consume_task_result)


def _consume_task_result(task: asyncio.Future[object]) -> None:
    try:
        task.result()
    except BaseException:
        pass
