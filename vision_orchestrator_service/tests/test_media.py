from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from vision_orchestrator_service.app.infrastructure.cache import VisionStream
from vision_orchestrator_service.app.infrastructure.media import (
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
