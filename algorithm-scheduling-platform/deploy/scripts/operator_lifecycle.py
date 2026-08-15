#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
PROVENANCE_KEYS = {
    "authoritative_paused_ledger",
    "authoritative_snapshot",
    "source_git_sha",
    "source_release_root",
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


def _validate_authoritative_ledgers(
    layout: ReleaseLayout,
    snapshot_text: str,
    paused_text: str,
) -> tuple[Path, Path]:
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
    _require_owned_file(snapshot, "authoritative snapshot")
    _require_owned_file(paused, "authoritative paused ledger")
    return snapshot, paused


def _load_provenance(
    layout: ReleaseLayout,
    provenance: Path,
    *,
    owning_release_root: Path,
    expected_source_root: Path | None = None,
) -> dict[str, str]:
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
    snapshot, paused = _validate_authoritative_ledgers(
        layout,
        payload["authoritative_snapshot"],
        payload["authoritative_paused_ledger"],
    )
    return {
        "authoritative_paused_ledger": str(paused),
        "authoritative_snapshot": str(snapshot),
        "source_git_sha": source_root.name,
        "source_release_root": str(source_root),
    }


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

    if snapshot_present != paused_present:
        raise LifecycleError("maintenance snapshot/paused ledger state is partial")
    if snapshot_present and provenance_present:
        raise LifecycleError("maintenance state is ambiguous: ledgers and provenance coexist")
    if snapshot_present:
        _require_owned_file(snapshot, "maintenance snapshot")
        _require_owned_file(paused, "maintenance paused ledger")
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
        return (
            "provenance",
            _load_provenance(
                layout,
                provenance,
                owning_release_root=release_root,
                expected_source_root=expected_source_root,
            ),
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

    current_kind, current = _maintenance_state(
        layout,
        current_root,
        expected_source_root=previous_root,
    )
    if current_kind == "direct":
        if previous_root is not None:
            raise LifecycleError(
                "local maintenance ledgers conflict with PREVIOUS_RELEASE_ROOT"
            )
        action = "reuse-local"
        selected = current
    elif current_kind == "provenance":
        if previous_root is None:
            raise LifecycleError(
                "existing maintenance provenance requires its original PREVIOUS_RELEASE_ROOT"
            )
        action = "reuse-provenance"
        selected = current
    elif previous_root is not None:
        previous_kind, selected = _maintenance_state(layout, previous_root)
        if previous_kind == "empty":
            raise LifecycleError("PREVIOUS_RELEASE_ROOT has no authoritative maintenance state")
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
        if action == "inherit" and previous_root is not None
        else selected["source_release_root"]
    )
    output = (
        action,
        provenance_source_root,
        selected["authoritative_snapshot"],
        selected["authoritative_paused_ledger"],
    )
    sys.stdout.write("\n".join(output) + "\n")


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
    snapshot, paused = _validate_authoritative_ledgers(
        layout, args.snapshot, args.paused
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
