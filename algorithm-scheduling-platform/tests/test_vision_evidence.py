from pathlib import Path

from services.vision_orchestrator_service.evidence import (
    EvidenceCandidate,
    EvidenceCategory,
    VisionEvidenceConfig,
    VisionEvidencePublisher,
)


def test_publishes_existing_five_and_new_teacher_behavior_evidence(
    tmp_path: Path,
) -> None:
    categories = [
        EvidenceCategory.STUDENT_HEAD_UP,
        EvidenceCategory.STUDENT_READING,
        EvidenceCategory.STUDENT_SLEEPING,
        EvidenceCategory.STUDENT_PHONE_USE,
        EvidenceCategory.TEACHER_ALERT,
        EvidenceCategory.TEACHER_WRITING,
        EvidenceCategory.TEACHER_SITTING,
        EvidenceCategory.TEACHER_TEACHING,
    ]
    candidates = []
    for index, category in enumerate(categories):
        source = tmp_path / "course" / f"frame-{index}.jpg"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(f"image-{category.value}".encode())
        candidates.append(
            EvidenceCandidate(
                category=category,
                capture_second=float(index * 10),
                confidence=0.9,
                source_path=source,
            )
        )
    publisher = VisionEvidencePublisher(
        result_root=tmp_path / "result",
        config=VisionEvidenceConfig(
            max_per_category=3,
            max_total=20,
            same_category_min_interval_seconds=0,
        ),
    )

    artifacts = publisher.publish("course-001", candidates)

    assert {artifact.category for artifact in artifacts} == set(categories)
    assert all(
        artifact.path.is_file()
        and artifact.path.is_relative_to(tmp_path / "result/course-001/vision")
        for artifact in artifacts
    )
    assert all("/data/course" not in str(artifact.path) for artifact in artifacts)


def test_same_category_selection_keeps_stronger_evidence_and_limits_count(
    tmp_path: Path,
) -> None:
    candidates = []
    for index, (second, confidence) in enumerate(((10, 0.6), (20, 0.9), (100, 0.8))):
        source = tmp_path / f"writing-{index}.jpg"
        source.write_bytes(f"image-{index}".encode())
        candidates.append(
            EvidenceCandidate(
                EvidenceCategory.TEACHER_WRITING,
                float(second),
                confidence,
                source,
            )
        )
    publisher = VisionEvidencePublisher(
        result_root=tmp_path / "result",
        config=VisionEvidenceConfig(
            max_per_category=2,
            max_total=10,
            same_category_min_interval_seconds=30,
        ),
    )

    artifacts = publisher.publish("course-001", candidates)

    assert [(item.capture_second, item.confidence) for item in artifacts] == [
        (20.0, 0.9),
        (100.0, 0.8),
    ]


def test_absent_behavior_creates_no_representative_image(tmp_path: Path) -> None:
    publisher = VisionEvidencePublisher(result_root=tmp_path / "result")

    artifacts = publisher.publish("course-001", [])

    assert artifacts == []
    assert not (tmp_path / "result/course-001/vision").exists()
