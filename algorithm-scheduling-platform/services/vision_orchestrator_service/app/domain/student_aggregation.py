from __future__ import annotations

import random
import statistics
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

JsonObject = dict[str, Any]


class FallbackValueRepository(Protocol):
    def get_or_create_visual_fallback(
        self,
        course_task_type_id: int,
        metric_code: str,
        candidate_value: float,
    ) -> float: ...


@dataclass(frozen=True, slots=True)
class StudentAggregationConfig:
    front_fallback_min: float = 0.10
    front_fallback_max: float = 0.15
    back_fallback_min: float = 0.25
    back_fallback_max: float = 0.40

    def __post_init__(self) -> None:
        for name, minimum, maximum in (
            ("前排", self.front_fallback_min, self.front_fallback_max),
            ("后排", self.back_fallback_min, self.back_fallback_max),
        ):
            if not 0 <= minimum <= maximum <= 1:
                raise ValueError(f"{name}兜底比例范围必须在 0 到 1 之间")


@dataclass(frozen=True, slots=True)
class StudentFrameObservation:
    detected_total: int
    stable_person_count: int
    front_stable_person_count: int
    back_stable_person_count: int

    def __post_init__(self) -> None:
        counts = (
            self.detected_total,
            self.stable_person_count,
            self.front_stable_person_count,
            self.back_stable_person_count,
        )
        if any(value < 0 for value in counts):
            raise ValueError("学生人数检测值不能小于 0")
        if any(value > self.detected_total for value in counts[1:]):
            raise ValueError("稳定人数不能大于识别总人数")


class StudentBehaviorAggregator:
    def __init__(
        self,
        fallback_repository: FallbackValueRepository,
        *,
        config: StudentAggregationConfig | None = None,
        random_uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._fallback_repository = fallback_repository
        self._config = config or StudentAggregationConfig()
        self._random_uniform = random_uniform

    def aggregate(
        self,
        *,
        course_task_type_id: int,
        student_count: int,
        observations: Iterable[StudentFrameObservation],
        front_points: list[JsonObject] | None,
        back_point: list[JsonObject] | None,
    ) -> JsonObject:
        if student_count < 0:
            raise ValueError("student_count 不能小于 0")
        valid = [item for item in observations if item.detected_total > 0]
        recognized_total = _median(item.detected_total for item in valid)
        stable_person_count = _median(item.stable_person_count for item in valid)
        attendance_rate = _ratio(stable_person_count, float(student_count))

        front_region_provided = bool(front_points)
        back_region_provided = bool(back_point)
        if front_region_provided:
            front_ratio = _median(
                item.front_stable_person_count / item.detected_total for item in valid
            )
        else:
            front_ratio = self._fallback(
                course_task_type_id,
                "FRONT_OCCUPANCY_RATIO",
                self._config.front_fallback_min,
                self._config.front_fallback_max,
            )
        if back_region_provided:
            back_ratio = _median(
                item.back_stable_person_count / item.detected_total for item in valid
            )
        else:
            back_ratio = self._fallback(
                course_task_type_id,
                "BACK_OCCUPANCY_RATIO",
                self._config.back_fallback_min,
                self._config.back_fallback_max,
            )
        return {
            "student_count": student_count,
            "recognized_total_person_count": recognized_total,
            "stable_person_count": stable_person_count,
            "attendance_rate": round(attendance_rate, 6),
            "front_occupancy_ratio": round(front_ratio, 6),
            "back_occupancy_ratio": round(back_ratio, 6),
            "front_region_provided": front_region_provided,
            "back_region_provided": back_region_provided,
        }

    def _fallback(
        self,
        course_task_type_id: int,
        metric_code: str,
        minimum: float,
        maximum: float,
    ) -> float:
        candidate = round(self._random_uniform(minimum, maximum), 6)
        return self._fallback_repository.get_or_create_visual_fallback(
            course_task_type_id,
            metric_code,
            candidate,
        )


def _median(values: Iterable[float]) -> float:
    materialized = list(values)
    if not materialized:
        return 0.0
    return float(statistics.median(materialized))


def _ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return min(1.0, max(0.0, numerator / denominator))
