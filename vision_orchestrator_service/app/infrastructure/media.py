from __future__ import annotations

import asyncio
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

from packages.platform_common.workspace import task_workspace

from .cache import VisionStream


class VideoFrameError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExtractedFrame:
    timestamp_seconds: float
    frame_index: int
    path: Path


class FFmpegFrameExtractor:
    def __init__(
        self,
        *,
        course_root: Path,
        ffmpeg_binary: str = "ffmpeg",
        ffprobe_binary: str = "ffprobe",
        command_timeout_seconds: float = 60.0,
    ) -> None:
        if command_timeout_seconds <= 0:
            raise ValueError("视频命令超时必须大于 0")
        self._course_root = course_root
        self._ffmpeg_binary = ffmpeg_binary
        self._ffprobe_binary = ffprobe_binary
        self._command_timeout_seconds = command_timeout_seconds

    async def duration_seconds(self, video_path: Path) -> float:
        resolved = self._validated_video(video_path)
        return await asyncio.to_thread(self._probe_duration, resolved)

    async def extract(
        self,
        *,
        task_id: str,
        stream: VisionStream,
        video_path: Path,
        timestamps: list[float],
    ) -> list[ExtractedFrame]:
        resolved = self._validated_video(video_path)
        output_root = (
            task_workspace(self._course_root, task_id) / "vision" / stream.value.lower()
        )
        output_root.mkdir(parents=True, exist_ok=True)
        unique_points = sorted({round(float(point), 6) for point in timestamps})
        if any(not math.isfinite(point) or point < 0 for point in unique_points):
            raise ValueError("视觉抽帧时间点必须是非负有限值")
        return await asyncio.gather(
            *(
                asyncio.to_thread(
                    self._extract_one,
                    resolved,
                    output_root,
                    point,
                )
                for point in unique_points
            )
        )

    def _validated_video(self, video_path: Path) -> Path:
        if not video_path.is_absolute():
            raise VideoFrameError("视觉视频路径必须是绝对本地路径")
        try:
            resolved = video_path.resolve(strict=True)
        except OSError as exc:
            raise VideoFrameError(f"视觉视频文件不存在: {video_path}") from exc
        root = self._course_root.resolve()
        if not resolved.is_relative_to(root):
            raise VideoFrameError(f"视觉视频必须位于课程目录: {resolved}")
        if not resolved.is_file():
            raise VideoFrameError(f"视觉视频路径不是文件: {resolved}")
        return resolved

    def _probe_duration(self, video_path: Path) -> float:
        completed = self._run(
            [
                self._ffprobe_binary,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ]
        )
        try:
            duration = float(completed.stdout.strip())
        except ValueError as exc:
            raise VideoFrameError(f"无法解析视频时长: {video_path}") from exc
        if not math.isfinite(duration) or duration <= 0:
            raise VideoFrameError(f"视频时长无效: {video_path}")
        return duration

    def _extract_one(
        self,
        video_path: Path,
        output_root: Path,
        timestamp_seconds: float,
    ) -> ExtractedFrame:
        timestamp_ms = round(timestamp_seconds * 1000)
        target = output_root / f"frame-{timestamp_ms:012d}.jpg"
        if not target.is_file() or target.stat().st_size <= 0:
            partial = output_root / f".{target.stem}.part.jpg"
            try:
                self._run(
                    [
                        self._ffmpeg_binary,
                        "-nostdin",
                        "-loglevel",
                        "error",
                        "-ss",
                        f"{timestamp_seconds:.6f}",
                        "-i",
                        str(video_path),
                        "-frames:v",
                        "1",
                        "-q:v",
                        "2",
                        "-y",
                        str(partial),
                    ]
                )
                if not partial.is_file() or partial.stat().st_size <= 0:
                    raise VideoFrameError(
                        f"视觉抽帧没有生成图片: {video_path} at {timestamp_seconds}s"
                    )
                partial.replace(target)
            finally:
                partial.unlink(missing_ok=True)
        return ExtractedFrame(
            timestamp_seconds=timestamp_seconds,
            frame_index=timestamp_ms,
            path=target,
        )

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=self._command_timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            detail = getattr(exc, "stderr", None) or str(exc)
            raise VideoFrameError(f"视频命令执行失败: {detail}") from exc
