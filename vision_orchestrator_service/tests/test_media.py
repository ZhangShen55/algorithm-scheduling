from __future__ import annotations

import asyncio
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from vision_orchestrator_service.app.infrastructure.cache import VisionStream
from vision_orchestrator_service.app.infrastructure.media import (
    ExtractedFrame,
    FFmpegFrameExtractor,
    VideoFrameError,
)


@pytest.mark.asyncio
async def test_ffmpeg_extractor_reads_shared_local_video_and_reuses_frame(
    tmp_path: Path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("本机未安装 ffmpeg/ffprobe")
    course_root = tmp_path / "course"
    task_root = course_root / "course-media"
    task_root.mkdir(parents=True)
    video = task_root / "teacher.mp4"
    await asyncio.to_thread(
        subprocess.run,
        [
            ffmpeg,
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:d=1",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(video),
        ],
        check=True,
    )
    extractor = FFmpegFrameExtractor(
        course_root=course_root,
        ffmpeg_binary=ffmpeg,
        ffprobe_binary=ffprobe,
    )

    duration = await extractor.duration_seconds(video)
    first = await extractor.extract(
        task_id="course-media",
        stream=VisionStream.TEACHER,
        video_path=video,
        timestamps=[0.0],
    )
    second = await extractor.extract(
        task_id="course-media",
        stream=VisionStream.TEACHER,
        video_path=video,
        timestamps=[0.0],
    )

    assert duration >= 1
    assert first[0].path == second[0].path
    assert first[0].path.is_file()
    assert first[0].path.is_relative_to(task_root / "vision/t")


@pytest.mark.asyncio
async def test_extractor_rejects_video_outside_course_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"not-a-video")
    extractor = FFmpegFrameExtractor(course_root=tmp_path / "course")

    with pytest.raises(VideoFrameError, match="必须位于课程目录"):
        await extractor.duration_seconds(outside)


@pytest.mark.asyncio
async def test_extractor_limits_concurrent_ffmpeg_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course_root = tmp_path / "course"
    task_root = course_root / "course-media"
    task_root.mkdir(parents=True)
    video = task_root / "teacher.mp4"
    video.write_bytes(b"fixture")
    extractor = FFmpegFrameExtractor(
        course_root=course_root,
        max_concurrent_processes=2,
    )
    lock = threading.Lock()
    active = 0
    maximum = 0

    def fake_extract_one(
        video_path: Path,
        output_root: Path,
        timestamp_seconds: float,
    ) -> ExtractedFrame:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        try:
            time.sleep(0.03)
            return ExtractedFrame(
                timestamp_seconds=timestamp_seconds,
                frame_index=round(timestamp_seconds * 1000),
                path=output_root / f"frame-{timestamp_seconds}.jpg",
            )
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(extractor, "_extract_one", fake_extract_one)

    teacher_frames, student_frames = await asyncio.gather(
        extractor.extract(
            task_id="course-media",
            stream=VisionStream.TEACHER,
            video_path=video,
            timestamps=[float(index) for index in range(4)],
        ),
        extractor.extract(
            task_id="course-media",
            stream=VisionStream.STUDENT,
            video_path=video,
            timestamps=[float(index) for index in range(4, 8)],
        ),
    )

    assert len(teacher_frames) + len(student_frames) == 8
    assert maximum == 2


@pytest.mark.parametrize("value", (0, -1, True, 1.5, "2"))
def test_extractor_rejects_invalid_process_limit(tmp_path: Path, value: object) -> None:
    with pytest.raises(ValueError, match="最大并发数必须为正整数"):
        FFmpegFrameExtractor(
            course_root=tmp_path / "course",
            max_concurrent_processes=value,  # type: ignore[arg-type]
        )
