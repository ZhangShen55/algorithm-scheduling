from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .models import BenchmarkGuardrails, JsonObject
from .telemetry import GpuSample, assess_memory_stability

_STAGE_PATTERN = re.compile(
    r"^视觉基准阶段 event=(?P<event>\S+) task_id=(?P<task_id>\S+) "
    r"stream=(?P<stream>\S+) batch_id=(?P<batch_id>\S+) "
    r"operation=(?P<operation>\S+) instance_id=(?P<instance_id>\S+) "
    r"outcome=(?P<outcome>\S+) monotonic_seconds=(?P<timestamp>\S+)$"
)


@dataclass(frozen=True, slots=True)
class FullChainStageEvent:
    event: str
    task_id: str
    stream: str
    batch_id: str
    operation: str
    instance_id: str | None
    outcome: str | None
    monotonic_seconds: float

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.task_id, self.stream, self.batch_id, self.operation


@dataclass(frozen=True, slots=True)
class _RequestInterval:
    batch_id: str
    instance_id: str | None
    started_at: float
    ended_at: float
    outcome: str


def load_stage_events(path: Path) -> tuple[FullChainStageEvent, ...]:
    events: list[FullChainStageEvent] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            try:
                document = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Vision 日志第 {line_number} 行不是合法 JSON") from exc
            message = document.get("event")
            if not isinstance(message, str) or not message.startswith("视觉基准阶段 "):
                continue
            match = _STAGE_PATTERN.fullmatch(message)
            if match is None:
                raise ValueError(f"Vision 基准日志第 {line_number} 行格式不完整")
            values = match.groupdict()
            try:
                timestamp = float(values["timestamp"])
            except ValueError as exc:
                raise ValueError(f"Vision 基准日志第 {line_number} 行时间戳无效") from exc
            if not math.isfinite(timestamp):
                raise ValueError(f"Vision 基准日志第 {line_number} 行时间戳无效")
            events.append(
                FullChainStageEvent(
                    event=values["event"],
                    task_id=values["task_id"],
                    stream=values["stream"],
                    batch_id=values["batch_id"],
                    operation=values["operation"],
                    instance_id=_optional(values["instance_id"]),
                    outcome=_optional(values["outcome"]),
                    monotonic_seconds=timestamp,
                )
            )
    if not events:
        raise ValueError("Vision 日志中没有基准阶段事件")
    return tuple(events)


def analyze_full_chain(
    events: Sequence[FullChainStageEvent],
    *,
    capacity: int,
) -> JsonObject:
    if capacity <= 0:
        raise ValueError("离线总槽位必须大于 0")
    stage_counts = Counter(event.event for event in events)
    paired = _paired_durations(events)
    intervals, incomplete_starts, unmatched_finishes = _request_intervals(events)
    successful_intervals = [item for item in intervals if item.outcome == "success"]
    instance_counts: Counter[str] = Counter()
    instance_attempt_counts: Counter[str] = Counter()
    instance_service: dict[str, list[float]] = defaultdict(list)
    for interval in intervals:
        if interval.instance_id is None:
            continue
        instance_attempt_counts[interval.instance_id] += 1
        if interval.outcome != "success":
            continue
        instance_counts[interval.instance_id] += 1
        instance_service[interval.instance_id].append(
            max(0.0, interval.ended_at - interval.started_at)
        )

    timeline = _timeline(events, intervals=intervals, capacity=capacity)
    lease_waits = paired.get(("lease_requested", "lease_acquired"), ())
    http_start_waits = paired.get(("lease_acquired", "vbas_started"), ())
    media_queue_waits = paired.get(("media_queued", "media_started"), ())
    media_execution = paired.get(("media_started", "media_finished"), ())
    return {
        "schema_version": 1,
        "evidence_type": "vbas_full_chain_analysis",
        "status": (
            "measured"
            if successful_intervals and not incomplete_starts and not unmatched_finishes
            else "incomplete_evidence"
        ),
        "capacity": capacity,
        "stage_counts": dict(sorted(stage_counts.items())),
        "vbas_batch_count": len(successful_intervals),
        "vbas_attempt_count": len(intervals),
        "released_without_finish_count": sum(
            item.outcome == "released_without_finish" for item in intervals
        ),
        "missing_vbas_started": sorted(unmatched_finishes),
        "missing_vbas_finished": sorted(incomplete_starts),
        "instance_batch_counts": dict(sorted(instance_counts.items())),
        "instance_attempt_counts": dict(sorted(instance_attempt_counts.items())),
        "instance_service_p95_seconds": {
            key: _quantile(value, 0.95)
            for key, value in sorted(instance_service.items())
        },
        "lease_retry_count": stage_counts.get("lease_retry", 0),
        "lease_wait_p95_seconds": _quantile(lease_waits, 0.95),
        "lease_to_http_start_p95_seconds": _quantile(http_start_waits, 0.95),
        "media_queue_wait_p95_seconds": _quantile(media_queue_waits, 0.95),
        "media_execution_p95_seconds": _quantile(media_execution, 0.95),
        **timeline,
        "attribution_note": (
            "完整链路没有显式 ready queue 深度；以 lease_requested 表示 batch 已可下发。"
            "空闲且待租约不足归为供料空档，空闲且待租约足够归为租约/分发空档。"
        ),
    }


