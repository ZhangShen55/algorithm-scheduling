#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SNAPSHOT_NAME = "existing-containers.jsonl"
PAUSED_NAME = f"{SNAPSHOT_NAME}.paused.jsonl"
PROVENANCE_NAME = "operator-maintenance-provenance.json"
PREDECESSOR_NAME = "operator-maintenance-predecessor.json"
OPERATOR_BASELINE_NAME = "baseline-operator-container-ids.txt"
OPERATOR_NEW_NAME = "new-operator-container-ids.txt"
ARCHIVE_METADATA_SUFFIX = ".archive.json"
COMPLETED_ARCHIVE_PATTERN = re.compile(
    rf"^{re.escape(PAUSED_NAME)}\.audit\.[0-9a-f]{{32}}\.jsonl$"
)
SNAPSHOT_KEYS = {
    "compose_project",
    "container_id",
    "image_id",
    "image_ref",
    "labels",
    "mounts",
    "name",
    "ports",
    "restart_policy",
    "state",
}
PAUSE_ENTRY_KEYS = {
    "binding",
    "container_id",
    "name",
    "policy_neutralized",
    "snapshot_sha256",
    "status",
    "version",
}
TERMINAL_PAUSE_STATUSES = {"not_stopped", "restored"}
PROVENANCE_KEYS = {
    "authoritative_paused_ledger",
    "authoritative_snapshot",
    "source_git_sha",
    "source_release_root",
}
PREDECESSOR_KEYS = {
    "predecessor_git_sha",
    "predecessor_release_root",
}


class LifecycleError(RuntimeError):
    pass


def _reject_control_characters(value: str, label: str) -> None:
    if any(character in value for character in "\n\r\t\0"):
        raise LifecycleError(f"{label} contains forbidden control characters")


def _lstat(path: Path, label: str) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as error:
        raise LifecycleError(f"cannot inspect {label}: {path}: {error}") from error


def _require_owned_directory(path: Path, label: str) -> os.stat_result:
    metadata = _lstat(path, label)
    if not stat.S_ISDIR(metadata.st_mode):
        raise LifecycleError(f"{label} must be a non-symlink directory: {path}")
    if metadata.st_uid != os.geteuid():
        raise LifecycleError(f"{label} must be owned by the deployment identity: {path}")
    return metadata


def _require_owned_file(path: Path, label: str) -> os.stat_result:
    metadata = _lstat(path, label)
    if not stat.S_ISREG(metadata.st_mode):
        raise LifecycleError(f"{label} must be a non-symlink regular file: {path}")
    if metadata.st_uid != os.geteuid():
        raise LifecycleError(f"{label} must be owned by the deployment identity: {path}")
    return metadata


def _require_single_link_file(
    path: Path,
    label: str,
    *,
    expected_mode: int,
) -> os.stat_result:
    metadata = _require_owned_file(path, label)
    if metadata.st_nlink != 1:
        raise LifecycleError(f"{label} must have exactly one directory entry")
    if stat.S_IMODE(metadata.st_mode) != expected_mode:
        raise LifecycleError(f"{label} must have mode {expected_mode:04o}")
    return metadata


class ReleaseLayout:
    def __init__(self, report_root: str, release_tag: str) -> None:
        _reject_control_characters(report_root, "REPORT_ROOT")
        _reject_control_characters(release_tag, "RELEASE_TAG")
        self.report_root = Path(report_root)
        self.release_tag = release_tag
        self.release_tag_root = (
            self.report_root / "milestone-2b" / "releases" / release_tag
        )

    def validate_release_root(self, raw_path: str, label: str) -> Path:
        _reject_control_characters(raw_path, label)
        path = Path(raw_path)
        if not path.is_absolute():
            raise LifecycleError(f"{label} must be absolute: {path}")
        if not SHA_PATTERN.fullmatch(path.name):
            raise LifecycleError(f"{label} must end in a lowercase 40-character Git SHA")
        if path != self.release_tag_root / path.name:
            raise LifecycleError(f"{label} must belong to the same REPORT_ROOT/release tag")
        _require_owned_directory(path, label)
        return path

    def validate_maintenance_directory(self, release_root: Path) -> Path:
        directory = release_root / "container-maintenance"
        _require_owned_directory(directory, "container-maintenance")
        if directory.parent != release_root:
            raise LifecycleError("container-maintenance escaped its release root")
        return directory


def _path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise LifecycleError(f"cannot read {label}: {error}") from error
    for line_number, line in enumerate(lines, 1):
        try:
            record: Any = json.loads(line)
        except json.JSONDecodeError as error:
            raise LifecycleError(
                f"{label} contains malformed JSON on line {line_number}: {error}"
            ) from error
        if not isinstance(record, dict):
            raise LifecycleError(f"{label} line {line_number} must be an object")
        records.append(record)
    return records


def _normalized_mounts(record: dict[str, Any]) -> list[dict[str, Any]]:
    mounts = record.get("Mounts", [])
    if not isinstance(mounts, list):
        raise LifecycleError("Docker inspect returned invalid mount metadata")
    return [
        {
            "destination": mount.get("Destination"),
            "mode": mount.get("Mode"),
            "propagation": mount.get("Propagation"),
            "rw": mount.get("RW"),
            "source": mount.get("Source"),
            "type": mount.get("Type"),
        }
        for mount in mounts
        if isinstance(mount, dict)
    ]


