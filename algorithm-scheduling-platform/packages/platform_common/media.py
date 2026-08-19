from __future__ import annotations

import asyncio
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from packages.platform_common.workspace import task_workspace


class MediaDownloadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    duration_seconds: float
    width: int | None
    height: int | None
    format_name: str


@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    path: Path
    size_bytes: int
    sha256: str
    metadata: MediaMetadata


class MediaInspector(Protocol):
    async def inspect(self, path: Path) -> MediaMetadata: ...


class FFprobeMediaInspector:
    async def inspect(self, path: Path) -> MediaMetadata:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name:stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            reason = stderr.decode(errors="replace").strip()
            raise MediaDownloadError(f"媒体元数据检查失败: {reason or 'ffprobe 返回错误'}")
        payload = json.loads(stdout)
        format_data = payload.get("format", {})
        video_stream: dict[str, Any] = next(
            (
                stream
                for stream in payload.get("streams", [])
                if stream.get("codec_type") == "video"
            ),
            {},
        )
        duration_seconds = float(format_data.get("duration", 0))
        if not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise MediaDownloadError("媒体时长必须是有限正数")
        return MediaMetadata(
            duration_seconds=duration_seconds,
            width=video_stream.get("width"),
            height=video_stream.get("height"),
            format_name=str(format_data.get("format_name", "unknown")),
        )


class MediaDownloader:
    _ALLOWED_ROLES = frozenset({"teacher", "student", "slides"})

    def __init__(
        self,
        *,
        course_root: Path,
        http_client: httpx.AsyncClient,
        inspector: MediaInspector,
        max_bytes: int,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("媒体下载大小上限必须大于 0")
        self._course_root = course_root
        self._http = http_client
        self._inspector = inspector
        self._max_bytes = max_bytes
        self._locks: dict[tuple[str, str | None, str], asyncio.Lock] = {}

    async def download(
        self,
        task_id: str,
        source_url: str,
        media_role: str,
        *,
        download_group_id: str | None = None,
    ) -> DownloadedMedia:
        parsed = urlsplit(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise MediaDownloadError("媒体地址必须是有效的 HTTP/HTTPS URL")
        if media_role not in self._ALLOWED_ROLES:
            raise MediaDownloadError(f"不支持的媒体角色: {media_role}")

        try:
            course_dir = task_workspace(self._course_root, task_id)
            if download_group_id is None:
                media_dir = course_dir / "media"
            else:
                media_dir = (
                    task_workspace(
                        course_dir / "submissions",
                        download_group_id,
                    )
                    / "media"
                )
        except ValueError as exc:
            raise MediaDownloadError(str(exc)) from exc
        media_dir.mkdir(parents=True, exist_ok=True)
        target = media_dir / f"{media_role}.mp4"
        partial = media_dir / f"{media_role}.mp4.part"
        lock_key = (task_id, download_group_id, media_role)
        lock = self._locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            if target.exists():
                return await self._existing_media(target)
            return await self._download_locked(source_url, target, partial)

    async def _existing_media(self, target: Path) -> DownloadedMedia:
        def calculate_digest() -> tuple[int, str]:
            digest = hashlib.sha256()
            with target.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            return target.stat().st_size, digest.hexdigest()

        size_bytes, sha256 = await asyncio.to_thread(calculate_digest)
        metadata = await self._inspector.inspect(target)
        if not math.isfinite(metadata.duration_seconds) or metadata.duration_seconds <= 0:
            raise MediaDownloadError("媒体时长必须是有限正数")
        return DownloadedMedia(
            path=target,
            size_bytes=size_bytes,
            sha256=sha256,
            metadata=metadata,
        )

    async def _download_locked(
        self,
        source_url: str,
        target: Path,
        partial: Path,
    ) -> DownloadedMedia:
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            async with self._http.stream(
                "GET",
                source_url,
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length is not None and int(content_length) > self._max_bytes:
                    raise MediaDownloadError("媒体文件超过配置的大小上限")
                with partial.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        size_bytes += len(chunk)
                        if size_bytes > self._max_bytes:
                            raise MediaDownloadError("媒体文件超过配置的大小上限")
                        digest.update(chunk)
                        output.write(chunk)
            partial.replace(target)
            metadata = await self._inspector.inspect(target)
            if not math.isfinite(metadata.duration_seconds) or metadata.duration_seconds <= 0:
                raise MediaDownloadError("媒体时长必须是有限正数")
        except MediaDownloadError:
            partial.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise
        except (httpx.HTTPError, OSError, ValueError) as exc:
            partial.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise MediaDownloadError(f"媒体下载失败: {exc}") from exc

        return DownloadedMedia(
            path=target,
            size_bytes=size_bytes,
            sha256=digest.hexdigest(),
            metadata=metadata,
        )
