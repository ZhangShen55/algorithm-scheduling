from __future__ import annotations

import asyncio
import math
import os
import shutil
import subprocess
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

from vision_orchestrator_service.app.infrastructure.cache import VisionStream
from vision_orchestrator_service.app.infrastructure.media import (
    FFmpegFrameExtractor,
    build_frame_batch_plans,
)

from .models import JsonObject


@dataclass(frozen=True, slots=True)
class MediaStageEvent:
    event: str
    task_id: str | None
    stream: str | None
    batch_id: str | None
    monotonic_seconds: float
    detail: dict[str, object]


@dataclass(frozen=True, slots=True)
class HostSample:
    monotonic_seconds: float
    cpu_percent: float
    iowait_percent: float
    load_1m: float
    load_5m: float
    load_15m: float
    memory_available_mib: float
    ffmpeg_processes: int
    ffmpeg_threads: int


@dataclass(frozen=True, slots=True)
class MediaRunResult:
    status: str
    reason: str
    started_at: str
    finished_at: str
    elapsed_seconds: float
    active_streams: int
    prepared_batches: int
    prepared_frames: int
    first_batch_seconds: float
    batch_interval_p95_seconds: float
    batch_interval_p99_seconds: float
    prepared_batches_per_second: float
    events: tuple[MediaStageEvent, ...]
    host_samples: tuple[HostSample, ...]

    def to_document(self) -> JsonObject:
        return {
            "schema_version": 1,
            "evidence_type": "vbas_media_feed_isolation",
            "status": self.status,
            "reason": self.reason,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": self.elapsed_seconds,
            "active_streams": self.active_streams,
            "prepared_batches": self.prepared_batches,
            "prepared_frames": self.prepared_frames,
            "first_batch_seconds": self.first_batch_seconds,
            "batch_interval_p95_seconds": self.batch_interval_p95_seconds,
            "batch_interval_p99_seconds": self.batch_interval_p99_seconds,
            "prepared_batches_per_second": self.prepared_batches_per_second,
            "events": [asdict(item) for item in self.events],
            "host_samples": [asdict(item) for item in self.host_samples],
        }


