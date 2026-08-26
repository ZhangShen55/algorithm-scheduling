from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Awaitable, Iterable
from typing import Any, Protocol

import httpx

from packages.platform_common.operator_registry import CapacityLease, WorkContext
from packages.platform_common.repository import (
    NodeResultWrite,
    NodeWorkItemRecord,
    NodeWorkItemWrite,
    WorkItemProgress,
)
from packages.platform_contracts.status import NodeStatus

from ..domain.ppt_work import (
    PptImageWork,
    PptWorkLimits,
    run_bounded_work,
)

logger = logging.getLogger(__name__)


class PptTextAdapterError(RuntimeError):
    pass


class OcrAdapter:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        transport_max_attempts: int = 2,
        transport_retry_delay_seconds: float = 0.2,
    ) -> None:
        if transport_max_attempts <= 0:
            raise ValueError("OCR 网络调用尝试次数必须大于 0")
        if transport_retry_delay_seconds < 0:
            raise ValueError("OCR 网络重试间隔不能小于 0")
        self._http = http_client
        self._transport_max_attempts = transport_max_attempts
        self._transport_retry_delay_seconds = transport_retry_delay_seconds

    async def recognize(
        self,
        instance_url: str,
        work: PptImageWork,
        *,
        enable_formula: bool = False,
    ) -> dict[str, Any]:
        try:
            image_bytes = await asyncio.to_thread(work.image_path.read_bytes)
        except OSError as exc:
            raise PptTextAdapterError(
                f"OCR 图片读取失败（{type(exc).__name__}）: {work.ppt_image_id}"
            ) from exc
        response = await self._post_with_transport_retry(
            f"{instance_url.rstrip('/')}/ocr/prediction",
            payload={
                "key": [work.ppt_image_id],
                "value": [base64.b64encode(image_bytes).decode()],
                "enable_formula": enable_formula,
            },
            ppt_image_id=work.ppt_image_id,
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

    async def _post_with_transport_retry(
        self,
        url: str,
        *,
        payload: dict[str, Any],
        ppt_image_id: str,
    ) -> httpx.Response:
        for attempt in range(1, self._transport_max_attempts + 1):
            try:
                return await self._http.post(url, json=payload)
            except (httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                if attempt >= self._transport_max_attempts:
                    raise PptTextAdapterError(
                        f"OCR 网络调用失败（{type(exc).__name__}）"
                    ) from exc
                # OCR 单图请求以 ppt_image_id 幂等；日志只保留受控标识和异常类型。
                logger.warning(
                    "PPT OCR 瞬时网络异常，准备有限重试",
                    extra={
                        "ppt_image_id": ppt_image_id,
                        "exception_type": type(exc).__name__,
                        "attempt": attempt,
                        "max_attempts": self._transport_max_attempts,
                    },
                )
                if self._transport_retry_delay_seconds > 0:
                    await asyncio.sleep(self._transport_retry_delay_seconds)
            except httpx.TimeoutException as exc:
                raise PptTextAdapterError(
                    f"OCR 网络调用超时（{type(exc).__name__}）"
                ) from exc
        raise AssertionError("unreachable")


class PptTextRepository(Protocol):
    def create_node_work_items(
        self,
        task_node_id: int,
        items: list[NodeWorkItemWrite],
    ) -> list[NodeWorkItemRecord]: ...

    def list_node_work_items(
        self,
        task_node_id: int,
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


class PptLeaseClient(Protocol):
    async def acquire(
        self,
        capability: str,
        *,
        ttl_seconds: int | None = None,
        work_context: WorkContext | None = None,
    ) -> CapacityLease: ...

    async def run_with_renewal(
        self,
        lease: CapacityLease,
        operation: Awaitable[dict[str, Any]],
        *,
        ttl_seconds: int | None = None,
        hard_timeout_seconds: float,
    ) -> dict[str, Any]: ...

    async def release(self, lease_id: str) -> None: ...


class PptTextPipeline:
    def __init__(
        self,
        repository: PptTextRepository,
        lease_client: PptLeaseClient,
        ocr_adapter: OcrClient,
        limits: PptWorkLimits,
        *,
        lease_ttl_seconds: int = 60,
        ocr_hard_timeout_seconds: float = 600.0,
    ) -> None:
        self._repository = repository
        self._lease_client = lease_client
        self._ocr_adapter = ocr_adapter
        self._limits = limits
        if lease_ttl_seconds <= 0:
            raise ValueError("PPT 工作项租约时长必须大于 0")
        if ocr_hard_timeout_seconds <= 0:
            raise ValueError("PPT 工作项 HTTP 硬超时必须大于 0")
        self._lease_ttl_seconds = lease_ttl_seconds
        self._ocr_hard_timeout_seconds = ocr_hard_timeout_seconds

    async def run_ocr(
        self,
        *,
        task_id: str,
        node_id: int,
        work: Iterable[PptImageWork],
        trace_id: str | None = None,
        complete_node: bool = True,
    ) -> dict[str, dict[str, Any]]:
        work_items = list(work)
        pending_work, retained_results = await self._prepare_work_items(
            node_id,
            work_items,
        )

        async def recognize(item: PptImageWork) -> dict[str, Any]:
            lease = await self._lease_client.acquire(
                "ocr",
                ttl_seconds=self._lease_ttl_seconds,
                work_context=self._work_context(
                    task_id=task_id,
                    node_id=node_id,
                    item=item,
                    work_type="ppt_ocr_item",
                    trace_id=trace_id,
                ),
            )
            try:
                result = await self._lease_client.run_with_renewal(
                    lease,
                    self._ocr_adapter.recognize(lease.service_url, item),
                    ttl_seconds=self._lease_ttl_seconds,
                    hard_timeout_seconds=self._ocr_hard_timeout_seconds,
                )
                await asyncio.to_thread(
                    self._repository.complete_node_work_item,
                    node_id,
                    item.ppt_image_id,
                    result,
                    reason="单张 PPT 图片 OCR 完成",
                )
                return result
            finally:
                await self._lease_client.release(lease.lease_id)

        completed = await run_bounded_work(pending_work, self._limits, recognize)
        results = dict(retained_results)
        results.update({str(item["ppt_image_id"]): item for item in completed})
        results = {
            item.ppt_image_id: results[item.ppt_image_id]
            for item in work_items
            if item.ppt_image_id in results
        }
        if complete_node:
            await self._complete_node(node_id, results, reason="PPT 图片 OCR 全部完成")
        return results

    async def _prepare_work_items(
        self,
        node_id: int,
        work: list[PptImageWork],
    ) -> tuple[list[PptImageWork], dict[str, dict[str, Any]]]:
        await asyncio.to_thread(
            self._repository.create_node_work_items,
            node_id,
            [NodeWorkItemWrite(item_key=item.ppt_image_id, ordinal=item.ordinal) for item in work],
        )
        records = await asyncio.to_thread(
            self._repository.list_node_work_items,
            node_id,
        )
        retained_results = {
            record.item_key: record.result
            for record in records
            if record.status is NodeStatus.COMPLETED and record.result is not None
        }
        pending_work = [
            item for item in work if item.ppt_image_id not in retained_results
        ]
        return pending_work, retained_results

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

    @staticmethod
    def _work_context(
        *,
        task_id: str,
        node_id: int,
        item: PptImageWork,
        work_type: str,
        trace_id: str | None,
    ) -> WorkContext:
        return WorkContext(
            source_service="orchestrator-service",
            work_type=work_type,
            work_id=item.ppt_image_id,
            task_id=task_id,
            node_id=str(node_id),
            item_id=item.ppt_image_id,
            trace_id=trace_id,
        )
