from __future__ import annotations

import asyncio
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Protocol

from vision_orchestrator_service.app.infrastructure.cache import VisionStream
from vision_orchestrator_service.app.infrastructure.vbas import VbasFrame

from .models import JsonObject


class ReplayVbasClient(Protocol):
    async def analyze(
        self,
        *,
        task_id: str,
        stream: VisionStream,
        frames: Sequence[VbasFrame],
        trace_id: str | None = None,
    ) -> list[JsonObject]: ...


@dataclass(frozen=True, slots=True)
class DispatchEvent:
    event: str
    batch_id: str
    monotonic_seconds: float
    instance_id: str | None = None


@dataclass(frozen=True, slots=True)
class QueueSample:
    monotonic_seconds: float
    ready_depth: int
    active_requests: int
    target_slots: int


@dataclass(frozen=True, slots=True)
class DispatchReplayResult:
    status: str
    reason: str
    started_at: str
    finished_at: str
    elapsed_seconds: float
    batch_count: int
    success_count: int
    events: tuple[DispatchEvent, ...]
    queue_samples: tuple[QueueSample, ...]
    summary: dict[str, object]

    def to_document(self) -> JsonObject:
        return {
            "schema_version": 1,
            "evidence_type": "vbas_dispatch_replay",
            "status": self.status,
            "reason": self.reason,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": self.elapsed_seconds,
            "batch_count": self.batch_count,
            "success_count": self.success_count,
            "events": [asdict(item) for item in self.events],
            "queue_samples": [asdict(item) for item in self.queue_samples],
            "summary": self.summary,
        }


async def run_dispatch_replay(
    *,
    client: ReplayVbasClient,
    stream: VisionStream,
    batches: Sequence[Sequence[VbasFrame]],
    target_slots: int,
    campaign_id: str,
    stage_events: list[DispatchEvent] | None = None,
    sample_interval_seconds: float = 0.05,
) -> DispatchReplayResult:
    if target_slots <= 0 or sample_interval_seconds <= 0:
        raise ValueError("目标槽位和采样间隔必须大于 0")
    if not batches:
        raise ValueError("预抽帧回放至少需要一个 batch")
    if any(not batch for batch in batches):
        raise ValueError("预抽帧 batch 不能为空")
    loop = asyncio.get_running_loop()
    started = loop.time()
    wall_started = _utc_now()
    queue: asyncio.Queue[tuple[int, Sequence[VbasFrame]]] = asyncio.Queue()
    for index, batch in enumerate(batches):
        queue.put_nowait((index, batch))
    events = stage_events if stage_events is not None else []
    queue_samples: list[QueueSample] = []
    active = 0
    successes = 0
    failure: BaseException | None = None
    finished = asyncio.Event()

    async def worker(worker_index: int) -> None:
        nonlocal active, successes, failure
        while failure is None:
            try:
                index, batch = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            batch_id = f"dispatch-{campaign_id}-{stream.value.lower()}-{index:06d}"
            events.append(DispatchEvent("frame_batch_ready", batch_id, loop.time()))
            active += 1
            try:
                result = await client.analyze(
                    task_id=batch_id,
                    stream=stream,
                    frames=batch,
                    trace_id=f"{campaign_id}-{worker_index}-{index}",
                )
                if len(result) != len(batch):
                    raise RuntimeError("VBas 回放结果数与帧数不一致")
                successes += 1
                events.append(DispatchEvent("batch_completed", batch_id, loop.time()))
            except BaseException as exc:  # noqa: BLE001 - 任一回放失败都中止并保留证据
                failure = exc
                events.append(DispatchEvent("batch_failed", batch_id, loop.time()))
                return
            finally:
                active -= 1
                queue.task_done()

    async def sample_queue() -> None:
        while not finished.is_set():
            queue_samples.append(
                QueueSample(loop.time(), queue.qsize(), active, target_slots)
            )
            await asyncio.sleep(sample_interval_seconds)

    sampler = asyncio.create_task(sample_queue(), name="vbas-dispatch-queue-sampler")
    workers = [
        asyncio.create_task(worker(index), name=f"vbas-dispatch-worker-{index:03d}")
        for index in range(min(target_slots, len(batches)))
    ]
    await asyncio.gather(*workers)
    finished.set()
    await sampler
    elapsed = loop.time() - started
    summary = classify_dispatch_gaps(queue_samples, stage_events=events)
    status = "passed" if failure is None and successes == len(batches) else "failed"
    return DispatchReplayResult(
        status=status,
        reason=(
            "预抽帧租约与分发回放完整终结"
            if status == "passed"
            else f"预抽帧分发失败: {type(failure).__name__}"
        ),
        started_at=wall_started,
        finished_at=_utc_now(),
        elapsed_seconds=elapsed,
        batch_count=len(batches),
        success_count=successes,
        events=tuple(events),
        queue_samples=tuple(queue_samples),
        summary=summary,
    )


