from __future__ import annotations

import asyncio
from collections.abc import Iterable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from .cache import VisionStream
from .capacity import CapacityLease, CapacityUnavailableError, WorkContext

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
    capacity_retry_delay_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.batch_size <= 0 or self.max_concurrency <= 0:
            raise ValueError("VBas 批次大小和并发上限必须大于 0")
        if self.lease_ttl_seconds <= 0:
            raise ValueError("VBas 容量租约时长必须大于 0")
        if self.request_timeout_seconds <= 0:
            raise ValueError("VBas 请求超时必须大于 0")
        if self.capacity_retry_delay_seconds <= 0:
            raise ValueError("VBas 容量重试间隔必须大于 0")


class VbasBatchClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        lease_client: CapacityLeaseClient,
        *,
        config: VbasBatchConfig | None = None,
        shutdown_event: asyncio.Event | None = None,
    ) -> None:
        self._http = http_client
        self._lease_client = lease_client
        self._config = config or VbasBatchConfig()
        self._shutdown_event = shutdown_event
        # 同一服务内的全部课程共享这组槽位，避免课程数放大全局 VBas 并发。
        self._request_slots = asyncio.Semaphore(self._config.max_concurrency)

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
        batch_failed = asyncio.Event()
        batch_iterator = iter(enumerate(batches))
        batch_results: list[list[JsonObject] | None] = [None] * len(batches)

        async def run_batches() -> None:
            while not batch_failed.is_set():
                try:
                    batch_index, batch = next(batch_iterator)
                except StopIteration:
                    return
                async with self._request_slots:
                    if batch_failed.is_set():
                        return
                    try:
                        batch_results[batch_index] = (
                            await self._analyze_batch_until_available(
                                task_id,
                                stream,
                                batch_index,
                                batch,
                                trace_id=trace_id,
                            )
                        )
                    except BaseException:
                        batch_failed.set()
                        raise

        worker_count = min(len(batches), self._config.max_concurrency)
        tasks = [
            asyncio.create_task(
                run_batches(),
                name=f"vbas-{task_id}-{stream.value.lower()}-worker-{index:02d}",
            )
            for index in range(worker_count)
        ]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        if any(result is None for result in batch_results):
            raise VbasAdapterError(f"VBas 批次结果不完整: {task_id}/{stream.value}")
        return [
            result
            for batch_result in batch_results
            if batch_result is not None
            for result in batch_result
        ]

    async def _analyze_batch_until_available(
        self,
        task_id: str,
        stream: VisionStream,
        batch_index: int,
        batch: list[VbasFrame],
        *,
        trace_id: str | None,
    ) -> list[JsonObject]:
        while True:
            try:
                return await self._analyze_batch(
                    task_id,
                    stream,
                    batch_index,
                    batch,
                    trace_id=trace_id,
                )
            except CapacityUnavailableError:
                await self._wait_for_capacity_retry()

    async def _wait_for_capacity_retry(self) -> None:
        delay = self._config.capacity_retry_delay_seconds
        if self._shutdown_event is None:
            await asyncio.sleep(delay)
            return
        try:
            await asyncio.wait_for(self._shutdown_event.wait(), timeout=delay)
        except TimeoutError:
            return
        raise asyncio.CancelledError

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
                if response.status_code == 429:
                    raise CapacityUnavailableError(
                        f"VBas 实例容量暂不可用: {lease.instance_id}"
                    )
                response.raise_for_status()
                body = response.json()
            except CapacityUnavailableError:
                raise
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