def load_gpu_assessment(path: Path) -> JsonObject:
    samples: dict[int, list[GpuSample]] = defaultdict(list)
    with path.open(encoding="utf-8", newline="") as source:
        for row_number, row in enumerate(csv.reader(source), start=1):
            fields = [value.strip() for value in row]
            if not fields:
                continue
            if len(fields) != 7:
                raise ValueError(f"GPU CSV 第 {row_number} 行列数不是 7")
            try:
                gpu_index = int(fields[1])
                memory_total = float(fields[3])
                memory_used = float(fields[4])
                utilization = float(fields[5])
                power = float(fields[6]) if fields[6] not in {"N/A", "[N/A]"} else 0.0
            except ValueError as exc:
                raise ValueError(f"GPU CSV 第 {row_number} 行数值无效") from exc
            samples[gpu_index].append(
                GpuSample(
                    recorded_at=fields[0],
                    monotonic_seconds=float(len(samples[gpu_index])),
                    phase="steady",
                    gpu_index=gpu_index,
                    memory_used_mib=memory_used,
                    memory_total_mib=memory_total,
                    utilization_percent=utilization,
                    power_watts=power,
                    process_count=0,
                    process_memory_mib=0.0,
                    container_name=f"vbas-gpu{gpu_index}",
                    container_restart_count=0,
                    container_running=True,
                )
            )
    if not samples:
        raise ValueError("GPU CSV 没有采样")
    guardrails = BenchmarkGuardrails()
    return {
        str(index): {
            **assess_memory_stability(values, guardrails=guardrails).to_document(),
            "sample_count": len(values),
            "utilization_p50_percent": _quantile(
                [sample.utilization_percent for sample in values], 0.50
            ),
            "utilization_p95_percent": _quantile(
                [sample.utilization_percent for sample in values], 0.95
            ),
        }
        for index, values in sorted(samples.items())
    }


def _timeline(
    events: Sequence[FullChainStageEvent],
    *,
    intervals: Sequence[_RequestInterval],
    capacity: int,
) -> JsonObject:
    relevant = [
        event
        for event in events
        if event.event
        in {"media_queued", "lease_requested", "lease_acquired", "vbas_started", "vbas_finished"}
    ]
    if not relevant:
        return {"timeline_status": "insufficient_evidence"}
    first_media = min(
        (event.monotonic_seconds for event in relevant if event.event == "media_queued"),
        default=min(event.monotonic_seconds for event in relevant),
    )
    last_finished = max(
        (interval.ended_at for interval in intervals),
        default=max(event.monotonic_seconds for event in relevant),
    )
    changes: list[tuple[float, int, int]] = []
    for event in relevant:
        if event.event == "lease_requested":
            changes.append((event.monotonic_seconds, 0, 1))
        elif event.event == "lease_acquired":
            changes.append((event.monotonic_seconds, 0, -1))
    for interval in intervals:
        changes.append((interval.started_at, 1, 0))
        changes.append((interval.ended_at, -1, 0))
    changes.sort(key=lambda item: (item[0], item[1]))
    active = 0
    pending = 0
    previous = first_media
    active_area = 0.0
    max_active = 0
    all_idle = 0.0
    full = 0.0
    feed_gap = 0.0
    dispatch_gap = 0.0
    longest_feed = 0.0
    longest_dispatch = 0.0
    current_kind: str | None = None
    current_duration = 0.0
    for timestamp, active_delta, pending_delta in changes:
        if timestamp < first_media:
            active = max(0, active + active_delta)
            pending = max(0, pending + pending_delta)
            continue
        if timestamp > last_finished:
            break
        duration = max(0.0, timestamp - previous)
        active_area += min(active, capacity) * duration
        if active == 0:
            all_idle += duration
        if active >= capacity:
            full += duration
        idle_slots = max(0, capacity - active)
        kind: str | None = None
        if idle_slots:
            kind = "dispatch" if pending >= idle_slots else "feed"
            if kind == "dispatch":
                dispatch_gap += duration
            else:
                feed_gap += duration
        if kind == current_kind:
            current_duration += duration
        else:
            if current_kind == "feed":
                longest_feed = max(longest_feed, current_duration)
            elif current_kind == "dispatch":
                longest_dispatch = max(longest_dispatch, current_duration)
            current_kind = kind
            current_duration = duration
        active = max(0, active + active_delta)
        max_active = max(max_active, active)
        pending = max(0, pending + pending_delta)
        previous = timestamp
    if current_kind == "feed":
        longest_feed = max(longest_feed, current_duration)
    elif current_kind == "dispatch":
        longest_dispatch = max(longest_dispatch, current_duration)
    window = max(0.0, last_finished - first_media)
    return {
        "timeline_status": "measured",
        "pipeline_window_seconds": window,
        "average_active_slots": active_area / window if window else 0.0,
        "max_active_slots": max_active,
        "all_instances_idle_seconds": all_idle,
        "all_instances_idle_ratio": all_idle / window if window else 0.0,
        "full_capacity_seconds": full,
        "full_capacity_ratio": full / window if window else 0.0,
        "feed_gap_seconds": feed_gap,
        "dispatch_gap_seconds": dispatch_gap,
        "longest_feed_gap_seconds": longest_feed,
        "longest_dispatch_gap_seconds": longest_dispatch,
    }


