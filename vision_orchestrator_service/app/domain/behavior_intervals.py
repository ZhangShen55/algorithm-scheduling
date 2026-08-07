from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adaptive_scan import BehaviorInterval


@dataclass(frozen=True, slots=True)
class TeacherBehaviorAggregationConfig:
    writing_max_gap_seconds: float = 3
    sitting_max_gap_seconds: float = 5
    min_valid_frame_count: int = 5
    min_valid_frame_ratio: float = 0.5

    def __post_init__(self) -> None:
        if self.writing_max_gap_seconds < 0 or self.sitting_max_gap_seconds < 0:
            raise ValueError("行为区间允许缺口不能小于 0")
        if self.min_valid_frame_count <= 0:
            raise ValueError("最小有效教师帧数必须大于 0")
        if not 0 < self.min_valid_frame_ratio <= 1:
            raise ValueError("最小有效教师帧比例必须在 0 到 1 之间")


@dataclass(frozen=True, slots=True)
class TeacherBehaviorOutcome:
    completed: bool
    reason: str
    result: dict[str, Any]


def merge_behavior_intervals(
    intervals: list[BehaviorInterval],
    *,
    max_gap_seconds: float,
) -> tuple[BehaviorInterval, ...]:
    if max_gap_seconds < 0:
        raise ValueError("行为区间允许缺口不能小于 0")
    ordered = sorted(intervals, key=lambda item: (item.start_seconds, item.end_seconds))
    if not ordered:
        return ()
    merged = [ordered[0]]
    for current in ordered[1:]:
        previous = merged[-1]
        gap = current.start_seconds - previous.end_seconds
        if gap <= max_gap_seconds:
            merged[-1] = BehaviorInterval(
                previous.start_seconds,
                max(previous.end_seconds, current.end_seconds),
            )
        else:
            merged.append(current)
    return tuple(merged)


def build_teacher_behavior_result(
    *,
    intervals: dict[str, list[BehaviorInterval]],
    valid_frame_count: int,
    total_frame_count: int,
    config: TeacherBehaviorAggregationConfig,
) -> TeacherBehaviorOutcome:
    if total_frame_count <= 0:
        raise ValueError("教师行为总检测帧数必须大于 0")
    if not 0 <= valid_frame_count <= total_frame_count:
        raise ValueError("教师行为有效帧数不合法")
    valid_ratio = valid_frame_count / total_frame_count
    interval_keys = ("writing", "sitting", "standing", "teaching")
    sufficient = (
        valid_frame_count >= config.min_valid_frame_count
        and valid_ratio >= config.min_valid_frame_ratio
    )
    if sufficient:
        normalized = {
            "writing": merge_behavior_intervals(
                intervals.get("writing", []),
                max_gap_seconds=config.writing_max_gap_seconds,
            ),
            "sitting": merge_behavior_intervals(
                intervals.get("sitting", []),
                max_gap_seconds=config.sitting_max_gap_seconds,
            ),
            "standing": merge_behavior_intervals(
                intervals.get("standing", []),
                max_gap_seconds=0,
            ),
            "teaching": merge_behavior_intervals(
                intervals.get("teaching", []),
                max_gap_seconds=0,
            ),
        }
        reason = (
            "教师行为分析完成"
            if any(normalized[key] for key in interval_keys)
            else "教师行为分析完成，未检测到目标行为"
        )
        quality = "SUFFICIENT"
    else:
        normalized = {key: () for key in interval_keys}
        reason = "有效教师画面不足，无法确认教师行为区间"
        quality = "INSUFFICIENT_VALID_FRAMES"

    result: dict[str, Any] = {
        "analysis_quality": quality,
        "valid_frame_count": valid_frame_count,
        "total_frame_count": total_frame_count,
        "valid_frame_ratio": valid_ratio,
    }
    for key in interval_keys:
        result[f"{key}_intervals"] = [
            {
                "start_seconds": item.start_seconds,
                "end_seconds": item.end_seconds,
            }
            for item in normalized[key]
        ]
    return TeacherBehaviorOutcome(completed=True, reason=reason, result=result)
