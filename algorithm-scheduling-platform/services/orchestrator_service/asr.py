from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Protocol

import httpx

from packages.platform_common.repository import NodeResultWrite


class OfflineAsrAdapterError(RuntimeError):
    pass


class OfflineAsrAdapter:
    _REQUIRED_PARAMS = (
        "language",
        "showSpk",
        "showEmotion",
        "showRoleIdentify",
        "wordTimestamps",
        "hotWords",
    )

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    async def transcribe(
        self,
        instance_url: str,
        audio_path: Path,
        *,
        effective_params: dict[str, Any],
    ) -> dict[str, Any]:
        missing = [key for key in self._REQUIRED_PARAMS if key not in effective_params]
        if missing:
            raise OfflineAsrAdapterError(f"缺少 ASR 有效参数: {', '.join(missing)}")
        if not audio_path.is_file():
            raise OfflineAsrAdapterError(f"ASR 音频文件不存在: {audio_path}")

        language = effective_params["language"]
        hot_words = effective_params["hotWords"]
        boolean_keys = (
            "showSpk",
            "showEmotion",
            "showRoleIdentify",
            "wordTimestamps",
        )
        if not isinstance(language, str) or not language:
            raise OfflineAsrAdapterError("ASR 有效参数 language 必须是非空字符串")
        if any(not isinstance(effective_params[key], bool) for key in boolean_keys):
            raise OfflineAsrAdapterError("ASR 开关类有效参数必须是布尔值")
        if not isinstance(hot_words, list) or any(not isinstance(item, str) for item in hot_words):
            raise OfflineAsrAdapterError("ASR 有效参数 hotWords 必须是字符串列表")

        audio_bytes = await asyncio.to_thread(audio_path.read_bytes)
        form = {
            "language": language,
            "showSpk": str(effective_params["showSpk"]).lower(),
            "showEmotion": str(effective_params["showEmotion"]).lower(),
            "showRoleIdentify": str(effective_params["showRoleIdentify"]).lower(),
            "wordTimestamps": str(effective_params["wordTimestamps"]).lower(),
            "hotWords": ",".join(hot_words),
        }
        try:
            response = await self._http.post(
                f"{instance_url.rstrip('/')}/v1.1.8/seacraft_asr",
                data=form,
                files={
                    "audioFile": (audio_path.name, audio_bytes, "audio/wav"),
                },
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OfflineAsrAdapterError(f"ASR HTTP 调用失败: {exc}") from exc
        if not isinstance(body, dict):
            raise OfflineAsrAdapterError("ASR 响应不是 JSON 对象")
        if body.get("code") not in (None, 0) and "msg" in body:
            raise OfflineAsrAdapterError(f"ASR 业务错误 {body['code']}: {body['msg']}")
        if body.get("code") == 0 and not body.get("segments"):
            raise OfflineAsrAdapterError("ASR 未生成有效转写片段，可能是静音音频")
        required_result_fields = (
            "language",
            "segments",
            "text",
            "speed_info",
            "load_audio_time_ms",
            "gpu_time_ms",
        )
        missing_result_fields = [key for key in required_result_fields if key not in body]
        if missing_result_fields:
            raise OfflineAsrAdapterError(
                f"ASR 成功响应缺少字段: {', '.join(missing_result_fields)}"
            )
        if not isinstance(body["segments"], list) or not body["segments"]:
            raise OfflineAsrAdapterError("ASR 未生成有效转写片段，可能是静音音频")
        return body


class OfflineAsrClient(Protocol):
    async def transcribe(
        self,
        instance_url: str,
        audio_path: Path,
        *,
        effective_params: dict[str, Any],
    ) -> dict[str, Any]: ...


class AsrResultRepository(Protocol):
    def complete_node(
        self,
        node_id: int,
        result: NodeResultWrite,
        *,
        reason: str,
    ) -> object: ...


class AsrTranscriptionPipeline:
    def __init__(
        self,
        repository: AsrResultRepository,
        adapter: OfflineAsrClient,
    ) -> None:
        self._repository = repository
        self._adapter = adapter

    async def run(
        self,
        *,
        node_id: int,
        instance_url: str,
        audio_path: Path,
        effective_params: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self._adapter.transcribe(
            instance_url,
            audio_path,
            effective_params=effective_params,
        )
        await asyncio.to_thread(
            self._repository.complete_node,
            node_id,
            NodeResultWrite(
                result=response,
                effective_params=effective_params,
            ),
            reason="离线语音转写完成",
        )
        return response