def _current_container_binding(
    expected: dict[str, Any],
) -> dict[str, Any]:
    container_id = expected["container_id"]
    try:
        completed = subprocess.run(
            ["docker", "inspect", container_id],
            check=True,
            text=True,
            capture_output=True,
        )
        payload: Any = json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        raise LifecycleError(
            f"cannot verify restored container {container_id}: {error}"
        ) from error
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
        or payload[0].get("Id") != container_id
    ):
        raise LifecycleError("Docker inspect did not return the exact restored container")
    record = payload[0]
    labels = record.get("Config", {}).get("Labels") or {}
    if not isinstance(labels, dict):
        raise LifecycleError("Docker inspect returned invalid restored-container labels")
    return {
        "compose_project": labels.get("com.docker.compose.project", ""),
        "container_id": record.get("Id"),
        "image_id": record.get("Image"),
        "image_ref": record.get("Config", {}).get("Image"),
        "labels": labels,
        "mounts": _normalized_mounts(record),
        "name": str(record.get("Name", "")).removeprefix("/"),
        "ports": record.get("HostConfig", {}).get("PortBindings") or {},
        "restart_policy": record.get("HostConfig", {}).get("RestartPolicy") or {},
        "state": record.get("State", {}).get("Status", ""),
    }


def _validate_completed_maintenance(
    layout: ReleaseLayout,
    release_root: Path,
    *,
    successor_binding: dict[str, Any] | None = None,
) -> dict[str, str]:
    directory = layout.validate_maintenance_directory(release_root)
    snapshot = directory / SNAPSHOT_NAME
    paused = directory / PAUSED_NAME
    metadata_path = Path(f"{paused}{ARCHIVE_METADATA_SUFFIX}")
    if _path_exists(metadata_path):
        raise LifecycleError("completed maintenance has residual archive metadata")

    candidates = [
        entry
        for entry in directory.iterdir()
        if entry.name.startswith(f"{PAUSED_NAME}.audit.")
    ]
    if len(candidates) != 1 or not COMPLETED_ARCHIVE_PATTERN.fullmatch(
        candidates[0].name
    ):
        raise LifecycleError(
            "completed maintenance requires exactly one canonical audit archive"
        )
    archive = candidates[0]
    _require_single_link_file(snapshot, "completed maintenance snapshot", expected_mode=0o600)
    _require_single_link_file(archive, "completed maintenance audit", expected_mode=0o400)

    snapshots = _read_jsonl(snapshot, "completed maintenance snapshot")
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for line_number, record in enumerate(snapshots, 1):
        if not SNAPSHOT_KEYS.issubset(record):
            raise LifecycleError(
                f"completed maintenance snapshot line {line_number} is incomplete"
            )
        container_id = record.get("container_id")
        name = record.get("name")
        if (
            not isinstance(container_id, str)
            or not re.fullmatch(r"[0-9a-f]{12,64}", container_id)
            or not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name)
            or container_id in by_id
            or name in by_name
        ):
            raise LifecycleError("completed maintenance snapshot has invalid identity data")
        labels = record.get("labels")
        if not isinstance(labels, dict) or record.get("compose_project") != labels.get(
            "com.docker.compose.project", ""
        ):
            raise LifecycleError("completed maintenance snapshot has invalid Compose identity")
        by_id[container_id] = record
        by_name[name] = record

    selected = by_name.get("ocr-v6-amd")
    if selected is None:
        raise LifecycleError("completed maintenance snapshot omits ocr-v6-amd")
    entries = _read_jsonl(archive, "completed maintenance audit")
    if not entries:
        if selected.get("state") == "running":
            raise LifecycleError(
                "completed maintenance audit is empty for an originally running container"
            )
        expected_current = selected
    else:
        if len(entries) != 1:
            raise LifecycleError("completed maintenance audit contains unexpected selectors")
        entry = entries[0]
        if (
            not PAUSE_ENTRY_KEYS.issubset(entry)
            or entry.get("version") != 1
            or entry.get("status") not in TERMINAL_PAUSE_STATUSES
            or entry.get("policy_neutralized") is not False
            or entry.get("name") != "ocr-v6-amd"
            or entry.get("container_id") != selected["container_id"]
            or entry.get("binding") != selected
        ):
            raise LifecycleError("completed maintenance audit is not terminal")
        digest = hashlib.sha256(
            json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if entry.get("snapshot_sha256") != digest:
            raise LifecycleError("completed maintenance audit snapshot hash differs")
        expected_current = {**selected, "state": "running"}
        if selected.get("state") != "running":
            raise LifecycleError("restored container does not match its original running state")

    if successor_binding is not None:
        if successor_binding != expected_current:
            raise LifecycleError(
                "active maintenance snapshot differs from completed predecessor binding"
            )
    elif _current_container_binding(selected) != expected_current:
        raise LifecycleError("restored container differs from completed maintenance binding")

    return {
        "authoritative_paused_ledger": str(paused),
        "authoritative_snapshot": str(snapshot),
        "source_git_sha": release_root.name,
        "source_release_root": str(release_root),
    }


def _validate_active_maintenance(
    layout: ReleaseLayout,
    release_root: Path,
) -> dict[str, Any]:
    directory = layout.validate_maintenance_directory(release_root)
    snapshot = directory / SNAPSHOT_NAME
    paused = directory / PAUSED_NAME
    metadata_path = Path(f"{paused}{ARCHIVE_METADATA_SUFFIX}")
    archive_present = any(
        entry.name.startswith(f"{PAUSED_NAME}.audit.")
        for entry in directory.iterdir()
    )
    if _path_exists(metadata_path) or archive_present:
        raise LifecycleError(
            "active maintenance cannot coexist with audit or archive metadata"
        )
    _require_single_link_file(snapshot, "active maintenance snapshot", expected_mode=0o600)
    _require_single_link_file(paused, "active maintenance paused ledger", expected_mode=0o600)

    snapshots = _read_jsonl(snapshot, "active maintenance snapshot")
    if not snapshots:
        raise LifecycleError("active maintenance snapshot must not be empty")
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for line_number, record in enumerate(snapshots, 1):
        if set(record) != SNAPSHOT_KEYS:
            raise LifecycleError(
                f"active maintenance snapshot line {line_number} has an invalid schema"
            )
        container_id = record.get("container_id")
        name = record.get("name")
        if (
            not isinstance(container_id, str)
            or not re.fullmatch(r"[0-9a-f]{12,64}", container_id)
            or not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name)
            or container_id in by_id
            or name in by_name
        ):
            raise LifecycleError("active maintenance snapshot has invalid identity data")
        labels = record.get("labels")
        if not isinstance(labels, dict) or record.get("compose_project") != labels.get(
            "com.docker.compose.project", ""
        ):
            raise LifecycleError("active maintenance snapshot has invalid Compose identity")
        by_id[container_id] = record
        by_name[name] = record

    selected = by_name.get("ocr-v6-amd")
    if selected is None:
        raise LifecycleError("active maintenance snapshot omits ocr-v6-amd")

    entries = _read_jsonl(paused, "active maintenance paused ledger")
    if not entries:
        if selected.get("state") == "running":
            raise LifecycleError(
                "active maintenance paused ledger is empty for an originally running container"
            )
        if _current_container_binding(selected) != selected:
            raise LifecycleError("active maintenance container binding has drifted")
        return selected
    if len(entries) != 1:
        raise LifecycleError(
            "active maintenance paused ledger must contain exactly one stopped entry"
        )
    entry = entries[0]
    if set(entry) != PAUSE_ENTRY_KEYS or entry.get("version") != 1:
        raise LifecycleError("active maintenance paused ledger has an invalid schema")
    if entry.get("status") != "stopped":
        raise LifecycleError(
            "active maintenance paused ledger contains a non-final pause state"
        )
    binding = entry.get("binding")
    entry_container_id = entry.get("container_id")
    if (
        not isinstance(binding, dict)
        or not isinstance(entry_container_id, str)
        or binding != by_id.get(entry_container_id)
        or entry.get("name") != binding.get("name")
        or entry.get("name") != "ocr-v6-amd"
        or binding.get("state") != "running"
    ):
        raise LifecycleError("active maintenance paused ledger binding is inconsistent")
    digest = hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if entry.get("snapshot_sha256") != digest:
        raise LifecycleError("active maintenance paused ledger snapshot hash differs")
    restart_policy = binding.get("restart_policy")
    if not isinstance(restart_policy, dict):
        raise LifecycleError("active maintenance snapshot restart policy is invalid")
    policy_neutralized = restart_policy.get("Name") not in {"", "no"}
    if entry.get("policy_neutralized") is not policy_neutralized:
        raise LifecycleError("active maintenance restart policy state is inconsistent")
    expected_current = {
        **binding,
        "restart_policy": (
            {"Name": "no", "MaximumRetryCount": 0}
            if policy_neutralized
            else restart_policy
        ),
        "state": "exited",
    }
    if _current_container_binding(binding) != expected_current:
        raise LifecycleError("active maintenance container binding has drifted")
    return binding


