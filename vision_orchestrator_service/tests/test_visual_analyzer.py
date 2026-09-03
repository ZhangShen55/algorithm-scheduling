from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from packages.platform_contracts.status import Priority, TaskType
from packages.platform_contracts.vision import VisualAnalysisCommand
from vision_orchestrator_service.app.application.analyzer import CourseVisualAnalyzer
from vision_orchestrator_service.app.core.config import VisionSettings
from vision_orchestrator_service.app.domain.evidence import VisionEvidencePublisher
from vision_orchestrator_service.app.infrastructure.cache import VisionStream
from vision_orchestrator_service.app.infrastructure.media import ExtractedFrame


class Repository:
    def __init__(self) -> None:
        self.fallbacks: dict[str, float] = {}

    def get_node(self, node_id: int) -> object:
        assert node_id > 0
        return SimpleNamespace(course_task_type_id=7)

    def get_or_create_visual_fallback(
        self,
        course_task_type_id: int,
        metric_code: str,
        candidate_value: float,
    ) -> float:
        assert course_task_type_id == 7
        return self.fallbacks.setdefault(metric_code, candidate_value)


class FrameExtractor:
    def __init__(self, root: Path, *, duration: float = 30.0) -> None:
        self.root = root
        self.duration = duration
        self.requested_timestamps: list[list[float]] = []

    async def duration_seconds(self, video_path: Path) -> float:
        assert video_path.is_absolute()
        return self.duration

    async def extract(
        self,
        *,
        task_id: str,
        stream: VisionStream,
        video_path: Path,
        timestamps: list[float],
    ) -> list[ExtractedFrame]:
        del video_path
        self.requested_timestamps.append(list(timestamps))
        output = self.root / task_id / stream.value.lower()
        output.mkdir(parents=True, exist_ok=True)
        frames = []
        for point in sorted(set(timestamps)):
            path = output / f"{round(point * 1000)}.jpg"
            path.write_bytes(b"jpeg")
            frames.append(ExtractedFrame(point, round(point * 1000), path))
        return frames


class Vbas:
    async def analyze(
        self,
        *,
        task_id: str,
        stream: VisionStream,
        frames: list[object],
        trace_id: str | None = None,
    ) -> list[dict[str, object]]:
        assert task_id
        assert trace_id
        results = []
        for frame in frames:
            point = frame.timestamp_seconds
            if stream is VisionStream.TEACHER:
                writing = int(9 <= point < 18)
                sitting = int(20 <= point < 24)
                result_list = [
                    _object(201, sitting),
                    _object(202, int(not sitting)),
                    _object(203, writing),
                    _object(204, int(not writing)),
                ]
            else:
                region_count = 8 if frame.points else 0
                result_list = [
                    _object(100, 30),
                    _object(101, region_count or 24),
                    _object(201, 1),
                    _object(202, 2),
                    _object(205, 4),
                ]
            results.append(
                {
                    "image_id": frame.image_id,
                    "response": {
                        "StatusObject": {
                            "StatusCode": 0,
                            "ImageId": frame.image_id,
                        },
                        "ResultList": result_list,
                    },
                }
            )
        return results


def _object(object_type: int, count: int) -> dict[str, object]:
    return {
        "ObjectType": object_type,
        "ObjectCount": count,
        "ObjectPostList": ([{"Confidence": 0.9}] if count else []),
    }


def _command(
    tmp_path: Path,
    task_type: TaskType,
    *,
    strategy: dict[str, object],
) -> VisualAnalysisCommand:
    video_path = tmp_path / "course" / "course-visual-runtime" / "teacher.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.touch()
    return VisualAnalysisCommand(
        command_id=UUID("00000000-0000-0000-0000-000000000201"),
        task_id="course-visual-runtime",
        task_type=task_type,
        node_id=31,
        submission_id="submission-visual",
        local_video_path=str(video_path.resolve()),
        priority=Priority.NORMAL,
        dispatch_attempt=1,
        claim_token=UUID("00000000-0000-0000-0000-000000000031"),
        strategy=strategy,
        student_count=38 if task_type is TaskType.STUDENT_BEHAVIOR else None,
        front_points=([{"X": 0, "Y": 0}] if task_type is TaskType.STUDENT_BEHAVIOR else None),
        back_point=None,
    )


def _analyzer(tmp_path: Path) -> CourseVisualAnalyzer:
    settings = VisionSettings(
        storage={
            "course_root": tmp_path / "course",
            "result_root": tmp_path / "result",
            "evidence_root": tmp_path / "result" / "evidence",
        },
        scan={
            "default_interval_seconds": 10,
            "refinement_intervals_seconds": (5, 2),
        },
        evidence={
            "same_category_min_interval_seconds": 0,
            "max_per_category": 10,
            "max_total": 20,
        },
    )
    return CourseVisualAnalyzer(
        Repository(),
        FrameExtractor(tmp_path / "frames"),
        Vbas(),
        VisionEvidencePublisher(result_root=settings.storage.result_root),
        settings=settings,
    )


