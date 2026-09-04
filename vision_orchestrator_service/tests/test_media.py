from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from vision_orchestrator_service.app.infrastructure.cache import VisionStream
from vision_orchestrator_service.app.infrastructure.media import (
    ExtractedFrame,
    FFmpegFrameExtractor,
    VideoFrameError,
    build_frame_batch_plans,
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
    active = 0
    maximum = 0

    async def fake_extract_one(
        video_path: Path,
        output_root: Path,
        timestamp_seconds: float,
    ) -> ExtractedFrame:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0.03)
            return ExtractedFrame(
                timestamp_seconds=timestamp_seconds,
                frame_index=round(timestamp_seconds * 1000),
                path=output_root / f"frame-{timestamp_seconds}.jpg",
            )
        finally:
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


def test_frame_batch_plan_is_deterministic_and_keeps_short_tail() -> None:
    first = build_frame_batch_plans(
        task_id="course-plan",
        stream=VisionStream.STUDENT,
        timestamps=[4.0, 1.0, 2.0, 2.0, 3.0, 5.0],
        batch_size=2,
        identity_suffix="assets",
    )
    replay = build_frame_batch_plans(
        task_id="course-plan",
        stream=VisionStream.STUDENT,
        timestamps=[5.0, 3.0, 2.0, 1.0, 4.0],
        batch_size=2,
        identity_suffix="assets",
    )

    assert first == replay
    assert [plan.timestamps for plan in first] == [
        (1.0, 2.0),
        (3.0, 4.0),
        (5.0,),
    ]
    assert len({plan.batch_id for plan in first}) == 3


@pytest.mark.asyncio
async def test_short_course_starts_before_long_course_finishes(
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
    started: list[float] = []

    async def fake_extract_one(
        video_path: Path,
        output_root: Path,
        timestamp_seconds: float,
    ) -> ExtractedFrame:
        del video_path
        started.append(timestamp_seconds)
        await asyncio.sleep(0.01)
        return ExtractedFrame(
            timestamp_seconds,
            round(timestamp_seconds * 1000),
            output_root / f"frame-{timestamp_seconds}.jpg",
        )

    monkeypatch.setattr(extractor, "_extract_one", fake_extract_one)
    long_task = asyncio.create_task(
        extractor.extract(
            task_id="course-media",
            stream=VisionStream.TEACHER,
            video_path=video,
            timestamps=[float(index) for index in range(20)],
        )
    )
    while len(started) < 2:
        await asyncio.sleep(0)
    short_task = asyncio.create_task(
        extractor.extract(
            task_id="course-media",
            stream=VisionStream.STUDENT,
            video_path=video,
            timestamps=[99.0],
        )
    )
    await asyncio.gather(long_task, short_task)

    assert started.index(99.0) < started.index(19.0)
    assert extractor.peak_running_jobs == 2
    assert extractor.peak_pending_jobs <= 3


@pytest.mark.asyncio
async def test_cancelled_media_command_reaps_subprocess(tmp_path: Path) -> None:
    sleep = Path("/bin/sleep")
    if not sleep.is_file():
        pytest.skip("本机没有 /bin/sleep")
    extractor = FFmpegFrameExtractor(
        course_root=tmp_path / "course",
        command_timeout_seconds=30,
    )
    task = asyncio.create_task(extractor._run([str(sleep), "10"]))
    while not extractor._active_processes:
        await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert extractor._active_processes == set()


@pytest.mark.asyncio
async def test_many_courses_keep_media_waiters_and_processes_bounded(
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

    async def fake_extract_one(
        video_path: Path,
        output_root: Path,
        timestamp_seconds: float,
    ) -> ExtractedFrame:
        del video_path
        await asyncio.sleep(0.001)
        return ExtractedFrame(
            timestamp_seconds,
            round(timestamp_seconds * 1000),
            output_root / f"frame-{timestamp_seconds}.jpg",
        )

    monkeypatch.setattr(extractor, "_extract_one", fake_extract_one)
    results = await asyncio.gather(
        *(
            extractor.extract(
                task_id="course-media",
                stream=VisionStream.TEACHER,
                video_path=video,
                timestamps=[course * 100.0 + float(index) for index in range(8)],
            )
            for course in range(8)
        )
    )

    assert sum(len(item) for item in results) == 64
    assert extractor.peak_running_jobs == 2
    assert extractor.peak_pending_jobs <= 8 * 2


@pytest.mark.asyncio
async def test_probe_is_not_starved_by_long_frame_extraction(
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
    order: list[str] = []

    async def fake_extract_one(
        video_path: Path,
        output_root: Path,
        timestamp_seconds: float,
    ) -> ExtractedFrame:
        del video_path
        order.append(f"frame-{timestamp_seconds:g}")
        await asyncio.sleep(0.01)
        return ExtractedFrame(
            timestamp_seconds,
            round(timestamp_seconds * 1000),
            output_root / f"frame-{timestamp_seconds}.jpg",
        )

    async def fake_probe_duration(video_path: Path) -> float:
        del video_path
        order.append("probe")
        return 120.0

    monkeypatch.setattr(extractor, "_extract_one", fake_extract_one)
    monkeypatch.setattr(extractor, "_probe_duration", fake_probe_duration)
    extraction = asyncio.create_task(
        extractor.extract(
            task_id="course-media",
            stream=VisionStream.TEACHER,
            video_path=video,
            timestamps=[float(index) for index in range(20)],
        )
    )
    while len(order) < 2:
        await asyncio.sleep(0)
    duration = await extractor.duration_seconds(video)
    await extraction

    assert duration == 120.0
    assert order.index("probe") < order.index("frame-19")
