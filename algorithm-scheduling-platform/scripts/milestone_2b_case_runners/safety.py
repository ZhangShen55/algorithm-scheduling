from __future__ import annotations

import asyncio
import contextvars
import fcntl
import os
import stat
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
    "filesystem",
]
RESOURCE_KINDS = frozenset(
    {
        "container",
        "kafka_topic",
        "kafka_group",
        "redis_prefix",
        "database",
        "filesystem",
    }
)
COMMAND_TASK_CANCEL_GRACE_SECONDS = 0.5


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


@dataclass(frozen=True, slots=True)
class _ExecutionCapability:
    context: CaseContext
    authority_context: CaseContext
    authority_case_id: str | None
    authority_category: str | None
    safety: CaseSafety
    maintenance_lock: MaintenanceLockGuard | None
    lease: _CapabilityLease


@dataclass(slots=True)
class _CapabilityLease:
    active: bool = True
    command_tasks: set[asyncio.Task[object]] = field(default_factory=set)


_ACTIVE_CAPABILITY: contextvars.ContextVar[_ExecutionCapability | None] = (
    contextvars.ContextVar("milestone_2b_case_capability", default=None)
)


@asynccontextmanager
async def _case_execution_scope(
    context: CaseContext,
    safety: CaseSafety,
    maintenance_lock: MaintenanceLockGuard | None,
    authority_case: CaseDefinition | None = None,
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
        active_tasks = tuple(lease.command_tasks)
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            done, pending = await asyncio.wait(
                active_tasks,
                timeout=COMMAND_TASK_CANCEL_GRACE_SECONDS,
            )
            for task in done:
                _consume_command_task_result(task)
            for task in pending:
                task.add_done_callback(_consume_command_task_result)
        _ACTIVE_CAPABILITY.reset(token)


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
) -> _ExecutionCapability:
    capability = _ACTIVE_CAPABILITY.get()
    if capability is None or capability.context is not context:
        raise ValueError("run_command requires the active case execution capability")
    if not capability.lease.active:
        raise ValueError("case execution capability is revoked")
    if not _context_matches_authority(context, capability.authority_context):
        raise ValueError("case execution context changed after capability binding")
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
            _validate_isolated_resource(capability.authority_context, resource)
    return capability


def register_command_task(
    capability: _ExecutionCapability,
    task: asyncio.Task[object],
) -> None:
    if (
        _ACTIVE_CAPABILITY.get() is not capability
        or not capability.lease.active
    ):
        task.cancel()
        raise ValueError("case execution capability is revoked")
    capability.lease.command_tasks.add(task)


def unregister_command_task(
    capability: _ExecutionCapability,
    task: asyncio.Task[object],
) -> None:
    capability.lease.command_tasks.discard(task)


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
    context: CaseContext, resource: ResourceSpec
) -> None:
    run_id = context.run_id
    valid = False
    if resource.kind == "container":
        valid = resource.name.startswith(f"m2b-{len(run_id)}-{run_id}-")
    elif resource.kind in {"kafka_topic", "kafka_group"}:
        valid = resource.name.startswith(f"m2b.{run_id}.")
    elif resource.kind == "redis_prefix":
        valid = resource.name.startswith(f"m2b:{run_id}:")
    elif resource.kind == "database":
        database_prefix = (
            f"m2b_{len(run_id)}_{run_id.replace('-', '_')}_"
        )
        valid = resource.name.startswith(database_prefix) and resource.name.endswith(
            "_test"
        )
    if not valid:
        raise ValueError(
            f"isolated resource is outside current run namespace: {resource.name}"
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