@pytest.mark.asyncio
async def test_teacher_analysis_refines_hits_and_persists_empty_safe_intervals(
    tmp_path: Path,
) -> None:
    progress: list[tuple[int, str, str]] = []

    async def report(percent: int, stage: str, reason: str) -> None:
        progress.append((percent, stage, reason))

    result = await _analyzer(tmp_path).analyze(
        _command(
            tmp_path,
            TaskType.TEACHER_BEHAVIOR,
            strategy={
                "coarse_interval_seconds": 10,
                "refinement_intervals_seconds": [5, 2],
            },
        ),
        report,
    )

    assert isinstance(result.result, dict)
    assert result.result["writing_intervals"]
    assert result.result["sitting_intervals"]
    assert isinstance(result.result["standing_intervals"], list)
    assert isinstance(result.result["teaching_intervals"], list)
    assert result.result["scan"]["stages"] == [
        "coarse_10s",
        "topology_5s",
        "topology_2s",
    ]
    assert result.artifact_count and result.artifact_count > 0
    assert Path(result.artifact_path or "").is_dir()
    assert [item[0] for item in progress] == [5, 20, 75, 95]


@pytest.mark.asyncio
async def test_teacher_adaptive_scan_never_resubmits_the_same_frame_identity(
    tmp_path: Path,
) -> None:
    class RecordingVbas(Vbas):
        def __init__(self) -> None:
            self.image_ids: list[str] = []

        async def analyze(self, **kwargs):
            self.image_ids.extend(frame.image_id for frame in kwargs["frames"])
            return await super().analyze(**kwargs)

    async def report(percent: int, stage: str, reason: str) -> None:
        del percent, stage, reason

    analyzer = _analyzer(tmp_path)
    recorder = RecordingVbas()
    analyzer._vbas = recorder
    await analyzer.analyze(
        _command(
            tmp_path,
            TaskType.TEACHER_BEHAVIOR,
            strategy={
                "coarse_interval_seconds": 10,
                "refinement_intervals_seconds": [5, 2],
            },
        ),
        report,
    )

    assert len(recorder.image_ids) > 4
    assert len(recorder.image_ids) == len(set(recorder.image_ids))


@pytest.mark.parametrize(
    ("duration", "expected_safe_end"),
    ((5.0, 4.5), (0.4, 0.2)),
)
@pytest.mark.asyncio
async def test_teacher_analysis_avoids_unstable_video_end_but_keeps_real_duration(
    tmp_path: Path,
    duration: float,
    expected_safe_end: float,
) -> None:
    analyzer = _analyzer(tmp_path)
    extractor = analyzer._frames
    assert isinstance(extractor, FrameExtractor)
    extractor.duration = duration

    async def report(percent: int, stage: str, reason: str) -> None:
        del percent, stage, reason

    result = await analyzer.analyze(
        _command(
            tmp_path,
            TaskType.TEACHER_BEHAVIOR,
            strategy={"coarse_interval_seconds": 10},
        ),
        report,
    )

    requested = sorted(
        {
            point
            for timestamps in extractor.requested_timestamps
            for point in timestamps
        }
    )
    assert requested == [0.0, expected_safe_end]
    assert isinstance(result.result, dict)
    assert result.result["duration_seconds"] == duration


@pytest.mark.asyncio
async def test_teacher_interval_uses_real_duration_after_safe_end_sampling(
    tmp_path: Path,
) -> None:
    class AlwaysWritingVbas(Vbas):
        async def analyze(self, **kwargs):
            results = await super().analyze(**kwargs)
            for result in results:
                for item in result["response"]["ResultList"]:
                    if item["ObjectType"] == 203:
                        item["ObjectCount"] = 1
                        item["ObjectPostList"] = [{"Confidence": 0.9}]
            return results

    analyzer = _analyzer(tmp_path)
    extractor = analyzer._frames
    assert isinstance(extractor, FrameExtractor)
    extractor.duration = 5.0
    analyzer._vbas = AlwaysWritingVbas()

    async def report(percent: int, stage: str, reason: str) -> None:
        del percent, stage, reason

    result = await analyzer.analyze(
        _command(
            tmp_path,
            TaskType.TEACHER_BEHAVIOR,
            strategy={
                "coarse_interval_seconds": 10,
                "refinement_intervals_seconds": [2, 1],
            },
        ),
        report,
    )

    assert isinstance(result.result, dict)
    assert max(
        point
        for timestamps in extractor.requested_timestamps
        for point in timestamps
    ) == 4.5
    assert result.result["writing_intervals"][-1]["end_seconds"] == 5.0


@pytest.mark.asyncio
async def test_student_analysis_uses_regions_and_stable_database_fallback(
    tmp_path: Path,
) -> None:
    async def report(percent: int, stage: str, reason: str) -> None:
        del percent, stage, reason

    command = _command(
        tmp_path,
        TaskType.STUDENT_BEHAVIOR,
        strategy={"interval_seconds": 10},
    )
    result = await _analyzer(tmp_path).analyze(command, report)

    assert isinstance(result.result, dict)
    assert result.result["recognized_total_person_count"] == 30
    assert result.result["stable_person_count"] == 24
    assert result.result["front_occupancy_ratio"] == pytest.approx(8 / 30, abs=1e-6)
    assert result.result["front_region_provided"] is True
    assert result.result["back_region_provided"] is False
    assert 0.25 <= result.result["back_occupancy_ratio"] <= 0.40
    assert len(result.result["frames"]) == 3
    assert result.artifact_count and result.artifact_count > 0