async def run_media_feed_isolation(
    *,
    video_path: Path,
    course_root: Path,
    campaign_id: str,
    stream: VisionStream,
    active_streams: int,
    batch_size: int = 8,
    sample_interval_seconds: float = 10.0,
    max_concurrent_processes: int | None = None,
    command_timeout_seconds: float = 600.0,
    host_sample_interval_seconds: float = 1.0,
) -> MediaRunResult:
    if active_streams <= 0 or batch_size <= 0:
        raise ValueError("活动流数和 batch_size 必须大于 0")
    if sample_interval_seconds <= 0 or host_sample_interval_seconds <= 0:
        raise ValueError("抽帧间隔和采样间隔必须大于 0")
    resolved_video = video_path.resolve(strict=True)
    root = course_root.resolve(strict=True)
    if not resolved_video.is_relative_to(root):
        raise ValueError("媒体基准视频必须位于 course_root 下")
    process_limit = max_concurrent_processes or active_streams
    if process_limit < active_streams:
        raise ValueError("纯媒体基准的进程槽位不得小于活动流数")

    loop = asyncio.get_running_loop()
    started = loop.time()
    wall_started = _utc_now()
    events: list[MediaStageEvent] = []
    host_samples: list[HostSample] = []
    stop_sampling = asyncio.Event()
    def observe(event: str, detail: dict[str, object]) -> None:
        raw_task_id = detail.get("task_id")
        raw_stream = detail.get("stream")
        raw_batch_id = detail.get("batch_id")
        raw_monotonic = detail.get("monotonic_seconds")
        monotonic_seconds = (
            float(raw_monotonic)
            if isinstance(raw_monotonic, (int, float))
            else loop.time()
        )
        events.append(
            MediaStageEvent(
                event=event,
                task_id=raw_task_id if isinstance(raw_task_id, str) else None,
                stream=raw_stream if isinstance(raw_stream, str) else None,
                batch_id=raw_batch_id if isinstance(raw_batch_id, str) else None,
                monotonic_seconds=monotonic_seconds,
                detail={key: value for key, value in detail.items() if key != "command"},
            )
        )

    extractor = FFmpegFrameExtractor(
        course_root=root,
        command_timeout_seconds=command_timeout_seconds,
        max_concurrent_processes=process_limit,
        batch_extraction_enabled=True,
        stage_observer=observe,
    )

    async def sample_host() -> None:
        previous = _read_cpu_ticks()
        while not stop_sampling.is_set():
            await asyncio.sleep(host_sample_interval_seconds)
            current = _read_cpu_ticks()
            cpu_percent, iowait_percent = _cpu_percent(previous, current)
            previous = current
            process_count, thread_count = await asyncio.to_thread(_ffmpeg_process_stats)
            loads = os.getloadavg()
            host_samples.append(
                HostSample(
                    monotonic_seconds=loop.time(),
                    cpu_percent=cpu_percent,
                    iowait_percent=iowait_percent,
                    load_1m=loads[0],
                    load_5m=loads[1],
                    load_15m=loads[2],
                    memory_available_mib=_memory_available_mib(),
                    ffmpeg_processes=process_count,
                    ffmpeg_threads=thread_count,
                )
            )

    async def run_stream(index: int) -> tuple[int, int]:
        task_id = f"vbas-feed-{campaign_id}-{stream.value.lower()}-{index:03d}"
        events.append(
            MediaStageEvent(
                "vision_command_started",
                task_id,
                stream.value.lower(),
                None,
                loop.time(),
                {},
            )
        )
        duration = await extractor.duration_seconds(resolved_video, task_id=task_id)
        points = _sample_points(duration, sample_interval_seconds)
        plans = build_frame_batch_plans(
            task_id=task_id,
            stream=stream,
            timestamps=points,
            batch_size=batch_size,
            identity_suffix="media-sink",
        )
        frame_count = 0
        for plan in plans:
            extracted = await extractor.extract(
                task_id=task_id,
                stream=stream,
                video_path=resolved_video,
                timestamps=list(plan.timestamps),
            )
            frame_count += len(extracted)
            ready_at = loop.time()
            events.append(
                MediaStageEvent(
                    "frame_batch_ready",
                    task_id,
                    stream.value.lower(),
                    plan.batch_id,
                    ready_at,
                    {"frame_count": len(extracted)},
                )
            )
            # 零延迟 sink 只记录供料完成，不进入租约或 VBas。
            events.append(
                MediaStageEvent(
                    "sink_completed",
                    task_id,
                    stream.value.lower(),
                    plan.batch_id,
                    loop.time(),
                    {"frame_count": len(extracted)},
                )
            )
        return len(plans), frame_count

    sampler = asyncio.create_task(sample_host(), name="vbas-media-host-sampler")
    status, reason = "passed", "纯媒体供料完整终结"
    stream_results: Sequence[tuple[int, int]] = ()
    try:
        stream_results = await asyncio.gather(
            *(run_stream(index) for index in range(active_streams))
        )
    except BaseException as exc:  # noqa: BLE001 - 基准失败要保留错误类型
        status = "failed"
        reason = f"媒体供料失败: {type(exc).__name__}"
    finally:
        stop_sampling.set()
        await extractor.close()
        sampler.cancel()
        await asyncio.gather(sampler, return_exceptions=True)

    elapsed = loop.time() - started
    ready_events = [event for event in events if event.event == "frame_batch_ready"]
    ready_times = sorted(event.monotonic_seconds for event in ready_events)
    intervals = [right - left for left, right in pairwise(ready_times)]
    prepared_batches = sum(value[0] for value in stream_results)
    prepared_frames = sum(value[1] for value in stream_results)
    return MediaRunResult(
        status=status,
        reason=reason,
        started_at=wall_started,
        finished_at=_utc_now(),
        elapsed_seconds=elapsed,
        active_streams=active_streams,
        prepared_batches=prepared_batches,
        prepared_frames=prepared_frames,
        first_batch_seconds=(ready_times[0] - started if ready_times else 0.0),
        batch_interval_p95_seconds=_quantile(intervals, 0.95),
        batch_interval_p99_seconds=_quantile(intervals, 0.99),
        prepared_batches_per_second=prepared_batches / elapsed if elapsed else 0.0,
        events=tuple(events),
        host_samples=tuple(host_samples),
    )


