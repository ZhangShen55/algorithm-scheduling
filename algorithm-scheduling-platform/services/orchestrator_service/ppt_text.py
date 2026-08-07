from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Iterable
from typing import Any, Protocol

import httpx

from packages.platform_common.repository import (
    NodeResultWrite,
    NodeWorkItemRecord,
    NodeWorkItemWrite,
    WorkItemProgress,
)
from services.orchestrator_service.ppt_work import (
    PptImageWork,
    PptWorkLimits,
    run_bounded_work,
)


class PptTextAdapterError(RuntimeError):
    pass


class OcrAdapter:
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    async def recognize(
        self,
        instance_url: str,
        work: PptImageWork,
        *,
        enable_formula: bool = False,
    ) -> dict[str, Any]:
        image_bytes = await asyncio.to_thread(work.image_path.read_bytes)
        response = await self._http.post(
            f"{instance_url.rstrip('/')}/ocr/prediction",
            json={
                "key": [work.ppt_image_id],
                "value": [base64.b64encode(image_bytes).decode()],
                "enable_formula": enable_formula,
            },
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise PptTextAdapterError("OCR 响应不是 JSON 对象")
        if body.get("err_no") != 0:
            raise PptTextAdapterError(str(body.get("err_msg") or "OCR 处理失败"))
        keys = body.get("key")
        values = body.get("value")
        if keys != [work.ppt_image_id] or not isinstance(values, list) or len(values) != 1:
            raise PptTextAdapterError("OCR 响应图片标识与请求不一致")
        try:
            ocr_items = json.loads(values[0])
        except (TypeError, json.JSONDecodeError) as exc:
            raise PptTextAdapterError("OCR value 不是有效 JSON 字符串") from exc
        if not isinstance(ocr_items, list):
            raise PptTextAdapterError("OCR value 必须是结果列表")
        text_value = "\n".join(
            str(item["text"]) for item in ocr_items if isinstance(item, dict) and item.get("text")
        )
        return {
            "ppt_image_id": work.ppt_image_id,
            "text": text_value,
            "ocr_response": body,
        }


class KeywordAdapter:
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    async def extract(
        self,
        instance_url: str,
        *,
        ppt_image_id: str,
        text: str,
    ) -> dict[str, Any]:
        response = await self._http.post(
            f"{instance_url.rstrip('/')}/v1/extract_keywords",
            json={"text": text},
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or "result" not in body:
            raise PptTextAdapterError("关键词响应缺少 result")
        return {
            "ppt_image_id": ppt_image_id,
            "keyword_response": body,
        }


class PptTextRepository(Protocol):
    def create_node_work_items(
        self,
        task_node_id: int,
        items: list[NodeWorkItemWrite],
    ) -> list[NodeWorkItemRecord]: ...

    def complete_node_work_item(
        self,
        task_node_id: int,
        item_key: str,
        result: dict[str, Any],
        *,
        reason: str,
    ) -> WorkItemProgress: ...

    def complete_node(
        self,
        node_id: int,
        result: NodeResultWrite,
        *,
        reason: str,
    ) -> object: ...


class OcrClient(Protocol):
    async def recognize(
        self,
        instance_url: str,
        work: PptImageWork,
        *,
        enable_formula: bool = False,
    ) -> dict[str, Any]: ...


class KeywordClient(Protocol):
    async def extract(
        self,
        instance_url: str,
        *,
        ppt_image_id: str,
        text: str,
    ) -> dict[str, Any]: ...


class PptTextPipeline:
    def __init__(
        self,
        repository: PptTextRepository,
        ocr_adapter: OcrClient,
        keyword_adapter: KeywordClient,
        limits: PptWorkLimits,
    ) -> None:
        self._repository = repository
        self._ocr_adapter = ocr_adapter
        self._keyword_adapter = keyword_adapter
        self._limits = limits

    async def run_ocr(
        self,
        *,
        node_id: int,
        work: Iterable[PptImageWork],
        instance_url: str,
    ) -> dict[str, dict[str, Any]]:
        work_items = list(work)
        await self._create_work_items(node_id, work_items)

        async def recognize(item: PptImageWork) -> dict[str, Any]:
            result = await self._ocr_adapter.recognize(instance_url, item)
            await asyncio.to_thread(
                self._repository.complete_node_work_item,
                node_id,
                item.ppt_image_id,
                result,
                reason="单张 PPT 图片 OCR 完成",
            )
            return result

        completed = await run_bounded_work(work_items, self._limits, recognize)
        results = {str(item["ppt_image_id"]): item for item in completed}
        await self._complete_node(node_id, results, reason="PPT 图片 OCR 全部完成")
        return results

    async def run_keywords(
        self,
        *,
        node_id: int,
        work: Iterable[PptImageWork],
        ocr_results: dict[str, dict[str, Any]],
        instance_url: str,
    ) -> dict[str, dict[str, Any]]:
        work_items = list(work)
        await self._create_work_items(node_id, work_items)

        async def extract(item: PptImageWork) -> dict[str, Any]:
            ocr_result = ocr_results.get(item.ppt_image_id)
            if ocr_result is None:
                raise PptTextAdapterError(f"缺少 PPT 图片 OCR 结果: {item.ppt_image_id}")
            text_value = ocr_result.get("text")
            if not isinstance(text_value, str):
                raise PptTextAdapterError(f"PPT 图片 OCR 文本格式错误: {item.ppt_image_id}")
            result = await self._keyword_adapter.extract(
                instance_url,
                ppt_image_id=item.ppt_image_id,
                text=text_value,
            )
            await asyncio.to_thread(
                self._repository.complete_node_work_item,
                node_id,
                item.ppt_image_id,
                result,
                reason="单张 PPT 图片关键词提取完成",
            )
            return result

        completed = await run_bounded_work(work_items, self._limits, extract)
        results = {str(item["ppt_image_id"]): item for item in completed}
        await self._complete_node(node_id, results, reason="PPT 关键词全部提取完成")
        return results

    async def _create_work_items(
        self,
        node_id: int,
        work: list[PptImageWork],
    ) -> None:
        await asyncio.to_thread(
            self._repository.create_node_work_items,
            node_id,
            [NodeWorkItemWrite(item_key=item.ppt_image_id, ordinal=item.ordinal) for item in work],
        )

    async def _complete_node(
        self,
        node_id: int,
        results: dict[str, dict[str, Any]],
        *,
        reason: str,
    ) -> None:
        count = len(results)
        await asyncio.to_thread(
            self._repository.complete_node,
            node_id,
            NodeResultWrite(
                result=results,
                progress={"completed_count": count, "total_count": count},
            ),
            reason=reason,
        )
