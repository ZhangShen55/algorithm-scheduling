from __future__ import annotations

from scripts.vbas_benchmark.aggregation import (
    ThroughputPoint,
    compare_isolation_capacities,
    select_throughput_points,
)


def test_selects_highest_throughput_and_lowest_95_percent_knee() -> None:
    points = (
        ThroughputPoint("student", "triple", 8, 100, 800, "passed", 1),
        ThroughputPoint("student", "triple", 6, 99, 792, "passed", 1),
        ThroughputPoint("student", "triple", 4, 96, 768, "passed", 1),
        ThroughputPoint("student", "triple", 4, 95, 760, "passed", 2),
        ThroughputPoint("student", "triple", 3, 90, 720, "passed", 1),
    )

    result = select_throughput_points(points)

    assert result["status"] == "converged"
    assert result["highest_stable_throughput"]["concurrency_per_instance"] == 8
    assert result["recommended_95_percent_knee"]["concurrency_per_instance"] == 4
    assert result["candidate_repeat_count"] == 2


def test_candidate_without_two_repeats_remains_not_converged() -> None:
    result = select_throughput_points(
        (ThroughputPoint("teacher", "single", 2, 10, 80, "passed", 1),)
    )

    assert result["status"] == "not_converged"


def test_capacity_comparison_keeps_feed_and_dispatch_separate() -> None:
    result = compare_isolation_capacities(
        vbas_batches_per_second=10,
        feed_batches_per_second=6,
        dispatch_batches_per_second=9,
        required_slot_batches_per_second=8,
    )

    assert result["bottleneck"] == "feed"
    assert result["feed_capacity_gap_batch_per_second"] == 2
    assert result["dispatch_loss_batch_per_second"] == 1
    assert result["feed_sufficient"] is False
