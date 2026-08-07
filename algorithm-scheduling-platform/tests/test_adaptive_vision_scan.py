from collections.abc import Iterable

from services.vision_orchestrator_service.adaptive_scan import (
    AdaptiveScanConfig,
    AdaptiveScanPlanner,
    BehaviorInterval,
)


def interval_detector(intervals: list[tuple[float, float]]):
    def detect(points: Iterable[float]) -> dict[float, bool]:
        return {
            point: any(start <= point < end for start, end in intervals)
            for point in points
        }

    return detect


def test_adaptive_scan_refines_only_coarse_candidate_neighborhoods() -> None:
    planner = AdaptiveScanPlanner(
        AdaptiveScanConfig(
            coarse_interval_seconds=30,
            refinement_intervals_seconds=(10, 5, 2, 1),
            max_candidate_windows=20,
            max_detection_points=2_000,
        )
    )

    result = planner.scan(
        duration_seconds=2_400,
        detector=interval_detector([(1_190, 1_206), (1_771, 1_866)]),
    )

    assert result.intervals == (
        BehaviorInterval(1_190, 1_206),
        BehaviorInterval(1_771, 1_866),
    )
    assert result.candidate_windows == ((1_170, 1_230), (1_770, 1_890))
    assert result.evaluated_point_count < 500
    assert result.stages == (
        "coarse_30s",
        "topology_10s",
        "topology_5s",
        "topology_2s",
        "topology_1s",
    )


def test_finer_topology_scan_splits_coarse_positive_group() -> None:
    planner = AdaptiveScanPlanner(
        AdaptiveScanConfig(
            coarse_interval_seconds=30,
            refinement_intervals_seconds=(10, 5, 2, 1),
        )
    )

    result = planner.scan(
        duration_seconds=120,
        detector=interval_detector([(50, 64), (68, 91)]),
    )

    assert result.candidate_windows == ((30, 120),)
    assert result.intervals == (
        BehaviorInterval(50, 64),
        BehaviorInterval(68, 91),
    )


def test_adaptive_scan_returns_empty_when_coarse_scan_has_no_candidate() -> None:
    planner = AdaptiveScanPlanner(AdaptiveScanConfig(coarse_interval_seconds=10))

    result = planner.scan(
        duration_seconds=60,
        detector=interval_detector([]),
    )

    assert result.intervals == ()
    assert result.candidate_windows == ()
    assert result.stages == ("coarse_10s",)
