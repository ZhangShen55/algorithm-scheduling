#!/usr/bin/env python3
"""Auditable Docker inventory and exact-ID production image lifecycle cleanup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

IMAGE_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
CONTAINER_ID_PATTERN = re.compile(r"[0-9a-f]{64}")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
RELEASE_TAG_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
REVISION_LABEL = "org.opencontainers.image.revision"
VERSION_LABEL = "org.opencontainers.image.version"
SMOKE_OPERATORS = {
    "asr_offline",
    "asr_online",
    "facerec",
    "ocr",
    "ppt_slice",
    "screen_det",
    "vbas",
}
FORBIDDEN_TEXT = (
    "docker system prune",
    "docker compose down",
    "docker volume",
    "/data/result",
    "/models/",
    "/model/",
    "/.git",
    "/reports/",
    "deploy/reports",
)
GLOB_CHARACTERS = frozenset("*?[]{}")
DOCKER_DF_COMMAND = ("docker", "system", "df", "-v", "--format", "json")
DOCKER_SIZE_PATTERN = re.compile(
    r"(?P<amount>(?:0|[1-9][0-9]*)(?:\.[0-9]+)?)(?P<unit>B|kB|MB|GB|TB|PB)"
)
DOCKER_SIZE_FACTORS: Mapping[str, int] = {
    "B": 1,
    "kB": 1_000,
    "MB": 1_000_000,
    "GB": 1_000_000_000,
    "TB": 1_000_000_000_000,
    "PB": 1_000_000_000_000_000,
}
RETIRED_CONTAINER_PROJECTS = frozenset(
    {"algorithm-scheduling-platform", "algorithm-operators"}
)
RETIRED_COMPOSE_IDENTITY_PATTERN = re.compile(
    r"(?:algorithm-scheduling-platform|algorithm-operators)/[a-z0-9][a-z0-9-]*"
)

JsonObject = dict[str, Any]
Stage = Literal["prebuild", "postacceptance"]
CommandOutput = Callable[[Sequence[str]], str]
CommandRunner = Callable[[Sequence[str]], None]
InventoryLoader = Callable[[], JsonObject]
TargetVerifier = Callable[[str, str], bool]


class ImageLifecycleError(RuntimeError):
    """Raised when cleanup cannot prove an exact and protected Docker state."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _atomic_publish(path: Path, payload: Mapping[str, object], *, write_once: bool) -> str:
    if path.exists() or path.is_symlink():
        if write_once:
            raise ImageLifecycleError(f"证据文件已经存在: {path}")
        if path.is_symlink() or not path.is_file():
            raise ImageLifecycleError(f"证据目标不是普通文件: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ImageLifecycleError(f"证据目录不得是符号链接: {path.parent}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_evidence(path: Path) -> JsonObject:
    if path.is_symlink() or not path.is_file():
        raise ImageLifecycleError(f"证据文件缺失或类型不安全: {path}")
    metadata = path.stat()
    if metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
        raise ImageLifecycleError(f"证据文件所有权或链接数不安全: {path}")
    if metadata.st_mode & 0o777 != 0o600:
        raise ImageLifecycleError(f"证据文件权限必须是 0600: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ImageLifecycleError(f"证据 JSON 无效: {path}") from error
    if type(value) is not dict:
        raise ImageLifecycleError(f"证据 JSON 顶层必须是对象: {path}")
    return cast(JsonObject, value)


def _docker_output(command: Sequence[str]) -> str:
    completed = subprocess.run(
        list(command),
        text=True,
        capture_output=True,
        check=False,
        timeout=900,
    )
    if completed.returncode != 0:
        raise ImageLifecycleError(f"Docker 盘点命令失败: {' '.join(command[:4])}")
    return completed.stdout


def _docker_run(command: Sequence[str]) -> None:
    completed = subprocess.run(
        list(command),
        text=True,
        capture_output=True,
        check=False,
        timeout=900,
    )
    if completed.returncode != 0:
        raise ImageLifecycleError(f"Docker 精确删除失败: {' '.join(command[:4])}")


def _inspect_list(
    command: Sequence[str],
    expected_ids: Sequence[str],
    *,
    output: CommandOutput,
) -> list[JsonObject]:
    if not expected_ids:
        return []
    try:
        raw = json.loads(output((*command, *expected_ids)))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ImageLifecycleError("Docker inspect 返回无效 JSON") from error
    if type(raw) is not list or len(raw) != len(expected_ids):
        raise ImageLifecycleError("Docker inspect 结果不完整")
    records: list[JsonObject] = []
    for item in raw:
        if type(item) is not dict:
            raise ImageLifecycleError("Docker inspect 记录不是对象")
        records.append(cast(JsonObject, item))
    return records


def _labels(raw: object) -> dict[str, str]:
    if raw is None:
        return {}
    if type(raw) is not dict or any(
        type(key) is not str or type(value) is not str for key, value in raw.items()
    ):
        raise ImageLifecycleError("Docker label 快照无效")
    return cast(dict[str, str], raw)


def parse_docker_size_bytes(value: object) -> int:
    """Parse Docker's decimal human-size output without binary-unit inflation."""

    if type(value) is not str:
        raise ImageLifecycleError("Docker UniqueSize 必须是字符串")
    match = DOCKER_SIZE_PATTERN.fullmatch(value)
    if match is None:
        raise ImageLifecycleError(f"Docker UniqueSize 无法解析: {value}")
    bytes_value = Decimal(match.group("amount")) * DOCKER_SIZE_FACTORS[match.group("unit")]
    integral = bytes_value.to_integral_value()
    if bytes_value != integral:
        raise ImageLifecycleError(f"Docker UniqueSize 不能表示完整字节: {value}")
    return int(integral)


def _docker_df_image_sizes(
    raw_output: str,
    expected_ids: Sequence[str],
) -> tuple[dict[str, tuple[str, int]], JsonObject]:
    try:
        document: object = json.loads(raw_output)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ImageLifecycleError("docker system df 返回无效 JSON") from error
    if type(document) is not dict or type(document.get("Images")) is not list:
        raise ImageLifecycleError("docker system df 缺少 Images 数组")
    rows = cast(list[object], document["Images"])
    sizes: dict[str, tuple[str, int]] = {}
    summary: list[JsonObject] = []
    for raw in rows:
        if type(raw) is not dict:
            raise ImageLifecycleError("docker system df Images 记录不是对象")
        image_id = raw.get("ID")
        unique_size = raw.get("UniqueSize")
        if IMAGE_ID_PATTERN.fullmatch(str(image_id)) is None:
            raise ImageLifecycleError("docker system df 返回了非完整镜像 ID")
        image_id = str(image_id)
        if image_id in sizes:
            raise ImageLifecycleError(f"docker system df 镜像 ID 重复: {image_id}")
        unique_size_bytes = parse_docker_size_bytes(unique_size)
        sizes[image_id] = (cast(str, unique_size), unique_size_bytes)
        summary.append(
            {
                "image_id": image_id,
                "unique_size": unique_size,
                "unique_size_bytes": unique_size_bytes,
            }
        )
    expected = set(expected_ids)
    actual = set(sizes)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append("缺失=" + ",".join(missing))
        if unknown:
            details.append("未知=" + ",".join(unknown))
        raise ImageLifecycleError("docker system df 镜像 ID 集合不完整: " + "；".join(details))
    evidence: JsonObject = {
        "command": list(DOCKER_DF_COMMAND),
        "raw_sha256": hashlib.sha256(raw_output.encode()).hexdigest(),
        "images": sorted(summary, key=lambda item: str(item["image_id"])),
    }
    return sizes, evidence


def summarize_docker_df(raw_output: str) -> JsonObject:
    """Keep only the image-size facts needed by cleanup evidence."""

    try:
        document: object = json.loads(raw_output)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ImageLifecycleError("docker system df 返回无效 JSON") from error
    if type(document) is not dict or type(document.get("Images")) is not list:
        raise ImageLifecycleError("docker system df 缺少 Images 数组")
    expected_ids: list[str] = []
    for raw in cast(list[object], document["Images"]):
        if type(raw) is not dict or type(raw.get("ID")) is not str:
            raise ImageLifecycleError("docker system df Images 记录缺少完整镜像 ID")
        expected_ids.append(cast(str, raw["ID"]))
    _, evidence = _docker_df_image_sizes(raw_output, expected_ids)
    images = cast(list[JsonObject], evidence["images"])
    return {
        **evidence,
        "image_count": len(images),
        "unique_size_bytes_total": sum(
            cast(int, image["unique_size_bytes"]) for image in images
        ),
    }


def capture_inventory(*, output: CommandOutput = _docker_output) -> JsonObject:
    """Inspect every container and image; any partial result fails closed."""

    container_ids = sorted(
        {line for line in output(("docker", "ps", "-aq", "--no-trunc")).splitlines() if line}
    )
    image_ids = sorted(
        {
            line
            for line in output(
                ("docker", "image", "ls", "--all", "--no-trunc", "--quiet")
            ).splitlines()
            if line
        }
    )
    if any(CONTAINER_ID_PATTERN.fullmatch(item) is None for item in container_ids):
        raise ImageLifecycleError("docker ps 返回了非完整容器 ID")
    if any(IMAGE_ID_PATTERN.fullmatch(item) is None for item in image_ids):
        raise ImageLifecycleError("docker image ls 返回了非完整镜像 ID")
    df_sizes, df_evidence = _docker_df_image_sizes(output(DOCKER_DF_COMMAND), image_ids)

    containers: list[JsonObject] = []
    for record in _inspect_list(("docker", "container", "inspect"), container_ids, output=output):
        container_id = record.get("Id")
        image_id = record.get("Image")
        config = record.get("Config") or {}
        state = record.get("State") or {}
        if type(config) is not dict or type(state) is not dict:
            raise ImageLifecycleError("容器 Config/State 快照无效")
        labels = _labels(config.get("Labels"))
        project = labels.get("com.docker.compose.project")
        service = labels.get("com.docker.compose.service")
        if (
            CONTAINER_ID_PATTERN.fullmatch(str(container_id)) is None
            or IMAGE_ID_PATTERN.fullmatch(str(image_id)) is None
            or (project is None) != (service is None)
            or type(state.get("Running")) is not bool
            or type(state.get("Status")) is not str
        ):
            raise ImageLifecycleError("容器身份、Compose 或状态快照不完整")
        containers.append(
            {
                "container_id": str(container_id),
                "image_id": str(image_id),
                "name": str(record.get("Name") or "").removeprefix("/"),
                "state": state["Status"],
                "running": state["Running"],
                "compose_project": project,
                "compose_service": service,
                "labels": dict(sorted(labels.items())),
            }
        )

    images: list[JsonObject] = []
    for record in _inspect_list(("docker", "image", "inspect"), image_ids, output=output):
        image_id = record.get("Id")
        config = record.get("Config") or {}
        if type(config) is not dict:
            raise ImageLifecycleError("镜像 Config 快照无效")
        labels = _labels(config.get("Labels"))
        repo_tags = record.get("RepoTags") or []
        repo_digests = record.get("RepoDigests") or []
        if (
            IMAGE_ID_PATTERN.fullmatch(str(image_id)) is None
            or type(repo_tags) is not list
            or any(type(item) is not str for item in repo_tags)
            or type(repo_digests) is not list
            or any(type(item) is not str for item in repo_digests)
        ):
            raise ImageLifecycleError("镜像 ID、标签或 digest 快照无效")
        image_id = str(image_id)
        if image_id not in df_sizes:
            raise ImageLifecycleError("镜像 inspect 与 system df ID 绑定不一致")
        unique_size, unique_size_bytes = df_sizes[image_id]
        images.append(
            {
                "image_id": image_id,
                "repo_tags": sorted(set(repo_tags)),
                "repo_digests": sorted(set(repo_digests)),
                "unique_size": unique_size,
                "unique_size_bytes": unique_size_bytes,
                "revision": labels.get(REVISION_LABEL),
                "release_tag": labels.get(VERSION_LABEL),
                "labels": dict(sorted(labels.items())),
            }
        )
    inventory: JsonObject = {
        "schema_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "containers": sorted(containers, key=lambda item: str(item["container_id"])),
        "images": sorted(images, key=lambda item: str(item["image_id"])),
        "docker_system_df": df_evidence,
    }
    validate_inventory(inventory)
    return inventory


def validate_inventory(inventory: Mapping[str, object]) -> None:
    if inventory.get("schema_version") != 1:
        raise ImageLifecycleError("Docker inventory schema_version 无效")
    containers = inventory.get("containers")
    images = inventory.get("images")
    docker_df = inventory.get("docker_system_df")
    if type(containers) is not list or type(images) is not list or type(docker_df) is not dict:
        raise ImageLifecycleError("Docker inventory 缺少容器、镜像或 system df 证据")
    if (
        docker_df.get("command") != list(DOCKER_DF_COMMAND)
        or re.fullmatch(r"[0-9a-f]{64}", str(docker_df.get("raw_sha256"))) is None
        or type(docker_df.get("images")) is not list
    ):
        raise ImageLifecycleError("Docker system df 证据摘要无效")
    df_sizes: dict[str, tuple[str, int]] = {}
    for raw in cast(list[object], docker_df["images"]):
        if type(raw) is not dict:
            raise ImageLifecycleError("Docker system df 镜像摘要无效")
        image_id = raw.get("image_id")
        unique_size = raw.get("unique_size")
        unique_size_bytes = raw.get("unique_size_bytes")
        if (
            IMAGE_ID_PATTERN.fullmatch(str(image_id)) is None
            or image_id in df_sizes
            or type(unique_size) is not str
            or type(unique_size_bytes) is not int
            or parse_docker_size_bytes(unique_size) != unique_size_bytes
        ):
            raise ImageLifecycleError("Docker system df 镜像摘要不完整或重复")
        df_sizes[str(image_id)] = (unique_size, unique_size_bytes)
    image_ids: set[str] = set()
    inventory_sizes: dict[str, tuple[str, int]] = {}
    for raw in images:
        if type(raw) is not dict:
            raise ImageLifecycleError("Docker inventory 镜像记录无效")
        image_id = raw.get("image_id")
        tags = raw.get("repo_tags")
        digests = raw.get("repo_digests")
        unique_size = raw.get("unique_size")
        unique_size_bytes = raw.get("unique_size_bytes")
        revision = raw.get("revision")
        if (
            IMAGE_ID_PATTERN.fullmatch(str(image_id)) is None
            or image_id in image_ids
            or type(tags) is not list
            or any(type(item) is not str for item in tags)
            or type(digests) is not list
            or any(type(item) is not str for item in digests)
            or type(unique_size) is not str
            or type(unique_size_bytes) is not int
            or parse_docker_size_bytes(unique_size) != unique_size_bytes
            or (revision is not None and SHA_PATTERN.fullmatch(str(revision)) is None)
        ):
            raise ImageLifecycleError("Docker inventory 镜像元数据不完整")
        image_id = str(image_id)
        image_ids.add(image_id)
        inventory_sizes[image_id] = (unique_size, unique_size_bytes)
    if inventory_sizes != df_sizes:
        raise ImageLifecycleError("Docker inventory 镜像与 system df UniqueSize 绑定不一致")
    container_ids: set[str] = set()
    for raw in containers:
        if type(raw) is not dict:
            raise ImageLifecycleError("Docker inventory 容器记录无效")
        container_id = raw.get("container_id")
        image_id = raw.get("image_id")
        project = raw.get("compose_project")
        service = raw.get("compose_service")
        if (
            CONTAINER_ID_PATTERN.fullmatch(str(container_id)) is None
            or container_id in container_ids
            or image_id not in image_ids
            or type(raw.get("running")) is not bool
            or type(raw.get("state")) is not str
            or (project is None) != (service is None)
            or (project is not None and type(project) is not str)
            or (service is not None and type(service) is not str)
        ):
            raise ImageLifecycleError("Docker inventory 容器元数据不完整")
        container_ids.add(str(container_id))


def inventory_fingerprint(inventory: Mapping[str, object]) -> str:
    validate_inventory(inventory)
    docker_df = cast(dict[str, object], inventory["docker_system_df"])
    stable = {
        "schema_version": inventory["schema_version"],
        "containers": inventory["containers"],
        "images": inventory["images"],
        "docker_system_df": {
            "command": docker_df["command"],
            "images": sorted(
                cast(list[JsonObject], docker_df["images"]),
                key=lambda item: str(item["image_id"]),
            ),
        },
    }
    return _sha256(stable)


def _exact_ids(values: Sequence[str], pattern: re.Pattern[str], description: str) -> list[str]:
    result = list(values)
    if any(pattern.fullmatch(value) is None for value in result) or len(result) != len(set(result)):
        raise ImageLifecycleError(f"{description} 必须是唯一完整 ID")
    return result


def _image_by_id(inventory: Mapping[str, object]) -> dict[str, JsonObject]:
    return {
        str(item["image_id"]): cast(JsonObject, item)
        for item in cast(list[object], inventory["images"])
        if type(item) is dict
    }


def _container_by_id(inventory: Mapping[str, object]) -> dict[str, JsonObject]:
    return {
        str(item["container_id"]): cast(JsonObject, item)
        for item in cast(list[object], inventory["containers"])
        if type(item) is dict
    }


def build_cleanup_plan(
    inventory: Mapping[str, object],
    *,
    stage: Stage,
    release_tag: str,
    git_sha: str,
    current_image_ids: Sequence[str],
    rollback_image_ids: Sequence[str],
    base_image_ids: Sequence[str],
    allow_image_ids: Sequence[str],
    retire_container_ids: Sequence[str],
    retire_compose_identities: Sequence[str],
    retired_release_shas: Sequence[str],
    acceptance_status: str | None = None,
) -> JsonObject:
    """Build the protected-set difference without mutating Docker."""

    validate_inventory(inventory)
    if stage not in {"prebuild", "postacceptance"}:
        raise ImageLifecycleError("清理阶段必须是 prebuild 或 postacceptance")
    if RELEASE_TAG_PATTERN.fullmatch(release_tag) is None or SHA_PATTERN.fullmatch(git_sha) is None:
        raise ImageLifecycleError("release_tag 或 git_sha 无效")
    protected_inputs = {
        "current_release": _exact_ids(current_image_ids, IMAGE_ID_PATTERN, "当前镜像"),
        "rollback_baseline": _exact_ids(rollback_image_ids, IMAGE_ID_PATTERN, "回滚镜像"),
        "base_images": _exact_ids(base_image_ids, IMAGE_ID_PATTERN, "基础镜像"),
        "allowlist": _exact_ids(allow_image_ids, IMAGE_ID_PATTERN, "允许列表镜像"),
    }
    if not protected_inputs["rollback_baseline"]:
        raise ImageLifecycleError("回滚镜像保护集不得为空")
    if not protected_inputs["base_images"]:
        raise ImageLifecycleError("基础镜像保护集不得为空")
    retire_ids = _exact_ids(retire_container_ids, CONTAINER_ID_PATTERN, "待退役容器")
    retire_identities = list(retire_compose_identities)
    if (
        len(retire_identities) != len(retire_ids)
        or len(retire_identities) != len(set(retire_identities))
        or any(
            RETIRED_COMPOSE_IDENTITY_PATTERN.fullmatch(identity) is None
            for identity in retire_identities
        )
    ):
        raise ImageLifecycleError("待退役容器必须逐项绑定唯一受控 Compose 身份")
    approved_retire_identities = dict(zip(retire_ids, retire_identities, strict=True))
    retired_shas = list(retired_release_shas)
    if any(SHA_PATTERN.fullmatch(value) is None for value in retired_shas) or len(
        retired_shas
    ) != len(set(retired_shas)):
        raise ImageLifecycleError("旧 release SHA 必须是唯一完整 SHA")
    if stage == "prebuild" and retire_ids:
        raise ImageLifecycleError("构建前清理不得退役容器")
    if stage == "postacceptance" and acceptance_status != "PASS":
        raise ImageLifecycleError("新版全部门禁未通过，禁止验收后退役")

    images = _image_by_id(inventory)
    containers = _container_by_id(inventory)
    configured_ids = {item for values in protected_inputs.values() for item in values}
    if not configured_ids.issubset(images):
        raise ImageLifecycleError("保护集包含 inventory 中不存在的镜像 ID")
    if not set(retire_ids).issubset(containers):
        raise ImageLifecycleError("待退役容器不在 inventory 中")
    candidate_containers: list[JsonObject] = []
    for container_id in retire_ids:
        record = containers[container_id]
        if record.get("running") is True:
            raise ImageLifecycleError(f"待退役容器仍在运行: {container_id}")
        image = images[str(record["image_id"])]
        compose_project = record.get("compose_project")
        compose_service = record.get("compose_service")
        compose_identity = f"{compose_project}/{compose_service}"
        if (
            compose_project not in RETIRED_CONTAINER_PROJECTS
            or type(compose_service) is not str
            or not compose_service
            or approved_retire_identities[container_id] != compose_identity
        ):
            raise ImageLifecycleError(f"待退役容器不属于受控 Compose 服务: {container_id}")
        if image.get("revision") not in retired_shas:
            raise ImageLifecycleError(f"待退役容器镜像不属于已退役 release: {container_id}")
        candidate_containers.append(
            {
                key: record.get(key)
                for key in (
                    "container_id",
                    "image_id",
                    "compose_project",
                    "compose_service",
                    "state",
                )
            }
            | {
                "approved_compose_identity": compose_identity,
                "before_snapshot_sha256": _sha256(record),
            }
        )

    protected_reasons: dict[str, set[str]] = {}
    for reason, ids in protected_inputs.items():
        for image_id in ids:
            protected_reasons.setdefault(image_id, set()).add(reason)
    for container_id, record in containers.items():
        if container_id not in retire_ids:
            protected_reasons.setdefault(str(record["image_id"]), set()).add("container_reference")

    remaining_references = {
        str(record["image_id"])
        for container_id, record in containers.items()
        if container_id not in retire_ids
    }
    candidate_images: list[JsonObject] = []
    for image_id, record in sorted(images.items()):
        if image_id in protected_reasons or image_id in remaining_references:
            continue
        dangling = not record.get("repo_tags") and not record.get("repo_digests")
        revision = record.get("revision")
        if revision == git_sha or record.get("release_tag") == release_tag:
            continue
        if revision not in retired_shas and not dangling:
            continue
        reason = "dangling_unreferenced" if dangling else "retired_release_unreferenced"
        candidate_images.append(
            {
                "image_id": image_id,
                "reason": reason,
                "release_revision": revision,
                "release_tag": record.get("release_tag"),
                "repo_tags": record.get("repo_tags"),
                "repo_digests": record.get("repo_digests"),
                "unique_size": record.get("unique_size"),
                "unique_size_bytes": record.get("unique_size_bytes"),
                "before_snapshot_sha256": _sha256(record),
            }
        )

    protected_ids = set(protected_reasons)
    candidate_ids = {str(item["image_id"]) for item in candidate_images}
    if protected_ids & candidate_ids:
        raise ImageLifecycleError("保护集与候选集存在交集")
    plan: JsonObject = {
        "schema_version": 1,
        "evidence_type": "production_image_cleanup_dry_run",
        "created_at": datetime.now(UTC).isoformat(),
        "stage": stage,
        "release_tag": release_tag,
        "git_sha": git_sha,
        "acceptance_status": acceptance_status,
        "inventory_fingerprint": inventory_fingerprint(inventory),
        "inventory": dict(inventory),
        "planning_inputs": {
            **protected_inputs,
            "retire_container_ids": retire_ids,
            "retire_compose_identities": retire_identities,
            "retired_release_shas": retired_shas,
        },
        "protected_image_ids": sorted(protected_ids),
        "protection_reasons": {
            image_id: sorted(reasons) for image_id, reasons in sorted(protected_reasons.items())
        },
        "candidate_containers": sorted(
            candidate_containers, key=lambda item: str(item["container_id"])
        ),
        "candidate_images": candidate_images,
        "estimated_reclaim_bytes": sum(
            cast(int, item["unique_size_bytes"]) for item in candidate_images
        ),
    }
    validate_no_forbidden_targets(plan)
    return plan


def publish_plan(path: Path, payload: Mapping[str, object]) -> str:
    validate_no_forbidden_targets(payload)
    _validate_plan_authority(payload)
    return _atomic_publish(path, payload, write_once=True)


def validate_no_forbidden_targets(value: object) -> None:
    """Reject destructive shortcuts, persistent paths, globs and unresolved variables."""

    def inspect(item: object) -> None:
        if type(item) is dict:
            for key, nested in item.items():
                if type(key) is str and key.lower() in {"target", "path", "command"}:
                    raise ImageLifecycleError(f"清理计划禁止通用删除字段: {key}")
                if key in {"inventory", "labels", "repo_tags", "repo_digests"}:
                    continue
                inspect(key)
                inspect(nested)
        elif type(item) is list:
            for nested in item:
                inspect(nested)
        elif type(item) is str:
            normalized = item.lower().replace("\\", "/")
            if (
                any(token in normalized for token in FORBIDDEN_TEXT)
                or any(character in item for character in GLOB_CHARACTERS)
                or "$" in item
                or item.startswith("~")
            ):
                raise ImageLifecycleError(f"清理计划包含禁止目标或宽泛表达式: {item}")

    inspect(value)


def deletion_commands(
    container_ids: Sequence[str],
    image_ids: Sequence[str],
) -> list[tuple[str, ...]]:
    containers = _exact_ids(container_ids, CONTAINER_ID_PATTERN, "删除容器")
    images = _exact_ids(image_ids, IMAGE_ID_PATTERN, "删除镜像")
    return [
        *(("docker", "container", "rm", container_id) for container_id in containers),
        *(("docker", "image", "rm", image_id) for image_id in images),
    ]


def _candidate_ids(plan: Mapping[str, object], key: str, field: str) -> list[str]:
    rows = plan.get(key)
    if type(rows) is not list or any(type(item) is not dict for item in rows):
        raise ImageLifecycleError(f"dry-run 缺少 {key}")
    return [str(cast(dict[str, object], item).get(field)) for item in rows]


def _replanned_images(plan: Mapping[str, object], inventory: JsonObject) -> list[str]:
    inputs = plan.get("planning_inputs")
    if type(inputs) is not dict:
        raise ImageLifecycleError("dry-run 缺少 planning_inputs")
    replanned = build_cleanup_plan(
        inventory,
        stage="prebuild",
        release_tag=str(plan.get("release_tag")),
        git_sha=str(plan.get("git_sha")),
        current_image_ids=cast(list[str], inputs.get("current_release") or []),
        rollback_image_ids=cast(list[str], inputs.get("rollback_baseline") or []),
        base_image_ids=cast(list[str], inputs.get("base_images") or []),
        allow_image_ids=cast(list[str], inputs.get("allowlist") or []),
        retire_container_ids=[],
        retire_compose_identities=[],
        retired_release_shas=cast(list[str], inputs.get("retired_release_shas") or []),
    )
    return _candidate_ids(replanned, "candidate_images", "image_id")


def _validate_plan_authority(plan: Mapping[str, object]) -> None:
    inventory = plan.get("inventory")
    inputs = plan.get("planning_inputs")
    stage = plan.get("stage")
    if type(inventory) is not dict or type(inputs) is not dict:
        raise ImageLifecycleError("dry-run 缺少 inventory 或 planning_inputs")
    if stage not in {"prebuild", "postacceptance"}:
        raise ImageLifecycleError("dry-run 清理阶段无效")
    rebuilt = build_cleanup_plan(
        cast(JsonObject, inventory),
        stage=cast(Stage, stage),
        release_tag=str(plan.get("release_tag")),
        git_sha=str(plan.get("git_sha")),
        current_image_ids=cast(list[str], inputs.get("current_release") or []),
        rollback_image_ids=cast(list[str], inputs.get("rollback_baseline") or []),
        base_image_ids=cast(list[str], inputs.get("base_images") or []),
        allow_image_ids=cast(list[str], inputs.get("allowlist") or []),
        retire_container_ids=cast(list[str], inputs.get("retire_container_ids") or []),
        retire_compose_identities=cast(
            list[str], inputs.get("retire_compose_identities") or []
        ),
        retired_release_shas=cast(list[str], inputs.get("retired_release_shas") or []),
        acceptance_status=cast(str | None, plan.get("acceptance_status")),
    )
    for key in (
        "inventory_fingerprint",
        "protected_image_ids",
        "protection_reasons",
        "candidate_containers",
        "candidate_images",
        "estimated_reclaim_bytes",
    ):
        if plan.get(key) != rebuilt.get(key):
            raise ImageLifecycleError(f"dry-run 权威字段已漂移: {key}")


def execute_plan(
    plan_path: Path,
    *,
    approved_sha256: str,
    live_inventory: JsonObject,
    command_runner: CommandRunner,
    result_path: Path,
    inventory_loader: InventoryLoader | None = None,
    target_verifier: TargetVerifier | None = None,
    docker_df_before: Mapping[str, object] | None = None,
    docker_df_after: Callable[[], Mapping[str, object]] | None = None,
) -> JsonObject:
    """Execute an approved immutable plan after full state and per-target checks."""

    if re.fullmatch(r"[0-9a-f]{64}", approved_sha256) is None:
        raise ImageLifecycleError("审核摘要必须是完整 SHA-256")
    plan = _read_evidence(plan_path)
    actual_digest = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    if actual_digest != approved_sha256:
        raise ImageLifecycleError("dry-run 文件摘要与审核值不一致")
    validate_no_forbidden_targets(plan)
    _validate_plan_authority(plan)
    validate_inventory(live_inventory)
    if plan.get("inventory_fingerprint") != inventory_fingerprint(live_inventory):
        raise ImageLifecycleError("dry-run 后 Docker 状态漂移，删除尚未开始")
    protected = set(cast(list[str], plan.get("protected_image_ids") or []))
    image_ids = _candidate_ids(plan, "candidate_images", "image_id")
    container_ids = _candidate_ids(plan, "candidate_containers", "container_id")
    if protected & set(image_ids):
        raise ImageLifecycleError("执行时保护集与候选集存在交集")
    deletion_commands(container_ids, image_ids)
    if (container_ids or image_ids) and (inventory_loader is None or target_verifier is None):
        raise ImageLifecycleError("实际删除必须提供二次 inventory 与删除后验证器")

    started_at = datetime.now(UTC).isoformat()
    results: list[JsonObject] = []
    failure: str | None = None

    def delete(kind: str, target_id: str, snapshot: Mapping[str, object]) -> None:
        nonlocal failure
        if failure is not None:
            results.append(
                {
                    f"{kind}_id": target_id,
                    "before_snapshot_sha256": snapshot.get("before_snapshot_sha256"),
                    "executed_at": None,
                    "success": False,
                    "verified_absent": False,
                    "error": "前一目标失败，未执行",
                }
            )
            return
        command = (
            ("docker", "container", "rm", target_id)
            if kind == "container"
            else ("docker", "image", "rm", target_id)
        )
        executed_at = datetime.now(UTC).isoformat()
        try:
            command_runner(command)
            assert target_verifier is not None
            absent = not target_verifier(kind, target_id)
            if not absent:
                raise ImageLifecycleError("删除后二次 inspect 仍发现目标")
            results.append(
                {
                    f"{kind}_id": target_id,
                    "before_snapshot_sha256": snapshot.get("before_snapshot_sha256"),
                    "executed_at": executed_at,
                    "success": True,
                    "verified_absent": True,
                    "error": None,
                }
            )
        except (OSError, subprocess.SubprocessError, ImageLifecycleError) as error:
            failure = str(error)
            results.append(
                {
                    f"{kind}_id": target_id,
                    "before_snapshot_sha256": snapshot.get("before_snapshot_sha256"),
                    "executed_at": executed_at,
                    "success": False,
                    "verified_absent": False,
                    "error": str(error),
                }
            )

    container_rows = cast(list[JsonObject], plan["candidate_containers"])
    for row in container_rows:
        delete("container", str(row["container_id"]), row)

    image_rows = cast(list[JsonObject], plan["candidate_images"])
    for index, row in enumerate(image_rows):
        if failure is None:
            assert inventory_loader is not None
            try:
                current = inventory_loader()
                if _replanned_images(plan, current) != image_ids[index:]:
                    failure = "镜像删除前重算候选发生状态漂移"
            except ImageLifecycleError as error:
                failure = str(error)
        delete("image", str(row["image_id"]), row)

    if failure is None and (container_ids or image_ids):
        assert inventory_loader is not None
        try:
            final_inventory = inventory_loader()
            final_containers = _container_by_id(final_inventory)
            final_images = _image_by_id(final_inventory)
            if set(container_ids) & set(final_containers) or set(image_ids) & set(final_images):
                failure = "最终 inventory 仍包含计划删除目标"
            if not protected.issubset(final_images):
                failure = "最终 inventory 缺少受保护镜像"
        except ImageLifecycleError as error:
            failure = str(error)

    completed_at = datetime.now(UTC).isoformat()
    status = (
        "FAIL"
        if failure is not None
        else ("AWAITING_REVALIDATION" if plan.get("stage") == "postacceptance" else "PASS")
    )
    df_after_value: Mapping[str, object] | None = None
    if docker_df_after is not None:
        try:
            df_after_value = docker_df_after()
        except (OSError, subprocess.SubprocessError, ImageLifecycleError) as error:
            failure = str(error)
            status = "FAIL"
    result: JsonObject = {
        "schema_version": 1,
        "evidence_type": "production_image_cleanup_execution",
        "stage": plan.get("stage"),
        "release_tag": plan.get("release_tag"),
        "git_sha": plan.get("git_sha"),
        "plan_sha256": approved_sha256,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": status,
        "error": failure,
        "docker_system_df_before": docker_df_before,
        "docker_system_df_after": df_after_value,
        "targets": results,
    }
    _atomic_publish(result_path, result, write_once=True)
    if failure is not None:
        raise ImageLifecycleError(f"精确清理未全部成功: {failure}")
    return result


def verify_cleanup_readiness(
    execution: Mapping[str, object],
    stack_status: Mapping[str, object],
    smoke_results: Sequence[Mapping[str, object]],
) -> JsonObject:
    """Publishable completion requires the new stack and fresh 7/7 Smoke."""

    if (
        execution.get("stage") != "postacceptance"
        or execution.get("status") != "AWAITING_REVALIDATION"
    ):
        raise ImageLifecycleError("只有待重验的验收后清理可以发布完成结论")
    summary = stack_status.get("summary")
    if (
        stack_status.get("status") != "PASS"
        or stack_status.get("git_sha") != execution.get("git_sha")
        or type(summary) is not dict
        or summary.get("infrastructure") != 4
        or summary.get("platform_services") != 4
        or summary.get("operator_instances") != 21
        or summary.get("gpu_instances") != 18
        or summary.get("cpu_instances") != 3
        or summary.get("registered_instances") != 21
    ):
        raise ImageLifecycleError("清理后常驻栈未证明四中间件、四平台和 21 实例就绪")
    operators: set[str] = set()
    for smoke in smoke_results:
        operator = smoke.get("operator_code")
        if (
            smoke.get("evidence_type") != "operator_smoke"
            or smoke.get("status") != "PASS"
            or smoke.get("git_sha") != execution.get("git_sha")
            or smoke.get("release_tag") != execution.get("release_tag")
            or smoke.get("mock") is not False
            or operator not in SMOKE_OPERATORS
            or operator in operators
        ):
            raise ImageLifecycleError("清理后 7/7 Smoke 证据无效或重复")
        operators.add(str(operator))
    if operators != SMOKE_OPERATORS:
        raise ImageLifecycleError("清理后 7/7 Smoke 证据不完整")
    return {
        "schema_version": 1,
        "evidence_type": "production_image_cleanup_completion",
        "status": "PASS",
        "release_tag": execution.get("release_tag"),
        "git_sha": execution.get("git_sha"),
        "cleanup_execution_sha256": _sha256(execution),
        "verified_at": datetime.now(UTC).isoformat(),
        "checks": {
            "infrastructure": 4,
            "platform_services": 4,
            "operator_instances": 21,
            "gpu_instances": 18,
            "cpu_instances": 3,
            "registered_instances": 21,
            "operator_smoke": 7,
        },
    }


def verify_evidence_freshness(
    execution: Mapping[str, object], evidence_paths: Sequence[Path]
) -> None:
    completed_at = execution.get("completed_at")
    if type(completed_at) is not str:
        raise ImageLifecycleError("清理执行证据缺少 completed_at")
    try:
        cutoff = datetime.fromisoformat(completed_at)
    except ValueError as error:
        raise ImageLifecycleError("清理执行 completed_at 无效") from error
    if cutoff.tzinfo is None:
        raise ImageLifecycleError("清理执行 completed_at 必须带时区")
    for path in evidence_paths:
        if path.is_symlink() or not path.is_file():
            raise ImageLifecycleError(f"清理后重验证据缺失或类型不安全: {path}")
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if modified < cutoff:
            raise ImageLifecycleError(f"清理后重验证据早于清理完成时间: {path}")


def _target_exists(kind: str, target_id: str) -> bool:
    command = ["docker", kind, "inspect", target_id]
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if completed.returncode == 0:
        return True
    if "no such" in completed.stderr.lower():
        return False
    raise ImageLifecycleError(f"无法验证删除结果: {kind} {target_id}")


def _docker_df() -> JsonObject:
    return summarize_docker_df(_docker_output(DOCKER_DF_COMMAND))


def _ids_argument(parser: argparse.ArgumentParser, name: str, *, required: bool = False) -> None:
    parser.add_argument(name, action="append", default=[], required=required)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按完整 ID 管理生产镜像生命周期", allow_abbrev=False
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser("inventory", allow_abbrev=False)
    inventory.add_argument("--output", type=Path, required=True)

    plan = subparsers.add_parser("plan", allow_abbrev=False)
    plan.add_argument("--inventory", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--stage", choices=("prebuild", "postacceptance"), required=True)
    plan.add_argument("--release-tag", required=True)
    plan.add_argument("--git-sha", required=True)
    _ids_argument(plan, "--current-image-id", required=True)
    _ids_argument(plan, "--rollback-image-id", required=True)
    _ids_argument(plan, "--base-image-id", required=True)
    _ids_argument(plan, "--allow-image-id")
    _ids_argument(plan, "--retire-container-id")
    plan.add_argument("--retire-compose-identity", action="append", default=[])
    plan.add_argument("--retired-release-sha", action="append", default=[])
    plan.add_argument("--acceptance-status")

    execute = subparsers.add_parser("execute", allow_abbrev=False)
    execute.add_argument("--plan", type=Path, required=True)
    execute.add_argument("--approved-sha256", required=True)
    execute.add_argument("--result", type=Path, required=True)

    verify = subparsers.add_parser("verify", allow_abbrev=False)
    verify.add_argument("--result", type=Path, required=True)
    verify.add_argument("--stack-status", type=Path, required=True)
    verify.add_argument("--smoke-evidence", type=Path, action="append", required=True)
    verify.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "inventory":
        inventory = capture_inventory()
        digest = _atomic_publish(args.output, inventory, write_once=True)
        print(f"inventory: PASS sha256={digest}")
    elif args.command == "plan":
        inventory = _read_evidence(args.inventory)
        if len(args.current_image_id) != 11:
            raise ImageLifecycleError("生产清理 CLI 必须显式保护当前 11 个目标镜像 ID")
        payload = build_cleanup_plan(
            inventory,
            stage=args.stage,
            release_tag=args.release_tag,
            git_sha=args.git_sha,
            current_image_ids=args.current_image_id,
            rollback_image_ids=args.rollback_image_id,
            base_image_ids=args.base_image_id,
            allow_image_ids=args.allow_image_id,
            retire_container_ids=args.retire_container_id,
            retire_compose_identities=args.retire_compose_identity,
            retired_release_shas=args.retired_release_sha,
            acceptance_status=args.acceptance_status,
        )
        digest = publish_plan(args.output, payload)
        print(f"cleanup-plan: DRY-RUN sha256={digest}")
    elif args.command == "execute":
        execute_plan(
            args.plan,
            approved_sha256=args.approved_sha256,
            live_inventory=capture_inventory(),
            command_runner=_docker_run,
            result_path=args.result,
            inventory_loader=capture_inventory,
            target_verifier=_target_exists,
            docker_df_before=_docker_df(),
            docker_df_after=_docker_df,
        )
        print("cleanup-execution: completed; postacceptance requires verify")
    else:
        execution = _read_evidence(args.result)
        verify_evidence_freshness(
            execution,
            [args.stack_status, *args.smoke_evidence],
        )
        stack_status = _read_evidence(args.stack_status)
        smoke = [_read_evidence(path) for path in args.smoke_evidence]
        completion = verify_cleanup_readiness(execution, stack_status, smoke)
        digest = _atomic_publish(args.output, completion, write_once=True)
        print(f"cleanup-verification: PASS sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