def classify_media_curve(results: Sequence[MediaRunResult]) -> JsonObject:
    if not results:
        raise ValueError("媒体曲线至少需要一档结果")
    best = max(results, key=lambda item: item.prepared_batches_per_second)
    candidates = [
        item
        for item in results
        if item.status == "passed"
        and item.prepared_batches_per_second >= best.prepared_batches_per_second * 0.95
    ]
    recommended = min(candidates, key=lambda item: item.active_streams) if candidates else best
    high = max(results, key=lambda item: item.active_streams)
    over_scheduled = high.prepared_batches_per_second < best.prepared_batches_per_second * 0.95
    return {
        "best_active_streams": best.active_streams,
        "best_prepared_batches_per_second": best.prepared_batches_per_second,
        "recommended_active_streams": recommended.active_streams,
        "high_tier_over_scheduled": over_scheduled,
        "bottleneck_signals": _host_bottleneck_signals(high.host_samples),
    }


def remove_media_fixture_outputs(course_root: Path, campaign_id: str) -> None:
    prefix = f"vbas-feed-{campaign_id}-"
    for child in course_root.iterdir():
        if child.is_dir() and child.name.startswith(prefix):
            shutil.rmtree(child)


def _sample_points(duration: float, interval: float) -> list[float]:
    last = max(0.0, duration - 0.5)
    points: list[float] = []
    value = 0.0
    while value <= last:
        points.append(round(value, 6))
        value += interval
    return points or [0.0]


def _read_cpu_ticks() -> tuple[int, int, int]:
    fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
    values = [int(item) for item in fields[1:]]
    total = sum(values)
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    iowait = values[4] if len(values) > 4 else 0
    return total, idle, iowait


def _cpu_percent(
    previous: tuple[int, int, int], current: tuple[int, int, int]
) -> tuple[float, float]:
    total = current[0] - previous[0]
    if total <= 0:
        return 0.0, 0.0
    idle = current[1] - previous[1]
    iowait = current[2] - previous[2]
    return max(0.0, 100.0 * (total - idle) / total), max(0.0, 100.0 * iowait / total)


def _memory_available_mib() -> float:
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024
    raise ValueError("/proc/meminfo 缺少 MemAvailable")


def _ffmpeg_process_stats() -> tuple[int, int]:
    output = subprocess.run(
        ["ps", "-eLo", "pid=,comm="],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout
    rows = [line.split() for line in output.splitlines() if line.strip()]
    ffmpeg_rows = [row for row in rows if len(row) >= 2 and "ffmpeg" in row[1].lower()]
    return len({row[0] for row in ffmpeg_rows}), len(ffmpeg_rows)


def _host_bottleneck_signals(samples: Sequence[HostSample]) -> dict[str, object]:
    if not samples:
        return {"status": "insufficient_evidence"}
    return {
        "peak_cpu_percent": max(item.cpu_percent for item in samples),
        "peak_iowait_percent": max(item.iowait_percent for item in samples),
        "peak_load_1m": max(item.load_1m for item in samples),
        "minimum_memory_available_mib": min(item.memory_available_mib for item in samples),
        "peak_ffmpeg_processes": max(item.ffmpeg_processes for item in samples),
        "peak_ffmpeg_threads": max(item.ffmpeg_threads for item in samples),
    }


def _quantile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def event_counts(events: Sequence[MediaStageEvent]) -> dict[str, int]:
    return dict(Counter(event.event for event in events))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