def _paired_durations(
    events: Iterable[FullChainStageEvent],
) -> dict[tuple[str, str], tuple[float, ...]]:
    pairs = (
        ("lease_requested", "lease_acquired"),
        ("lease_acquired", "vbas_started"),
        ("media_queued", "media_started"),
        ("media_started", "media_finished"),
    )
    ordered = sorted(events, key=lambda item: item.monotonic_seconds)
    result: dict[tuple[str, str], tuple[float, ...]] = {}
    for pair in pairs:
        pending: dict[tuple[str, str, str, str], list[float]] = defaultdict(list)
        durations: list[float] = []
        for event in ordered:
            if event.event == pair[0]:
                pending[event.key].append(event.monotonic_seconds)
            elif event.event == pair[1] and pending[event.key]:
                started_at = pending[event.key].pop(0)
                durations.append(max(0.0, event.monotonic_seconds - started_at))
        result[pair] = tuple(durations)
    return result


def _request_intervals(
    events: Sequence[FullChainStageEvent],
) -> tuple[tuple[_RequestInterval, ...], tuple[str, ...], tuple[str, ...]]:
    open_requests: dict[str, FullChainStageEvent] = {}
    intervals: list[_RequestInterval] = []
    unmatched_finishes: list[str] = []
    ordered = sorted(enumerate(events), key=lambda item: (item[1].monotonic_seconds, item[0]))
    for _, event in ordered:
        if event.event == "vbas_started":
            if event.batch_id in open_requests:
                unmatched_finishes.append(event.batch_id)
            open_requests[event.batch_id] = event
        elif event.event == "vbas_finished":
            started = open_requests.pop(event.batch_id, None)
            if started is None:
                unmatched_finishes.append(event.batch_id)
                continue
            intervals.append(
                _RequestInterval(
                    event.batch_id,
                    started.instance_id,
                    started.monotonic_seconds,
                    event.monotonic_seconds,
                    "success",
                )
            )
        elif event.event == "lease_released":
            started = open_requests.pop(event.batch_id, None)
            if started is not None:
                intervals.append(
                    _RequestInterval(
                        event.batch_id,
                        started.instance_id,
                        started.monotonic_seconds,
                        event.monotonic_seconds,
                        "released_without_finish",
                    )
                )
    return (
        tuple(intervals),
        tuple(open_requests),
        tuple(unmatched_finishes),
    )


def _optional(value: str) -> str | None:
    return None if value == "-" else value


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


def build_full_chain_document(
    vision_log: Path,
    gpu_csv: Path,
    *,
    capacity: int,
) -> JsonObject:
    return {
        **analyze_full_chain(load_stage_events(vision_log), capacity=capacity),
        "gpu_assessment": load_gpu_assessment(gpu_csv),
    }