def _validate_authoritative_ledgers(
    layout: ReleaseLayout,
    snapshot_text: str,
    paused_text: str,
    *,
    completed_successor_binding: dict[str, Any] | None = None,
) -> tuple[Path, Path, bool]:
    _reject_control_characters(snapshot_text, "authoritative snapshot")
    _reject_control_characters(paused_text, "authoritative paused ledger")
    snapshot = Path(snapshot_text)
    paused = Path(paused_text)
    if snapshot.name != SNAPSHOT_NAME or paused != snapshot.with_name(PAUSED_NAME):
        raise LifecycleError(
            "authoritative snapshot/paused ledger names or shared directory are invalid"
        )
    if snapshot.parent.name != "container-maintenance":
        raise LifecycleError("authoritative ledgers must stay in container-maintenance")
    authority_root = layout.validate_release_root(
        str(snapshot.parent.parent), "authoritative release root"
    )
    if snapshot.parent != layout.validate_maintenance_directory(authority_root):
        raise LifecycleError("authoritative ledger directory escaped its release root")
    snapshot_present = _path_exists(snapshot)
    paused_present = _path_exists(paused)
    if snapshot_present and paused_present:
        _validate_active_maintenance(layout, authority_root)
        return snapshot, paused, False
    if snapshot_present and not paused_present:
        completed = _validate_completed_maintenance(
            layout,
            authority_root,
            successor_binding=completed_successor_binding,
        )
        if (
            completed["authoritative_snapshot"] != str(snapshot)
            or completed["authoritative_paused_ledger"] != str(paused)
        ):
            raise LifecycleError("completed maintenance authority paths differ")
        return snapshot, paused, True
    raise LifecycleError("authoritative maintenance ledger state is partial")


