"""Shared-directory result publication for PPT slices."""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Awaitable, Callable

import cv2

from app.core.logger import get_logger

logger = get_logger("shared_result")

_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_identifier(value: str, field_name: str) -> str:
    if not _TASK_ID_PATTERN.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"{field_name} 只能包含字母、数字、点、下划线和连字符")
    return value


def _safe_task_root(result_root: Path, task_id: str) -> Path:
    root = result_root.expanduser().resolve()
    task_root = root / _validate_identifier(task_id, "task_id")
    if task_root.parent.resolve() != root:
        raise ValueError("task_id 不能跳出 result_root")
    return task_root


@dataclass(frozen=True)
class SliceImage:
    frame_seq: int
    snap_time: int
    path: str


class SharedResultWriter:
    """Atomically writes slice images and one manifest below result_root."""

    def __init__(self, result_root: Path, task_id: str, operator_task_id: str):
        self.task_id = _validate_identifier(task_id, "task_id")
        self.operator_task_id = _validate_identifier(
            operator_task_id,
            "operator_task_id",
        )
        self.task_root = _safe_task_root(Path(result_root), self.task_id)
        ppt_dir = self.task_root / "ppt"
        self.output_dir = ppt_dir / "slices"
        self.manifest_path = ppt_dir / "manifest.json"
        for directory in (self.task_root, ppt_dir, self.output_dir):
            if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
                raise ValueError(f"共享结果目录不安全: {directory}")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not self.output_dir.resolve().is_relative_to(self.task_root.parent.resolve()):
            raise ValueError("PPT 输出目录不能跳出 result_root")
        self._images: list[SliceImage] = []
        self._dynamic_segments: list[dict] = []
        self._lock = threading.Lock()

    @property
    def images(self) -> tuple[SliceImage, ...]:
        with self._lock:
            return tuple(self._images)

    def write_image(self, *, frame_seq: int, snap_time: int, frame) -> SliceImage:
        with self._lock:
            image_number = len(self._images) + 1
            filename = (
                f"ppt-{image_number:04d}-f{int(frame_seq)}-t{int(snap_time)}s.jpg"
            )
            final_path = self.output_dir / filename
            partial_path = final_path.with_name(f"{final_path.name}.part")
            encoded, buffer = cv2.imencode(".jpg", frame)
            if not encoded:
                raise RuntimeError("PPT 切片 JPEG 编码失败")
            try:
                partial_path.write_bytes(buffer.tobytes())
                os.replace(partial_path, final_path)
            finally:
                partial_path.unlink(missing_ok=True)
            image = SliceImage(
                frame_seq=int(frame_seq),
                snap_time=int(snap_time),
                path=str(final_path),
            )
            self._images.append(image)
            return image

    def set_dynamic_segments(self, segments) -> None:
        normalized = []
        for segment in segments:
            value = segment.as_dict() if hasattr(segment, "as_dict") else dict(segment)
            start_ms = int(value["start_ms"])
            end_ms = int(value["end_ms"])
            confidence = float(value["confidence"])
            if start_ms >= end_ms:
                raise ValueError("动态区间必须满足 start_ms < end_ms")
            if not 0 <= confidence <= 1:
                raise ValueError("动态区间 confidence 必须位于 [0,1]")
            normalized.append(
                {
                    "type": str(value["type"]),
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "confidence": confidence,
                    "reason": str(value["reason"]),
                }
            )
        normalized.sort(key=lambda item: item["start_ms"])
        for previous, current in zip(normalized, normalized[1:]):
            if current["start_ms"] < previous["end_ms"]:
                raise ValueError("动态区间不能重叠")
        with self._lock:
            self._dynamic_segments = normalized

    def build_manifest(self, *, status: int, reason: str = "") -> dict:
        with self._lock:
            images = [asdict(image) for image in self._images]
            dynamic_segments = [dict(segment) for segment in self._dynamic_segments]
        return {
            "schema_version": 1,
            "task_id": self.task_id,
            "operator_task_id": self.operator_task_id,
            "status": int(status),
            "path": str(self.output_dir),
            "manifest_path": str(self.manifest_path),
            "count": len(images),
            "reason": reason,
            "images": images,
            "dynamic_segments": dynamic_segments,
        }

    def write_manifest(self, *, status: int, reason: str = "") -> Path:
        manifest = self.build_manifest(status=status, reason=reason)
        partial_path = self.manifest_path.with_name(f"{self.manifest_path.name}.part")
        try:
            partial_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(partial_path, self.manifest_path)
        finally:
            partial_path.unlink(missing_ok=True)
        return self.manifest_path


TerminalCallback = Callable[[dict], Awaitable[object]]


class TerminalResultPublisher:
    """Publishes manifest and callback at most once for a task."""

    def __init__(self, writer: SharedResultWriter, callback: TerminalCallback):
        self.writer = writer
        self.callback = callback
        self._published = False
        self._lock = threading.Lock()

    async def publish_once(self, *, status: int, reason: str = "") -> bool:
        with self._lock:
            if self._published:
                return False
            self.writer.write_manifest(status=status, reason=reason)
            self._published = True

        payload = self.writer.build_manifest(status=status, reason=reason)
        payload.pop("schema_version")
        payload.pop("images")
        try:
            await self.callback(payload)
        except Exception as exc:
            logger.error(
                "终态回调失败: task_id=%s operator_task_id=%s uri_error=%s",
                self.writer.task_id,
                self.writer.operator_task_id,
                exc,
                exc_info=True,
            )
        return True
