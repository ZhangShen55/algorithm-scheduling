from __future__ import annotations

from pathlib import Path

import pytest
from vision_orchestrator_service.app.infrastructure.cache import VisionStream
from vision_orchestrator_service.app.infrastructure.media import (
    ExtractedFrame,
    FFmpegFrameExtractor,
)

from scripts.vbas_benchmark import media as media_module


@pytest.mark.asyncio
async def test_media_stage_observer_records_probe_and_extraction_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course_root = tmp_path / "course"
    course_root.mkdir()
    video = course_root / "teacher.mp4"
    video.write_bytes(b"video")
    events: list[tuple[str, dict[str, object]]] = []
    extractor = FFmpegFrameExtractor(
        course_root=course_root,
        max_concurrent_processes=2,
        stage_observer=lambda event, detail: events.append((event, detail)),
    )

    async def probe(_path: Path) -> float:
        return 20.0

    async def extract_batch(
        _video: Path,
        output_root: Path,
        timestamps: list[float],
    ) -> list[ExtractedFrame]:
        results: list[ExtractedFrame] = []
        for index, timestamp in enumerate(timestamps):
            path = output_root / f"fake-{index}.jpg"
            path.write_bytes(b"image")
            results.append(ExtractedFrame(timestamp, index, path))
        return results

    monkeypatch.setattr(extractor, "_probe_duration", probe)
    monkeypatch.setattr(extractor, "_extract_uniform_batch", extract_batch)

    assert await extractor.duration_seconds(video, task_id="course-001") == 20
    frames = await extractor.extract(
        task_id="course-001",
        stream=VisionStream.TEACHER,
        video_path=video,
        timestamps=[0.0, 10.0],
    )

    assert len(frames) == 2
    phases = [(event, detail["operation"]) for event, detail in events]
    assert phases == [
        ("media_queued", "ffprobe"),
        ("media_started", "ffprobe"),
        ("media_finished", "ffprobe"),
        ("media_queued", "ffmpeg_batch"),
        ("media_started", "ffmpeg_batch"),
        ("media_finished", "ffmpeg_batch"),
    ]
    assert all(detail["task_id"] == "course-001" for _, detail in events)


@pytest.mark.asyncio
async def test_zero_delay_media_sink_never_constructs_vbas_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course_root = tmp_path / "course"
    course_root.mkdir()
    video = course_root / "student.mp4"
    video.write_bytes(b"video")

    class Extractor:
        def __init__(self, **kwargs):
            self.observer = kwargs["stage_observer"]

        async def duration_seconds(self, video_path, *, task_id=None):
            del video_path, task_id
            return 20.0

        async def extract(self, *, task_id, stream, video_path, timestamps):
            del stream, video_path
            results = []
            for index, timestamp in enumerate(timestamps):
                path = course_root / task_id / f"{index}.jpg"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"image")
                results.append(ExtractedFrame(timestamp, index, path))
            return results

        async def close(self):
            return None

    monkeypatch.setattr(media_module, "FFmpegFrameExtractor", Extractor)
    monkeypatch.setattr(media_module, "_read_cpu_ticks", lambda: (1, 1, 0))

    result = await media_module.run_media_feed_isolation(
        video_path=video,
        course_root=course_root,
        campaign_id="test",
        stream=VisionStream.STUDENT,
        active_streams=2,
        host_sample_interval_seconds=10,
    )

    assert result.status == "passed"
    assert result.prepared_batches == 2
    assert [event.event for event in result.events].count("sink_completed") == 2
    assert not any(event.event.startswith("vbas") for event in result.events)


def test_media_cleanup_only_removes_current_campaign_outputs(tmp_path: Path) -> None:
    current = tmp_path / "vbas-feed-campaign-current-student-000"
    other = tmp_path / "vbas-feed-campaign-other-student-000"
    current.mkdir()
    other.mkdir()

    media_module.remove_media_fixture_outputs(tmp_path, "campaign-current")

    assert not current.exists()
    assert other.is_dir()
