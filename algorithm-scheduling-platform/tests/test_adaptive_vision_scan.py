from collections.abc import Iterable

import pytest
from vision_orchestrator_service.app.domain.adaptive_scan import (
    AdaptiveScanConfig,
    AdaptiveScanLimitError,
    AdaptiveScanPlanner,
    BehaviorInterval,
)


def interval_detector(intervals: list[tuple[float, float]]):
    def detect(points: Iterable[float]) -> dict[float, bool]:
        return {point: any(start <= point < end for start, end in intervals) for point in points}

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
    assert result.candidate_windows == ((1_189, 1_206), (1_770, 1_866))
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

    assert result.candidate_windows == ((49, 64), (67, 91))
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


@pytest.mark.asyncio
async def test_async_adaptive_scan_preserves_sync_planning_semantics() -> None:
    planner = AdaptiveScanPlanner(
        AdaptiveScanConfig(
            coarse_interval_seconds=10,
            refinement_intervals_seconds=(5, 2),
        )
    )

    async def detector(points: Iterable[float]) -> dict[float, bool]:
        return {point: 9 <= point < 18 for point in points}

    result = await planner.scan_async(duration_seconds=30, detector=detector)

    assert result.stages == ("coarse_10s", "topology_5s", "topology_2s")
    assert result.candidate_windows == ((7, 19),)
    assert result.intervals == (BehaviorInterval(9, 19),)


def test_default_candidate_limit_accepts_real_course_with_31_windows() -> None:
    planner = AdaptiveScanPlanner(
        AdaptiveScanConfig(
            coarse_interval_seconds=10,
            refinement_intervals_seconds=(5,),
            max_detection_points=1_000,
        )
    )

    result = planner.scan(
        duration_seconds=620,
        detector=lambda points: {point: int(point / 10) % 2 == 1 for point in points},
    )

    assert AdaptiveScanConfig().max_candidate_windows == 128
    assert len(result.candidate_windows) == 31


def test_each_refinement_stage_narrows_the_next_scan_range() -> None:
    planner = AdaptiveScanPlanner(
        AdaptiveScanConfig(
            coarse_interval_seconds=10,
            refinement_intervals_seconds=(5, 2),
        )
    )
    requested: list[tuple[float, ...]] = []

    def detector(points: Iterable[float]) -> dict[float, bool]:
        materialized = tuple(points)
        requested.append(materialized)
        return {point: 19 <= point < 24 for point in materialized}

    result = planner.scan(duration_seconds=40, detector=detector)

    assert requested == [
        (0.0, 10.0, 20.0, 30.0, 40.0),
        (15.0, 25.0),
        (17.0, 19.0, 21.0, 23.0),
    ]
    assert result.candidate_windows == ((17, 25),)
    assert result.intervals == (BehaviorInterval(19, 25),)


def test_candidate_limit_remains_bounded_above_128_windows() -> None:
    planner = AdaptiveScanPlanner(
        AdaptiveScanConfig(
            coarse_interval_seconds=10,
            refinement_intervals_seconds=(5,),
            max_detection_points=1_000,
        )
    )

    with pytest.raises(AdaptiveScanLimitError, match="视觉候选窗口超过上限: 129"):
        planner.scan(
            duration_seconds=2_580,
            detector=lambda points: {point: int(point / 10) % 2 == 1 for point in points},
        )
