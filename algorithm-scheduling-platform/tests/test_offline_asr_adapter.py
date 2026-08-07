from pathlib import Path

import httpx
import pytest

from packages.platform_common.repository import NodeResultWrite
from services.orchestrator_service.asr import (
    AsrTranscriptionPipeline,
    OfflineAsrAdapter,
    OfflineAsrAdapterError,
)

EFFECTIVE_PARAMS = {
    "language": "auto",
    "showSpk": True,
    "showEmotion": True,
    "showRoleIdentify": False,
    "wordTimestamps": False,
    "hotWords": ["板书", "函数"],
}


@pytest.mark.asyncio
async def test_offline_asr_adapter_calls_v118_with_explicit_effective_params(
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "teacher.wav"
    audio_path.write_bytes(b"wav-content")
    captured: dict[str, object] = {}
    asr_response = {
        "language": "auto",
        "segments": [{"segment_text": "函数", "bg": "0.10", "ed": "1.00"}],
        "text": "函数",
        "speed_info": [],
        "load_audio_time_ms": "10.20",
        "gpu_time_ms": "100.30",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["content_type"] = request.headers["content-type"]
        captured["body"] = request.content
        return httpx.Response(200, json=asr_response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OfflineAsrAdapter(client).transcribe(
            "http://asr-offline:8083",
            audio_path,
            effective_params=EFFECTIVE_PARAMS,
        )

    body = captured["body"]
    assert isinstance(body, bytes)
    assert captured["path"] == "/v1.1.8/seacraft_asr"
    assert str(captured["content_type"]).startswith("multipart/form-data; boundary=")
    for expected in (
        b'name="audioFile"; filename="teacher.wav"',
        b'name="language"',
        b"auto",
        b'name="showSpk"',
        b"true",
        b'name="showEmotion"',
        b'name="showRoleIdentify"',
        b"false",
        b'name="wordTimestamps"',
        b'name="hotWords"',
        "板书,函数".encode(),
    ):
        assert expected in body
    assert result == asr_response


@pytest.mark.asyncio
async def test_offline_asr_adapter_rejects_business_error_inside_http_200(
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "teacher.wav"
    audio_path.write_bytes(b"wav-content")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={"msg": "音频文件为空或未检测到任何人声", "code": 4008},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(
            OfflineAsrAdapterError,
            match="ASR 业务错误 4008: 音频文件为空或未检测到任何人声",
        ):
            await OfflineAsrAdapter(client).transcribe(
                "http://asr-offline:8083",
                audio_path,
                effective_params=EFFECTIVE_PARAMS,
            )


@pytest.mark.asyncio
async def test_offline_asr_adapter_rejects_empty_code_zero_silent_body(
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "teacher.wav"
    audio_path.write_bytes(b"wav-content")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"text": "", "sentences": [], "code": 0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OfflineAsrAdapterError, match="ASR 未生成有效转写片段"):
            await OfflineAsrAdapter(client).transcribe(
                "http://asr-offline:8083",
                audio_path,
                effective_params=EFFECTIVE_PARAMS,
            )


@pytest.mark.asyncio
async def test_offline_asr_adapter_requires_complete_effective_params(
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "teacher.wav"
    audio_path.write_bytes(b"wav-content")

    async with httpx.AsyncClient() as client:
        with pytest.raises(OfflineAsrAdapterError, match="缺少 ASR 有效参数: hotWords"):
            await OfflineAsrAdapter(client).transcribe(
                "http://asr-offline:8083",
                audio_path,
                effective_params={
                    key: value
                    for key, value in EFFECTIVE_PARAMS.items()
                    if key != "hotWords"
                },
            )


@pytest.mark.asyncio
async def test_asr_pipeline_persists_complete_response_and_effective_params(
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "teacher.wav"
    audio_path.write_bytes(b"wav-content")
    asr_response = {
        "language": "auto",
        "segments": [
            {
                "segment_text": "第一章函数",
                "bg": "0.10",
                "ed": "2.20",
                "speed": 180,
                "segment_words": [],
                "role": "teacher",
                "emotion": "平淡",
            }
        ],
        "text": "第一章函数",
        "speed_info": [{"unit": 1, "segment_info": {"segment_count": 1, "speed": [180]}}],
        "load_audio_time_ms": "10.20",
        "gpu_time_ms": "100.30",
    }

    class StubAdapter:
        async def transcribe(
            self,
            instance_url: str,
            audio_path: Path,
            *,
            effective_params: dict[str, object],
        ) -> dict[str, object]:
            del instance_url, audio_path, effective_params
            return asr_response

    class RecordingRepository:
        completed: NodeResultWrite | None = None

        def complete_node(
            self,
            node_id: int,
            result: NodeResultWrite,
            *,
            reason: str,
        ) -> object:
            assert node_id == 21
            assert reason == "离线语音转写完成"
            self.completed = result
            return object()

    repository = RecordingRepository()
    pipeline = AsrTranscriptionPipeline(repository, StubAdapter())

    result = await pipeline.run(
        node_id=21,
        instance_url="http://asr-offline:8083",
        audio_path=audio_path,
        effective_params=EFFECTIVE_PARAMS,
    )

    assert result == asr_response
    assert repository.completed is not None
    assert repository.completed.result == asr_response
    assert repository.completed.effective_params == EFFECTIVE_PARAMS
