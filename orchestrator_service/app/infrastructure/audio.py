from __future__ import annotations

import asyncio
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from packages.platform_common.workspace import task_workspace

CommandRunner = Callable[[tuple[str, ...]], Awaitable[tuple[int, str]]]


class AudioExtractionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExtractedAudio:
    path: Path
    size_bytes: int
    sample_rate: int
    channels: int
    sample_width_bytes: int
    duration_seconds: float


async def run_audio_command(command: tuple[str, ...]) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    return int(process.returncode or 0), stderr.decode(errors="replace").strip()


class FFmpegAudioExtractor:
    def __init__(self, *, course_root: Path, runner: CommandRunner = run_audio_command) -> None:
        self._course_root = course_root
        self._runner = runner
        self._locks: dict[tuple[str, str | None], asyncio.Lock] = {}

    async def extract(
        self,
        task_id: str,
        source_video_path: Path,
        *,
        download_group_id: str | None = None,
    ) -> ExtractedAudio:
        if not source_video_path.is_file():
            raise AudioExtractionError(f"教师视频文件不存在: {source_video_path}")
        try:
            course_dir = task_workspace(self._course_root, task_id)
            if download_group_id is None:
                audio_dir = course_dir / "audio"
            else:
                submission_dir = task_workspace(
                    course_dir / "submissions",
                    download_group_id,
                )
                audio_dir = submission_dir / "audio"
        except ValueError as exc:
            raise AudioExtractionError(str(exc)) from exc

        audio_dir.mkdir(parents=True, exist_ok=True)
        target = audio_dir / "teacher.wav"
        partial = audio_dir / "teacher.wav.part"
        lock = self._locks.setdefault((task_id, download_group_id), asyncio.Lock())
        async with lock:
            if target.exists():
                return await self._inspect(target)
            command = (
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source_video_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                "-f",
                "wav",
                str(partial),
            )
            return_code, error = await self._runner(command)
            if return_code != 0:
                partial.unlink(missing_ok=True)
                raise AudioExtractionError(f"音频提取失败: {error or 'ffmpeg 返回错误'}")
            try:
                artifact = await self._inspect(partial)
                partial.replace(target)
            except (AudioExtractionError, OSError):
                partial.unlink(missing_ok=True)
                raise
            return ExtractedAudio(
                path=target,
                size_bytes=target.stat().st_size,
                sample_rate=artifact.sample_rate,
                channels=artifact.channels,
                sample_width_bytes=artifact.sample_width_bytes,
                duration_seconds=artifact.duration_seconds,
            )

    async def _inspect(self, path: Path) -> ExtractedAudio:
        def inspect_wave() -> ExtractedAudio:
            try:
                with wave.open(str(path), "rb") as audio:
                    channels = audio.getnchannels()
                    sample_width = audio.getsampwidth()
                    sample_rate = audio.getframerate()
                    frame_count = audio.getnframes()
            except (OSError, wave.Error) as exc:
                raise AudioExtractionError(f"WAV 文件校验失败: {exc}") from exc
            if channels != 1 or sample_rate != 16_000 or sample_width != 2:
                raise AudioExtractionError("WAV 必须是 16kHz、单声道、16-bit PCM")
            if frame_count <= 0:
                raise AudioExtractionError("WAV 音频内容为空")
            return ExtractedAudio(
                path=path,
                size_bytes=path.stat().st_size,
                sample_rate=sample_rate,
                channels=channels,
                sample_width_bytes=sample_width,
                duration_seconds=frame_count / sample_rate,
            )

        return await asyncio.to_thread(inspect_wave)
