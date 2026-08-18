from __future__ import annotations

import asyncio
import contextvars
import fcntl
import os
import shlex
import stat
import subprocess
import tempfile
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Literal

from scripts.milestone_2b_case_catalog import CaseDefinition

from .base import CaseContext

CaseSafety = Literal["read_only", "isolated_mutation", "canonical_runtime"]
CommandOperation = Literal[
    "read",
    "filesystem_mutation",
    "docker_mutation",
    "database_mutation",
    "redis_mutation",
    "kafka_mutation",
]
ResourceKind = Literal[
    "container",
    "kafka_topic",
    "kafka_group",
    "redis_prefix",
    "database",
    "mongodb_database",
    "filesystem",
]
RESOURCE_KINDS = frozenset(
    {
        "container",
        "kafka_topic",
        "kafka_group",
        "redis_prefix",
        "database",
        "mongodb_database",
        "filesystem",
    }
)
COMMAND_TASK_TERMINATION_MARGIN_SECONDS = 0.5


class CommandTaskTerminationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    kind: ResourceKind
    name: str

    def __post_init__(self) -> None:
        if type(self.kind) is not str or self.kind not in RESOURCE_KINDS:
            raise ValueError("resource kind must be a supported plain string")
        if type(self.name) is not str or not self.name.strip():
            raise ValueError("resource name must be a non-empty plain string")


