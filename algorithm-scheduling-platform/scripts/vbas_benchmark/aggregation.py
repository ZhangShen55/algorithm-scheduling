from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .models import JsonObject


@dataclass(frozen=True, slots=True)
class ThroughputPoint:
    mode: str
    scope: str
    concurrency_per_instance: int
    batch_per_second: float
    frame_per_second: float
    status: str
    attempt: int

    @classmethod
    def from_document(cls, document: object) -> ThroughputPoint:
        if not isinstance(document, dict):
            raise TypeError("吞吐点证据必须是对象")
        config = document.get("config")
        summary = document.get("summary")
        if not isinstance(config, dict) or not isinstance(summary, dict):
            raise TypeError("吞吐点缺少 config 或 summary")
        targets = config.get("targets")
        if not isinstance(targets, list) or not targets:
            raise ValueError("吞吐点缺少目标实例")
        return cls(
            mode=str(config["mode"]),
            scope="single" if len(targets) == 1 else "triple",
            concurrency_per_instance=_integer(config["concurrency_per_instance"]),
            batch_per_second=_number(summary["batch_per_second"]),
            frame_per_second=_number(summary["frame_per_second"]),
            status=str(document["status"]),
            attempt=_integer(document.get("attempt", 1)),
        )


def select_throughput_points(points: Sequence[ThroughputPoint]) -> JsonObject:
    stable = [point for point in points if point.status == "passed"]
    if not stable:
        return {"status": "not_converged", "reason": "没有稳定通过的并发档"}
    highest = max(stable, key=lambda item: item.batch_per_second)
    candidates = [
        item
        for item in stable
        if item.batch_per_second >= highest.batch_per_second * 0.95
    ]
    recommended = min(candidates, key=lambda item: item.concurrency_per_instance)
    grouped: dict[int, list[float]] = defaultdict(list)
    for point in stable:
        grouped[point.concurrency_per_instance].append(point.batch_per_second)
    candidate_values = grouped[recommended.concurrency_per_instance]
    deviation = _relative_spread(candidate_values)
    converged = len(candidate_values) >= 2 and deviation <= 0.05
    return {
        "status": "converged" if converged else "not_converged",
        "mode": highest.mode,
        "scope": highest.scope,
        "highest_stable_throughput": {
            "concurrency_per_instance": highest.concurrency_per_instance,
            "batch_per_second": highest.batch_per_second,
            "frame_per_second": highest.frame_per_second,
        },
        "recommended_95_percent_knee": {
            "concurrency_per_instance": recommended.concurrency_per_instance,
            "batch_per_second": recommended.batch_per_second,
            "frame_per_second": recommended.frame_per_second,
        },
        "candidate_repeat_count": len(candidate_values),
        "candidate_throughput_relative_spread": deviation,
    }


def compare_isolation_capacities(
    *,
    vbas_batches_per_second: float,
    feed_batches_per_second: float,
    dispatch_batches_per_second: float,
    required_slot_batches_per_second: float,
) -> JsonObject:
    values = (
        vbas_batches_per_second,
        feed_batches_per_second,
        dispatch_batches_per_second,
        required_slot_batches_per_second,
    )
    if any(value < 0 for value in values):
        raise ValueError("隔离容量不能为负数")
    candidates = {
        "vbas": vbas_batches_per_second,
        "feed": feed_batches_per_second,
        "dispatch": dispatch_batches_per_second,
    }
    bottleneck = min(candidates, key=candidates.__getitem__)
    feed_gap = max(0.0, required_slot_batches_per_second - feed_batches_per_second)
    dispatch_loss = max(0.0, vbas_batches_per_second - dispatch_batches_per_second)
    return {
        "C_vbas_batch_per_second": vbas_batches_per_second,
        "C_feed_batch_per_second": feed_batches_per_second,
        "C_dispatch_batch_per_second": dispatch_batches_per_second,
        "required_slot_batch_per_second": required_slot_batches_per_second,
        "feed_capacity_gap_batch_per_second": feed_gap,
        "dispatch_loss_batch_per_second": dispatch_loss,
        "feed_sufficient": feed_gap == 0,
        "bottleneck": bottleneck,
    }


def _relative_spread(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = statistics.fmean(values)
    return (max(values) - min(values)) / mean if mean else 0.0


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("字段必须是整数")
    return int(value)


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("字段必须是数值")
    return float(value)
