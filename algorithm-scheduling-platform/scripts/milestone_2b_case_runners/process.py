from __future__ import annotations

import asyncio
import math
import os
import signal
import sys
from dataclasses import dataclass
from typing import Literal

from .base import CaseContext
from .safety import (
    CommandOperation,
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
_SUPERVISOR_PROCESS_FD: int | None = None


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


CommandAction = ReadAction | MutationAction | OutputProbeAction | ProcessGroupProbeAction


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


class ProcessCleanupError(RuntimeError):
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
    operation, resources, _, _ = _materialize_command(command)
    validate_command_authority(
        context=context,
        operation=operation,
        resources=resources,
    )


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
    terminate_grace_seconds: float = 2.0,
) -> CommandResult:
    _require_positive_finite_seconds(timeout_seconds, "command timeout")
    _require_positive_finite_seconds(
        terminate_grace_seconds, "command termination grace"
    )
    operation, resources, argv, max_output_bytes = _materialize_command(command)
    capability = validate_command_authority(
        context=context,
        operation=operation,
        resources=resources,
    )
    command_task = asyncio.create_task(
        _run_argv(
            argv=argv,
            max_output_bytes=max_output_bytes,
            timeout_seconds=timeout_seconds,
            terminate_grace_seconds=terminate_grace_seconds,
        )
    )
    register_command_task(capability, command_task)
    try:
        return await command_task
    finally:
        unregister_command_task(capability, command_task)


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
            returncode, stdout_result, stderr_result = await asyncio.wait_for(
                asyncio.shield(completion_task),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            collected = await _finish_process_group_termination(
                process,
                completion_task,
                terminate_grace_seconds,
            )
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
            raise
    finally:
        if not completion_task.done():
            completion_task.cancel()
        if (
            supervisor_registered
            and process.returncode is not None
            and completion_task.done()
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
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        return await asyncio.wait_for(
            asyncio.shield(completion_task),
            timeout=grace_seconds,
        )
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            return await asyncio.wait_for(
                asyncio.shield(completion_task),
                timeout=POST_KILL_DRAIN_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            await _cancel_completion_task(completion_task)
            raise ProcessCleanupError(
                "process output/reap did not finish after SIGKILL"
            ) from exc


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
