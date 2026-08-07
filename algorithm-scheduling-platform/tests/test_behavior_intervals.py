from services.vision_orchestrator_service.adaptive_scan import BehaviorInterval
from services.vision_orchestrator_service.behavior_intervals import (
    TeacherBehaviorAggregationConfig,
    build_teacher_behavior_result,
    merge_behavior_intervals,
)


def test_writing_intervals_merge_when_gap_equals_three_seconds() -> None:
    merged = merge_behavior_intervals(
        [BehaviorInterval(1, 9), BehaviorInterval(12, 21)],
        max_gap_seconds=3,
    )

    assert merged == (BehaviorInterval(1, 21),)


def test_writing_intervals_stay_separate_when_gap_exceeds_threshold() -> None:
    merged = merge_behavior_intervals(
        [BehaviorInterval(1, 9), BehaviorInterval(12.1, 21)],
        max_gap_seconds=3,
    )

    assert merged == (BehaviorInterval(1, 9), BehaviorInterval(12.1, 21))


def test_valid_analysis_without_behavior_returns_completed_empty_result() -> None:
    outcome = build_teacher_behavior_result(
        intervals={
            "writing": [],
            "sitting": [],
            "standing": [],
            "teaching": [],
        },
        valid_frame_count=20,
        total_frame_count=20,
        config=TeacherBehaviorAggregationConfig(
            min_valid_frame_count=5,
            min_valid_frame_ratio=0.5,
        ),
    )

    assert outcome.completed is True
    assert outcome.reason == "教师行为分析完成，未检测到目标行为"
    assert outcome.result["writing_intervals"] == []
    assert outcome.result["sitting_intervals"] == []
    assert outcome.result["analysis_quality"] == "SUFFICIENT"


def test_insufficient_teacher_frames_do_not_fabricate_behavior() -> None:
    outcome = build_teacher_behavior_result(
        intervals={
            "writing": [BehaviorInterval(10, 20)],
            "sitting": [BehaviorInterval(30, 40)],
            "standing": [BehaviorInterval(0, 50)],
            "teaching": [BehaviorInterval(0, 50)],
        },
        valid_frame_count=2,
        total_frame_count=20,
        config=TeacherBehaviorAggregationConfig(
            min_valid_frame_count=5,
            min_valid_frame_ratio=0.5,
        ),
    )

    assert outcome.completed is True
    assert outcome.reason == "有效教师画面不足，无法确认教师行为区间"
    assert outcome.result["analysis_quality"] == "INSUFFICIENT_VALID_FRAMES"
    assert outcome.result["writing_intervals"] == []
    assert outcome.result["sitting_intervals"] == []
    assert outcome.result["standing_intervals"] == []
    assert outcome.result["teaching_intervals"] == []
