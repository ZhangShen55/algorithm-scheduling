from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import tempfile
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from packages.platform_common.workspace import task_workspace

from .cache import VisionStream
from .metrics import VisionPipelineMetrics


class VideoFrameError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExtractedFrame:
    timestamp_seconds: float
    frame_index: int
    path: Path


@dataclass(frozen=True, slots=True)
class FrameBatchPlan:
    batch_id: str
    batch_index: int
    timestamps: tuple[float, ...]


def build_frame_batch_plans(
    *,
    task_id: str,
    stream: VisionStream,
    timestamps: list[float],
    batch_size: int,
    identity_suffix: str = "full",
) -> tuple[FrameBatchPlan, ...]:
    if batch_size <= 0:
        raise ValueError("视觉抽帧批次大小必须大于 0")
    points = tuple(sorted({round(float(point), 6) for point in timestamps}))
    if any(not math.isfinite(point) or point < 0 for point in points):
        raise ValueError("视觉抽帧时间点必须是非负有限值")
    plans: list[FrameBatchPlan] = []
    for batch_index, start in enumerate(range(0, len(points), batch_size)):
        batch_points = points[start : start + batch_size]
        digest = hashlib.sha256(
            json.dumps(
                {
                    "task_id": task_id,
                    "stream": stream.value,
                    "identity": identity_suffix,
                    "timestamps": batch_points,
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:20]
        plans.append(
            FrameBatchPlan(
                batch_id=(
                    f"{stream.value.lower()}-{identity_suffix}-"
                    f"{batch_index:04d}-{digest}"
                ),
                batch_index=batch_index,
                timestamps=batch_points,
            )
        )
    return tuple(plans)


logger = logging.getLogger(__name__)


class FFmpegFrameExtractor:
    def __init__(
        self,
        *,
        course_root: Path,
        ffmpeg_binary: str = "ffmpeg",
        ffprobe_binary: str = "ffprobe",
        command_timeout_seconds: float = 60.0,
        max_concurrent_processes: int = 2,
        batch_extraction_enabled: bool = True,
        metrics: VisionPipelineMetrics | None = None,
    ) -> None:
        if command_timeout_seconds <= 0:
            raise ValueError("视频命令超时必须大于 0")
        if (
            isinstance(max_concurrent_processes, bool)
            or not isinstance(max_concurrent_processes, int)
            or max_concurrent_processes <= 0
        ):
            raise ValueError("视频命令最大并发数必须为正整数")
        self._course_root = course_root
        self._ffmpeg_binary = ffmpeg_binary
        self._ffprobe_binary = ffprobe_binary
        self._command_timeout_seconds = command_timeout_seconds
        self._max_concurrent_processes = max_concurrent_processes
        self._batch_extraction_enabled = batch_extraction_enabled
        self._process_slots = asyncio.Semaphore(max_concurrent_processes)
        self._metrics = metrics
        self._active_processes: set[asyncio.subprocess.Process] = set()
        self._pending_jobs = 0
        self._running_jobs = 0
        self._peak_pending_jobs = 0
        self._peak_running_jobs = 0

    @property
    def pending_jobs(self) -> int:
        return self._pending_jobs

    @property
    def running_jobs(self) -> int:
        return self._running_jobs

    @property
    def peak_pending_jobs(self) -> int:
        return self._peak_pending_jobs

    @property
    def peak_running_jobs(self) -> int:
        return self._peak_running_jobs

    async def duration_seconds(self, video_path: Path) -> float:
        resolved = self._validated_video(video_path)
        async with self._process_slot():
            return await self._probe_duration(resolved)

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
        if not unique_points:
            return []
        if (
            self._batch_extraction_enabled
            and _uniform_interval(unique_points) is not None
        ):
            cached = [self._cached_frame(output_root, point) for point in unique_points]
            if all(item is not None for item in cached):
                return [item for item in cached if item is not None]
            if all(item is None for item in cached):
                try:
                    return await self._extract_uniform_batch_limited(
                        resolved,
                        output_root,
                        unique_points,
                    )
                except VideoFrameError as exc:
                    logger.warning(
                        "批量抽帧失败，回退为并行单帧抽取",
                        extra={
                            "task_id": task_id,
                            "stream": stream.value.lower(),
                            "frame_count": len(unique_points),
                            "exception_type": type(exc).__name__,
                        },
                    )
        results: list[ExtractedFrame | None] = [None] * len(unique_points)
        point_iterator = iter(enumerate(unique_points))

        async def extract_worker() -> None:
            while True:
                try:
                    index, point = next(point_iterator)
                except StopIteration:
                    return
                results[index] = await self._extract_one_limited(
                    resolved,
                    output_root,
                    point,
                )

        workers = [
            asyncio.create_task(
                extract_worker(),
                name=f"vision-frame-extractor-{index:02d}",
            )
            for index in range(min(self._max_concurrent_processes, len(unique_points)))
        ]
        try:
            await asyncio.gather(*workers)
        except BaseException:
            for worker in workers:
                if not worker.done():
                    worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            raise
        if any(item is None for item in results):
            raise VideoFrameError("视觉抽帧结果不完整")
        return [item for item in results if item is not None]

    async def _extract_uniform_batch_limited(
        self,
        video_path: Path,
        output_root: Path,
        timestamps: list[float],
    ) -> list[ExtractedFrame]:
        async with self._process_slot():
            return await self._extract_uniform_batch(
                video_path,
                output_root,
                timestamps,
            )

    async def _extract_uniform_batch(
        self,
        video_path: Path,
        output_root: Path,
        timestamps: list[float],
    ) -> list[ExtractedFrame]:
        interval = _uniform_interval(timestamps)
        if interval is None:
            raise VideoFrameError("批量抽帧时间点必须等间隔且至少包含两帧")
        with tempfile.TemporaryDirectory(
            prefix=".frame-batch-", dir=output_root
        ) as raw:
            temporary_root = Path(raw)
            pattern = temporary_root / "frame-%06d.jpg"
            await self._run(
                [
                    self._ffmpeg_binary,
                    "-nostdin",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{timestamps[0]:.6f}",
                    "-i",
                    str(video_path),
                    "-vf",
                    f"fps=fps=1/{interval:.6f}:start_time=0:round=near",
                    "-frames:v",
                    str(len(timestamps)),
                    "-q:v",
                    "2",
                    "-start_number",
                    "0",
                    "-y",
                    str(pattern),
                ]
            )
            generated = sorted(temporary_root.glob("frame-*.jpg"))
            if len(generated) != len(timestamps):
                raise VideoFrameError(
                    f"批量抽帧结果数量不完整: {len(generated)}/{len(timestamps)}"
                )
            results: list[ExtractedFrame] = []
            for timestamp_seconds, source in zip(timestamps, generated, strict=True):
                if source.stat().st_size <= 0:
                    raise VideoFrameError("批量抽帧生成了空图片")
                target = self._frame_path(output_root, timestamp_seconds)
                source.replace(target)
                results.append(self._frame_result(target, timestamp_seconds))
            return results

    async def _extract_one_limited(
        self,
        video_path: Path,
        output_root: Path,
        timestamp_seconds: float,
    ) -> ExtractedFrame:
        async with self._process_slot():
            result = self._extract_one(
                video_path,
                output_root,
                timestamp_seconds,
            )
            if isinstance(result, Awaitable):
                return await result
            return result

    @asynccontextmanager
    async def _process_slot(self):
        self._pending_jobs += 1
        self._peak_pending_jobs = max(self._peak_pending_jobs, self._pending_jobs)
        self._record_metrics()
        try:
            await self._process_slots.acquire()
        except BaseException:
            self._pending_jobs -= 1
            self._record_metrics()
            raise
        self._pending_jobs -= 1
        self._running_jobs += 1
        self._peak_running_jobs = max(self._peak_running_jobs, self._running_jobs)
        self._record_metrics()
        try:
            yield
        finally:
            self._running_jobs -= 1
            self._process_slots.release()
            self._record_metrics()

    def _record_metrics(self) -> None:
        if self._metrics is not None:
            self._metrics.set_media_counts(
                pending=self._pending_jobs,
                running=self._running_jobs,
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

    async def _probe_duration(self, video_path: Path) -> float:
        stdout = await self._run(
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
            duration = float(stdout.strip())
        except ValueError as exc:
            raise VideoFrameError(f"无法解析视频时长: {video_path}") from exc
        if not math.isfinite(duration) or duration <= 0:
            raise VideoFrameError(f"视频时长无效: {video_path}")
        return duration

    async def _extract_one(
        self,
        video_path: Path,
        output_root: Path,
        timestamp_seconds: float,
    ) -> ExtractedFrame:
        target = self._frame_path(output_root, timestamp_seconds)
        if not target.is_file() or target.stat().st_size <= 0:
            partial = output_root / f".{target.stem}.part.jpg"
            try:
                await self._run(
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
        return self._frame_result(target, timestamp_seconds)

    @staticmethod
    def _frame_path(output_root: Path, timestamp_seconds: float) -> Path:
        timestamp_ms = round(timestamp_seconds * 1000)
        return output_root / f"frame-{timestamp_ms:012d}.jpg"

    def _cached_frame(
        self,
        output_root: Path,
        timestamp_seconds: float,
    ) -> ExtractedFrame | None:
        target = self._frame_path(output_root, timestamp_seconds)
        if not target.is_file() or target.stat().st_size <= 0:
            return None
        return self._frame_result(target, timestamp_seconds)

    @staticmethod
    def _frame_result(target: Path, timestamp_seconds: float) -> ExtractedFrame:
        return ExtractedFrame(
            timestamp_seconds=timestamp_seconds,
            frame_index=round(timestamp_seconds * 1000),
            path=target,
        )

    async def _run(self, command: list[str]) -> str:
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._active_processes.add(process)
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self._command_timeout_seconds,
            )
            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace").strip()
                raise VideoFrameError(
                    f"视频命令执行失败: {detail or f'退出码 {process.returncode}'}"
                )
            return stdout.decode("utf-8", errors="replace")
        except TimeoutError as exc:
            if process is not None:
                await self._terminate_process(process)
            raise VideoFrameError(
                f"视频命令执行超过 {self._command_timeout_seconds:g} 秒"
            ) from exc
        except asyncio.CancelledError:
            if process is not None:
                await self._terminate_process(process)
            raise
        except OSError as exc:
            raise VideoFrameError(f"视频命令执行失败: {exc}") from exc
        finally:
            if process is not None:
                self._active_processes.discard(process)

    async def close(self) -> None:
        processes = tuple(self._active_processes)
        if not processes:
            return
        await asyncio.gather(
            *(self._terminate_process(process) for process in processes),
            return_exceptions=True,
        )

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except TimeoutError:
            process.kill()
            await process.wait()
        logger.info(
            "视觉媒体子进程已回收",
            extra={"pid": process.pid, "outcome": "terminated"},
        )


def _uniform_interval(points: list[float]) -> float | None:
    if len(points) < 2:
        return None
    interval = points[1] - points[0]
    if interval <= 0:
        return None
    if any(
        not math.isclose(
            right - left,
            interval,
            rel_tol=0,
            abs_tol=1e-6,
        )
        for left, right in pairwise(points)
    ):
        return None
    return interval
