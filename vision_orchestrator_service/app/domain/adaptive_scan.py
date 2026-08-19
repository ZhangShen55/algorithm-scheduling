from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass

Detector = Callable[[Iterable[float]], Mapping[float, bool]]
AsyncDetector = Callable[[Iterable[float]], Awaitable[Mapping[float, bool]]]


class AdaptiveScanError(RuntimeError):
    pass


class AdaptiveScanLimitError(AdaptiveScanError):
    pass


@dataclass(frozen=True, slots=True)
class AdaptiveScanConfig:
    coarse_interval_seconds: float = 30
    refinement_intervals_seconds: tuple[float, ...] = (10, 5, 2, 1)
    max_candidate_windows: int = 20
    max_detection_points: int = 10_000

    def __post_init__(self) -> None:
        if self.coarse_interval_seconds <= 0:
            raise ValueError("视觉粗扫间隔必须大于 0")
        if not self.refinement_intervals_seconds:
            raise ValueError("视觉加密检测间隔不能为空")
        if any(value <= 0 for value in self.refinement_intervals_seconds):
            raise ValueError("视觉加密检测间隔必须大于 0")
        if any(
            left <= right
            for left, right in zip(
                self.refinement_intervals_seconds,
                self.refinement_intervals_seconds[1:],
                strict=False,
            )
        ):
            raise ValueError("视觉检测间隔必须从粗到细严格递减")
        if not self.effective_refinement_intervals:
            raise ValueError("视觉加密检测间隔必须小于粗扫间隔")
        if self.max_candidate_windows <= 0 or self.max_detection_points <= 0:
            raise ValueError("视觉候选窗口和检测点上限必须大于 0")

    @property
    def effective_refinement_intervals(self) -> tuple[float, ...]:
        return tuple(
            value
            for value in self.refinement_intervals_seconds
            if value < self.coarse_interval_seconds
        )


@dataclass(frozen=True, slots=True)
class BehaviorInterval:
    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        if self.start_seconds < 0 or self.end_seconds <= self.start_seconds:
            raise ValueError("行为区间必须是非空正向半开区间")


@dataclass(frozen=True, slots=True)
class AdaptiveScanResult:
    intervals: tuple[BehaviorInterval, ...]
    candidate_windows: tuple[tuple[float, float], ...]
    evaluated_point_count: int
    stages: tuple[str, ...]


