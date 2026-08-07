from __future__ import annotations

import asyncio
import math
from typing import Any, Protocol

import httpx

from packages.platform_common.repository import NodeResultWrite


class CourseOverviewAdapterError(RuntimeError):
    pass


def build_course_overview_request(
    asr_response: dict[str, Any],
    *,
    model: str | None = None,
) -> dict[str, Any]:
    raw_segments = asr_response.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise CourseOverviewAdapterError("ASR 响应没有可用于课程脑图的 segments")

    text_segments: list[dict[str, str | float]] = []
    for index, raw_segment in enumerate(raw_segments, start=1):
        if not isinstance(raw_segment, dict):
            raise CourseOverviewAdapterError(f"ASR 第 {index} 个片段不是对象")
        text_value = raw_segment.get("segment_text")
        if not isinstance(text_value, str) or not text_value.strip():
            raise CourseOverviewAdapterError(f"ASR 第 {index} 个片段文本为空")
        try:
            bg = float(raw_segment["bg"])
            ed = float(raw_segment["ed"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CourseOverviewAdapterError(f"ASR 第 {index} 个片段时间格式不合法") from exc
        if not math.isfinite(bg) or not math.isfinite(ed) or bg < 0 or ed < bg:
            raise CourseOverviewAdapterError(f"ASR 第 {index} 个片段时间范围不合法")
        text_segments.append({"text": text_value, "bg": bg, "ed": ed})

    request: dict[str, Any] = {"textSegments": text_segments}
    if model is not None:
        if not model:
            raise CourseOverviewAdapterError("课程脑图模型名称不能为空")
        request["model"] = model
    return request


class CourseOverviewAdapter:
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    async def generate(
        self,
        instance_url: str,
        asr_response: dict[str, Any],
        *,
        model: str | None = None,
    ) -> dict[str, Any]:
        request = build_course_overview_request(asr_response, model=model)
        try:
            response = await self._http.post(
                f"{instance_url.rstrip('/')}/v1/course_overviews",
                json=request,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CourseOverviewAdapterError(f"课程脑图 HTTP 调用失败: {exc}") from exc
        if not isinstance(body, dict):
            raise CourseOverviewAdapterError("课程脑图响应不是 JSON 对象")
        missing = [key for key in ("model", "id", "result", "usage") if key not in body]
        if missing:
            raise CourseOverviewAdapterError(
                f"课程脑图 GenericResponse 缺少字段: {', '.join(missing)}"
            )
        nested_result = body["result"]
        if not isinstance(nested_result, dict) or not all(
            key in nested_result
            for key in (
                "overview",
                "finished_time",
                "process_time_ms",
                "finished_reason",
            )
        ):
            raise CourseOverviewAdapterError("课程脑图响应缺少完整的嵌套 result")
        return body


class CourseOverviewClient(Protocol):
    async def generate(
        self,
        instance_url: str,
        asr_response: dict[str, Any],
        *,
        model: str | None = None,
    ) -> dict[str, Any]: ...


class CourseOverviewRepository(Protocol):
    def complete_node(
        self,
        node_id: int,
        result: NodeResultWrite,
        *,
        reason: str,
    ) -> object: ...


class CourseOverviewPipeline:
    def __init__(
        self,
        repository: CourseOverviewRepository,
        adapter: CourseOverviewClient,
    ) -> None:
        self._repository = repository
        self._adapter = adapter

    async def run(
        self,
        *,
        node_id: int,
        instance_url: str,
        asr_response: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        response = await self._adapter.generate(
            instance_url,
            asr_response,
            model=model,
        )
        await asyncio.to_thread(
            self._repository.complete_node,
            node_id,
            NodeResultWrite(result=response),
            reason="课程脑图生成完成",
        )
        return response
