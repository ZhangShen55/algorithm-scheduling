from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .models import BenchmarkMode, FixtureEntry, FixtureManifest, JsonObject


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_image(path: Path, *, ffprobe_binary: str = "ffprobe") -> tuple[int, int]:
    process = subprocess.run(
        [
            ffprobe_binary,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    document = json.loads(process.stdout)
    streams = document.get("streams")
    if not isinstance(streams, list) or len(streams) != 1:
        raise ValueError(f"无法读取图片分辨率: {path}")
    stream = streams[0]
    if not isinstance(stream, dict):
        raise TypeError(f"图片流信息不是对象: {path}")
    width, height = stream.get("width"), stream.get("height")
    if isinstance(width, bool) or not isinstance(width, int):
        raise TypeError(f"图片宽度不合法: {path}")
    if isinstance(height, bool) or not isinstance(height, int):
        raise TypeError(f"图片高度不合法: {path}")
    return width, height


def build_manifest(
    fixture_root: Path,
    categorized_paths: Iterable[tuple[Path, str]],
    *,
    source_videos: Iterable[JsonObject] = (),
) -> FixtureManifest:
    root = fixture_root.resolve(strict=True)
    entries: list[FixtureEntry] = []
    for raw_path, category in categorized_paths:
        path = raw_path.resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"fixture 必须是根目录下的普通文件: {path}")
        width, height = probe_image(path)
        entries.append(
            FixtureEntry(
                relative_path=path.relative_to(root).as_posix(),
                byte_size=path.stat().st_size,
                width=width,
                height=height,
                category=category,
                sha256=sha256_file(path),
            )
        )
    return FixtureManifest(1, tuple(source_videos), tuple(entries))


def load_manifest(path: Path) -> FixtureManifest:
    return FixtureManifest.from_document(json.loads(path.read_text(encoding="utf-8")))


def validate_manifest_files(manifest: FixtureManifest, fixture_root: Path) -> None:
    root = fixture_root.resolve(strict=True)
    for entry in manifest.entries:
        path = (root / entry.relative_path).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"fixture 路径逃逸或不是文件: {entry.relative_path}")
        if path.stat().st_size != entry.byte_size:
            raise ValueError(f"fixture 字节数不一致: {entry.relative_path}")
        if sha256_file(path) != entry.sha256:
            raise ValueError(f"fixture SHA-256 不一致: {entry.relative_path}")


def build_batch_payload(
    *,
    manifest: FixtureManifest,
    fixture_root_in_container: Path,
    mode: BenchmarkMode,
    batch_size: int,
    sequence: int,
    seed: int,
) -> tuple[str, JsonObject]:
    if batch_size <= 0:
        raise ValueError("batch_size 必须大于 0")
    stream = _stream_for_sequence(mode, sequence)
    entries = tuple(
        entry
        for entry in manifest.entries_for(stream)
        if entry.category in {stream.value, "empty", "multi_person"}
    )
    if not entries:
        raise ValueError(f"manifest 中没有 {stream.value} 可用图片")
    offset = (seed + sequence * batch_size) % len(entries)
    selected = [entries[(offset + index) % len(entries)] for index in range(batch_size)]
    task_id = f"vbas-bench-{stream.value}-{sequence:012d}"
    image_list: list[JsonObject] = []
    for index, entry in enumerate(selected):
        image_id = f"{stream.value}-{sequence:012d}-{index:03d}"
        image_list.append(
            {
                "StoragePath": str(fixture_root_in_container / entry.relative_path),
                "ImageId": image_id,
                "frame_id": image_id,
                "frame_index": sequence * batch_size + index,
                "timestamp_seconds": float(sequence * batch_size + index),
            }
        )
    payload: JsonObject = {
        "task_id": task_id,
        "batch_id": f"{task_id}-batch",
        "stream_type": stream.value,
        "ImageList": image_list,
    }
    if stream is BenchmarkMode.TEACHER:
        payload["ReturnHeadPose"] = False
    return stream.value, payload


def _stream_for_sequence(mode: BenchmarkMode, sequence: int) -> BenchmarkMode:
    if mode is not BenchmarkMode.MIXED:
        return mode
    return BenchmarkMode.TEACHER if sequence % 2 == 0 else BenchmarkMode.STUDENT


def endpoint_for_stream(stream: str) -> str:
    if stream == BenchmarkMode.TEACHER.value:
        return "/ImageDetect/teacher/v1.0.0"
    if stream == BenchmarkMode.STUDENT.value:
        return "/ImageDetect/student/v1.0.0"
    raise ValueError(f"不支持的 VBas 流类型: {stream}")


def parse_successful_batch(body: Any, expected_count: int) -> bool:
    if not isinstance(body, dict):
        return False
    status = body.get("StatusObject")
    data = body.get("DataList")
    return (
        isinstance(status, dict)
        and status.get("StatusCode") == 0
        and isinstance(data, list)
        and len(data) == expected_count
    )