class MaintenanceLockGuard:
    def __init__(self, release_root: Path) -> None:
        self._release_root = release_root
        self._release_tag_root = release_root.parent
        self._directory_fd = -1
        self._lock_fd = -1
        self._held = False

    @property
    def held(self) -> bool:
        return self._held

    def held_for(self, release_root: Path) -> bool:
        if (
            not self._held
            or release_root != self._release_root
            or self._directory_fd < 0
            or self._lock_fd < 0
        ):
            return False
        try:
            named_root = os.lstat(self._release_tag_root)
            opened_root = os.fstat(self._directory_fd)
            _require_owned_directory_metadata(named_root, "release tag root")
            _require_owned_directory_metadata(opened_root, "release tag root")
            if not _same_file(named_root, opened_root):
                return False
            named_lock = os.stat(
                ".operator-lifecycle.lock",
                dir_fd=self._directory_fd,
                follow_symlinks=False,
            )
            opened_lock = os.fstat(self._lock_fd)
            _require_lock_metadata(named_lock)
            _require_lock_metadata(opened_lock)
            return _same_file(named_lock, opened_lock)
        except (OSError, ValueError):
            return False

    def __enter__(self) -> MaintenanceLockGuard:
        if self._held:
            raise ValueError("maintenance lock guard is already held")
        root_metadata = _require_owned_directory(
            self._release_tag_root, "release tag root"
        )
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            self._directory_fd = os.open(self._release_tag_root, directory_flags)
            opened_root = os.fstat(self._directory_fd)
            if not _same_file(root_metadata, opened_root):
                raise ValueError(
                    "release tag root changed while opening maintenance lock"
                )
            try:
                fcntl.flock(
                    self._directory_fd,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            except BlockingIOError as exc:
                raise ValueError(
                    "another release is holding the maintenance lock"
                ) from exc
            self._lock_fd = os.open(
                ".operator-lifecycle.lock",
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=self._directory_fd,
            )
            opened_lock = os.fstat(self._lock_fd)
            named_lock = os.stat(
                ".operator-lifecycle.lock",
                dir_fd=self._directory_fd,
                follow_symlinks=False,
            )
            _require_lock_metadata(opened_lock)
            _require_lock_metadata(named_lock)
            if not _same_file(opened_lock, named_lock):
                raise ValueError("maintenance lock path changed while opening")
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError(
                    "another release is holding the maintenance lock"
                ) from exc
            self._held = True
            if not self.held_for(self._release_root):
                raise ValueError(
                    "maintenance lock binding changed after acquisition"
                )
            return self
        except BaseException:
            self._close_descriptors()
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._close_descriptors()

    def _close_descriptors(self) -> None:
        self._held = False
        if self._lock_fd >= 0:
            os.close(self._lock_fd)
            self._lock_fd = -1
        if self._directory_fd >= 0:
            os.close(self._directory_fd)
            self._directory_fd = -1


class DelegatedMaintenanceLockGuard:
    def __init__(self, release_root: Path, holder_pid: int, lock_path: Path) -> None:
        self._release_root = release_root
        self._release_tag_root = release_root.parent
        self._holder_pid = holder_pid
        self._lock_path = lock_path
        self._directory_fd = -1
        self._lock_fd = -1
        self._held = False

    @property
    def held(self) -> bool:
        return self._held

    def __enter__(self) -> DelegatedMaintenanceLockGuard:
        if self._held:
            raise ValueError("delegated maintenance lock guard is already held")
        if type(self._holder_pid) is not int or self._holder_pid <= 0:
            raise ValueError("delegated maintenance lock holder PID is invalid")
        if self._holder_pid == os.getpid():
            raise ValueError("delegated maintenance lock holder cannot be the batch process")
        expected_path = self._release_tag_root / ".operator-lifecycle.lock"
        if self._lock_path != expected_path or not self._lock_path.is_absolute():
            raise ValueError("delegated maintenance lock path is not exact")
        root_metadata = _require_owned_directory(
            self._release_tag_root, "release tag root"
        )
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            self._directory_fd = os.open(self._release_tag_root, directory_flags)
            opened_root = os.fstat(self._directory_fd)
            if not _same_file(root_metadata, opened_root):
                raise ValueError(
                    "release tag root changed while opening delegated maintenance lock"
                )
            self._lock_fd = os.open(
                self._lock_path.name,
                os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self._directory_fd,
            )
            opened_lock = os.fstat(self._lock_fd)
            named_lock = os.stat(
                self._lock_path.name,
                dir_fd=self._directory_fd,
                follow_symlinks=False,
            )
            _require_lock_metadata(opened_lock)
            _require_lock_metadata(named_lock)
            if not _same_file(opened_lock, named_lock):
                raise ValueError("delegated maintenance lock path changed while opening")
            self._held = True
            if not self.held_for(self._release_root):
                raise ValueError(
                    "delegated maintenance lock holder or binding is invalid"
                )
            return self
        except BaseException:
            self._close_descriptors()
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        still_held = self.held_for(self._release_root)
        self._close_descriptors()
        if not still_held:
            raise ValueError("delegated maintenance lock was lost before batch exit")

    def held_for(self, release_root: Path) -> bool:
        if (
            not self._held
            or release_root != self._release_root
            or self._directory_fd < 0
            or self._lock_fd < 0
        ):
            return False
        try:
            os.kill(self._holder_pid, 0)
            named_root = os.lstat(self._release_tag_root)
            opened_root = os.fstat(self._directory_fd)
            _require_owned_directory_metadata(named_root, "release tag root")
            _require_owned_directory_metadata(opened_root, "release tag root")
            if not _same_file(named_root, opened_root):
                return False
            named_lock = os.stat(
                self._lock_path.name,
                dir_fd=self._directory_fd,
                follow_symlinks=False,
            )
            opened_lock = os.fstat(self._lock_fd)
            _require_lock_metadata(named_lock)
            _require_lock_metadata(opened_lock)
            if not _same_file(named_lock, opened_lock):
                return False
            if not _holder_command_matches(
                self._holder_pid,
                self._release_tag_root,
                self._lock_path,
            ):
                return False
            if not _holder_has_open_inode(self._holder_pid, opened_lock):
                return False
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            else:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                return False
        except (OSError, subprocess.SubprocessError, ValueError):
            return False

    def _close_descriptors(self) -> None:
        self._held = False
        if self._lock_fd >= 0:
            os.close(self._lock_fd)
            self._lock_fd = -1
        if self._directory_fd >= 0:
            os.close(self._directory_fd)
            self._directory_fd = -1


MaintenanceLock = MaintenanceLockGuard | DelegatedMaintenanceLockGuard


def _holder_command_tokens(holder_pid: int) -> tuple[str, ...]:
    proc_command = Path(f"/proc/{holder_pid}/cmdline")
    try:
        payload = proc_command.read_bytes()
    except OSError:
        completed = subprocess.run(
            ("ps", "-p", str(holder_pid), "-o", "command="),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return tuple(shlex.split(completed.stdout.strip()))
    return tuple(
        item.decode("utf-8", errors="strict")
        for item in payload.split(b"\0")
        if item
    )


def _holder_command_matches(
    holder_pid: int,
    release_tag_root: Path,
    lock_path: Path,
) -> bool:
    tokens = _holder_command_tokens(holder_pid)
    try:
        script_index = next(
            index
            for index, token in enumerate(tokens)
            if Path(token).name == "operator_lifecycle.py"
        )
    except StopIteration:
        return False
    arguments = tokens[script_index:]
    if len(arguments) != 6 or arguments[1] != "hold-lock":
        return False
    raw_script_path = Path(arguments[0])
    script_path = (
        raw_script_path
        if raw_script_path.is_absolute()
        else _holder_working_directory(holder_pid) / raw_script_path
    ).resolve(strict=False)
    expected_script = (
        Path(__file__).resolve().parents[2]
        / "deploy"
        / "scripts"
        / "operator_lifecycle.py"
    )
    if script_path != expected_script:
        return False
    option_values = dict(zip(arguments[2::2], arguments[3::2], strict=True))
    return option_values == {
        "--release-tag-root": str(release_tag_root),
        "--lock-path": str(lock_path),
    }


def _holder_working_directory(holder_pid: int) -> Path:
    proc_cwd = Path(f"/proc/{holder_pid}/cwd")
    try:
        return Path(os.readlink(proc_cwd)).resolve(strict=True)
    except OSError:
        completed = subprocess.run(
            ("lsof", "-a", "-p", str(holder_pid), "-d", "cwd", "-F", "n"),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        paths = [
            Path(line[1:])
            for line in completed.stdout.splitlines()
            if line.startswith("n/")
        ]
        if len(paths) != 1:
            raise ValueError(
                "delegated maintenance lock holder cwd is unavailable"
            ) from None
        return paths[0].resolve(strict=True)


def _holder_has_open_inode(holder_pid: int, expected: os.stat_result) -> bool:
    proc_fds = Path(f"/proc/{holder_pid}/fd")
    try:
        descriptors = tuple(proc_fds.iterdir())
    except OSError:
        completed = subprocess.run(
            ("lsof", "-a", "-p", str(holder_pid), "-F", "n"),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        paths = tuple(
            Path(line[1:])
            for line in completed.stdout.splitlines()
            if line.startswith("n/")
        )
        for path in paths:
            try:
                if _same_file(os.stat(path), expected):
                    return True
            except OSError:
                continue
        return False
    for descriptor in descriptors:
        try:
            if _same_file(os.stat(descriptor), expected):
                return True
        except OSError:
            continue
    return False


@dataclass(frozen=True, slots=True)
class _ExecutionCapability:
    context: CaseContext
    authority_context: CaseContext
    authority_case_id: str | None
    authority_category: str | None
    safety: CaseSafety
    maintenance_lock: MaintenanceLock | None
    lease: _CapabilityLease


@dataclass(slots=True)
class _CapabilityLease:
    active: bool = True
    command_tasks: dict[asyncio.Task[object], float] = field(default_factory=dict)


_ACTIVE_CAPABILITY: contextvars.ContextVar[_ExecutionCapability | None] = (
    contextvars.ContextVar("milestone_2b_case_capability", default=None)
)


@asynccontextmanager
async def _case_execution_scope(
    context: CaseContext,
    safety: CaseSafety,
    maintenance_lock: MaintenanceLock | None,
    authority_case: CaseDefinition | None = None,
    termination_errors: list[CommandTaskTerminationError] | None = None,
) -> AsyncIterator[None]:
    if type(context) is not CaseContext:
        raise ValueError("case execution context type must be CaseContext")
    if (
        type(safety) is not str
        or safety not in {"read_only", "isolated_mutation", "canonical_runtime"}
    ):
        raise ValueError(f"unknown case safety: {safety}")
    safety_snapshot: CaseSafety = safety
    authority_context = CaseContext(
        release_root=context.release_root,
        run_id=context.run_id,
        target=context.target,
    )
    authority_case_id: str | None = None
    authority_category: str | None = None
    if authority_case is not None:
        if type(authority_case) is not CaseDefinition:
            raise ValueError("authority case type must be CaseDefinition")
        authority_case_id = authority_case.case_id
        authority_category = authority_case.category
        if (
            type(authority_case_id) is not str
            or not authority_case_id
            or type(authority_category) is not str
            or not authority_category
        ):
            raise ValueError("authority case identity must use plain strings")
    if _ACTIVE_CAPABILITY.get() is not None:
        raise ValueError("case execution capability is already active")
    lease = _CapabilityLease()
    capability = _ExecutionCapability(
        context,
        authority_context,
        authority_case_id,
        authority_category,
        safety_snapshot,
        maintenance_lock,
        lease,
    )
    token = _ACTIVE_CAPABILITY.set(
        capability
    )
    try:
        yield
    finally:
        lease.active = False
        active_tasks = tuple(lease.command_tasks.items())
        cancellation_seen = False
        termination_error: BaseException | None = None
        if active_tasks:
            drain_task = asyncio.create_task(
                _cancel_and_drain_command_tasks(active_tasks)
            )
            while not drain_task.done():
                try:
                    await asyncio.shield(drain_task)
                except asyncio.CancelledError:
                    cancellation_seen = True
                except BaseException:
                    pass
            try:
                drain_task.result()
            except BaseException as exc:
                termination_error = exc
        _ACTIVE_CAPABILITY.reset(token)
        if cancellation_seen:
            raise asyncio.CancelledError from termination_error
        if termination_error is not None:
            if termination_errors is None:
                raise termination_error
            if isinstance(termination_error, CommandTaskTerminationError):
                termination_errors.append(termination_error)
            else:
                termination_errors.append(
                    CommandTaskTerminationError(str(termination_error))
                )


async def _cancel_and_drain_command_tasks(
    tasks: tuple[tuple[asyncio.Task[object], float], ...],
) -> None:
    for task, _ in tasks:
        task.cancel()
    task_objects = tuple(task for task, _ in tasks)
    done, pending = await asyncio.wait(
        task_objects,
        timeout=max(timeout_seconds for _, timeout_seconds in tasks),
    )
    unsafe_errors: list[str] = []
    for task in done:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except CommandTaskTerminationError as exc:
            unsafe_errors.append(str(exc) or type(exc).__name__)
        except BaseException:
            pass
    for task in pending:
        task.add_done_callback(_consume_command_task_result)
    if pending:
        unsafe_errors.append(
            f"{len(pending)} authorized command task(s) remained active"
        )
    if unsafe_errors:
        raise CommandTaskTerminationError("; ".join(unsafe_errors))


def _consume_command_task_result(task: asyncio.Future[object]) -> None:
    try:
        task.result()
    except BaseException:
        pass


def maintenance_lock_held(release_root: Path) -> bool:
    capability = _ACTIVE_CAPABILITY.get()
    return bool(
        capability is not None
        and capability.lease.active
        and capability.maintenance_lock is not None
        and release_root == capability.authority_context.release_root
        and capability.maintenance_lock.held_for(
            capability.authority_context.release_root
        )
    )


def validate_command_authority(
    *,
    context: CaseContext,
    operation: CommandOperation,
    resources: Sequence[ResourceSpec],
    claimed_case_id: str | None = None,
) -> _ExecutionCapability:
    capability = _ACTIVE_CAPABILITY.get()
    if capability is None or capability.context is not context:
        raise ValueError("run_command requires the active case execution capability")
    if not capability.lease.active:
        raise ValueError("case execution capability is revoked")
    if not _context_matches_authority(context, capability.authority_context):
        raise ValueError("case execution context changed after capability binding")
    if (
        claimed_case_id is not None
        and capability.authority_case_id is not None
        and claimed_case_id != capability.authority_case_id
    ):
        raise ValueError("foundation action does not match the claimed case")
    if capability.safety == "read_only" and operation != "read":
        raise ValueError("read_only cases cannot execute mutation actions")
    if capability.safety == "canonical_runtime" and (
        capability.maintenance_lock is None
        or not capability.maintenance_lock.held_for(
            capability.authority_context.release_root
        )
    ):
        raise ValueError("canonical_runtime requires a held maintenance lock")
    if capability.safety == "isolated_mutation" and operation != "read":
        for resource in resources:
            _validate_isolated_resource(
                capability.authority_context,
                resource,
                case_id=capability.authority_case_id,
            )
    return capability


def register_command_task(
    capability: _ExecutionCapability,
    task: asyncio.Task[object],
    termination_timeout_seconds: float,
) -> None:
    if (
        _ACTIVE_CAPABILITY.get() is not capability
        or not capability.lease.active
    ):
        task.cancel()
        raise ValueError("case execution capability is revoked")
    if (
        type(termination_timeout_seconds) not in {int, float}
        or termination_timeout_seconds <= 0
    ):
        task.cancel()
        raise ValueError("command task termination timeout must be positive")
    capability.lease.command_tasks[task] = (
        termination_timeout_seconds + COMMAND_TASK_TERMINATION_MARGIN_SECONDS
    )


def unregister_command_task(
    capability: _ExecutionCapability,
    task: asyncio.Task[object],
) -> None:
    capability.lease.command_tasks.pop(task, None)


def validate_case_evidence_authority(
    *,
    context: CaseContext,
    case_id: str,
    category: str,
) -> None:
    capability = _ACTIVE_CAPABILITY.get()
    if capability is None or capability.context is not context:
        raise ValueError(
            "case evidence publication requires the active execution capability"
        )
    if not capability.lease.active:
        raise ValueError("case execution capability is revoked")
    if not _context_matches_authority(context, capability.authority_context):
        raise ValueError("case execution context changed after capability binding")
    if type(case_id) is not str or type(category) is not str:
        raise ValueError("case evidence identity must use plain strings")
    if (
        capability.authority_case_id is None
        or capability.authority_category is None
        or case_id != capability.authority_case_id
        or category != capability.authority_category
    ):
        raise ValueError("case evidence identity does not match the claimed case")


def _context_matches_authority(
    context: CaseContext,
    authority: CaseContext,
) -> bool:
    return bool(
        type(context) is CaseContext
        and type(context.release_root) is type(authority.release_root)
        and context.release_root == authority.release_root
        and type(context.run_id) is str
        and context.run_id == authority.run_id
        and type(context.target) is str
        and context.target == authority.target
    )


def _validate_isolated_resource(
    context: CaseContext,
    resource: ResourceSpec,
    *,
    case_id: str | None,
) -> None:
    run_id = context.run_id
    case_name = case_id.lower() if case_id is not None else None
    valid = False
    if resource.kind == "container":
        prefix = f"m2b-{len(run_id)}-{run_id}-"
        if case_name is not None:
            prefix += f"{case_name}-"
        valid = resource.name.startswith(prefix)
    elif resource.kind in {"kafka_topic", "kafka_group"}:
        prefix = f"m2b.{run_id}."
        if case_name is not None:
            prefix += case_name
        valid = resource.name.startswith(prefix)
    elif resource.kind == "redis_prefix":
        prefix = f"m2b:{run_id}:"
        if case_name is not None:
            prefix += f"{case_name}:"
        valid = resource.name.startswith(prefix)
    elif resource.kind in {"database", "mongodb_database"}:
        database_prefix = (
            f"m2b_{len(run_id)}_{run_id.replace('-', '_')}_"
        )
        if case_name is not None:
            database_prefix += f"{case_name.replace('-', '_')}_"
        expected_suffix = (
            "_mongo_test" if resource.kind == "mongodb_database" else "_test"
        )
        valid = (
            resource.name.startswith(database_prefix)
            and resource.name.endswith(expected_suffix)
            and (
                resource.kind == "mongodb_database"
                or not resource.name.endswith("_mongo_test")
            )
        )
    elif resource.kind == "filesystem":
        path = Path(resource.name)
        expected_prefix = f"m2b-{len(run_id)}-{run_id}-"
        if case_name is not None:
            expected_prefix += f"{case_name}-"
        try:
            temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
            parent = path.parent.resolve(strict=True)
        except OSError:
            valid = False
        else:
            base_valid = bool(
                path.is_absolute()
                and ".." not in path.parts
                and parent == temporary_root
                and path.name.startswith(expected_prefix)
            )
            if path.name.endswith("-"):
                valid = base_valid
            else:
                try:
                    metadata = os.lstat(path)
                except OSError:
                    valid = False
                else:
                    valid = bool(
                        base_valid
                        and stat.S_ISDIR(metadata.st_mode)
                        and metadata.st_uid == os.geteuid()
                        and stat.S_IMODE(metadata.st_mode) == 0o700
                    )
    if not valid:
        namespace = "current case" if case_name is not None else "current run"
        raise ValueError(
            f"isolated resource is outside {namespace} namespace: {resource.name}"
        )


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _require_owned_directory(path: Path, label: str) -> os.stat_result:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise ValueError(f"cannot inspect {label}: {path}") from exc
    _require_owned_directory_metadata(metadata, label)
    return metadata


def _require_owned_directory_metadata(
    metadata: os.stat_result,
    label: str,
) -> None:
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
        raise ValueError(
            f"{label} must be a non-symlink directory owned by the current UID"
        )


def _require_lock_metadata(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("maintenance lock must be a regular file")
    if metadata.st_uid != os.geteuid():
        raise ValueError("maintenance lock must be owned by the current UID")
    if metadata.st_nlink != 1:
        raise ValueError("maintenance lock must have exactly one directory entry")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ValueError("maintenance lock must have mode 0600")