def stage_observer(events: list[DispatchEvent]) -> Callable[[str, dict[str, object]], None]:
    def observe(event: str, detail: dict[str, object]) -> None:
        batch_id = detail.get("batch_id")
        timestamp = detail.get("monotonic_seconds")
        instance_id = detail.get("instance_id")
        if not isinstance(batch_id, str) or not isinstance(timestamp, (int, float)):
            raise ValueError("VBas 阶段事件缺少 batch_id 或单调时间")
        events.append(
            DispatchEvent(
                event,
                batch_id,
                float(timestamp),
                instance_id if isinstance(instance_id, str) else None,
            )
        )

    return observe


def classify_dispatch_gaps(
    samples: Sequence[QueueSample],
    *,
    stage_events: Sequence[DispatchEvent],
) -> dict[str, object]:
    if not samples:
        return {"status": "insufficient_evidence"}
    feed_gap_seconds = 0.0
    dispatch_gap_seconds = 0.0
    longest_feed_gap = 0.0
    longest_dispatch_gap = 0.0
    for left, right in pairwise(samples):
        duration = max(0.0, right.monotonic_seconds - left.monotonic_seconds)
        idle_slots = max(0, left.target_slots - left.active_requests)
        if idle_slots <= 0:
            continue
        if left.ready_depth < idle_slots:
            feed_gap_seconds += duration
            longest_feed_gap = max(longest_feed_gap, duration)
        else:
            dispatch_gap_seconds += duration
            longest_dispatch_gap = max(longest_dispatch_gap, duration)
    by_batch: dict[str, dict[str, float]] = defaultdict(dict)
    instance_counts: Counter[str] = Counter()
    authority_rejections = 0
    for event in stage_events:
        by_batch[event.batch_id][event.event] = event.monotonic_seconds
        if event.event == "lease_acquired" and event.instance_id:
            instance_counts[event.instance_id] += 1
        if event.event == "lease_retry":
            authority_rejections += 1
    lease_waits = [
        item["lease_acquired"] - item["lease_requested"]
        for item in by_batch.values()
        if "lease_requested" in item and "lease_acquired" in item
    ]
    http_start_waits = [
        item["vbas_started"] - item["lease_acquired"]
        for item in by_batch.values()
        if "lease_acquired" in item and "vbas_started" in item
    ]
    service_times = [
        item["vbas_finished"] - item["vbas_started"]
        for item in by_batch.values()
        if "vbas_started" in item and "vbas_finished" in item
    ]
    counts = list(instance_counts.values())
    imbalance = (
        (max(counts) - min(counts)) / (sum(counts) / len(counts))
        if len(counts) > 1 and sum(counts) > 0
        else 0.0
    )
    return {
        "status": "measured",
        "feed_gap_seconds": feed_gap_seconds,
        "dispatch_gap_seconds": dispatch_gap_seconds,
        "longest_feed_gap_seconds": longest_feed_gap,
        "longest_dispatch_gap_seconds": longest_dispatch_gap,
        "lease_wait_p95_seconds": _quantile(lease_waits, 0.95),
        "lease_to_http_start_p95_seconds": _quantile(http_start_waits, 0.95),
        "vbas_service_p95_seconds": _quantile(service_times, 0.95),
        "instance_batch_counts": dict(sorted(instance_counts.items())),
        "instance_imbalance_ratio": imbalance,
        "authority_lease_retry_count": authority_rejections,
        "attribution": (
            "dispatch"
            if dispatch_gap_seconds > feed_gap_seconds
            else "feed"
            if feed_gap_seconds > 0
            else "none"
        ),
    }


def batches_from_paths(
    paths: Sequence[Path],
    *,
    batch_size: int,
    stream: VisionStream,
) -> tuple[tuple[VbasFrame, ...], ...]:
    if batch_size <= 0 or batch_size > 8:
        raise ValueError("batch_size 必须介于 1 和 8")
    frames = [
        VbasFrame(
            image_id=f"replay-{stream.value.lower()}-{index:08d}",
            path=path.resolve(strict=True),
            frame_index=index,
            timestamp_seconds=float(index),
        )
        for index, path in enumerate(paths)
    ]
    return tuple(
        tuple(frames[index : index + batch_size])
        for index in range(0, len(frames), batch_size)
    )


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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
