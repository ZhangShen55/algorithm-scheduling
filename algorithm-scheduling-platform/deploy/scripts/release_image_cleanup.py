from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PLATFORM_SERVICES = (
    "control-service",
    "orchestrator-service",
    "vision-orchestrator-service",
    "online-gateway-service",
)
GPU_OPERATORS = (
    "asr-offline",
    "asr-online",
    "ocr",
    "vbas",
    "facerec",
    "screen-det",
)
CPU_OPERATORS = ("ppt-slice", "text-analysis")
OPERATOR_SERVICES = tuple(
    [f"{operator}-gpu{gpu}" for operator in GPU_OPERATORS for gpu in range(3)]
    + [f"{operator}-cpu{index}" for operator in CPU_OPERATORS for index in range(3)]
)
SMOKE_OPERATORS = frozenset(
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
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
IMAGE_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
CONTAINER_ID_PATTERN = re.compile(r"[0-9a-f]{64}")
REVISION_LABEL = "org.opencontainers.image.revision"


class ImageCleanupError(RuntimeError):
    pass


def _run(command: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            list(command),
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except subprocess.TimeoutExpired as error:
        raise ImageCleanupError(f"command timed out: {command[0]}") from error
    if check and completed.returncode != 0:
        raise ImageCleanupError(f"command failed: {' '.join(command[:3])}")
    return completed


def _json_command(command: Sequence[str]) -> Any:
    completed = _run(command)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ImageCleanupError(f"command returned invalid JSON: {command[0]}") from error


def _validate_release_root(path: Path) -> tuple[str, str]:
    absolute = path.absolute()
    if absolute.name == "" or absolute.parent.name == "":
        raise ImageCleanupError("release root is invalid")
    sha = absolute.name
    tag = absolute.parent.name
    if absolute.parent.parent.name != "releases" or SHA_PATTERN.fullmatch(sha) is None:
        raise ImageCleanupError("release root must end with releases/<tag>/<git-sha>")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", tag) is None:
        raise ImageCleanupError("release tag is invalid")
    for parent in (absolute, *absolute.parents):
        if parent == Path(parent.anchor):
            continue
        try:
            metadata = os.lstat(parent)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise ImageCleanupError(f"release path cannot contain symlinks: {parent}")
    return tag, sha


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise ImageCleanupError(f"evidence already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        view = memoryview(
            (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
        )
        while view:
            view = view[os.write(descriptor, view) :]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
    except BaseException:
        temporary.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        raise


def _compose_container_ids(
    deploy_root: Path, compose_name: str, services: tuple[str, ...]
) -> list[str]:
    command = [
        "docker",
        "compose",
        "--project-directory",
        str(deploy_root),
        "-f",
        str(deploy_root / compose_name),
    ]
    if compose_name == "docker-compose.operators.yml":
        command.extend(["--profile", "*"])
    command.extend(["ps", "--no-trunc", "--status", "running", "-q", *services])
    ids = _run(command).stdout.splitlines()
    if len(ids) != len(services) or len(ids) != len(set(ids)):
        raise ImageCleanupError(
            f"{compose_name}: expected {len(services)} unique running containers"
        )
    if any(CONTAINER_ID_PATTERN.fullmatch(item) is None for item in ids):
        raise ImageCleanupError(f"{compose_name}: container ID is not a full ID")
    return ids


def _inspect_containers(ids: Sequence[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    payload = _json_command(["docker", "inspect", *ids])
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ImageCleanupError("docker inspect container result is invalid")
    return payload


def _controlled_containers(deploy_root: Path, *, require_healthy: bool) -> list[dict[str, Any]]:
    ids = _compose_container_ids(
        deploy_root, "docker-compose.platform.yml", PLATFORM_SERVICES
    ) + _compose_container_ids(
        deploy_root, "docker-compose.operators.yml", OPERATOR_SERVICES
    )
    records = _inspect_containers(ids)
    expected = set(PLATFORM_SERVICES) | set(OPERATOR_SERVICES)
    observed: dict[str, dict[str, Any]] = {}
    for record in records:
        container_id = record.get("Id")
        labels = (record.get("Config") or {}).get("Labels") or {}
        service = labels.get("com.docker.compose.service")
        state = record.get("State") or {}
        if container_id not in ids or service not in expected or service in observed:
            raise ImageCleanupError("controlled container identity does not match Compose")
        if state.get("Running") is not True:
            raise ImageCleanupError(f"controlled container is not running: {service}")
        if require_healthy and (state.get("Health") or {}).get("Status") != "healthy":
            raise ImageCleanupError(f"controlled container is not healthy: {service}")
        observed[service] = record
    if set(observed) != expected:
        raise ImageCleanupError("controlled container service set is incomplete")
    return [observed[name] for name in (*PLATFORM_SERVICES, *OPERATOR_SERVICES)]


def _inspect_images(ids: Sequence[str], *, check: bool = True) -> list[dict[str, Any]]:
    if not ids:
        return []
    completed = _run(["docker", "image", "inspect", *ids], check=check)
    if completed.returncode != 0:
        return []
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ImageCleanupError("docker image inspect returned invalid JSON") from error
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ImageCleanupError("docker image inspect result is invalid")
    return payload


def _all_container_references() -> dict[str, list[str]]:
    ids = _run(["docker", "ps", "-aq", "--no-trunc"]).stdout.splitlines()
    if any(CONTAINER_ID_PATTERN.fullmatch(item) is None for item in ids):
        raise ImageCleanupError("docker ps returned an invalid container ID")
    references: dict[str, list[str]] = {}
    for record in _inspect_containers(ids):
        container_id = record.get("Id")
        image_id = record.get("Image")
        if not isinstance(container_id, str) or not isinstance(image_id, str):
            raise ImageCleanupError("container image reference is incomplete")
        references.setdefault(image_id, []).append(container_id)
    return references


def _inventory(
    containers: list[dict[str, Any]], references: dict[str, list[str]]
) -> list[dict[str, Any]]:
    slots_by_image: dict[str, list[str]] = {}
    for container in containers:
        image_id = container.get("Image")
        service = ((container.get("Config") or {}).get("Labels") or {}).get(
            "com.docker.compose.service"
        )
        if IMAGE_ID_PATTERN.fullmatch(str(image_id)) is None or not isinstance(service, str):
            raise ImageCleanupError("controlled container image identity is invalid")
        slots_by_image.setdefault(str(image_id), []).append(service)
    records = _inspect_images(sorted(slots_by_image))
    by_id = {record.get("Id"): record for record in records}
    if set(by_id) != set(slots_by_image):
        raise ImageCleanupError("controlled image inspection is incomplete")
    result: list[dict[str, Any]] = []
    for image_id in sorted(slots_by_image):
        record = by_id[image_id]
        labels = (record.get("Config") or {}).get("Labels") or {}
        size = record.get("Size")
        tags = record.get("RepoTags") or []
        if not isinstance(size, int) or size < 0 or not all(isinstance(tag, str) for tag in tags):
            raise ImageCleanupError(f"image metadata is invalid: {image_id}")
        result.append(
            {
                "image_id": image_id,
                "revision": labels.get(REVISION_LABEL),
                "size_bytes": size,
                "repo_tags": sorted(tags),
                "compose_slots": sorted(slots_by_image[image_id]),
                "container_references": sorted(references.get(image_id, [])),
            }
        )
    return result


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ImageCleanupError(f"required evidence is missing or unsafe: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ImageCleanupError(f"required evidence is invalid: {path}") from error
    if not isinstance(payload, dict):
        raise ImageCleanupError(f"required evidence is not an object: {path}")
    return payload


def _validate_release_gates(release_root: Path, tag: str, sha: str) -> None:
    registration = _read_json(
        release_root / "registration" / "operator-registration.json"
    )
    if (
        registration.get("evidence_type") != "operator_registration"
        or registration.get("status") != "通过"
        or registration.get("release_tag") != tag
        or registration.get("git_sha") != sha
        or (registration.get("selection") or {}).get("mode") != "full"
        or (registration.get("summary") or {}).get("valid") != 24
        or len(registration.get("validated_instances") or []) != 24
    ):
        raise ImageCleanupError("full 24-instance registration evidence did not pass")
    observed_smoke: set[str] = set()
    for path in sorted((release_root / "smoke").glob("*.json")):
        payload = _read_json(path)
        operator = payload.get("operator_code")
        if (
            payload.get("evidence_type") != "operator_smoke"
            or payload.get("status") != "PASS"
            or payload.get("mock") is not False
            or payload.get("release_tag") != tag
            or payload.get("git_sha") != sha
            or operator not in SMOKE_OPERATORS
            or operator in observed_smoke
        ):
            raise ImageCleanupError(f"operator smoke evidence did not pass: {path.name}")
        observed_smoke.add(str(operator))
    if observed_smoke != SMOKE_OPERATORS:
        raise ImageCleanupError("eight-operator smoke evidence is incomplete")


def _snapshot(release_root: Path, deploy_root: Path, tag: str, sha: str) -> dict[str, Any]:
    containers = _controlled_containers(deploy_root, require_healthy=False)
    references = _all_container_references()
    return {
        "schema_version": 1,
        "evidence_type": "release_image_inventory_before",
        "status": "PASS",
        "release_tag": tag,
        "git_sha": sha,
        "created_at": datetime.now(UTC).isoformat(),
        "images": _inventory(containers, references),
    }


def _cleanup(
    release_root: Path,
    deploy_root: Path,
    tag: str,
    sha: str,
    *,
    execute: bool,
) -> dict[str, Any]:
    snapshot = _read_json(release_root / "preflight" / "image-inventory-before.json")
    if (
        snapshot.get("evidence_type") != "release_image_inventory_before"
        or snapshot.get("status") != "PASS"
        or snapshot.get("release_tag") != tag
        or snapshot.get("git_sha") != sha
        or not isinstance(snapshot.get("images"), list)
    ):
        raise ImageCleanupError("pre-build image snapshot does not match this release")
    _validate_release_gates(release_root, tag, sha)
    current_containers = _controlled_containers(deploy_root, require_healthy=True)
    current_inventory = _inventory(current_containers, _all_container_references())
    if any(image.get("revision") != sha for image in current_inventory):
        raise ImageCleanupError("current controlled images do not all match release Git SHA")
    current_ids = {str(image["image_id"]) for image in current_inventory}
    references = _all_container_references()
    deleted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for raw in snapshot["images"]:
        if not isinstance(raw, dict):
            raise ImageCleanupError("pre-build image snapshot contains an invalid row")
        image_id = raw.get("image_id")
        revision = raw.get("revision")
        reason: str | None = None
        if IMAGE_ID_PATTERN.fullmatch(str(image_id)) is None:
            raise ImageCleanupError("pre-build image snapshot contains an invalid image ID")
        if image_id in current_ids:
            reason = "current release still uses this image"
        elif SHA_PATTERN.fullmatch(str(revision)) is None or revision == sha:
            reason = "old release revision cannot be proven"
        elif references.get(str(image_id)):
            reason = "image is still referenced by a container"
        if reason is not None:
            skipped.append({"image_id": image_id, "reason": reason})
        else:
            candidates.append(raw)
    if execute:
        for candidate in candidates:
            image_id = str(candidate["image_id"])
            current_record = _inspect_images([image_id])
            if len(current_record) != 1:
                raise ImageCleanupError(f"candidate image disappeared before deletion: {image_id}")
            record = current_record[0]
            labels = (record.get("Config") or {}).get("Labels") or {}
            if (
                record.get("Id") != image_id
                or record.get("Size") != candidate.get("size_bytes")
                or labels.get(REVISION_LABEL) != candidate.get("revision")
            ):
                raise ImageCleanupError(f"candidate image changed before deletion: {image_id}")
            if _all_container_references().get(image_id):
                skipped.append(
                    {"image_id": image_id, "reason": "image gained a container reference"}
                )
                continue
            _run(["docker", "image", "rm", image_id])
            if _inspect_images([image_id], check=False):
                raise ImageCleanupError(f"deleted image is still inspectable: {image_id}")
            deleted.append(
                {
                    "image_id": image_id,
                    "revision": candidate.get("revision"),
                    "size_bytes": candidate.get("size_bytes"),
                }
            )
    return {
        "schema_version": 1,
        "evidence_type": "release_image_cleanup",
        "status": "PASS",
        "mode": "EXECUTE" if execute else "DRY_RUN",
        "release_tag": tag,
        "git_sha": sha,
        "created_at": datetime.now(UTC).isoformat(),
        "candidate_image_ids": [str(item["image_id"]) for item in candidates],
        "deleted": deleted,
        "skipped": skipped,
        "declared_reclaimed_bytes": sum(int(item["size_bytes"]) for item in deleted),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="快照并精确清理里程碑 2B 受控平台/算子旧镜像"
    )
    parser.add_argument("mode", choices=("snapshot", "cleanup"))
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--deploy-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        tag, sha = _validate_release_root(arguments.release_root)
        if arguments.mode == "snapshot":
            if arguments.execute:
                raise ImageCleanupError("snapshot mode does not accept --execute")
            output = arguments.release_root / "preflight" / "image-inventory-before.json"
            payload = _snapshot(
                arguments.release_root, arguments.deploy_root.absolute(), tag, sha
            )
        else:
            output_name = (
                "image-cleanup-result.json"
                if arguments.execute
                else "image-cleanup-plan.json"
            )
            output = arguments.release_root / "summary" / output_name
            payload = _cleanup(
                arguments.release_root,
                arguments.deploy_root.absolute(),
                tag,
                sha,
                execute=arguments.execute,
            )
        _atomic_json(output, payload)
    except (ImageCleanupError, OSError) as error:
        print(f"release image transaction failed: {error}", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
