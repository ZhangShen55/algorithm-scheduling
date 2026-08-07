import wave
from pathlib import Path

import pytest
from orchestrator_service.app.infrastructure.audio import AudioExtractionError, FFmpegAudioExtractor


class WavWritingRunner:
    def __init__(self) -> None:
        self.command: tuple[str, ...] | None = None

    async def __call__(self, command: tuple[str, ...]) -> tuple[int, str]:
        self.command = command
        output_path = Path(command[-1])
        with wave.open(str(output_path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16_000)
            output.writeframes(b"\x00\x00" * 16_000)
        return 0, ""


@pytest.mark.asyncio
async def test_extracts_teacher_video_to_16k_mono_pcm_wav(tmp_path: Path) -> None:
    source = tmp_path / "teacher.mp4"
    source.write_bytes(b"video")
    runner = WavWritingRunner()
    extractor = FFmpegAudioExtractor(course_root=tmp_path / "course", runner=runner)

    artifact = await extractor.extract(
        "course-001",
        source,
        download_group_id="submission-001",
    )

    assert artifact.path == (
        tmp_path
        / "course/course-001/submissions/submission-001/audio/teacher.wav"
    )
    assert artifact.sample_rate == 16_000
    assert artifact.channels == 1
    assert artifact.sample_width_bytes == 2
    assert artifact.duration_seconds == 1.0
    assert runner.command is not None
    assert runner.command[:4] == ("ffmpeg", "-nostdin", "-hide_banner", "-loglevel")
    assert ("-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le") == (
        runner.command[runner.command.index("-vn") : runner.command.index("-f")]
    )
    assert runner.command[-2] == "wav"


@pytest.mark.asyncio
async def test_failed_audio_extraction_removes_partial_output(tmp_path: Path) -> None:
    source = tmp_path / "teacher.mp4"
    source.write_bytes(b"video")

    async def failing_runner(command: tuple[str, ...]) -> tuple[int, str]:
        Path(command[-1]).write_bytes(b"partial")
        return 1, "Output file contains no streams"

    extractor = FFmpegAudioExtractor(
        course_root=tmp_path / "course",
        runner=failing_runner,
    )

    with pytest.raises(AudioExtractionError, match="音频提取失败"):
        await extractor.extract("course-001", source)

    assert not (tmp_path / "course/course-001/audio/teacher.wav.part").exists()
