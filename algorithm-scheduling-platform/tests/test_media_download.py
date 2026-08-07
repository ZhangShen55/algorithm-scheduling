import asyncio
from pathlib import Path

import httpx
import pytest

from packages.platform_common.media import (
    MediaDownloader,
    MediaDownloadError,
    MediaMetadata,
)


class FakeInspector:
    async def inspect(self, path: Path) -> MediaMetadata:
        assert path.exists()
        return MediaMetadata(duration_seconds=120.5, width=1920, height=1080, format_name="mp4")


@pytest.mark.asyncio
async def test_media_download_uses_task_workspace_and_returns_metadata(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            headers={"content-length": "11"},
            content=b"video-bytes",
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        downloader = MediaDownloader(
            course_root=tmp_path,
            http_client=client,
            inspector=FakeInspector(),
            max_bytes=1024,
        )
        downloaded = await downloader.download(
            task_id="course-001",
            source_url="http://media.example/teacher.mp4",
            media_role="teacher",
        )

    assert downloaded.path == tmp_path / "course-001/media/teacher.mp4"
    assert downloaded.path.read_bytes() == b"video-bytes"
    assert downloaded.size_bytes == 11
    assert downloaded.metadata.duration_seconds == 120.5


@pytest.mark.asyncio
async def test_media_download_rejects_unsafe_scheme_and_role(tmp_path: Path) -> None:
    async with httpx.AsyncClient() as client:
        downloader = MediaDownloader(
            course_root=tmp_path,
            http_client=client,
            inspector=FakeInspector(),
            max_bytes=1024,
        )
        with pytest.raises(MediaDownloadError, match="HTTP/HTTPS"):
            await downloader.download("course-001", "file:///etc/passwd", "teacher")
        with pytest.raises(MediaDownloadError, match="媒体角色"):
            await downloader.download("course-001", "http://media/video.mp4", "../escape")


@pytest.mark.asyncio
async def test_oversized_download_removes_partial_file(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"too-large"))
    async with httpx.AsyncClient(transport=transport) as client:
        downloader = MediaDownloader(
            course_root=tmp_path,
            http_client=client,
            inspector=FakeInspector(),
            max_bytes=4,
        )
        with pytest.raises(MediaDownloadError, match="大小上限"):
            await downloader.download(
                "course-oversized",
                "http://media/video.mp4",
                "slides",
            )

    assert not (tmp_path / "course-oversized/media/slides.mp4.part").exists()


@pytest.mark.asyncio
async def test_concurrent_teacher_consumers_share_one_download(tmp_path: Path) -> None:
    request_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, content=b"shared-teacher-video")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = MediaDownloader(
            course_root=tmp_path,
            http_client=client,
            inspector=FakeInspector(),
            max_bytes=1024,
        )
        asr_media, teacher_behavior_media = await asyncio.gather(
            downloader.download(
                "course-shared",
                "http://media/teacher.mp4",
                "teacher",
                download_group_id="submission-001",
            ),
            downloader.download(
                "course-shared",
                "http://media/teacher.mp4",
                "teacher",
                download_group_id="submission-001",
            ),
        )

    assert request_count == 1
    assert asr_media.path == teacher_behavior_media.path
    assert asr_media.sha256 == teacher_behavior_media.sha256


@pytest.mark.asyncio
async def test_later_submission_downloads_teacher_video_again(tmp_path: Path) -> None:
    request_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, content=f"teacher-{request_count}".encode())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = MediaDownloader(
            course_root=tmp_path,
            http_client=client,
            inspector=FakeInspector(),
            max_bytes=1024,
        )
        first = await downloader.download(
            "course-later",
            "http://media/teacher.mp4",
            "teacher",
            download_group_id="submission-first",
        )
        later = await downloader.download(
            "course-later",
            "http://media/teacher.mp4",
            "teacher",
            download_group_id="submission-later",
        )

    assert request_count == 2
    assert first.path != later.path