def _load_provenance(
    layout: ReleaseLayout,
    provenance: Path,
    *,
    owning_release_root: Path,
    expected_source_root: Path | None = None,
    completed_successor_binding: dict[str, Any] | None = None,
) -> tuple[dict[str, str], bool]:
    metadata = _require_owned_file(provenance, "maintenance provenance")
    if stat.S_IMODE(metadata.st_mode) != 0o400:
        raise LifecycleError("maintenance provenance must have immutable mode 0400")
    try:
        payload: Any = json.loads(provenance.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise LifecycleError(f"maintenance provenance is invalid: {error}") from error
    if not isinstance(payload, dict) or set(payload) != PROVENANCE_KEYS:
        raise LifecycleError("maintenance provenance has an invalid schema")
    if not all(isinstance(payload[key], str) for key in PROVENANCE_KEYS):
        raise LifecycleError("maintenance provenance values must all be strings")

    source_root = layout.validate_release_root(
        payload["source_release_root"], "provenance source release root"
    )
    if source_root == owning_release_root:
        raise LifecycleError("maintenance provenance cannot point to its own release")
    if payload["source_git_sha"] != source_root.name:
        raise LifecycleError("maintenance provenance source Git SHA does not match its root")
    if expected_source_root is not None and source_root != expected_source_root:
        raise LifecycleError(
            "maintenance provenance conflicts with PREVIOUS_RELEASE_ROOT; refusing rebinding"
        )
    snapshot, paused, authority_completed = _validate_authoritative_ledgers(
        layout,
        payload["authoritative_snapshot"],
        payload["authoritative_paused_ledger"],
        completed_successor_binding=completed_successor_binding,
    )
    return (
        {
            "authoritative_paused_ledger": str(paused),
            "authoritative_snapshot": str(snapshot),
            "source_git_sha": source_root.name,
            "source_release_root": str(source_root),
        },
        authority_completed,
    )


def _validate_completed_predecessor_authority(
    layout: ReleaseLayout,
    predecessor_root: Path,
    *,
    successor_binding: dict[str, Any] | None = None,
) -> None:
    directory = layout.validate_maintenance_directory(predecessor_root)
    snapshot = directory / SNAPSHOT_NAME
    paused = directory / PAUSED_NAME
    provenance = directory / PROVENANCE_NAME
    snapshot_present = _path_exists(snapshot)
    paused_present = _path_exists(paused)
    provenance_present = _path_exists(provenance)
    if snapshot_present and not paused_present and not provenance_present:
        _validate_completed_maintenance(
            layout,
            predecessor_root,
            successor_binding=successor_binding,
        )
        return
    if not snapshot_present and not paused_present and provenance_present:
        _, authority_completed = _load_provenance(
            layout,
            provenance,
            owning_release_root=predecessor_root,
            completed_successor_binding=successor_binding,
        )
        if authority_completed:
            return
    raise LifecycleError(
        "predecessor transaction marker no longer points to completed authority"
    )


def _load_predecessor_marker(
    layout: ReleaseLayout,
    marker: Path,
    *,
    owning_release_root: Path,
    expected_predecessor_root: Path | None = None,
) -> dict[str, str]:
    _require_single_link_file(
        marker,
        "predecessor transaction marker",
        expected_mode=0o400,
    )
    try:
        payload: Any = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise LifecycleError(
            f"predecessor transaction marker is invalid: {error}"
        ) from error
    if not isinstance(payload, dict) or set(payload) != PREDECESSOR_KEYS:
        raise LifecycleError("predecessor transaction marker has an invalid schema")
    if not all(isinstance(payload[key], str) for key in PREDECESSOR_KEYS):
        raise LifecycleError("predecessor transaction marker values must be strings")

    predecessor_root = layout.validate_release_root(
        payload["predecessor_release_root"],
        "predecessor transaction marker release root",
    )
    if predecessor_root == owning_release_root:
        raise LifecycleError(
            "predecessor transaction marker cannot point to its own release"
        )
    if payload["predecessor_git_sha"] != predecessor_root.name:
        raise LifecycleError(
            "predecessor transaction marker Git SHA does not match its root"
        )
    if (
        expected_predecessor_root is not None
        and predecessor_root != expected_predecessor_root
    ):
        raise LifecycleError(
            "predecessor transaction marker conflicts with PREVIOUS_RELEASE_ROOT"
        )
    return {
        "predecessor_git_sha": predecessor_root.name,
        "predecessor_release_root": str(predecessor_root),
    }


def _publish_predecessor_marker(
    layout: ReleaseLayout,
    current_root: Path,
    previous_root: Path,
) -> None:
    directory = layout.validate_maintenance_directory(current_root)
    destination = directory / PREDECESSOR_NAME
    if _path_exists(destination):
        raise LifecycleError(
            "predecessor transaction marker already exists; refusing replacement"
        )
    content = (
        json.dumps(
            {
                "predecessor_git_sha": previous_root.name,
                "predecessor_release_root": str(previous_root),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
    ).encode()
    descriptor, temporary_text = tempfile.mkstemp(
        prefix=".operator-maintenance-predecessor.",
        dir=directory,
    )
    temporary = Path(temporary_text)
    try:
        os.fchmod(descriptor, 0o400)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.close(descriptor)
        descriptor = -1
        os.link(temporary, destination, follow_symlinks=False)
        _fsync_directory(directory)
    except FileExistsError as error:
        raise LifecycleError(
            "predecessor transaction marker appeared concurrently; refusing replacement"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    _fsync_directory(directory)
    _load_predecessor_marker(
        layout,
        destination,
        owning_release_root=current_root,
        expected_predecessor_root=previous_root,
    )


def _maintenance_state(
    layout: ReleaseLayout,
    release_root: Path,
    *,
    expected_source_root: Path | None = None,
) -> tuple[str, dict[str, str] | None]:
    directory = layout.validate_maintenance_directory(release_root)
    snapshot = directory / SNAPSHOT_NAME
    paused = directory / PAUSED_NAME
    provenance = directory / PROVENANCE_NAME
    snapshot_present = _path_exists(snapshot)
    paused_present = _path_exists(paused)
    provenance_present = _path_exists(provenance)

    if snapshot_present and not paused_present and not provenance_present:
        return "completed", _validate_completed_maintenance(layout, release_root)
    if snapshot_present != paused_present:
        raise LifecycleError("maintenance snapshot/paused ledger state is partial")
    if snapshot_present and provenance_present:
        raise LifecycleError("maintenance state is ambiguous: ledgers and provenance coexist")
    if snapshot_present:
        _validate_active_maintenance(layout, release_root)
        return (
            "direct",
            {
                "authoritative_paused_ledger": str(paused),
                "authoritative_snapshot": str(snapshot),
                "source_git_sha": release_root.name,
                "source_release_root": str(release_root),
            },
        )
    if provenance_present:
        payload, authority_completed = _load_provenance(
            layout,
            provenance,
            owning_release_root=release_root,
            expected_source_root=expected_source_root,
        )
        return (
            "completed-provenance" if authority_completed else "provenance",
            payload,
        )
    return "empty", None


def resolve_maintenance(args: argparse.Namespace) -> None:
    layout = ReleaseLayout(args.report_root, args.release_tag)
    current_root = layout.validate_release_root(args.release_root, "RELEASE_ROOT")
    previous_root = (
        layout.validate_release_root(args.previous_release_root, "PREVIOUS_RELEASE_ROOT")
        if args.previous_release_root
        else None
    )
    if previous_root == current_root:
        raise LifecycleError("PREVIOUS_RELEASE_ROOT must belong to a different Git SHA")

    current_directory = layout.validate_maintenance_directory(current_root)
    current_snapshot = current_directory / SNAPSHOT_NAME
    current_paused = current_directory / PAUSED_NAME
    current_provenance = current_directory / PROVENANCE_NAME
    predecessor_marker = current_directory / PREDECESSOR_NAME
    marker_present = _path_exists(predecessor_marker)
    snapshot_present = _path_exists(current_snapshot)
    paused_present = _path_exists(current_paused)
    provenance_present = _path_exists(current_provenance)
    selected_local = {
        "authoritative_paused_ledger": str(current_paused),
        "authoritative_snapshot": str(current_snapshot),
        "source_git_sha": current_root.name,
        "source_release_root": str(current_root),
    }
    current_kind: str
    current: dict[str, str] | None

    if marker_present:
        if previous_root is None:
            raise LifecycleError(
                "predecessor transaction marker requires PREVIOUS_RELEASE_ROOT"
            )
        _load_predecessor_marker(
            layout,
            predecessor_marker,
            owning_release_root=current_root,
            expected_predecessor_root=previous_root,
        )
        if provenance_present:
            raise LifecycleError(
                "predecessor transaction marker conflicts with maintenance provenance"
            )
        if paused_present and not snapshot_present:
            raise LifecycleError("maintenance snapshot/paused ledger state is partial")
        active_binding: dict[str, Any] | None = None
        if snapshot_present and paused_present:
            active_binding = _validate_active_maintenance(layout, current_root)
            current_kind, current = "direct", selected_local
        elif snapshot_present:
            completion_artifacts = _path_exists(
                Path(f"{current_paused}{ARCHIVE_METADATA_SUFFIX}")
            ) or any(
                entry.name.startswith(f"{PAUSED_NAME}.audit.")
                for entry in current_directory.iterdir()
            )
            if completion_artifacts:
                current_kind = "completed"
                current = _validate_completed_maintenance(layout, current_root)
            else:
                _require_single_link_file(
                    current_snapshot,
                    "interrupted maintenance snapshot",
                    expected_mode=0o600,
                )
                current_kind, current = "snapshot-only", selected_local
        else:
            current_kind, current = "marked-empty", selected_local
        _validate_completed_predecessor_authority(
            layout,
            previous_root,
            successor_binding=active_binding,
        )
    else:
        if previous_root is not None and (snapshot_present or paused_present):
            raise LifecycleError(
                "predecessor transaction marker is missing for local maintenance state"
            )
        current_kind, current = _maintenance_state(
            layout,
            current_root,
            expected_source_root=previous_root,
        )

    selected: dict[str, str] | None
    if current_kind == "direct":
        action = "reuse-local"
        selected = current
    elif current_kind == "snapshot-only":
        action = "resume-pause-after-restored-previous"
        selected = current
    elif current_kind == "marked-empty":
        action = "fresh-after-restored-previous"
        selected = current
    elif current_kind == "provenance":
        if previous_root is None:
            raise LifecycleError(
                "existing maintenance provenance requires its original PREVIOUS_RELEASE_ROOT"
            )
        action = "reuse-provenance"
        selected = current
    elif current_kind in {"completed", "completed-provenance"}:
        raise LifecycleError(
            "current release maintenance is already restored; use a new Git SHA release"
        )
    elif previous_root is not None:
        previous_kind, selected = _maintenance_state(layout, previous_root)
        if previous_kind == "empty":
            raise LifecycleError("PREVIOUS_RELEASE_ROOT has no authoritative maintenance state")
        if previous_kind in {"completed", "completed-provenance"}:
            directory = layout.validate_maintenance_directory(current_root)
            _publish_predecessor_marker(layout, current_root, previous_root)
            action = "fresh-after-restored-previous"
            selected = {
                "authoritative_paused_ledger": str(directory / PAUSED_NAME),
                "authoritative_snapshot": str(directory / SNAPSHOT_NAME),
                "source_git_sha": current_root.name,
                "source_release_root": str(current_root),
            }
        else:
            action = "inherit"
    else:
        directory = layout.validate_maintenance_directory(current_root)
        action = "fresh"
        selected = {
            "authoritative_paused_ledger": str(directory / PAUSED_NAME),
            "authoritative_snapshot": str(directory / SNAPSHOT_NAME),
            "source_git_sha": current_root.name,
            "source_release_root": str(current_root),
        }

    assert selected is not None
    provenance_source_root = (
        str(previous_root)
        if previous_root is not None
        and action
        in {
            "fresh-after-restored-previous",
            "inherit",
            "resume-pause-after-restored-previous",
            "reuse-local",
        }
        else selected["source_release_root"]
    )
    output = (
        action,
        provenance_source_root,
        selected["authoritative_snapshot"],
        selected["authoritative_paused_ledger"],
    )
    sys.stdout.write("\n".join(output) + "\n")


def _operator_ledger_pair(
    layout: ReleaseLayout, release_root: Path
) -> tuple[Path, Path] | None:
    directory = layout.validate_maintenance_directory(release_root)
    baseline = directory / OPERATOR_BASELINE_NAME
    new = directory / OPERATOR_NEW_NAME
    baseline_present = _path_exists(baseline)
    new_present = _path_exists(new)
    if baseline_present != new_present:
        raise LifecycleError("operator ledger state is partial")
    if not baseline_present:
        return None
    _require_owned_file(baseline, "operator baseline ledger")
    _require_owned_file(new, "operator new ledger")
    return baseline, new


def resolve_operator_ledgers(args: argparse.Namespace) -> None:
    layout = ReleaseLayout(args.report_root, args.release_tag)
    release_root = layout.validate_release_root(
        args.previous_release_root, "PREVIOUS_RELEASE_ROOT"
    )
    visited: set[Path] = set()

    while True:
        if release_root in visited:
            raise LifecycleError("operator ledger provenance cycle detected")
        visited.add(release_root)

        pair = _operator_ledger_pair(layout, release_root)
        if pair is not None:
            baseline, new = pair
            sys.stdout.write(
                "\n".join((str(release_root), str(baseline), str(new))) + "\n"
            )
            return

        maintenance_kind, maintenance_state = _maintenance_state(
            layout, release_root
        )
        if maintenance_kind in {"provenance", "completed-provenance"}:
            assert maintenance_state is not None
            release_root = Path(maintenance_state["source_release_root"])
            continue
        if maintenance_kind in {"direct", "completed"}:
            predecessor_marker = (
                layout.validate_maintenance_directory(release_root)
                / PREDECESSOR_NAME
            )
            if _path_exists(predecessor_marker):
                predecessor = _load_predecessor_marker(
                    layout,
                    predecessor_marker,
                    owning_release_root=release_root,
                )
                release_root = Path(predecessor["predecessor_release_root"])
                continue
            raise LifecycleError(
                "operator ledger ancestor has direct maintenance state "
                "without a complete operator ledger pair"
            )
        raise LifecycleError("no complete operator ledger ancestor")


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(
        directory,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_provenance(args: argparse.Namespace) -> None:
    layout = ReleaseLayout(args.report_root, args.release_tag)
    current_root = layout.validate_release_root(args.release_root, "RELEASE_ROOT")
    source_root = layout.validate_release_root(
        args.source_release_root, "provenance source release root"
    )
    if source_root == current_root:
        raise LifecycleError("maintenance provenance source must be another release")
    snapshot, paused, authority_completed = _validate_authoritative_ledgers(
        layout, args.snapshot, args.paused
    )
    if authority_completed:
        raise LifecycleError(
            "cannot publish provenance for a completed maintenance authority"
        )
    directory = layout.validate_maintenance_directory(current_root)
    destination = directory / PROVENANCE_NAME
    if _path_exists(destination):
        raise LifecycleError("maintenance provenance already exists; refusing replacement")

    content = (
        json.dumps(
            {
                "authoritative_paused_ledger": str(paused),
                "authoritative_snapshot": str(snapshot),
                "source_git_sha": source_root.name,
                "source_release_root": str(source_root),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
    ).encode()
    descriptor, temporary_text = tempfile.mkstemp(
        prefix=".operator-maintenance-provenance.", dir=directory
    )
    temporary = Path(temporary_text)
    try:
        os.fchmod(descriptor, 0o400)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.close(descriptor)
        descriptor = -1
        os.link(temporary, destination, follow_symlinks=False)
        _fsync_directory(directory)
    except FileExistsError as error:
        raise LifecycleError(
            "maintenance provenance appeared concurrently; refusing replacement"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    _fsync_directory(directory)
    _load_provenance(
        layout,
        destination,
        owning_release_root=current_root,
        expected_source_root=source_root,
    )


def _normalize_host_ip(value: Any) -> str:
    text = "" if value is None else str(value)
    return "0.0.0.0" if text in {"", "0.0.0.0"} else text


def _compose_bindings(service: dict[str, Any]) -> set[tuple[str, int, int, str]]:
    bindings: set[tuple[str, int, int, str]] = set()
    ports = service.get("ports", [])
    if not isinstance(ports, list):
        raise LifecycleError("authoritative Compose service ports must be a list")
    for port in ports:
        if not isinstance(port, dict) or "published" not in port or "target" not in port:
            raise LifecycleError("authoritative Compose contains an invalid published port")
        try:
            published = int(port["published"])
            target = int(port["target"])
        except (TypeError, ValueError) as error:
            raise LifecycleError("authoritative Compose contains a non-integer port") from error
        protocol = str(port.get("protocol", "tcp")).lower()
        if protocol not in {"tcp", "udp"} or not (1 <= published <= 65535):
            raise LifecycleError("authoritative Compose contains an invalid port binding")
        bindings.add(
            (_normalize_host_ip(port.get("host_ip")), published, target, protocol)
        )
    return bindings


def _inspect_bindings(record: dict[str, Any]) -> set[tuple[str, int, int, str]]:
    result: set[tuple[str, int, int, str]] = set()
    ports = record.get("NetworkSettings", {}).get("Ports", {})
    if not isinstance(ports, dict):
        raise LifecycleError("Docker inspect returned invalid port metadata")
    for target_protocol, bindings in ports.items():
        if bindings is None:
            continue
        if not isinstance(bindings, list) or "/" not in target_protocol:
            raise LifecycleError("Docker inspect returned invalid port bindings")
        target_text, protocol = target_protocol.rsplit("/", 1)
        try:
            target = int(target_text)
        except ValueError as error:
            raise LifecycleError("Docker inspect returned a non-integer target port") from error
        for binding in bindings:
            if not isinstance(binding, dict):
                raise LifecycleError("Docker inspect returned an invalid host binding")
            try:
                published = int(binding["HostPort"])
            except (KeyError, TypeError, ValueError) as error:
                raise LifecycleError("Docker inspect returned an invalid host port") from error
            result.add(
                (
                    _normalize_host_ip(binding.get("HostIp")),
                    published,
                    target,
                    protocol.lower(),
                )
            )
    return result


def _bindings_match(
    expected: set[tuple[str, int, int, str]],
    actual: set[tuple[str, int, int, str]],
) -> bool:
    allowed = set(expected)
    for host_ip, published, target, protocol in expected:
        if host_ip == "0.0.0.0":
            allowed.add(("::", published, target, protocol))
    return expected <= actual <= allowed


def _format_published_endpoint(host_ip: str, published: int) -> str:
    try:
        address = ipaddress.ip_address(host_ip)
    except ValueError as error:
        raise LifecycleError("Docker inspect returned an invalid host IP") from error
    rendered = str(address)
    if address.version == 6:
        rendered = f"[{rendered}]"
    return f"{rendered}:{published}"


def _authoritative_compose_records(
    compose_file: str, expected_project: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    compose_command = [
        "docker",
        "compose",
        "-f",
        compose_file,
    ]
    if expected_project == "algorithm-operators":
        compose_command.extend(("--profile", "*"))
    try:
        rendered = subprocess.run(
            [*compose_command, "config", "--format", "json"],
            check=True,
            text=True,
            capture_output=True,
        )
        compose: Any = json.loads(rendered.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        raise LifecycleError(f"cannot render authoritative Compose: {error}") from error
    if not isinstance(compose, dict) or compose.get("name") != expected_project:
        raise LifecycleError("authoritative Compose project identity is invalid")
    services = compose.get("services")
    if not isinstance(services, dict) or not services:
        if services == {}:
            return services, []
        raise LifecycleError("authoritative Compose service set is invalid")

    service_names = sorted(services)
    if any(
        not isinstance(service_name, str) or not service_name
        for service_name in service_names
    ):
        raise LifecycleError("authoritative Compose contains an invalid service name")
    running_command = [
        *compose_command,
        "ps",
        "--status",
        "running",
        "--no-trunc",
        "-q",
        *service_names,
    ]
    try:
        running = subprocess.run(
            running_command,
            check=True,
            text=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise LifecycleError(f"cannot query authoritative Compose services: {error}") from error

    container_ids = [line.strip() for line in running.stdout.splitlines() if line.strip()]
    if len(container_ids) != len(set(container_ids)) or any(
        not re.fullmatch(r"[0-9a-f]{64}", container_id) for container_id in container_ids
    ):
        raise LifecycleError("authoritative Compose returned invalid container IDs")
    if not container_ids:
        return services, []
    try:
        inspected = subprocess.run(
            ["docker", "inspect", *container_ids],
            check=True,
            text=True,
            capture_output=True,
        )
        records: Any = json.loads(inspected.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        raise LifecycleError(f"cannot inspect authoritative Compose containers: {error}") from error
    if not isinstance(records, list) or len(records) != len(container_ids):
        raise LifecycleError("Docker inspect did not return the exact Compose container set")

    seen: set[str] = set()
    expected_ids = set(container_ids)
    for record in records:
        if not isinstance(record, dict):
            raise LifecycleError("Docker inspect returned an invalid container record")
        container_id = record.get("Id")
        if container_id not in expected_ids or container_id in seen:
            raise LifecycleError("Docker inspect container identity does not match Compose")
        seen.add(container_id)
        if record.get("State", {}).get("Running") is not True:
            raise LifecycleError("authoritative Compose returned a non-running container")
        labels = record.get("Config", {}).get("Labels", {})
        if not isinstance(labels, dict):
            raise LifecycleError("Docker inspect returned invalid Compose labels")
        project = labels.get("com.docker.compose.project")
        service_name = labels.get("com.docker.compose.service")
        if project != expected_project or service_name not in services:
            raise LifecycleError("running container has a non-authoritative Compose identity")
        service = services[service_name]
        if not isinstance(service, dict):
            raise LifecycleError("authoritative Compose service definition is invalid")
        expected_bindings = _compose_bindings(service)
        actual_bindings = _inspect_bindings(record)
        if not _bindings_match(expected_bindings, actual_bindings):
            raise LifecycleError(
                f"running Compose container ports differ from service {service_name}"
            )
    if seen != expected_ids:
        raise LifecycleError("Docker inspect omitted an authoritative Compose container")
    return services, records


def authoritative_published_endpoints(args: argparse.Namespace) -> None:
    endpoints: set[str] = set()
    for compose_file, expected_project in (
        (args.platform_compose_file, "algorithm-scheduling-platform"),
        (args.operator_compose_file, "algorithm-operators"),
    ):
        _, records = _authoritative_compose_records(compose_file, expected_project)
        for record in records:
            endpoints.update(
                _format_published_endpoint(host_ip, published)
                for host_ip, published, _, _ in _inspect_bindings(record)
            )
    sys.stdout.write(" ".join(sorted(endpoints)))


def hold_lock(args: argparse.Namespace) -> None:
    release_tag_root = Path(args.release_tag_root)
    lock_path = Path(args.lock_path)
    _reject_control_characters(str(release_tag_root), "release tag root")
    _reject_control_characters(str(lock_path), "operator lifecycle lock path")
    if not release_tag_root.is_absolute() or lock_path.parent != release_tag_root:
        raise LifecycleError("operator lifecycle lock escaped the release tag root")
    if lock_path.name != ".operator-lifecycle.lock":
        raise LifecycleError("operator lifecycle lock has an unexpected name")
    directory_metadata = _require_owned_directory(release_tag_root, "release tag root")
    directory_fd = os.open(
        release_tag_root,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    lock_fd = -1
    try:
        opened_directory = os.fstat(directory_fd)
        if (opened_directory.st_dev, opened_directory.st_ino) != (
            directory_metadata.st_dev,
            directory_metadata.st_ino,
        ):
            raise LifecycleError("release tag root changed while opening the lock")
        lock_fd = os.open(
            lock_path.name,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        opened_lock = os.fstat(lock_fd)
        named_lock = os.stat(lock_path.name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(opened_lock.st_mode):
            raise LifecycleError("operator lifecycle lock must be a regular file")
        if opened_lock.st_uid != os.geteuid():
            raise LifecycleError("operator lifecycle lock must be owned by the deployment identity")
        if opened_lock.st_nlink != 1:
            raise LifecycleError("operator lifecycle lock must have exactly one directory entry")
        if stat.S_IMODE(opened_lock.st_mode) != 0o600:
            raise LifecycleError("operator lifecycle lock must have mode 0600")
        if (opened_lock.st_dev, opened_lock.st_ino) != (
            named_lock.st_dev,
            named_lock.st_ino,
        ):
            raise LifecycleError("operator lifecycle lock path changed while opening")
        locked = subprocess.run(
            ["flock", "-n", str(lock_fd)],
            check=False,
            pass_fds=(lock_fd,),
        )
        if locked.returncode != 0:
            raise LifecycleError(
                "another SHA is running operator maintenance for this release tag"
            )
        print("LOCKED", flush=True)
        while os.read(sys.stdin.fileno(), 4096):
            pass
    finally:
        if lock_fd >= 0:
            os.close(lock_fd)
        os.close(directory_fd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    lock = commands.add_parser("hold-lock")
    lock.add_argument("--release-tag-root", required=True)
    lock.add_argument("--lock-path", required=True)
    lock.set_defaults(handler=hold_lock)

    resolve = commands.add_parser("resolve-maintenance")
    resolve.add_argument("--report-root", required=True)
    resolve.add_argument("--release-tag", required=True)
    resolve.add_argument("--release-root", required=True)
    resolve.add_argument("--previous-release-root", default="")
    resolve.set_defaults(handler=resolve_maintenance)

    resolve_ledgers = commands.add_parser("resolve-operator-ledgers")
    resolve_ledgers.add_argument("--report-root", required=True)
    resolve_ledgers.add_argument("--release-tag", required=True)
    resolve_ledgers.add_argument("--previous-release-root", required=True)
    resolve_ledgers.set_defaults(handler=resolve_operator_ledgers)

    publish = commands.add_parser("publish-provenance")
    publish.add_argument("--report-root", required=True)
    publish.add_argument("--release-tag", required=True)
    publish.add_argument("--release-root", required=True)
    publish.add_argument("--source-release-root", required=True)
    publish.add_argument("--snapshot", required=True)
    publish.add_argument("--paused", required=True)
    publish.set_defaults(handler=publish_provenance)

    endpoints = commands.add_parser("authoritative-published-endpoints")
    endpoints.add_argument("--platform-compose-file", required=True)
    endpoints.add_argument("--operator-compose-file", required=True)
    endpoints.set_defaults(handler=authoritative_published_endpoints)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.handler(args)
    except LifecycleError as error:
        print(f"operator lifecycle: FAIL: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"operator lifecycle: FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
