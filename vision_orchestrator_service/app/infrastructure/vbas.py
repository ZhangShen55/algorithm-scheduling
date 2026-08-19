from __future__ import annotations

import asyncio
from collections.abc import Iterable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from .cache import VisionStream
from .capacity import CapacityLease, WorkContext

JsonObject = dict[str, Any]


class VbasAdapterError(RuntimeError):
    pass


class CapacityLeaseClient(Protocol):
    def acquire(
        self,
        capability: str,
        *,
        ttl_seconds: int = 60,
        work_context: WorkContext | None = None,
        renew_interval_seconds: float | None = None,
    ) -> AbstractAsyncContextManager[CapacityLease]: ...


@dataclass(frozen=True, slots=True)
class VbasFrame:
    image_id: str
    path: Path
    frame_index: int
    timestamp_seconds: float
    points: list[JsonObject] | None = None

    def __post_init__(self) -> None:
        if not self.image_id:
            raise ValueError("VBas 帧 image_id 不能为空")
        if not self.path.is_absolute():
            raise ValueError("VBas 帧路径必须是绝对本地路径")
        if self.frame_index < 0 or self.timestamp_seconds < 0:
            raise ValueError("VBas 帧序号和时间戳不能小于 0")


@dataclass(frozen=True, slots=True)
class VbasBatchConfig:
    batch_size: int = 8
    max_concurrency: int = 2
    lease_ttl_seconds: int = 60
    request_timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.batch_size <= 0 or self.max_concurrency <= 0:
            raise ValueError("VBas 批次大小和并发上限必须大于 0")
        if self.lease_ttl_seconds <= 0:
            raise ValueError("VBas 容量租约时长必须大于 0")
        if self.request_timeout_seconds <= 0:
            raise ValueError("VBas 请求超时必须大于 0")


class VbasBatchClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        lease_client: CapacityLeaseClient,
        *,
        config: VbasBatchConfig | None = None,
    ) -> None:
        self._http = http_client
        self._lease_client = lease_client
        self._config = config or VbasBatchConfig()

    async def analyze(
        self,
        *,
        task_id: str,
        stream: VisionStream,
        frames: Iterable[VbasFrame],
        trace_id: str | None = None,
    ) -> list[JsonObject]:
        frame_list = list(frames)
        if not frame_list:
            return []
        batches = [
            frame_list[index : index + self._config.batch_size]
            for index in range(0, len(frame_list), self._config.batch_size)
        ]
        semaphore = asyncio.Semaphore(self._config.max_concurrency)

        async def run_batch(batch_index: int, batch: list[VbasFrame]) -> list[JsonObject]:
            async with semaphore:
                return await self._analyze_batch(
                    task_id,
                    stream,
                    batch_index,
                    batch,
                    trace_id=trace_id,
                )

        batch_results = await asyncio.gather(
            *(run_batch(index, batch) for index, batch in enumerate(batches))
        )
        return [result for batch in batch_results for result in batch]

    async def _analyze_batch(
        self,
        task_id: str,
        stream: VisionStream,
        batch_index: int,
        batch: list[VbasFrame],
        *,
        trace_id: str | None,
    ) -> list[JsonObject]:
        if stream is VisionStream.TEACHER:
            capability = "teacher_behavior"
            endpoint = "/ImageDetect/teacher/v1.0.0"
            stream_type = "teacher"
        else:
            capability = "student_behavior"
            endpoint = "/ImageDetect/student/v1.0.0"
            stream_type = "student"
        batch_id = f"{task_id}-{stream.value.lower()}-{batch_index:04d}"
        image_list = [self._frame_request(frame) for frame in batch]
        request: JsonObject = {
            "task_id": task_id,
            "batch_id": batch_id,
            "stream_type": stream_type,
            "ImageList": image_list,
        }
        if stream is VisionStream.TEACHER:
            request["ReturnHeadPose"] = False

        async with self._lease_client.acquire(
            capability,
            ttl_seconds=self._config.lease_ttl_seconds,
            work_context=WorkContext(
                source_service="vision-orchestrator-service",
                work_type=f"vbas_{stream_type}_batch",
                work_id=batch_id,
                task_id=task_id,
                item_id=batch_id,
                trace_id=trace_id,
            ),
        ) as lease:
            try:
                response = await asyncio.wait_for(
                    self._http.post(
                        f"{lease.service_url.rstrip('/')}{endpoint}",
                        json=request,
                    ),
                    timeout=self._config.request_timeout_seconds,
                )
                response.raise_for_status()
                body = response.json()
            except (TimeoutError, httpx.HTTPError, ValueError) as exc:
                raise VbasAdapterError(
                    f"VBas 批次调用失败: {task_id}/{batch_id}: {exc}"
                ) from exc
        return self._parse_response(body, batch)

    @staticmethod
    def _frame_request(frame: VbasFrame) -> JsonObject:
        request: JsonObject = {
            "StoragePath": str(frame.path),
            "ImageId": frame.image_id,
            "frame_id": frame.image_id,
            "frame_index": frame.frame_index,
            "timestamp_seconds": frame.timestamp_seconds,
        }
        if frame.points is not None:
            request["Points"] = frame.points
        return request

    @staticmethod
    def _parse_response(body: Any, batch: list[VbasFrame]) -> list[JsonObject]:
        if not isinstance(body, dict):
            raise VbasAdapterError("VBas 响应不是 JSON 对象")
        status = body.get("StatusObject")
        if not isinstance(status, dict) or status.get("StatusCode") != 0:
            raise VbasAdapterError(
                f"VBas 批次业务失败: {status if isinstance(status, dict) else body}"
            )
        data_list = body.get("DataList")
        if not isinstance(data_list, list) or len(data_list) != len(batch):
            raise VbasAdapterError("VBas 批次返回图片数量与请求不一致")
        results: list[JsonObject] = []
        for expected, item in zip(batch, data_list, strict=True):
            if not isinstance(item, dict):
                raise VbasAdapterError("VBas 单帧结果不是对象")
            item_status = item.get("StatusObject")
            image_id = item_status.get("ImageId") if isinstance(item_status, dict) else None
            if image_id != expected.image_id:
                raise VbasAdapterError(
                    f"VBas 单帧结果标识不一致: {expected.image_id}/{image_id}"
                )
            results.append({"image_id": expected.image_id, "response": item})
        return results
