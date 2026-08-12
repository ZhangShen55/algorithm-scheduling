#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ACTIVE = {"pending_stop", "stopped", "restoring"}
TERMINAL = {"restored", "not_stopped"}


def fail(tool: str, message: str) -> None:
    raise SystemExit(f"{tool}: {message}")


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def absolute(path: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(tool: str, path: Path, records: list[dict]) -> None:
    if path.is_symlink():
        fail(tool, f"refusing symlink output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def open_lock(tool: str, path: Path) -> int:
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        fail(tool, f"operation lock directory must be a real directory: {parent}")
    if parent.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        fail(tool, f"operation lock directory must not be group/other writable: {parent}")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as error:
        fail(tool, f"cannot securely open operation lock {path}: {error}")
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
        os.close(fd)
        fail(tool, f"operation lock must be an owned regular file: {path}")
    os.fchmod(fd, 0o600)
    return fd


def paths(snapshot_argument: str) -> tuple[Path, Path, Path]:
    snapshot = absolute(snapshot_argument)
    ledger = absolute(os.environ.get("PAUSE_RECORD_PATH", f"{snapshot}.paused.jsonl"))
    lock = absolute(os.environ.get("DEPLOY_OPERATION_LOCK", f"{snapshot}.operation.lock"))
    return snapshot, ledger, lock


def read_jsonl(tool: str, path: Path, kind: str) -> list[dict]:
    if path.is_symlink() or not path.is_file():
        fail(tool, f"{kind} must be a regular file: {path}")
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            fail(tool, f"malformed {kind} line {number}: {error}")
        if not isinstance(record, dict):
            fail(tool, f"invalid {kind} line {number}")
        records.append(record)
    return records


def archive_ledger(tool: str, ledger: Path, records: list[dict]) -> Path:
    if ledger.is_symlink() or not ledger.is_file():
        fail(tool, f"pause ledger must be a regular file: {ledger}")
    while True:
        archive = ledger.with_name(
            f"{ledger.name}.audit.{time.time_ns()}.{uuid.uuid4().hex}.jsonl"
        )
        try:
            os.link(ledger, archive, follow_symlinks=False)
            break
        except FileExistsError:
            continue
    os.chmod(archive, 0o400)
    fsync_directory(ledger.parent)
    os.unlink(ledger)
    fsync_directory(ledger.parent)
    return archive


def inspect(tool: str, selector: str) -> dict:
    completed = subprocess.run(
        ["docker", "inspect", selector], text=True, capture_output=True, check=False
    )
    if completed.returncode:
        fail(tool, f"container no longer exists: {selector}")
    try:
        values = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        fail(tool, f"docker inspect returned invalid JSON: {error}")
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        fail(tool, "docker inspect returned an unexpected payload")
    return values[0]


def current_binding(item: dict, snapshot_state: str) -> dict:
    labels = item.get("Config", {}).get("Labels") or {}
    return {
        "container_id": item.get("Id"),
        "name": item.get("Name", "").removeprefix("/"),
        "image_ref": item.get("Config", {}).get("Image"),
        "image_id": item.get("Image"),
        "state": snapshot_state,
        "labels": labels,
        "ports": item.get("HostConfig", {}).get("PortBindings") or {},
        "mounts": [
            {
                "type": mount.get("Type"),
                "source": mount.get("Source"),
                "destination": mount.get("Destination"),
                "mode": mount.get("Mode"),
                "rw": mount.get("RW"),
                "propagation": mount.get("Propagation"),
            }
            for mount in item.get("Mounts", [])
        ],
        "restart_policy": item.get("HostConfig", {}).get("RestartPolicy") or {},
        "compose_project": labels.get("com.docker.compose.project", ""),
    }


def validate_snapshot(tool: str, snapshot: Path) -> list[dict]:
    records = read_jsonl(tool, snapshot, "snapshot")
    required = {
        "container_id", "name", "image_ref", "image_id", "state", "labels", "ports",
        "mounts", "restart_policy", "compose_project",
    }
    for number, record in enumerate(records, 1):
        if not required.issubset(record):
            fail(tool, f"incomplete snapshot line {number}")
        if not re.fullmatch(r"[0-9a-f]{12,64}", record["container_id"]):
            fail(tool, f"invalid container ID on snapshot line {number}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", record["name"]):
            fail(tool, f"invalid container name on snapshot line {number}")
        labels = record.get("labels") or {}
        if record["compose_project"] != labels.get("com.docker.compose.project", ""):
            fail(tool, f"compose_project mismatch on snapshot line {number}")
    return records


def verify_identity(
    tool: str,
    expected: dict,
    *,
    allow_policy_change: bool = False,
    allow_state_change: bool = False,
) -> dict:
    by_id = inspect(tool, expected["container_id"])
    by_name = inspect(tool, expected["name"])
    if by_name.get("Id") != expected["container_id"]:
        fail(tool, f"{expected['name']}: name reuse detected")
    current = current_binding(by_id, expected["state"])
    if not allow_state_change and by_id.get("State", {}).get("Status") != expected["state"]:
        fail(tool, f"{expected['name']}: state changed")
    for attribute, value in expected.items():
        if allow_policy_change and attribute == "restart_policy":
            continue
        if current.get(attribute) != value:
            fail(tool, f"{expected['name']}: {attribute} changed")
    return by_id


def restart_policy(item: dict) -> dict:
    return item.get("HostConfig", {}).get("RestartPolicy") or {}


def policy_argument(policy: dict) -> str:
    name = policy.get("Name") or "no"
    retries = int(policy.get("MaximumRetryCount") or 0)
    return f"{name}:{retries}" if name == "on-failure" and retries else name


def set_policy(tool: str, entry: dict, policy: dict) -> None:
    completed = subprocess.run(
        ["docker", "update", f"--restart={policy_argument(policy)}", entry["container_id"]],
        check=False,
    )
    if completed.returncode:
        fail(tool, f"{entry['name']}: docker update restart policy failed; intent preserved")
    item = verify_identity(tool, entry["binding"], allow_policy_change=True)
    if restart_policy(item) != policy:
        fail(tool, f"{entry['name']}: restart policy was not confirmed")


def float_setting(tool: str, name: str, default: str) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        fail(tool, f"{name} must be a non-negative number")
    if value < 0:
        fail(tool, f"{name} must be a non-negative number")
    return value


def wait_for_state(tool: str, entry: dict, wanted: str, timeout_name: str) -> dict:
    timeout = float_setting(tool, timeout_name, "30")
    interval = float_setting(tool, "STATE_POLL_INTERVAL_SECONDS", "0.2")
    deadline = time.monotonic() + timeout
    while True:
        item = verify_identity(
            tool, entry["binding"], allow_policy_change=True, allow_state_change=True
        )
        state = item.get("State", {}).get("Status", "")
        if state == wanted:
            return item
        if time.monotonic() >= deadline:
            fail(
                tool,
                f"{entry['name']}: state did not converge to {wanted}; current state {state}",
            )
        time.sleep(interval)


def validate_ledger(tool: str, snapshots: list[dict], ledger: Path) -> list[dict]:
    entries = read_jsonl(tool, ledger, "pause ledger")
    snapshot_by_id = {record["container_id"]: record for record in snapshots}
    required = {
        "version", "status", "container_id", "name", "snapshot_sha256", "binding",
        "policy_neutralized",
    }
    for number, entry in enumerate(entries, 1):
        if (
            not required.issubset(entry)
            or entry["version"] != 1
            or entry["status"] not in ACTIVE | TERMINAL
            or not isinstance(entry["policy_neutralized"], bool)
        ):
            fail(tool, f"incomplete pause ledger line {number}")
        binding = entry["binding"]
        if binding != snapshot_by_id.get(entry["container_id"]):
            fail(tool, f"ledger binding differs from snapshot on line {number}")
        if entry["name"] != binding.get("name"):
            fail(tool, f"ledger name differs from binding on line {number}")
        if entry["snapshot_sha256"] != hashlib.sha256(canonical(binding)).hexdigest():
            fail(tool, f"snapshot hash mismatch on pause ledger line {number}")
        if binding.get("state") != "running":
            fail(tool, f"refusing originally non-running container on line {number}")
    return entries


def snapshot_command(argument: str) -> None:
    tool = "snapshot"
    snapshot, ledger, lock = paths(argument)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if snapshot.is_symlink():
        fail(tool, f"refusing symlink output: {snapshot}")
    lock_fd = open_lock(tool, lock)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if ledger.exists() or ledger.is_symlink():
            entries = read_jsonl(tool, ledger, "pause ledger")
            if any(entry.get("status") in ACTIVE for entry in entries):
                fail(tool, f"active pause ledger prevents snapshot replacement: {ledger}")
            archive_ledger(tool, ledger, entries)
        completed = subprocess.run(
            ["docker", "ps", "-aq"], text=True, capture_output=True, check=False
        )
        if completed.returncode:
            fail(tool, "failed to list containers")
        records = []
        for container_id in completed.stdout.splitlines():
            if not container_id:
                continue
            item = inspect(tool, container_id)
            record = current_binding(item, item.get("State", {}).get("Status", ""))
            records.append(record)
            print(f"snapshot: {container_id}: recorded")
        atomic_write(tool, snapshot, records)
    finally:
        os.close(lock_fd)
    print(f"snapshot: complete: {snapshot}")


def pause_command(snapshot_argument: str, selectors: list[str]) -> None:
    tool = "pause"
    snapshot, ledger, lock = paths(snapshot_argument)
    lock_fd = open_lock(tool, lock)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        records = validate_snapshot(tool, snapshot)
        by_id = {record["container_id"]: record for record in records}
        by_name = {record["name"]: record for record in records}
        if len(by_id) != len(records) or len(by_name) != len(records):
            fail(tool, "duplicate container ID or name in snapshot")
        selected = []
        seen = set()
        for selector in selectors:
            record = by_id.get(selector) or by_name.get(selector)
            if record is None:
                fail(tool, f"selector is not present in snapshot: {selector}")
            if record["container_id"] in seen:
                fail(tool, f"duplicate selector: {selector}")
            seen.add(record["container_id"])
            selected.append(record)
        if ledger.exists() or ledger.is_symlink():
            entries = validate_ledger(tool, records, ledger)
            if any(entry["status"] in ACTIVE for entry in entries):
                fail(tool, f"refusing existing active ledger: {ledger}")
            archive_ledger(tool, ledger, entries)
        for record in selected:
            if record["state"] == "running":
                verify_identity(tool, record)
        entries: list[dict] = []
        atomic_write(tool, ledger, entries)
        for record in selected:
            if record["state"] != "running":
                print(
                    f"pause: {record['name']} ({record['container_id']}): unchanged "
                    f"(snapshot state {record['state']})"
                )
                continue
            verify_identity(tool, record)
            needs_neutralization = record["restart_policy"].get("Name") not in {"", "no"}
            entry = {
                "version": 1,
                "status": "pending_stop",
                "container_id": record["container_id"],
                "name": record["name"],
                "snapshot_sha256": hashlib.sha256(canonical(record)).hexdigest(),
                "binding": record,
                "policy_neutralized": False,
            }
            entries.append(entry)
            atomic_write(tool, ledger, entries)
            if needs_neutralization:
                set_policy(tool, entry, {"Name": "no", "MaximumRetryCount": 0})
                entry["policy_neutralized"] = True
                atomic_write(tool, ledger, entries)
            completed = subprocess.run(["docker", "stop", record["container_id"]], check=False)
            if completed.returncode:
                fail(tool, f"{record['name']}: docker stop failed; pending_stop intent preserved")
            wait_for_state(tool, entry, "exited", "STOP_STATE_TIMEOUT_SECONDS")
            entry["status"] = "stopped"
            atomic_write(tool, ledger, entries)
            print(f"pause: {record['name']} ({record['container_id']}): stopped")
    finally:
        os.close(lock_fd)
    print(f"pause: complete: {ledger}")


def restore_original_policy(tool: str, entry: dict, current: dict | None = None) -> None:
    original = entry["binding"]["restart_policy"]
    item = current or verify_identity(
        tool, entry["binding"], allow_policy_change=True, allow_state_change=True
    )
    if restart_policy(item) != original:
        set_policy(tool, entry, original)
    entry["policy_neutralized"] = False


def restore_command(snapshot_argument: str, ledger_argument: str) -> None:
    tool = "restore"
    snapshot, _, lock = paths(snapshot_argument)
    ledger = absolute(ledger_argument)
    lock_fd = open_lock(tool, lock)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        snapshots = validate_snapshot(tool, snapshot)
        entries = validate_ledger(tool, snapshots, ledger)
        for entry in entries:
            item = verify_identity(
                tool, entry["binding"], allow_policy_change=True, allow_state_change=True
            )
            state = item.get("State", {}).get("Status", "")
            status = entry["status"]
            if status in TERMINAL:
                if state != "running":
                    fail(tool, f"{entry['name']}: terminal container is not running")
                restore_original_policy(tool, entry, item)
                continue
            if status == "pending_stop" and state == "running":
                restore_original_policy(tool, entry, item)
                entry["status"] = "not_stopped"
                atomic_write(tool, ledger, entries)
                print(f"restore: {entry['name']}: not_stopped; pending stop did not execute")
                continue
            if status == "restoring" and state == "running":
                restore_original_policy(tool, entry, item)
                entry["status"] = "restored"
                atomic_write(tool, ledger, entries)
                print(f"restore: {entry['name']}: already restored")
                continue
            if state != "exited":
                fail(tool, f"{entry['name']}: state {state} is not safe to restore")
            recovered_pending = status == "pending_stop"
            entry["status"] = "restoring"
            atomic_write(tool, ledger, entries)
            completed = subprocess.run(["docker", "start", entry["container_id"]], check=False)
            if completed.returncode:
                fail(tool, f"{entry['name']}: docker start failed; restoring state preserved")
            item = wait_for_state(tool, entry, "running", "START_STATE_TIMEOUT_SECONDS")
            restore_original_policy(tool, entry, item)
            entry["status"] = "restored"
            atomic_write(tool, ledger, entries)
            suffix = " recovered_from_pending" if recovered_pending else ""
            print(f"restore: {entry['name']} ({entry['container_id']}): started{suffix}")
        if all(
            entry["status"] in TERMINAL and not entry["policy_neutralized"]
            for entry in entries
        ):
            archive_ledger(tool, ledger, entries)
    finally:
        os.close(lock_fd)
    print("restore: complete")


def main() -> None:
    if len(sys.argv) < 3:
        fail("container-protection", "missing command arguments")
    command = sys.argv[1]
    if command == "snapshot" and len(sys.argv) == 3:
        snapshot_command(sys.argv[2])
    elif command == "pause" and len(sys.argv) >= 4:
        pause_command(sys.argv[2], sys.argv[3:])
    elif command == "restore" and len(sys.argv) == 4:
        restore_command(sys.argv[2], sys.argv[3])
    else:
        fail(command, "invalid arguments")


if __name__ == "__main__":
    main()
