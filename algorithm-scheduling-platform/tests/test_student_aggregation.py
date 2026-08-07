from services.vision_orchestrator_service.student_aggregation import (
    StudentAggregationConfig,
    StudentBehaviorAggregator,
    StudentFrameObservation,
)


class RecordingFallbackRepository:
    def __init__(self) -> None:
        self.values: dict[tuple[int, str], float] = {}
        self.candidates: list[tuple[int, str, float]] = []

    def get_or_create_visual_fallback(
        self,
        course_task_type_id: int,
        metric_code: str,
        candidate_value: float,
    ) -> float:
        self.candidates.append((course_task_type_id, metric_code, candidate_value))
        return self.values.setdefault(
            (course_task_type_id, metric_code),
            candidate_value,
        )


def test_student_metrics_use_stable_people_and_detected_total_denominator() -> None:
    repository = RecordingFallbackRepository()
    aggregator = StudentBehaviorAggregator(repository)
    observations = [
        StudentFrameObservation(
            detected_total=40,
            stable_person_count=38,
            front_stable_person_count=15,
            back_stable_person_count=20,
        ),
        StudentFrameObservation(
            detected_total=38,
            stable_person_count=37,
            front_stable_person_count=14,
            back_stable_person_count=19,
        ),
    ]

    result = aggregator.aggregate(
        course_task_type_id=41,
        student_count=38,
        observations=observations,
        front_points=[{"X": 0, "Y": 0}],
        back_point=[{"X": 100, "Y": 100}],
    )

    assert result["student_count"] == 38
    assert result["stable_person_count"] == 37.5
    assert result["recognized_total_person_count"] == 39.0
    assert result["attendance_rate"] == round(37.5 / 38, 6)
    assert result["front_occupancy_ratio"] == round(((15 / 40) + (14 / 38)) / 2, 6)
    assert result["back_occupancy_ratio"] == round(((20 / 40) + (19 / 38)) / 2, 6)
    assert result["front_region_provided"] is True
    assert result["back_region_provided"] is True
    assert repository.candidates == []


def test_missing_regions_use_persisted_configured_fallback_once() -> None:
    repository = RecordingFallbackRepository()
    generated = iter([0.12, 0.30, 0.14, 0.38])
    aggregator = StudentBehaviorAggregator(
        repository,
        config=StudentAggregationConfig(
            front_fallback_min=0.10,
            front_fallback_max=0.15,
            back_fallback_min=0.25,
            back_fallback_max=0.40,
        ),
        random_uniform=lambda minimum, maximum: next(generated),
    )
    observations = [StudentFrameObservation(30, 28, 0, 0)]

    first = aggregator.aggregate(
        course_task_type_id=42,
        student_count=38,
        observations=observations,
        front_points=None,
        back_point=None,
    )
    second = aggregator.aggregate(
        course_task_type_id=42,
        student_count=38,
        observations=observations,
        front_points=None,
        back_point=None,
    )

    assert first["front_occupancy_ratio"] == second["front_occupancy_ratio"] == 0.12
    assert first["back_occupancy_ratio"] == second["back_occupancy_ratio"] == 0.30
    assert first["front_region_provided"] is False
    assert first["back_region_provided"] is False
    assert "is_estimated" not in first
    assert "source" not in first
