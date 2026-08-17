#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import importlib
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from scripts.milestone_2b_report_contract import (
        DECLARATION_CATEGORY_BY_CASE_ID,
        DECLARATION_PLACEHOLDER,
        DECLARATION_TARGET,
        EXECUTION_CASE_KIND_BY_CATEGORY,
        EXECUTION_RECORD_FIELDS,
        CaseRecord,
        Coverage,
        expand_declaration_cases,
        load_report_plan_bytes,
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
    DECLARATION_PLACEHOLDER = _contract.DECLARATION_PLACEHOLDER
    DECLARATION_TARGET = _contract.DECLARATION_TARGET
    DECLARATION_CATEGORY_BY_CASE_ID = _contract.DECLARATION_CATEGORY_BY_CASE_ID
    EXECUTION_CASE_KIND_BY_CATEGORY = _contract.EXECUTION_CASE_KIND_BY_CATEGORY
    EXECUTION_RECORD_FIELDS = _contract.EXECUTION_RECORD_FIELDS
    CaseRecord = _contract.CaseRecord
    Coverage = _contract.Coverage
    expand_declaration_cases = _contract.expand_declaration_cases
    load_report_plan_bytes = _contract.load_report_plan_bytes
    strict_json_loads = _contract.strict_json_loads
    validate_cases_envelope = _contract.validate_cases_envelope
    validate_raw_execution_evidence = _contract.validate_raw_execution_evidence

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
CONTAINER_ID_PATTERN = re.compile(r"[0-9a-f]{64}")
SOURCE_CASE_ID_PATTERN = re.compile(r"[A-Z0-9]+(?:-[A-Z0-9]+)*")
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SMOKE_MANIFEST_FIELDS = {"schema_version", "cases"}
SMOKE_MANIFEST_CASE_FIELDS = {"case_id", "operator_code", "fixtures", "checks"}
SMOKE_LOGICAL_CASE_FIELDS = {
    "case_id",
    "status",
    "started_at",
    "finished_at",
    "target",
    "command",
    "evidence",
    "reason",
    "mock",
    "release_tag",
    "git_sha",
}
SMOKE_EVIDENCE_BASE_FIELDS = {
    "schema_version",
    "evidence_type",
    "operator_code",
    "target",
    "checks",
    "status",
    "mock",
    "release_tag",
    "git_sha",
}
EXPECTED_SMOKE_OPERATOR_CODES = frozenset(
    {
        "asr_offline",
        "asr_online",
        "facerec",
        "ocr",
        "ppt_slice",
        "screen_det",
        "text_analysis",
        "vbas",
    }
)
DECLARATION_CATEGORIES = ("negative", "load")
DECLARATION_FIELDS = {
    "schema_version",
    "evidence_type",
    "category",
    "status",
    "mock",
    "release_tag",
    "git_sha",
    "reason",
    "cases",
}
DECLARATION_CASE_FIELDS = {"case_id", "status"}
EXECUTION_FIELDS = set(EXECUTION_RECORD_FIELDS)
PUBLISH_PATHS = frozenset(
    {
        Path("negative/cases.json"),
        Path("load/cases.json"),
        Path("summary/cases.json"),
    }
)


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


@dataclass(frozen=True)
class InstanceSmokeRun:
    instance: OperatorInstance
    run_id: str
    status: str
    case: CaseRecord


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


def _require_exact_fields(
    document: dict[str, Any], expected: set[str], context: str
) -> None:
    actual = set(document)
    if actual != expected:
        raise ValueError(
            f"{context} fields invalid: "
            f"missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}"
        )


def _require_unique_string_list(
    value: object,
    context: str,
    *,
    allow_empty: bool,
) -> list[str]:
    raw_items = _require_list(value, context)
    if not allow_empty and not raw_items:
        raise ValueError(f"{context} must not be empty")
    items = [
        _require_string(item, f"{context}[{index}]")
        for index, item in enumerate(raw_items)
    ]
    if len(items) != len(set(items)):
        raise ValueError(f"{context} must contain unique strings")
    return items


def _stable_json_bytes(document: object) -> bytes:
    try:
        serialized = json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("publication document is not strict JSON") from exc
    return (serialized + "\n").encode("utf-8")


def _require_secure_publication_parent(
    metadata: os.stat_result, parent_path: Path
) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"publication parent must be a directory: {parent_path}")
    if metadata.st_uid != os.getuid():
        raise ValueError(
            f"publication parent must be owned by the current UID: {parent_path}"
        )
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise ValueError(
            "publication parent must not be group- or other-writable: "
            f"{parent_path}"
        )