class AdaptiveScanPlanner:
    def __init__(self, config: AdaptiveScanConfig) -> None:
        self._config = config

    def scan(
        self,
        *,
        duration_seconds: float,
        detector: Detector,
    ) -> AdaptiveScanResult:
        if not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise ValueError("视频时长必须是大于 0 的有限值")

        cache: dict[float, bool] = {}
        stages = [f"coarse_{_stage_value(self._config.coarse_interval_seconds)}s"]
        coarse_points = _time_grid(
            0,
            duration_seconds,
            self._config.coarse_interval_seconds,
        )
        self._evaluate(coarse_points, detector, cache)
        candidate_windows = self._candidate_windows(coarse_points, cache)
        if len(candidate_windows) > self._config.max_candidate_windows:
            raise AdaptiveScanLimitError(
                f"视觉候选窗口超过上限: {len(candidate_windows)}"
            )
        if not candidate_windows:
            return AdaptiveScanResult((), (), len(cache), tuple(stages))

        refinement_intervals = self._config.effective_refinement_intervals
        for interval in refinement_intervals:
            stages.append(f"topology_{_stage_value(interval)}s")
            for start, end in candidate_windows:
                self._evaluate(_time_grid(start, end, interval), detector, cache)

        finest_interval = refinement_intervals[-1]
        intervals: list[BehaviorInterval] = []
        for start, end in candidate_windows:
            finest_points = _time_grid(start, end, finest_interval)
            intervals.extend(_positive_intervals(finest_points, cache))
        return AdaptiveScanResult(
            intervals=tuple(sorted(intervals, key=lambda item: item.start_seconds)),
            candidate_windows=tuple(candidate_windows),
            evaluated_point_count=len(cache),
            stages=tuple(stages),
        )

    async def scan_async(
        self,
        *,
        duration_seconds: float,
        detector: AsyncDetector,
    ) -> AdaptiveScanResult:
        """Run the same deterministic plan with an asynchronous detector."""
        if not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise ValueError("视频时长必须是大于 0 的有限值")

        cache: dict[float, bool] = {}
        stages = [f"coarse_{_stage_value(self._config.coarse_interval_seconds)}s"]
        coarse_points = _time_grid(
            0,
            duration_seconds,
            self._config.coarse_interval_seconds,
        )
        await self._evaluate_async(coarse_points, detector, cache)
        candidate_windows = self._candidate_windows(coarse_points, cache)
        if len(candidate_windows) > self._config.max_candidate_windows:
            raise AdaptiveScanLimitError(
                f"视觉候选窗口超过上限: {len(candidate_windows)}"
            )
        if not candidate_windows:
            return AdaptiveScanResult((), (), len(cache), tuple(stages))

        refinement_intervals = self._config.effective_refinement_intervals
        for interval in refinement_intervals:
            stages.append(f"topology_{_stage_value(interval)}s")
            for start, end in candidate_windows:
                await self._evaluate_async(
                    _time_grid(start, end, interval), detector, cache
                )

        finest_interval = refinement_intervals[-1]
        intervals: list[BehaviorInterval] = []
        for start, end in candidate_windows:
            finest_points = _time_grid(start, end, finest_interval)
            intervals.extend(_positive_intervals(finest_points, cache))
        return AdaptiveScanResult(
            intervals=tuple(sorted(intervals, key=lambda item: item.start_seconds)),
            candidate_windows=tuple(candidate_windows),
            evaluated_point_count=len(cache),
            stages=tuple(stages),
        )

    def _evaluate(
        self,
        points: list[float],
        detector: Detector,
        cache: dict[float, bool],
    ) -> None:
        missing = [point for point in points if point not in cache]
        if not missing:
            return
        if len(cache) + len(missing) > self._config.max_detection_points:
            raise AdaptiveScanLimitError(
                f"视觉检测点超过上限: {self._config.max_detection_points}"
            )
        detected = detector(missing)
        for point in missing:
            value = detected.get(point)
            if not isinstance(value, bool):
                raise AdaptiveScanError(f"检测器未返回有效布尔结果: {point}")
            cache[point] = value

    async def _evaluate_async(
        self,
        points: list[float],
        detector: AsyncDetector,
        cache: dict[float, bool],
    ) -> None:
        missing = [point for point in points if point not in cache]
        if not missing:
            return
        if len(cache) + len(missing) > self._config.max_detection_points:
            raise AdaptiveScanLimitError(
                f"视觉检测点超过上限: {self._config.max_detection_points}"
            )
        detected = await detector(missing)
        for point in missing:
            value = detected.get(point)
            if not isinstance(value, bool):
                raise AdaptiveScanError(f"检测器未返回有效布尔结果: {point}")
            cache[point] = value

    @staticmethod
    def _candidate_windows(
        coarse_points: list[float],
        cache: Mapping[float, bool],
    ) -> list[tuple[float, float]]:
        positive_indices = [
            index for index, point in enumerate(coarse_points) if cache[point]
        ]
        if not positive_indices:
            return []
        groups: list[list[int]] = []
        for index in positive_indices:
            if not groups or index != groups[-1][-1] + 1:
                groups.append([index])
            else:
                groups[-1].append(index)
        windows: list[tuple[float, float]] = []
        last_index = len(coarse_points) - 1
        for group in groups:
            left_index = max(0, group[0] - 1)
            right_index = min(last_index, group[-1] + 1)
            windows.append((coarse_points[left_index], coarse_points[right_index]))
        return windows


def _positive_intervals(
    points: list[float],
    values: Mapping[float, bool],
) -> list[BehaviorInterval]:
    intervals: list[BehaviorInterval] = []
    run_start_index: int | None = None
    for index, point in enumerate(points):
        if values[point] and run_start_index is None:
            run_start_index = index
        if not values[point] and run_start_index is not None:
            intervals.append(BehaviorInterval(points[run_start_index], point))
            run_start_index = None
    if run_start_index is not None and points[-1] > points[run_start_index]:
        intervals.append(BehaviorInterval(points[run_start_index], points[-1]))
    return intervals


def _time_grid(start: float, end: float, interval: float) -> list[float]:
    start = _point(start)
    end = _point(end)
    count = math.floor((end - start) / interval)
    points = [_point(start + index * interval) for index in range(count + 1)]
    if points[-1] < end:
        points.append(end)
    return points


def _point(value: float) -> float:
    return round(float(value), 6)


def _stage_value(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)
