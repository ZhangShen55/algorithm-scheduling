from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

JsonObject = dict[str, Any]
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class BenchmarkMode(StrEnum):
    TEACHER = "teacher"
    STUDENT = "student"
    MIXED = "mixed"


@dataclass(frozen=True, slots=True)
class FixtureEntry:
    relative_path: str
    byte_size: int
    width: int
    height: int
    category: str
    sha256: str

    def __post_init__(self) -> None:
        path = Path(self.relative_path)
        if path.is_absolute() or ".." in path.parts or not self.relative_path:
            raise ValueError("fixture 路径必须是不逃逸的相对路径")
        if self.byte_size <= 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("fixture 字节数和分辨率必须大于 0")
        if self.category not in {"teacher", "student", "empty", "multi_person"}:
            raise ValueError(f"fixture 类别不受支持: {self.category}")
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("fixture SHA-256 格式不正确")

    def to_document(self) -> JsonObject:
        return {
            "relative_path": self.relative_path,
            "byte_size": self.byte_size,
            "width": self.width,
            "height": self.height,
            "category": self.category,
            "sha256": self.sha256,
        }

    @classmethod
    def from_document(cls, value: object) -> FixtureEntry:
        if not isinstance(value, dict):
            raise TypeError("fixture manifest 条目必须是对象")
        return cls(
            relative_path=str(value["relative_path"]),
            byte_size=_strict_int(value["byte_size"], "byte_size"),
            width=_strict_int(value["width"], "width"),
            height=_strict_int(value["height"], "height"),
            category=str(value["category"]),
            sha256=str(value["sha256"]),
        )


@dataclass(frozen=True, slots=True)
class FixtureManifest:
    schema_version: int
    source_videos: tuple[JsonObject, ...]
    entries: tuple[FixtureEntry, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("fixture manifest schema_version 必须为 1")
        if not self.entries:
            raise ValueError("fixture manifest 至少需要一张图片")
        paths = [item.relative_path for item in self.entries]
        if len(paths) != len(set(paths)):
            raise ValueError("fixture manifest 不允许重复路径")

    def entries_for(self, mode: BenchmarkMode) -> tuple[FixtureEntry, ...]:
        if mode is BenchmarkMode.MIXED:
            return tuple(
                item
                for item in self.entries
                if item.category in {"teacher", "student", "empty", "multi_person"}
            )
        accepted = {mode.value, "empty", "multi_person"}
        return tuple(item for item in self.entries if item.category in accepted)

    def to_document(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "source_videos": list(self.source_videos),
            "entries": [item.to_document() for item in self.entries],
        }

    @classmethod
    def from_document(cls, value: object) -> FixtureManifest:
        if not isinstance(value, dict):
            raise TypeError("fixture manifest 必须是对象")
        source_videos = value.get("source_videos", [])
        entries = value.get("entries")
        if not isinstance(source_videos, list) or not all(
            isinstance(item, dict) for item in source_videos
        ):
            raise TypeError("source_videos 必须是对象列表")
        if not isinstance(entries, list):
            raise TypeError("entries 必须是列表")
        return cls(
            schema_version=_strict_int(value.get("schema_version"), "schema_version"),
            source_videos=tuple(dict(item) for item in source_videos),
            entries=tuple(FixtureEntry.from_document(item) for item in entries),
        )


@dataclass(frozen=True, slots=True)
class BenchmarkIdentity:
    campaign_id: str
    attempt: int
    seed: int
    git_sha: str
    image_ids: tuple[str, ...]
    config_digest: str

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.campaign_id):
            raise ValueError("campaign_id 必须是 1-128 位安全标识")
        if self.attempt <= 0:
            raise ValueError("attempt 必须大于 0")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed 必须是整数")
        if not re.fullmatch(r"[0-9a-f]{7,40}", self.git_sha):
            raise ValueError("git_sha 格式不正确")
        if not self.image_ids or any(not value for value in self.image_ids):
            raise ValueError("必须记录非空镜像 ID")
        if not re.fullmatch(r"[0-9a-f]{64}", self.config_digest):
            raise ValueError("配置摘要必须是 SHA-256")


@dataclass(frozen=True, slots=True)
class VbasTarget:
    instance_id: str
    base_url: str
    gpu_index: int
    container_name: str

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.instance_id):
            raise ValueError("VBas instance_id 不合法")
        parsed = urlsplit(self.base_url.rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("VBas base_url 必须是 HTTP/HTTPS origin")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("VBas base_url 不得包含凭据或查询参数")
        if parsed.path not in {"", "/"}:
            raise ValueError("VBas base_url 不得包含业务路径")
        if self.gpu_index < 0 or not self.container_name:
            raise ValueError("GPU 序号不能为负且容器名不能为空")


@dataclass(frozen=True, slots=True)
class BenchmarkGuardrails:
    max_error_rate: float = 0.01
    max_gpu_memory_mib: int = 22_000
    max_latency_degradation_ratio: float = 2.0
    max_replenish_p95_seconds: float = 0.1
    min_inflight_ratio: float = 0.90
    memory_half_delta_tolerance_mib: float = 256.0
    memory_slope_tolerance_mib_per_minute: float = 64.0

    def __post_init__(self) -> None:
        ratios = (
            self.max_error_rate,
            self.max_latency_degradation_ratio,
            self.min_inflight_ratio,
        )
        if any(not math.isfinite(value) or value < 0 for value in ratios):
            raise ValueError("护栏比率必须是有限非负数")
        if self.max_error_rate > 1 or self.min_inflight_ratio > 1:
            raise ValueError("错误率和最低在途比例不能大于 1")
        if self.max_gpu_memory_mib <= 0:
            raise ValueError("显存硬护栏必须大于 0")


def config_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} 必须是整数")
    return value