@contextmanager
def _publication_directory_descriptor(
    release_root: Path, parent_path: Path
) -> Iterator[tuple[int, Callable[[], None]]]:
    if parent_path.is_absolute() or len(parent_path.parts) != 1:
        raise ValueError(f"publication parent path is not canonical: {parent_path}")
    parent_name = parent_path.parts[0]
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    with _release_directory_descriptor_access(
        release_root, Path()
    ) as release_access:
        root_descriptor, verify_root = release_access
        try:
            named_parent = os.stat(
                parent_name,
                dir_fd=root_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            try:
                os.mkdir(parent_name, mode=0o700, dir_fd=root_descriptor)
            except FileExistsError:
                pass
            else:
                os.fsync(root_descriptor)
            try:
                named_parent = os.stat(
                    parent_name,
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise ValueError(
                    f"failed to create publication directory: {parent_path}"
                ) from exc
        except OSError as exc:
            raise ValueError(
                f"failed to inspect publication directory: {parent_path}"
            ) from exc
        if stat.S_ISLNK(named_parent.st_mode) or not stat.S_ISDIR(
            named_parent.st_mode
        ):
            raise ValueError(
                f"publication parent must be a real directory: {parent_path}"
            )
        _require_secure_publication_parent(named_parent, parent_path)
        try:
            parent_descriptor = os.open(
                parent_name,
                directory_flags,
                dir_fd=root_descriptor,
            )
        except OSError as exc:
            raise ValueError(
                f"failed to open publication directory: {parent_path}"
            ) from exc
        try:
            opened_parent = os.fstat(parent_descriptor)
            if not stat.S_ISDIR(opened_parent.st_mode) or not _same_filesystem_object(
                named_parent, opened_parent
            ):
                raise ValueError(
                    f"publication directory changed while opening: {parent_path}"
                )
            _require_secure_publication_parent(opened_parent, parent_path)

            def verify_directories() -> None:
                try:
                    named_after = os.stat(
                        parent_name,
                        dir_fd=root_descriptor,
                        follow_symlinks=False,
                    )
                    opened_after = os.fstat(parent_descriptor)
                except OSError as exc:
                    raise ValueError(
                        "failed to recheck publication directory: "
                        f"{parent_path}"
                    ) from exc
                if (
                    stat.S_ISLNK(named_after.st_mode)
                    or not stat.S_ISDIR(named_after.st_mode)
                    or not _same_filesystem_object(named_after, opened_after)
                ):
                    raise ValueError(
                        f"publication directory changed during access: {parent_path}"
                    )
                _require_secure_publication_parent(named_after, parent_path)
                _require_secure_publication_parent(opened_after, parent_path)
                verify_root()

            yield parent_descriptor, verify_directories
        except OSError as exc:
            raise ValueError(
                f"failed to access publication directory: {parent_path}"
            ) from exc
        finally:
            os.close(parent_descriptor)


def _read_existing_publication(
    parent_descriptor: int,
    name: str,
    *,
    expected_inode: os.stat_result | None = None,
    expected_nlink: int = 1,
) -> bytes:
    try:
        named = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as exc:
        raise ValueError(f"failed to inspect existing publication: {name}") from exc
    _require_publication_file_metadata(
        named,
        f"existing publication {name}",
        expected_nlink=expected_nlink,
    )
    if expected_inode is not None and not _same_filesystem_object(
        named, expected_inode
    ):
        raise ValueError(f"existing publication inode does not match temp: {name}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise ValueError(f"failed to open existing publication: {name}") from exc
    try:
        opened = os.fstat(descriptor)
        _require_publication_file_metadata(
            opened,
            f"opened publication {name}",
            expected_nlink=expected_nlink,
        )
        if not _same_filesystem_object(named, opened):
            raise ValueError(f"existing publication changed while opening: {name}")
        if expected_inode is not None and not _same_filesystem_object(
            opened, expected_inode
        ):
            raise ValueError(f"opened publication inode does not match temp: {name}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        named_after = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        opened_after = os.fstat(descriptor)
        _require_publication_file_metadata(
            named_after,
            f"existing publication {name}",
            expected_nlink=expected_nlink,
        )
        _require_publication_file_metadata(
            opened_after,
            f"opened publication {name}",
            expected_nlink=expected_nlink,
        )
        if not _same_filesystem_object(named_after, opened_after) or (
            expected_inode is not None
            and not _same_filesystem_object(opened_after, expected_inode)
        ):
            raise ValueError(f"existing publication changed during read: {name}")
        return b"".join(chunks)
    except OSError as exc:
        raise ValueError(f"failed to read existing publication: {name}") from exc
    finally:
        os.close(descriptor)


def _create_publication_temp(parent_descriptor: int, final_name: str) -> tuple[int, str]:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    for _ in range(128):
        temp_name = f".{final_name}.{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(
                temp_name,
                flags,
                0o600,
                dir_fd=parent_descriptor,
            )
        except FileExistsError:
            continue
        except OSError as exc:
            raise ValueError(f"failed to create publication temp for {final_name}") from exc
        return descriptor, temp_name
    raise ValueError(f"failed to allocate unique publication temp for {final_name}")


def _publication_name_metadata(parent_descriptor: int, name: str) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as exc:
        raise ValueError(f"failed to inspect publication name: {name}") from exc


def _require_publication_file_metadata(
    metadata: os.stat_result,
    context: str,
    *,
    expected_nlink: int,
) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{context} must be a regular file")
    if metadata.st_uid != os.getuid():
        raise ValueError(f"{context} must be owned by the current UID")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ValueError(f"{context} must have mode 0600")
    if metadata.st_nlink != expected_nlink:
        raise ValueError(
            f"{context} must have exactly {expected_nlink} link(s), "
            f"got {metadata.st_nlink}"
        )


def _require_temp_name_binding(
    parent_descriptor: int,
    temp_name: str,
    temp_metadata: os.stat_result,
    relative_path: Path,
) -> None:
    _require_publication_file_metadata(
        temp_metadata,
        f"publication temp for {relative_path}",
        expected_nlink=1,
    )
    named_temp = _publication_name_metadata(parent_descriptor, temp_name)
    _require_publication_file_metadata(
        named_temp,
        f"named publication temp for {relative_path}",
        expected_nlink=1,
    )
    if not _same_filesystem_object(named_temp, temp_metadata):
        raise ValueError(
            f"publication temp name changed before link: {relative_path}"
        )


def _unlink_temp_name_if_bound(
    parent_descriptor: int,
    temp_descriptor: int,
    temp_name: str,
    relative_path: Path,
) -> bool:
    opened_temp = os.fstat(temp_descriptor)
    try:
        named_temp = os.stat(
            temp_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return True
    except OSError as exc:
        raise ValueError(
            f"failed to inspect publication temp during cleanup: {relative_path}"
        ) from exc
    if (
        stat.S_ISLNK(named_temp.st_mode)
        or not stat.S_ISREG(named_temp.st_mode)
        or named_temp.st_uid != os.getuid()
        or stat.S_IMODE(named_temp.st_mode) != 0o600
        or not _same_filesystem_object(named_temp, opened_temp)
    ):
        return False
    try:
        os.unlink(temp_name, dir_fd=parent_descriptor)
    except FileNotFoundError:
        return True
    except OSError as exc:
        raise ValueError(
            f"failed to remove publication temp: {relative_path}"
        ) from exc
    return True


def _rollback_linked_publication_if_unchanged(
    parent_descriptor: int,
    final_name: str,
    temp_descriptor: int,
    relative_path: Path,
) -> None:
    # This conditional unlink relies on every publisher honoring the parent flock.
    # A same-UID process that bypasses that lock can still race the name lookup.
    temp_metadata = os.fstat(temp_descriptor)
    try:
        current_final = os.stat(
            final_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        os.fsync(parent_descriptor)
        return
    except OSError as exc:
        raise ValueError(
            f"failed to inspect publication inode for rollback: {relative_path}"
        ) from exc
    if not _same_filesystem_object(current_final, temp_metadata):
        return
    try:
        os.unlink(final_name, dir_fd=parent_descriptor)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ValueError(
            f"failed to roll back publication inode: {relative_path}"
        ) from exc
    os.fsync(parent_descriptor)


def _recover_interrupted_publication(
    *,
    parent_descriptor: int,
    verify_directories: Callable[[], None],
    final_name: str,
    expected_inode: os.stat_result,
    relative_path: Path,
    payload: bytes,
) -> os.stat_result:
    temp_pattern = re.compile(
        rf"\.{re.escape(final_name)}\.[0-9a-f]{{32}}\.tmp"
    )
    try:
        names = os.listdir(parent_descriptor)
    except OSError as exc:
        raise ValueError(
            f"failed to scan interrupted publication: {relative_path}"
        ) from exc

    matching_names: list[str] = []
    for name in names:
        if temp_pattern.fullmatch(name) is None:
            continue
        try:
            metadata = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ValueError(
                f"failed to inspect interrupted publication temp: {relative_path}"
            ) from exc
        if _same_filesystem_object(metadata, expected_inode):
            matching_names.append(name)
    if len(matching_names) != 1:
        raise ValueError(
            "interrupted publication must have exactly one matching canonical "
            f"hard-link temp: {relative_path}"
        )

    temp_name = matching_names[0]
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        temp_descriptor = os.open(
            temp_name,
            flags,
            dir_fd=parent_descriptor,
        )
    except OSError as exc:
        raise ValueError(
            f"failed to open interrupted publication temp: {relative_path}"
        ) from exc
    try:
        opened_temp = os.fstat(temp_descriptor)
        named_temp = _publication_name_metadata(parent_descriptor, temp_name)
        current_final = _publication_name_metadata(parent_descriptor, final_name)
        for metadata, context in (
            (opened_temp, "opened interrupted publication temp"),
            (named_temp, "named interrupted publication temp"),
            (current_final, "interrupted publication final"),
        ):
            _require_publication_file_metadata(
                metadata,
                f"{context} for {relative_path}",
                expected_nlink=2,
            )
            if not _same_filesystem_object(metadata, expected_inode):
                raise ValueError(
                    f"interrupted publication inode changed: {relative_path}"
                )
        verify_directories()
        try:
            os.unlink(temp_name, dir_fd=parent_descriptor)
        except OSError as exc:
            raise ValueError(
                f"failed to remove interrupted publication temp: {relative_path}"
            ) from exc
        os.fsync(parent_descriptor)
        recovered = _read_existing_publication(
            parent_descriptor,
            final_name,
            expected_inode=opened_temp,
            expected_nlink=1,
        )
        if recovered != payload:
            raise ValueError(
                f"recovered publication bytes changed unexpectedly: {relative_path}"
            )
        verify_directories()
        recovered_metadata = os.fstat(temp_descriptor)
        _require_publication_file_metadata(
            recovered_metadata,
            f"recovered publication for {relative_path}",
            expected_nlink=1,
        )
        return recovered_metadata
    except OSError as exc:
        raise ValueError(
            f"failed to recover interrupted publication: {relative_path}"
        ) from exc
    finally:
        os.close(temp_descriptor)


def _publish_json_locked(
    *,
    parent_descriptor: int,
    verify_directories: Callable[[], None],
    final_name: str,
    relative_path: Path,
    payload: bytes,
) -> None:
    temp_descriptor, temp_name = _create_publication_temp(
        parent_descriptor, final_name
    )
    link_succeeded = False
    temp_name_cleaned = False
    parent_synced = False
    recovered_inode: os.stat_result | None = None
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(temp_descriptor, payload[offset:])
            if written <= 0:
                raise ValueError(
                    f"short write while publishing JSON: {relative_path}"
                )
            offset += written
        os.fchmod(temp_descriptor, 0o600)
        os.fsync(temp_descriptor)
        temp_metadata = os.fstat(temp_descriptor)
        _require_temp_name_binding(
            parent_descriptor,
            temp_name,
            temp_metadata,
            relative_path,
        )
        try:
            os.link(
                temp_name,
                final_name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise ValueError(
                    f"failed to hard-link publication: {relative_path}"
                ) from exc
            existing_metadata = _publication_name_metadata(
                parent_descriptor, final_name
            )
            if existing_metadata.st_nlink == 2:
                existing = _read_existing_publication(
                    parent_descriptor,
                    final_name,
                    expected_inode=existing_metadata,
                    expected_nlink=2,
                )
            else:
                existing = _read_existing_publication(
                    parent_descriptor,
                    final_name,
                    expected_nlink=1,
                )
            if existing != payload:
                raise ValueError(
                    "publication already exists with different bytes: "
                    f"{relative_path}"
                ) from exc
            if existing_metadata.st_nlink == 2:
                recovered_inode = _recover_interrupted_publication(
                    parent_descriptor=parent_descriptor,
                    verify_directories=verify_directories,
                    final_name=final_name,
                    expected_inode=existing_metadata,
                    relative_path=relative_path,
                    payload=payload,
                )
        else:
            link_succeeded = True
            linked_temp = os.fstat(temp_descriptor)
            _require_publication_file_metadata(
                linked_temp,
                f"linked publication temp for {relative_path}",
                expected_nlink=2,
            )
            final_metadata = _publication_name_metadata(
                parent_descriptor, final_name
            )
            _require_publication_file_metadata(
                final_metadata,
                f"published final for {relative_path}",
                expected_nlink=2,
            )
            if not _same_filesystem_object(linked_temp, temp_metadata) or not (
                _same_filesystem_object(final_metadata, linked_temp)
            ):
                raise ValueError(
                    f"published inode does not match temp: {relative_path}"
                )
            published = _read_existing_publication(
                parent_descriptor,
                final_name,
                expected_inode=linked_temp,
                expected_nlink=2,
            )
            if published != payload:
                raise ValueError(
                    f"published bytes changed unexpectedly: {relative_path}"
                )

        temp_name_cleaned = _unlink_temp_name_if_bound(
            parent_descriptor,
            temp_descriptor,
            temp_name,
            relative_path,
        )
        if not temp_name_cleaned:
            raise ValueError(
                f"publication temp name changed during cleanup: {relative_path}"
            )
        os.fsync(parent_descriptor)
        parent_synced = True

        if link_succeeded:
            current_temp = os.fstat(temp_descriptor)
            _require_publication_file_metadata(
                current_temp,
                f"published temp descriptor for {relative_path}",
                expected_nlink=1,
            )
            current_final = _publication_name_metadata(
                parent_descriptor, final_name
            )
            _require_publication_file_metadata(
                current_final,
                f"published final for {relative_path}",
                expected_nlink=1,
            )
            if not _same_filesystem_object(current_final, current_temp):
                raise ValueError(
                    f"published final changed after temp cleanup: {relative_path}"
                )
        elif recovered_inode is not None:
            current = _read_existing_publication(
                parent_descriptor,
                final_name,
                expected_inode=recovered_inode,
                expected_nlink=1,
            )
            if current != payload:
                raise ValueError(
                    f"recovered publication changed after cleanup: {relative_path}"
                )
        verify_directories()
    except OSError as exc:
        if link_succeeded:
            _rollback_linked_publication_if_unchanged(
                parent_descriptor,
                final_name,
                temp_descriptor,
                relative_path,
            )
        raise ValueError(f"failed to publish JSON: {relative_path}") from exc
    except ValueError:
        if link_succeeded:
            _rollback_linked_publication_if_unchanged(
                parent_descriptor,
                final_name,
                temp_descriptor,
                relative_path,
            )
        raise
    finally:
        cleanup_error: ValueError | None = None
        if not temp_name_cleaned:
            try:
                _unlink_temp_name_if_bound(
                    parent_descriptor,
                    temp_descriptor,
                    temp_name,
                    relative_path,
                )
            except (OSError, ValueError) as exc:
                cleanup_error = ValueError(
                    f"failed to clean publication temp: {relative_path}"
                )
                cleanup_error.__cause__ = exc
        if not parent_synced:
            try:
                os.fsync(parent_descriptor)
            except OSError as exc:
                if cleanup_error is None:
                    cleanup_error = ValueError(
                        f"failed to sync publication directory: {relative_path}"
                    )
                    cleanup_error.__cause__ = exc
        try:
            os.close(temp_descriptor)
        except OSError as exc:
            if cleanup_error is None:
                cleanup_error = ValueError(
                    f"failed to close publication temp: {relative_path}"
                )
                cleanup_error.__cause__ = exc
        if cleanup_error is not None:
            raise cleanup_error


def publish_json_once(
    *, release_root: Path, relative_path: Path, document: object
) -> None:
    if relative_path not in PUBLISH_PATHS:
        raise ValueError(f"publication path is not canonical: {relative_path}")
    _require_release_root(release_root)
    payload = _stable_json_bytes(document)
    parent_path = relative_path.parent
    final_name = relative_path.name
    with _publication_directory_descriptor(release_root, parent_path) as publication:
        parent_descriptor, verify_directories = publication
        lock_acquired = False
        try:
            try:
                fcntl.flock(parent_descriptor, fcntl.LOCK_EX)
            except OSError as exc:
                raise ValueError(
                    f"failed to lock publication directory: {relative_path}"
                ) from exc
            lock_acquired = True
            verify_directories()
            _publish_json_locked(
                parent_descriptor=parent_descriptor,
                verify_directories=verify_directories,
                final_name=final_name,
                relative_path=relative_path,
                payload=payload,
            )
        finally:
            if lock_acquired:
                try:
                    fcntl.flock(parent_descriptor, fcntl.LOCK_UN)
                except OSError as exc:
                    raise ValueError(
                        f"failed to unlock publication directory: {relative_path}"
                    ) from exc


def _declaration_document(
    *,
    category: str,
    source_cases: list[dict[str, Any]],
    release_tag: str,
    git_sha: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "evidence_type": "execution_declaration",
        "category": category,
        "status": DECLARATION_PLACEHOLDER,
        "mock": False,
        "release_tag": release_tag,
        "git_sha": git_sha,
        "reason": reason,
        "cases": [
            {"case_id": case["case_id"], "status": DECLARATION_PLACEHOLDER}
            for case in source_cases
        ],
    }


def _validate_declaration_document(
    value: object,
    *,
    expected: dict[str, Any],
    context: str,
) -> None:
    document = _require_object(value, context)
    _require_exact_fields(document, DECLARATION_FIELDS, context)
    raw_cases = _require_list(document.get("cases"), f"{context}.cases")
    expected_cases = cast(list[dict[str, Any]], expected["cases"])
    if len(raw_cases) != len(expected_cases):
        raise ValueError(
            f"{context}.cases must contain exactly {len(expected_cases)} items"
        )
    for index, (raw_case, expected_case) in enumerate(
        zip(raw_cases, expected_cases, strict=True)
    ):
        case_context = f"{context}.cases[{index}]"
        case = _require_object(raw_case, case_context)
        _require_exact_fields(case, DECLARATION_CASE_FIELDS, case_context)
        if case.get("status") != DECLARATION_PLACEHOLDER:
            raise ValueError(
                f"{case_context}.status must equal {DECLARATION_PLACEHOLDER}"
            )
        if case != expected_case:
            raise ValueError(f"{case_context} does not match the report plan authority")
    if document != expected:
        raise ValueError(f"{context} does not match the report plan authority")


def materialize_declaration_cases(
    *,
    release_root: Path,
    report_plan: dict[str, Any],
    release_tag: str,
    git_sha: str,
    executed_case_ids: frozenset[str] = frozenset(),
) -> tuple[list[CaseRecord], dict[str, Coverage]]:
    _require_string(release_tag, "release_tag")
    if GIT_SHA_PATTERN.fullmatch(git_sha) is None:
        raise ValueError("git_sha must be 40 lowercase hexadecimal characters")
    _require_release_root(release_root)
    expanded = cast(list[dict[str, Any]], expand_declaration_cases(report_plan))
    reason = _require_string(expanded[0]["reason"], "declarations.reason")
    cases: list[CaseRecord] = []
    coverage: dict[str, Coverage] = {}
    for category in DECLARATION_CATEGORIES:
        authority_source_cases = [
            case for case in expanded if case.get("case_kind") == category
        ]
        source_cases = [
            case
            for case in authority_source_cases
            if case["case_id"] not in executed_case_ids
        ]
        relative_path = Path(f"{category}/cases.json")
        document = _declaration_document(
            category=category,
            source_cases=source_cases,
            release_tag=release_tag,
            git_sha=git_sha,
            reason=reason,
        )
        if source_cases:
            try:
                publish_json_once(
                    release_root=release_root,
                    relative_path=relative_path,
                    document=document,
                )
            except ValueError as publication_error:
                try:
                    existing = _load_release_json(release_root, relative_path)
                except ValueError as read_error:
                    raise publication_error from read_error
                _validate_declaration_document(
                    existing,
                    expected=document,
                    context=relative_path.as_posix(),
                )
                raise publication_error
        for source_case in source_cases:
            if source_case["case_id"] in executed_case_ids:
                continue
            cases.append(
                _case_record(
                    case_id=cast(str, source_case["case_id"]),
                    case_kind="execution_declaration",
                    run_id=DECLARATION_PLACEHOLDER,
                    status="未执行及原因",
                    started_at=DECLARATION_PLACEHOLDER,
                    finished_at=DECLARATION_PLACEHOLDER,
                    target=DECLARATION_TARGET,
                    command=DECLARATION_PLACEHOLDER,
                    evidence=relative_path,
                    reason=reason,
                    release_tag=release_tag,
                    git_sha=git_sha,
                )
            )
        coverage[f"{category}_declarations"] = {
            "expected": len(authority_source_cases),
            "observed": len(source_cases),
            "passed": 0,
        }
    return cases, coverage


def load_smoke_manifest(path: str | Path) -> list[dict[str, Any]]:
    manifest_path = Path(path)
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"failed to read smoke manifest: {manifest_path}") from exc
    try:
        loaded = strict_json_loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid smoke manifest {manifest_path}: {exc}") from exc

    document = _require_object(loaded, f"smoke manifest {manifest_path}")
    _require_exact_fields(document, SMOKE_MANIFEST_FIELDS, f"smoke manifest {manifest_path}")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError(f"smoke manifest {manifest_path}: schema_version must equal 1")
    raw_cases = _require_list(document["cases"], f"smoke manifest {manifest_path}: cases")
    if len(raw_cases) != 8:
        raise ValueError(f"smoke manifest {manifest_path}: cases must contain exactly 8 items")

    cases: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    seen_operator_codes: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        context = f"smoke manifest {manifest_path}: cases[{index}]"
        case = _require_object(raw_case, context)
        _require_exact_fields(case, SMOKE_MANIFEST_CASE_FIELDS, context)
        case_id = _require_string(case["case_id"], f"{context}.case_id")
        if SOURCE_CASE_ID_PATTERN.fullmatch(case_id) is None:
            raise ValueError(f"{context}.case_id has an invalid format")
        operator_code = _require_string(
            case["operator_code"], f"{context}.operator_code"
        )
        if OPERATOR_CODE_PATTERN.fullmatch(operator_code) is None:
            raise ValueError(f"{context}.operator_code has an invalid format")
        if case_id in seen_case_ids:
            raise ValueError(f"{context}.case_id is duplicate: {case_id}")
        if operator_code in seen_operator_codes:
            raise ValueError(f"{context}.operator_code is duplicate: {operator_code}")
        seen_case_ids.add(case_id)
        seen_operator_codes.add(operator_code)
        cases.append(
            {
                "case_id": case_id,
                "operator_code": operator_code,
                "fixtures": _require_unique_string_list(
                    case["fixtures"], f"{context}.fixtures", allow_empty=True
                ),
                "checks": _require_unique_string_list(
                    case["checks"], f"{context}.checks", allow_empty=False
                ),
            }
        )

    if seen_operator_codes != EXPECTED_SMOKE_OPERATOR_CODES:
        raise ValueError(
            "smoke manifest operator_code set does not match the 8-operator authority"
        )
    return cases


def instance_smoke_case_id(
    scope: str,
    instance_id: str,
    run_id: str,
    source_case_id: str,
) -> str:
    if type(scope) is not str or scope not in {"gpu", "cpu"}:
        raise ValueError("scope must be gpu or cpu")
    instance = _require_string(instance_id, "instance_id")
    run = _require_string(run_id, "run_id")
    source = _require_string(source_case_id, "source_case_id")
    digest = hashlib.sha256(f"{run}\0{source}".encode()).hexdigest()[:12]
    return f"SMOKE-{scope.upper()}-{instance}-{digest}"


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


def _same_filesystem_object(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


@contextmanager
def _release_directory_descriptor_access(
    release_root: Path, relative_path: Path
) -> Iterator[tuple[int, Callable[[], None]]]:
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"release directory path is unsafe: {relative_path}")

    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        with ExitStack() as descriptors:
            named_root = os.lstat(release_root)
            if stat.S_ISLNK(named_root.st_mode) or not stat.S_ISDIR(named_root.st_mode):
                raise ValueError(f"release root must be a real directory: {release_root}")
            current_descriptor = os.open(release_root, directory_flags)
            descriptors.callback(os.close, current_descriptor)
            opened_root = os.fstat(current_descriptor)
            if not stat.S_ISDIR(opened_root.st_mode) or not _same_filesystem_object(
                named_root, opened_root
            ):
                raise ValueError(f"release root changed while opening: {release_root}")

            bindings: list[tuple[int, str, int]] = []
            for part in relative_path.parts:
                named_directory = os.stat(
                    part,
                    dir_fd=current_descriptor,
                    follow_symlinks=False,
                )
                if stat.S_ISLNK(named_directory.st_mode) or not stat.S_ISDIR(
                    named_directory.st_mode
                ):
                    raise ValueError(
                        f"release source directory is unsafe: {relative_path}"
                    )
                next_descriptor = os.open(
                    part,
                    directory_flags,
                    dir_fd=current_descriptor,
                )
                descriptors.callback(os.close, next_descriptor)
                opened_directory = os.fstat(next_descriptor)
                if not stat.S_ISDIR(opened_directory.st_mode) or not (
                    _same_filesystem_object(named_directory, opened_directory)
                ):
                    raise ValueError(
                        "release source directory changed while opening: "
                        f"{relative_path}"
                    )
                bindings.append((current_descriptor, part, next_descriptor))
                current_descriptor = next_descriptor

            def verify_bindings() -> None:
                try:
                    for parent_descriptor, part, opened_descriptor in bindings:
                        named_directory = os.stat(
                            part,
                            dir_fd=parent_descriptor,
                            follow_symlinks=False,
                        )
                        opened_directory = os.fstat(opened_descriptor)
                        if (
                            stat.S_ISLNK(named_directory.st_mode)
                            or not stat.S_ISDIR(named_directory.st_mode)
                            or not _same_filesystem_object(
                                named_directory, opened_directory
                            )
                        ):
                            raise ValueError(
                                "release source directory changed during access: "
                                f"{relative_path}"
                            )
                    named_root_after = os.lstat(release_root)
                    if (
                        stat.S_ISLNK(named_root_after.st_mode)
                        or not stat.S_ISDIR(named_root_after.st_mode)
                        or not _same_filesystem_object(named_root_after, opened_root)
                    ):
                        raise ValueError(
                            f"release root changed during access: {release_root}"
                        )
                except OSError as exc:
                    raise ValueError(
                        f"failed to access release source directory: {relative_path}"
                    ) from exc

            yield current_descriptor, verify_bindings
    except OSError as exc:
        raise ValueError(
            f"failed to access release source directory: {relative_path}"
        ) from exc


@contextmanager
def _release_directory_descriptor(
    release_root: Path, relative_path: Path
) -> Iterator[int]:
    with _release_directory_descriptor_access(
        release_root, relative_path
    ) as directory_access:
        directory_descriptor, verify_bindings = directory_access
        yield directory_descriptor
        verify_bindings()


def _require_release_root(release_root: Path) -> None:
    with _release_directory_descriptor(release_root, Path()):
        pass


def _read_release_text(release_root: Path, relative_path: Path) -> str:
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"release source path escapes release root: {relative_path}")
    if not relative_path.parts:
        raise ValueError("release source path must not be empty")
    parent_path = Path(*relative_path.parts[:-1])
    source_name = relative_path.parts[-1]
    chunks: list[bytes] = []
    with _release_directory_descriptor(release_root, parent_path) as parent_descriptor:
        descriptor = -1
        try:
            metadata = os.stat(
                source_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise ValueError(
                    "release source must be a regular non-symlink file: "
                    f"{relative_path}"
                )
            descriptor = os.open(
                source_name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_descriptor,
            )
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise ValueError(f"release source must be a regular file: {relative_path}")
            if not _same_filesystem_object(metadata, opened):
                raise ValueError(f"release source changed while opening: {relative_path}")
            while chunk := os.read(descriptor, 65_536):
                chunks.append(chunk)
            named_after = os.stat(
                source_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (
                stat.S_ISLNK(named_after.st_mode)
                or not stat.S_ISREG(named_after.st_mode)
                or not _same_filesystem_object(named_after, opened)
            ):
                raise ValueError(f"release source changed while reading: {relative_path}")
        except OSError as exc:
            raise ValueError(f"failed to read release source: {relative_path}") from exc
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    raise ValueError(
                        f"failed to close release source: {relative_path}"
                    ) from exc
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


def _load_release_json_list(release_root: Path, relative_path: Path) -> list[Any]:
    text = _read_release_text(release_root, relative_path)
    try:
        loaded = strict_json_loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid JSON release source {relative_path}: {exc}") from exc
    return _require_list(loaded, f"release source {relative_path}")


def _release_source_metadata(
    release_root: Path, relative_path: Path
) -> os.stat_result:
    if relative_path.is_absolute() or ".." in relative_path.parts or not relative_path.parts:
        raise ValueError(f"release source path escapes release root: {relative_path}")
    parent_path = Path(*relative_path.parts[:-1])
    source_name = relative_path.parts[-1]
    with _release_directory_descriptor(release_root, parent_path) as parent_descriptor:
        try:
            metadata = os.stat(
                source_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise ValueError(f"failed to stat release source: {relative_path}") from exc
        _require_regular_source(metadata, relative_path)
        if metadata.st_nlink != 1:
            raise ValueError(f"release source must not be hard linked: {relative_path}")
        return metadata


def _execution_evidence_paths(
    value: object,
    *,
    category: str,
    case_id: str,
    context: str,
) -> list[Path]:
    raw_paths = _require_list(value, f"{context}.evidence")
    if not raw_paths:
        raise ValueError(f"{context}.evidence must contain raw execution evidence")
    paths: list[Path] = []
    expected_prefix = (category, "evidence", case_id)
    for index, raw_path in enumerate(raw_paths):
        relative = Path(_require_string(raw_path, f"{context}.evidence[{index}]"))
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or len(relative.parts) <= len(expected_prefix)
            or relative.parts[: len(expected_prefix)] != expected_prefix
            or relative.suffix != ".json"
        ):
            raise ValueError(
                f"{context}.evidence[{index}] must be JSON evidence below "
                f"{category}/evidence/{case_id}/"
            )
        paths.append(relative)
    if len(paths) != len(set(paths)):
        raise ValueError(f"{context}.evidence must contain unique paths")
    return paths


def collect_case_executions(
    *,
    release_root: Path,
    release_tag: str,
    git_sha: str,
) -> list[CaseRecord]:
    _require_string(release_tag, "release_tag")
    if GIT_SHA_PATTERN.fullmatch(git_sha) is None:
        raise ValueError("git_sha must be 40 lowercase hexadecimal characters")
    root_entries = _real_directory_entries(release_root, Path())
    loaded: list[tuple[str, Path, dict[str, Any], os.stat_result]] = []
    for category in DECLARATION_CATEGORIES:
        category_metadata = root_entries.get(category)
        if category_metadata is None:
            continue
        _require_real_subdirectory(category_metadata, Path(category))
        category_entries = _real_directory_entries(release_root, Path(category))
        executions_metadata = category_entries.get("executions")
        if executions_metadata is None:
            continue
        executions_root = Path(category) / "executions"
        _require_real_subdirectory(executions_metadata, executions_root)
        for name, metadata in sorted(
            _real_directory_entries(release_root, executions_root).items()
        ):
            relative_path = executions_root / name
            _require_regular_source(metadata, relative_path)
            if metadata.st_nlink != 1 or relative_path.suffix != ".json":
                raise ValueError(
                    f"execution record must be one regular JSON file: {relative_path}"
                )
            document = _load_release_json(release_root, relative_path)
            _require_exact_fields(document, EXECUTION_FIELDS, relative_path.as_posix())
            loaded.append((category, relative_path, document, metadata))

    case_ids = [
        _require_string(document.get("case_id"), f"{relative_path}.case_id")
        for _category, relative_path, document, _metadata in loaded
    ]
    duplicate_ids = sorted(
        case_id for case_id in set(case_ids) if case_ids.count(case_id) > 1
    )
    if duplicate_ids:
        raise ValueError(f"duplicate execution record case_id: {duplicate_ids}")

    cases: list[CaseRecord] = []
    for category, relative_path, document, execution_metadata in loaded:
        context = relative_path.as_posix()
        case_id = _require_string(document.get("case_id"), f"{context}.case_id")
        authority_category = DECLARATION_CATEGORY_BY_CASE_ID.get(case_id)
        if authority_category != category:
            raise ValueError(f"{context}.case_id does not belong to {category}")
        expected_path = Path(category) / "executions" / f"{case_id}.json"
        if relative_path != expected_path:
            raise ValueError(f"execution record path must equal {expected_path}")
        if type(document.get("schema_version")) is not int or document["schema_version"] != 2:
            raise ValueError(f"{context}.schema_version must equal 2")
        expected_type = f"{category}_case"
        if document.get("evidence_type") != expected_type:
            raise ValueError(f"{context}.evidence_type must equal {expected_type}")
        status = _require_string(document.get("status"), f"{context}.status")
        if status not in {"通过", "失败"}:
            raise ValueError(f"{context}.status must equal 通过 or 失败")
        if document.get("mock") is not False:
            raise ValueError(f"{context}.mock must be false")
        if (
            document.get("release_tag") != release_tag
            or document.get("git_sha") != git_sha
        ):
            raise ValueError(f"{context} release_tag/git_sha does not match release")
        command = _require_string(document.get("command"), f"{context}.command")
        if command == DECLARATION_PLACEHOLDER:
            raise ValueError(f"{context}.command must record the real command")
        evidence_paths = _execution_evidence_paths(
            document.get("evidence"),
            category=category,
            case_id=case_id,
            context=context,
        )
        for evidence_path in evidence_paths:
            evidence_metadata = _release_source_metadata(release_root, evidence_path)
            if _same_filesystem_object(execution_metadata, evidence_metadata):
                raise ValueError(
                    f"{evidence_path} must be raw evidence, not the execution record"
                )
            evidence = _load_release_json(release_root, evidence_path)
            validate_raw_execution_evidence(
                evidence,
                evidence_path.as_posix(),
                expected_case_id=case_id,
            )
            if (
                evidence.get("release_tag") != release_tag
                or evidence.get("git_sha") != git_sha
            ):
                raise ValueError(
                    f"{evidence_path} release_tag/git_sha does not match release"
                )
        cases.append(
            {
                "case_id": case_id,
                "source_case_id": case_id,
                "case_kind": EXECUTION_CASE_KIND_BY_CATEGORY[category],
                "run_id": case_id,
                "status": status,
                "started_at": _require_string(
                    document.get("started_at"), f"{context}.started_at"
                ),
                "finished_at": _require_string(
                    document.get("finished_at"), f"{context}.finished_at"
                ),
                "target": _require_string(document.get("target"), f"{context}.target"),
                "command": command,
                "evidence": [path.as_posix() for path in evidence_paths],
                "reason": _require_string(document.get("reason"), f"{context}.reason"),
                "mock": False,
                "release_tag": release_tag,
                "git_sha": git_sha,
            }
        )
    return cases


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

    status = _require_string(payload.get("status"), f"{context}: status")
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
    target_container = _require_string(
        target.get("container"), f"{context}.target.container"
    )
    if target_container == instance.service_name:
        return
    if CONTAINER_ID_PATTERN.fullmatch(target_container) is None:
        raise ValueError(f"{context}.target.container does not match inventory")
    if "container" not in payload and payload.get("status") == "FAIL":
        return
    nested = _require_object(payload.get("container"), f"{context}.container")
    if nested.get("id") != target_container:
        raise ValueError(f"{context}.target.container does not match container.id")


def _validate_container(
    value: object,
    instance: OperatorInstance,
    context: str,
) -> dict[str, Any]:
    container = _require_object(value, context)
    if container.get("instance_id") != instance.instance_id:
        raise ValueError(f"{context}.instance_id does not match inventory")
    container_id = _require_string(container.get("id"), f"{context}.id")
    if CONTAINER_ID_PATTERN.fullmatch(container_id) is None:
        raise ValueError(f"{context}.id must be a 64-character lowercase container ID")
    container_name = _require_string(container.get("name"), f"{context}.name")
    if container_name not in {instance.service_name, container_id}:
        raise ValueError(f"{context}.name does not match container id or inventory")
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
    *,
    require_run_id: bool,
) -> str | None:
    activity = _require_object(value, context)
    if activity.get("instance_id") != instance.instance_id:
        raise ValueError(f"{context}.instance_id does not match inventory")
    if activity.get("operator_code") != instance.operator_code:
        raise ValueError(f"{context}.operator_code does not match inventory")
    if "run_id" not in activity and not require_run_id:
        return None
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
        processes = _require_list(
            sample.get("processes"), f"{sample_context}.processes"
        )
        sample_host_pids: set[int] = set()
        for process_index, raw_process in enumerate(processes):
            process_context = f"{sample_context}.processes[{process_index}]"
            process = _require_object(raw_process, process_context)
            process_name = _require_string(
                process.get("process_name"), f"{process_context}.process_name"
            )
            if process_name != instance.process_name:
                raise ValueError(
                    f"{process_context}.process_name does not match inventory"
                )
            host_pid = _require_nonnegative_int(
                process.get("host_pid"), f"{process_context}.host_pid"
            )
            if host_pid == 0:
                raise ValueError(f"{process_context}.host_pid must be positive")
            mapping = _require_object(
                process.get("mapping"), f"{process_context}.mapping"
            )
            for field in ("docker_top", "cgroup_full_container_id"):
                if type(mapping.get(field)) is not bool or mapping[field] is not True:
                    raise ValueError(
                        f"{process_context}.mapping.{field} must be boolean true"
                    )
            raw_nspid = _require_list(
                mapping.get("nspid"), f"{process_context}.mapping.nspid"
            )
            if not raw_nspid:
                raise ValueError(
                    f"{process_context}.mapping.nspid must be a non-empty list"
                )
            namespace_pids = [
                _require_nonnegative_int(
                    namespace_pid,
                    f"{process_context}.mapping.nspid[{namespace_index}]",
                )
                for namespace_index, namespace_pid in enumerate(raw_nspid)
            ]
            if any(namespace_pid == 0 for namespace_pid in namespace_pids):
                raise ValueError(
                    f"{process_context}.mapping.nspid must contain positive integers"
                )
            if namespace_pids[0] != host_pid:
                raise ValueError(
                    f"{process_context}.mapping.nspid[0] must equal host_pid"
                )
            container_pid = _require_nonnegative_int(
                process.get("container_pid"), f"{process_context}.container_pid"
            )
            if container_pid == 0:
                raise ValueError(f"{process_context}.container_pid must be positive")
            if container_pid != namespace_pids[-1]:
                raise ValueError(
                    f"{process_context}.container_pid must equal mapping.nspid[-1]"
                )
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
        status_value = _require_string(payload.get("status"), f"{context}.status")
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
            _activity_run_id(
                payload["activity"],
                instance,
                f"{context}.activity",
                require_run_id=label == "running" and status_value == "PASS",
            )
        if "synchronous_samples" in payload:
            _sample_host_pids(
                payload["synchronous_samples"],
                instance,
                f"{context}.synchronous_samples",
            )

    running_target = _require_object(
        running.get("target"), f"{instance.instance_id}.running.target"
    )
    stopped_target = _require_object(
        stopped.get("target"), f"{instance.instance_id}.stopped.target"
    )
    if stopped_target["container"] != running_target["container"]:
        raise ValueError(
            f"{instance.instance_id}.stopped target.container does not match running"
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
            running.get("activity"),
            instance,
            f"{instance.instance_id}.running.activity",
            require_run_id=True,
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


def _smoke_authority(report_plan: dict[str, Any]) -> None:
    smoke = _require_object(report_plan.get("smoke"), "report_plan.smoke")
    expected = {
        "require_full",
        "require_gpu_linked_runs",
        "require_cpu_instances",
    }
    _require_exact_fields(smoke, expected, "report_plan.smoke")
    for field in sorted(expected):
        if type(smoke[field]) is not bool or smoke[field] is not True:
            raise ValueError(f"report_plan.smoke.{field} must be true")


def _smoke_manifest_index(
    smoke_manifest: list[dict[str, Any]], inventory: OperatorInventory
) -> dict[str, dict[str, Any]]:
    if type(smoke_manifest) is not list or len(smoke_manifest) != 8:
        raise ValueError("smoke_manifest must contain exactly 8 cases")
    by_operator: dict[str, dict[str, Any]] = {}
    source_ids: set[str] = set()
    for index, raw_case in enumerate(smoke_manifest):
        context = f"smoke_manifest[{index}]"
        case = _require_object(raw_case, context)
        _require_exact_fields(case, SMOKE_MANIFEST_CASE_FIELDS, context)
        case_id = _require_string(case["case_id"], f"{context}.case_id")
        operator_code = _require_string(
            case["operator_code"], f"{context}.operator_code"
        )
        fixtures = _require_unique_string_list(
            case["fixtures"], f"{context}.fixtures", allow_empty=True
        )
        checks = _require_unique_string_list(
            case["checks"], f"{context}.checks", allow_empty=False
        )
        if case_id in source_ids:
            raise ValueError(f"{context}.case_id is duplicate: {case_id}")
        if operator_code in by_operator:
            raise ValueError(f"{context}.operator_code is duplicate: {operator_code}")
        source_ids.add(case_id)
        by_operator[operator_code] = {
            "case_id": case_id,
            "operator_code": operator_code,
            "fixtures": fixtures,
            "checks": checks,
        }
    inventory_codes = {instance.operator_code for instance in inventory.instances}
    if set(by_operator) != inventory_codes or inventory_codes != EXPECTED_SMOKE_OPERATOR_CODES:
        raise ValueError("smoke manifest operator set does not match Compose inventory")
    return by_operator


def _smoke_evidence_status(value: object, context: str) -> str:
    raw_status = _require_string(value, f"{context}.status")
    mapping = {
        "PASS": "通过",
        "失败": "失败",
        "未执行及原因": "未执行及原因",
    }
    if raw_status not in mapping:
        raise ValueError(f"{context}.status is unsupported: {raw_status}")
    return mapping[raw_status]


def _validate_smoke_evidence(
    payload: dict[str, Any],
    *,
    context: str,
    manifest_case: dict[str, Any],
    expected_target: str,
    release_tag: str,
    git_sha: str,
) -> tuple[str, str | None]:
    actual_fields = set(payload)
    allowed_fields = SMOKE_EVIDENCE_BASE_FIELDS | {"summary", "reason"}
    if not SMOKE_EVIDENCE_BASE_FIELDS.issubset(actual_fields) or not actual_fields.issubset(
        allowed_fields
    ):
        raise ValueError(
            f"{context} fields invalid: "
            f"missing={sorted(SMOKE_EVIDENCE_BASE_FIELDS - actual_fields)}, "
            f"unknown={sorted(actual_fields - allowed_fields)}"
        )
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError(f"{context}.schema_version must equal 1")
    if payload["evidence_type"] != "operator_smoke":
        raise ValueError(f"{context}.evidence_type must equal operator_smoke")
    if payload["operator_code"] != manifest_case["operator_code"]:
        raise ValueError(f"{context}.operator_code does not match manifest")
    if payload["target"] != expected_target:
        raise ValueError(f"{context}.target does not match expected target")
    if payload["checks"] != manifest_case["checks"]:
        raise ValueError(f"{context}.checks do not match manifest")
    if type(payload["mock"]) is not bool or payload["mock"] is not False:
        raise ValueError(f"{context}.mock must be false")
    if payload["release_tag"] != release_tag:
        raise ValueError(f"{context}.release_tag does not match current release")
    if payload["git_sha"] != git_sha:
        raise ValueError(f"{context}.git_sha does not match current release")

    status = _smoke_evidence_status(payload["status"], context)
    if status == "通过":
        summary = _require_object(payload.get("summary"), f"{context}.summary")
        if not summary:
            raise ValueError(f"{context}.summary must not be empty")
        attempts = _require_list(
            summary.get("attempts"), f"{context}.summary.attempts"
        )
        if not attempts:
            raise ValueError(f"{context}.summary.attempts must not be empty")
        for index, attempt in enumerate(attempts):
            attempt_payload = _require_object(
                attempt, f"{context}.summary.attempts[{index}]"
            )
            if not attempt_payload:
                raise ValueError(
                    f"{context}.summary.attempts[{index}] must not be empty"
                )
        if "reason" in payload:
            raise ValueError(f"{context}.reason is not allowed for PASS")
        return status, None
    reason = _require_string(payload.get("reason"), f"{context}.reason")
    if status == "未执行及原因" and "summary" in payload:
        raise ValueError(f"{context}.summary is not allowed when unexecuted")
    if "summary" in payload:
        _require_object(payload["summary"], f"{context}.summary")
    return status, reason


def _validate_smoke_logical_case(
    raw_case: object,
    *,
    context: str,
    manifest_case: dict[str, Any],
    expected_target: str,
    expected_evidence: Path,
    evidence_status: str,
    evidence_reason: str | None,
    release_tag: str,
    git_sha: str,
) -> dict[str, Any]:
    case = _require_object(raw_case, context)
    _require_exact_fields(case, SMOKE_LOGICAL_CASE_FIELDS, context)
    if case["case_id"] != manifest_case["case_id"]:
        raise ValueError(f"{context}.case_id does not match manifest")
    status = _require_string(case["status"], f"{context}.status")
    if status != evidence_status:
        raise ValueError(f"{context}.status does not match evidence")
    for field in ("started_at", "finished_at", "command", "reason"):
        _require_string(case[field], f"{context}.{field}")
    if case["target"] != expected_target:
        raise ValueError(f"{context}.target does not match expected target")
    if type(case["mock"]) is not bool or case["mock"] is not False:
        raise ValueError(f"{context}.mock must be false")
    if case["release_tag"] != release_tag:
        raise ValueError(f"{context}.release_tag does not match current release")
    if case["git_sha"] != git_sha:
        raise ValueError(f"{context}.git_sha does not match current release")
    evidence = _require_list(case["evidence"], f"{context}.evidence")
    evidence_paths = [
        _require_string(item, f"{context}.evidence[{index}]")
        for index, item in enumerate(evidence)
    ]
    expected_path = expected_evidence.as_posix()
    allowed_evidence = [[], [expected_path]] if status == "未执行及原因" else [[expected_path]]
    if evidence_paths not in allowed_evidence:
        raise ValueError(f"{context}.evidence does not match {expected_path}")
    if evidence_reason is not None and case["reason"] != evidence_reason:
        raise ValueError(f"{context}.reason does not match evidence")
    return case


def _smoke_case_record(
    logical_case: dict[str, Any],
    *,
    case_id: str,
    source_case_id: str,
    case_kind: str,
    run_id: str,
    evidence: Path,
) -> CaseRecord:
    return {
        "case_id": case_id,
        "source_case_id": source_case_id,
        "case_kind": case_kind,
        "run_id": run_id,
        "status": cast(str, logical_case["status"]),
        "started_at": cast(str, logical_case["started_at"]),
        "finished_at": cast(str, logical_case["finished_at"]),
        "target": cast(str, logical_case["target"]),
        "command": cast(str, logical_case["command"]),
        "evidence": [evidence.as_posix()],
        "reason": cast(str, logical_case["reason"]),
        "mock": False,
        "release_tag": cast(str, logical_case["release_tag"]),
        "git_sha": cast(str, logical_case["git_sha"]),
    }


def _real_directory_entries(
    release_root: Path, relative_path: Path
) -> dict[str, os.stat_result]:
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"release directory path is unsafe: {relative_path}")
    with _release_directory_descriptor(release_root, relative_path) as directory_descriptor:
        try:
            return {
                name: os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                for name in os.listdir(directory_descriptor)
            }
        except OSError as exc:
            raise ValueError(f"failed to scan release directory: {relative_path}") from exc


def _require_real_subdirectory(
    metadata: os.stat_result, relative_path: Path
) -> None:
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"release source must be a real directory: {relative_path}")


def _require_regular_source(metadata: os.stat_result, relative_path: Path) -> None:
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(
            f"release source must be a regular non-symlink file: {relative_path}"
        )


def _safe_run_id(value: object, context: str) -> str:
    run_id = _require_string(value, context)
    if run_id in {".", ".."} or RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError(f"{context} is an unsafe run ID")
    return run_id


def _collect_instance_smoke_runs(
    *,
    release_root: Path,
    inventory: OperatorInventory,
    manifest_by_operator: dict[str, dict[str, Any]],
    release_tag: str,
    git_sha: str,
) -> dict[tuple[str, str], InstanceSmokeRun]:
    instances_root = Path("smoke/instances")
    instance_entries = _real_directory_entries(release_root, instances_root)
    inventory_by_id = {instance.instance_id: instance for instance in inventory.instances}
    unknown_instances = sorted(set(instance_entries) - set(inventory_by_id))
    if unknown_instances:
        raise ValueError(f"unknown instance Smoke directories: {unknown_instances}")

    runs: dict[tuple[str, str], InstanceSmokeRun] = {}
    for instance_id in sorted(instance_entries):
        instance_path = instances_root / instance_id
        _require_real_subdirectory(instance_entries[instance_id], instance_path)
        instance = inventory_by_id[instance_id]
        instance_children = _real_directory_entries(release_root, instance_path)
        if set(instance_children) != {"runs"}:
            raise ValueError(
                f"{instance_path} must contain only the canonical runs directory"
            )
        runs_path = instance_path / "runs"
        _require_real_subdirectory(instance_children["runs"], runs_path)
        run_entries = _real_directory_entries(release_root, runs_path)
        for raw_run_id in sorted(run_entries):
            run_id = _safe_run_id(raw_run_id, f"{runs_path}.run_id")
            run_path = runs_path / run_id
            _require_real_subdirectory(run_entries[raw_run_id], run_path)
            run_children = _real_directory_entries(release_root, run_path)
            expected_names = {"cases.json", f"{instance.operator_code}.json"}
            actual_names = set(run_children)
            if actual_names != expected_names:
                raise ValueError(
                    f"{run_path} canonical sources invalid: "
                    f"missing={sorted(expected_names - actual_names)}, "
                    f"extra={sorted(actual_names - expected_names)}"
                )
            for source_name in sorted(expected_names):
                _require_regular_source(run_children[source_name], run_path / source_name)

            manifest_case = manifest_by_operator[instance.operator_code]
            evidence_path = run_path / f"{instance.operator_code}.json"
            evidence_payload = _load_release_json(release_root, evidence_path)
            evidence_status, evidence_reason = _validate_smoke_evidence(
                evidence_payload,
                context=evidence_path.as_posix(),
                manifest_case=manifest_case,
                expected_target=instance_id,
                release_tag=release_tag,
                git_sha=git_sha,
            )
            logical_path = run_path / "cases.json"
            logical_cases = _load_release_json_list(release_root, logical_path)
            if len(logical_cases) != 1:
                raise ValueError(f"{logical_path} must contain exactly one logical case")
            logical_case = _validate_smoke_logical_case(
                logical_cases[0],
                context=f"{logical_path.as_posix()}[0]",
                manifest_case=manifest_case,
                expected_target=instance_id,
                expected_evidence=evidence_path,
                evidence_status=evidence_status,
                evidence_reason=evidence_reason,
                release_tag=release_tag,
                git_sha=git_sha,
            )
            scope = "gpu" if instance.physical_gpu is not None else "cpu"
            source_case_id = cast(str, manifest_case["case_id"])
            key = (instance_id, run_id)
            if key in runs:
                raise ValueError(f"duplicate instance Smoke run: {instance_id}/{run_id}")
            runs[key] = InstanceSmokeRun(
                instance=instance,
                run_id=run_id,
                status=evidence_status,
                case=_smoke_case_record(
                    logical_case,
                    case_id=instance_smoke_case_id(
                        scope, instance_id, run_id, source_case_id
                    ),
                    source_case_id=source_case_id,
                    case_kind=(
                        "smoke_gpu_trigger" if scope == "gpu" else "smoke_cpu_instance"
                    ),
                    run_id=run_id,
                    evidence=evidence_path,
                ),
            )
    return runs


def _cpu_smoke_coverage(
    inventory: OperatorInventory,
    runs: dict[tuple[str, str], InstanceSmokeRun],
) -> Coverage:
    cpu_instance_ids = {instance.instance_id for instance in inventory.cpu_instances}
    observed_instance_ids = {
        run.instance.instance_id
        for run in runs.values()
        if run.instance.physical_gpu is None
    }
    missing = sorted(cpu_instance_ids - observed_instance_ids)
    if missing:
        raise ValueError(f"CPU instances are missing real Smoke runs: {missing}")
    passed_instance_ids = {
        run.instance.instance_id
        for run in runs.values()
        if run.instance.physical_gpu is None and run.status == "通过"
    }
    return {
        "expected": len(cpu_instance_ids),
        "observed": len(observed_instance_ids),
        "passed": len(passed_instance_ids),
    }


def _gpu_smoke_coverage(
    *,
    release_root: Path,
    inventory: OperatorInventory,
    runs: dict[tuple[str, str], InstanceSmokeRun],
    git_sha: str,
) -> Coverage:
    observed = 0
    passed = 0
    for instance in inventory.gpu_instances:
        running_path = Path(f"gpu-instances/{instance.instance_id}.json")
        running = _load_release_json(release_root, running_path)
        context = f"{instance.instance_id}.running"
        if type(running.get("schema_version")) is not int or running["schema_version"] != 1:
            raise ValueError(f"{context}.schema_version must equal 1")
        if running.get("mode") != "running-inference":
            raise ValueError(f"{context}.mode must equal running-inference")
        _validate_gpu_target(running, instance, context)
        status = _require_string(running.get("status"), f"{context}.status")
        if status not in {"PASS", "FAIL"}:
            raise ValueError(f"{context}.status must be PASS or FAIL")
        if "release_sha" in running and running["release_sha"] != git_sha:
            raise ValueError(f"{context}.release_sha does not match current release")
        if status == "PASS" and running.get("release_sha") != git_sha:
            raise ValueError(f"{context} PASS requires current release_sha")
        if status == "FAIL":
            _require_string(running.get("reason"), f"{context}.reason")

        activity = running.get("activity")
        activity_run_id = (
            _activity_run_id(
                activity,
                instance,
                f"{context}.activity",
                require_run_id=status == "PASS",
            )
            if activity is not None
            else None
        )
        if status == "PASS" and activity_run_id is None:
            raise ValueError(f"{context} PASS requires activity.run_id")
        if activity_run_id is None:
            continue
        run_id = _safe_run_id(activity_run_id, f"{context}.activity.run_id")
        linked = runs.get((instance.instance_id, run_id))
        if linked is None:
            raise ValueError(
                f"{context}.activity.run_id has no matching instance Smoke run: {run_id}"
            )
        required_status = "通过" if status == "PASS" else "失败"
        if linked.status != required_status:
            raise ValueError(
                f"{context} {status} must link a {required_status} Smoke run, "
                f"got {linked.status}"
            )
        observed += 1
        if status == "PASS":
            passed += 1
    return {
        "expected": len(inventory.gpu_instances),
        "observed": observed,
        "passed": passed,
    }


def collect_smoke_cases(
    *,
    release_root: Path,
    inventory: OperatorInventory,
    report_plan: dict[str, Any],
    smoke_manifest: list[dict[str, Any]],
    release_tag: str,
    git_sha: str,
) -> tuple[list[CaseRecord], dict[str, Coverage]]:
    _require_string(release_tag, "release_tag")
    if GIT_SHA_PATTERN.fullmatch(git_sha) is None:
        raise ValueError("git_sha must be 40 lowercase hexadecimal characters")
    _require_release_root(release_root)
    _smoke_authority(report_plan)
    manifest_by_operator = _smoke_manifest_index(smoke_manifest, inventory)

    logical_path = Path("smoke/cases.json")
    logical_cases = _load_release_json_list(release_root, logical_path)
    if len(logical_cases) != len(manifest_by_operator):
        raise ValueError("smoke/cases.json must contain exactly 8 manifest cases")
    logical_by_source: dict[str, object] = {}
    for index, raw_case in enumerate(logical_cases):
        case = _require_object(raw_case, f"{logical_path.as_posix()}[{index}]")
        source_case_id = _require_string(
            case.get("case_id"), f"{logical_path.as_posix()}[{index}].case_id"
        )
        if source_case_id in logical_by_source:
            raise ValueError(f"smoke/cases.json duplicate case_id: {source_case_id}")
        logical_by_source[source_case_id] = raw_case
    expected_source_ids = {
        manifest_case["case_id"] for manifest_case in manifest_by_operator.values()
    }
    if set(logical_by_source) != expected_source_ids:
        raise ValueError("smoke/cases.json case IDs do not exactly match smoke manifest")

    cases: list[CaseRecord] = []
    statuses: list[str] = []
    for operator_code in sorted(manifest_by_operator):
        manifest_case = manifest_by_operator[operator_code]
        source_case_id = cast(str, manifest_case["case_id"])
        evidence_path = Path(f"smoke/{operator_code}.json")
        evidence_payload = _load_release_json(release_root, evidence_path)
        evidence_status, evidence_reason = _validate_smoke_evidence(
            evidence_payload,
            context=evidence_path.as_posix(),
            manifest_case=manifest_case,
            expected_target=operator_code,
            release_tag=release_tag,
            git_sha=git_sha,
        )
        logical_case = _validate_smoke_logical_case(
            logical_by_source[source_case_id],
            context=f"smoke/cases.json[{source_case_id}]",
            manifest_case=manifest_case,
            expected_target=operator_code,
            expected_evidence=evidence_path,
            evidence_status=evidence_status,
            evidence_reason=evidence_reason,
            release_tag=release_tag,
            git_sha=git_sha,
        )
        statuses.append(evidence_status)
        cases.append(
            _smoke_case_record(
                logical_case,
                case_id=f"SMOKE-FULL-{source_case_id}",
                source_case_id=source_case_id,
                case_kind="smoke_full",
                run_id="",
                evidence=evidence_path,
            )
        )
    runs = _collect_instance_smoke_runs(
        release_root=release_root,
        inventory=inventory,
        manifest_by_operator=manifest_by_operator,
        release_tag=release_tag,
        git_sha=git_sha,
    )
    cases.extend(run.case for run in runs.values())
    case_ids = [case["case_id"] for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("generated Smoke case_id collision or duplicate")
    return cases, {
        "smoke_full": {
            "expected": len(manifest_by_operator),
            "observed": len(statuses),
            "passed": statuses.count("通过"),
        },
        "smoke_gpu_trigger": _gpu_smoke_coverage(
            release_root=release_root,
            inventory=inventory,
            runs=runs,
            git_sha=git_sha,
        ),
        "smoke_cpu_instance": _cpu_smoke_coverage(inventory, runs),
    }


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
        activity_run_id = (
            _activity_run_id(
                activity,
                instance,
                f"{instance.instance_id}.running.activity",
                require_run_id=running["status"] == "PASS",
            )
            if activity is not None
            else None
        )
        run_id = activity_run_id or f"gpu-{instance.instance_id}"
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


def _case_sort_key(case: CaseRecord) -> tuple[int, str, str, str]:
    case_kind = case["case_kind"]
    evidence = case["evidence"]
    if case_kind.startswith("registration_"):
        group = 0
    elif case_kind == "gpu_running":
        group = 1
    elif case_kind == "gpu_stopped":
        group = 2
    elif case_kind == "smoke_full":
        group = 3
    elif case_kind in {"smoke_gpu_trigger", "smoke_cpu_instance"}:
        group = 4
    elif case_kind == "negative_execution":
        group = 5
    elif case_kind == "execution_declaration" and evidence == [
        "negative/cases.json"
    ]:
        group = 5
    elif case_kind == "load_execution":
        group = 6
    elif case_kind == "execution_declaration" and evidence == ["load/cases.json"]:
        group = 6
    else:
        raise ValueError(
            f"case has an unknown aggregation source: {case['case_id']}"
        )
    return group, case["target"], case["run_id"], case["case_id"]


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
    parser.add_argument("--require-all-executed", action="store_true")
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        inventory = load_operator_inventory(arguments.operator_compose)
        try:
            plan_bytes = arguments.report_plan.read_bytes()
        except OSError as exc:
            raise ValueError(
                f"failed to read report plan bytes: {arguments.report_plan}"
            ) from exc
        report_plan = load_report_plan_bytes(plan_bytes)
        smoke_manifest = load_smoke_manifest(arguments.smoke_manifest)
        release_tag = arguments.release_root.parent.name
        git_sha = arguments.release_root.name
        if arguments.release_root.parent.parent.name != "releases":
            raise ValueError("release root must use releases/<release_tag>/<git_sha>")
        expected_output = arguments.release_root / "summary" / "cases.json"
        if arguments.output != expected_output:
            raise ValueError(f"--output must equal {expected_output}")
        registration_cases, registration_coverage = collect_registration_gpu_cases(
            release_root=arguments.release_root,
            inventory=inventory,
            report_plan=report_plan,
            release_tag=release_tag,
            git_sha=git_sha,
        )
        smoke_cases, smoke_coverage = collect_smoke_cases(
            release_root=arguments.release_root,
            inventory=inventory,
            report_plan=report_plan,
            smoke_manifest=smoke_manifest,
            release_tag=release_tag,
            git_sha=git_sha,
        )
        execution_cases = collect_case_executions(
            release_root=arguments.release_root,
            release_tag=release_tag,
            git_sha=git_sha,
        )
        executed_case_ids = frozenset(case["case_id"] for case in execution_cases)
        declaration_cases, declaration_coverage = materialize_declaration_cases(
            release_root=arguments.release_root,
            report_plan=report_plan,
            release_tag=release_tag,
            git_sha=git_sha,
            executed_case_ids=executed_case_ids,
        )
        if getattr(arguments, "require_all_executed", False) and declaration_cases:
            raise ValueError(
                "--require-all-executed rejects remaining execution_declaration cases"
            )
        schema_version = 2 if execution_cases else 1
        if schema_version == 1:
            coverage = {
                **registration_coverage,
                **smoke_coverage,
                **declaration_coverage,
            }
        else:
            negative_expected = sum(
                category == "negative"
                for category in DECLARATION_CATEGORY_BY_CASE_ID.values()
            )
            load_expected = sum(
                category == "load"
                for category in DECLARATION_CATEGORY_BY_CASE_ID.values()
            )
            coverage = {
                **registration_coverage,
                **smoke_coverage,
                "negative_cases": {
                    "expected": negative_expected,
                    "observed": sum(
                        case["case_id"] in DECLARATION_CATEGORY_BY_CASE_ID
                        and DECLARATION_CATEGORY_BY_CASE_ID[case["case_id"]]
                        == "negative"
                        for case in declaration_cases + execution_cases
                    ),
                    "passed": sum(
                        case["case_kind"] == "negative_execution"
                        and case["status"] == "通过"
                        for case in execution_cases
                    ),
                },
                "load_cases": {
                    "expected": load_expected,
                    "observed": sum(
                        case["case_id"] in DECLARATION_CATEGORY_BY_CASE_ID
                        and DECLARATION_CATEGORY_BY_CASE_ID[case["case_id"]] == "load"
                        for case in declaration_cases + execution_cases
                    ),
                    "passed": sum(
                        case["case_kind"] == "load_execution"
                        and case["status"] == "通过"
                        for case in execution_cases
                    ),
                },
            }
        cases = sorted(
            registration_cases + smoke_cases + execution_cases + declaration_cases,
            key=_case_sort_key,
        )
        envelope = {
            "schema_version": schema_version,
            "release_tag": release_tag,
            "git_sha": git_sha,
            "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
            "coverage": coverage,
            "cases": cases,
        }
        validate_cases_envelope(envelope)
        publish_json_once(
            release_root=arguments.release_root,
            relative_path=Path("summary/cases.json"),
            document=envelope,
        )
        return 0
    except (OSError, ValueError) as exc:
        print(f"milestone 2B aggregation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
