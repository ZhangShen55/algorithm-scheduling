from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator, Iterable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from .cache import VisionStream
from .capacity import CapacityLease, CapacityUnavailableError, WorkContext

JsonObject = dict[str, Any]
logger = logging.getLogger(__name__)


class VbasAdapterError(RuntimeError):
    pass


class _VbasTransientCallError(RuntimeError):
    def __init__(self, instance_id: str, cause: BaseException) -> None:
        self.instance_id = instance_id
        self.cause = cause
        super().__init__(_exception_detail(cause))


class CapacityLeaseClient(Protocol):
    def acquire(
        self,
        capability: str,
        *,
        ttl_seconds: int = 60,
        work_context: WorkContext | None = None,
        renew_interval_seconds: float | None = None,
    ) -> AbstractAsyncContextManager[CapacityLease]: ...


class OfflineCapacitySource(Protocol):
    async def total_capacity(self) -> int: ...


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
    lease_ttl_seconds: int = 60
    request_timeout_seconds: float = 60.0
    capacity_retry_delay_seconds: float = 1.0
    transient_max_attempts: int = 3
    transient_retry_base_delay_seconds: float = 0.2
    transient_retry_max_delay_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.batch_size <= 0:
            raise ValueError("VBas 批次大小必须大于 0")
        if self.lease_ttl_seconds <= 0:
            raise ValueError("VBas 容量租约时长必须大于 0")
        if self.request_timeout_seconds <= 0:
            raise ValueError("VBas 请求超时必须大于 0")
        if self.capacity_retry_delay_seconds <= 0:
            raise ValueError("VBas 容量重试间隔必须大于 0")
        if self.transient_max_attempts <= 0:
            raise ValueError("VBas 瞬时故障最大尝试次数必须大于 0")
        if (
            self.transient_retry_base_delay_seconds < 0
            or self.transient_retry_max_delay_seconds < 0
            or self.transient_retry_base_delay_seconds
            > self.transient_retry_max_delay_seconds
        ):
            raise ValueError("VBas 瞬时故障退避配置不合法")


class ControlVbasOfflineCapacitySource:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        control_service_url: str,
        refresh_seconds: float = 1.0,
        request_timeout_seconds: float = 10.0,
    ) -> None:
        if refresh_seconds <= 0 or request_timeout_seconds <= 0:
            raise ValueError("VBas 容量快照刷新周期和请求超时必须大于 0")
        self._http = http_client
        self._control_service_url = control_service_url.rstrip("/")
        self._refresh_seconds = refresh_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._cached_capacity: int | None = None
        self._cached_until = 0.0
        self._refresh_lock = asyncio.Lock()

    async def total_capacity(self) -> int:
        now = time.monotonic()
        if self._cached_capacity is not None and now < self._cached_until:
            return self._cached_capacity
        async with self._refresh_lock:
            now = time.monotonic()
            if self._cached_capacity is not None and now < self._cached_until:
                return self._cached_capacity
            try:
                response = await asyncio.wait_for(
                    self._http.get(
                        f"{self._control_service_url}/ops/operator-instances/snapshot"
                    ),
                    timeout=self._request_timeout_seconds,
                )
                response.raise_for_status()
                capacity = self._parse_total_capacity(response.json())
            except (TimeoutError, httpx.HTTPError, ValueError, TypeError) as exc:
                if self._cached_capacity is not None:
                    self._cached_until = time.monotonic() + self._refresh_seconds
                    logger.warning(
                        "VBas 离线容量快照刷新失败，暂用最近一次容量",
                        extra={
                            "capacity": self._cached_capacity,
                            "error_type": type(exc).__name__,
                            "outcome": "stale_capacity_used",
                        },
                    )
                    return self._cached_capacity
                raise VbasAdapterError(
                    f"读取 VBas 离线容量快照失败: {_exception_detail(exc)}"
                ) from exc
            previous_capacity = self._cached_capacity
            self._cached_capacity = capacity
            self._cached_until = now + self._refresh_seconds
            if capacity != previous_capacity:
                logger.info(
                    "VBas 离线有效并发已更新",
                    extra={
                        "previous_capacity": previous_capacity,
                        "effective_capacity": capacity,
                        "outcome": "capacity_refreshed",
                    },
                )
            return capacity

    @staticmethod
    def _parse_total_capacity(body: object) -> int:
        if not isinstance(body, list):
            raise TypeError("算子容量快照不是列表")
        total = 0
        for item in body:
            if not isinstance(item, dict) or item.get("operator_code") != "vbas":
                continue
            if item.get("lifecycle") != "ONLINE" or item.get("model_ready") is not True:
                continue
            pools = item.get("capacity_pools")
            if not isinstance(pools, dict):
                raise TypeError("VBas 容量快照缺少 capacity_pools")
            offline = pools.get("offline")
            if isinstance(offline, bool) or not isinstance(offline, int) or offline < 0:
                raise ValueError("VBas offline 容量必须是非负整数")
            if offline == 0:
                continue
            total += offline
        return total


class VbasOfflineCapacityGate:
    def __init__(
        self,
        source: OfflineCapacitySource,
        *,
        wait_timeout_seconds: float,
        retry_interval_seconds: float,
        shutdown_event: asyncio.Event | None = None,
    ) -> None:
        if wait_timeout_seconds <= 0 or retry_interval_seconds <= 0:
            raise ValueError("VBas 容量等待超时和重试间隔必须大于 0")
        self._source = source
        self._wait_timeout_seconds = wait_timeout_seconds
        self._retry_interval_seconds = retry_interval_seconds
        self._shutdown_event = shutdown_event
        self._condition = asyncio.Condition()
        self._active = 0

    async def worker_limit(self) -> int:
        return await self._wait_for_positive_capacity()

    @asynccontextmanager
    async def admit(self) -> AsyncIterator[None]:
        deadline = time.monotonic() + self._wait_timeout_seconds
        while True:
            capacity = await self._capacity_until(deadline)
            async with self._condition:
                if self._active < capacity:
                    self._active += 1
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise VbasAdapterError(
                        f"等待 VBas 离线容量超过 {self._wait_timeout_seconds:g} 秒"
                    )
                try:
                    await asyncio.wait_for(
                        self._condition.wait(),
                        timeout=min(remaining, self._retry_interval_seconds),
                    )
                except TimeoutError:
                    pass
        try:
            yield
        finally:
            async with self._condition:
                self._active -= 1
                self._condition.notify_all()

    async def _wait_for_positive_capacity(self) -> int:
        deadline = time.monotonic() + self._wait_timeout_seconds
        return await self._capacity_until(deadline)

    async def _capacity_until(self, deadline: float) -> int:
        last_error: BaseException | None = None
        while True:
            self._raise_if_stopping()
            try:
                capacity = await self._source.total_capacity()
                if capacity > 0:
                    return capacity
            except VbasAdapterError as exc:
                last_error = exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                detail = (
                    f": {_exception_detail(last_error)}" if last_error is not None else ""
                )
                raise VbasAdapterError(
                    f"等待可调度 VBas 离线容量超过 "
                    f"{self._wait_timeout_seconds:g} 秒{detail}"
                ) from last_error
            await self._wait_or_stop(min(remaining, self._retry_interval_seconds))

    async def _wait_or_stop(self, delay: float) -> None:
        if self._shutdown_event is None:
            await asyncio.sleep(delay)
            return
        try:
            await asyncio.wait_for(self._shutdown_event.wait(), timeout=delay)
        except TimeoutError:
            return
        raise asyncio.CancelledError

    def _raise_if_stopping(self) -> None:
        if self._shutdown_event is not None and self._shutdown_event.is_set():
            raise asyncio.CancelledError


class VbasBatchClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        lease_client: CapacityLeaseClient,
        *,
        config: VbasBatchConfig | None = None,
        capacity_gate: VbasOfflineCapacityGate | None = None,
        shutdown_event: asyncio.Event | None = None,
    ) -> None:
        self._http = http_client
        self._lease_client = lease_client
        self._config = config or VbasBatchConfig()
        self._shutdown_event = shutdown_event
        self._capacity_gate = capacity_gate

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
                async with self._capacity_slot():
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

        worker_limit = (
            await self._capacity_gate.worker_limit()
            if self._capacity_gate is not None
            else len(batches)
        )
        worker_count = min(len(batches), worker_limit)
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
        batch_id = self._batch_id(task_id, stream, batch_index, batch)
        transient_attempt = 1
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
            except _VbasTransientCallError as exc:
                if transient_attempt >= self._config.transient_max_attempts:
                    raise VbasAdapterError(
                        "VBas 批次调用失败: "
                        f"task_id={task_id} batch_id={batch_id} "
                        f"instance_id={exc.instance_id} "
                        f"attempt={transient_attempt}/{self._config.transient_max_attempts} "
                        f"reason={_exception_detail(exc.cause)}"
                    ) from exc.cause
                logger.warning(
                    "VBas 批次发生瞬时传输故障，准备重新申请租约",
                    extra={
                        "task_id": task_id,
                        "batch_id": batch_id,
                        "instance_id": exc.instance_id,
                        "attempt": transient_attempt,
                        "max_attempts": self._config.transient_max_attempts,
                        "error_type": type(exc.cause).__name__,
                        "outcome": "retrying",
                    },
                )
                await self._wait_for_transient_retry(transient_attempt)
                transient_attempt += 1

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

    async def _wait_for_transient_retry(self, attempt: int) -> None:
        delay = min(
            self._config.transient_retry_base_delay_seconds * (2 ** (attempt - 1)),
            self._config.transient_retry_max_delay_seconds,
        )
        if delay <= 0:
            return
        if self._shutdown_event is None:
            await asyncio.sleep(delay)
            return
        try:
            await asyncio.wait_for(self._shutdown_event.wait(), timeout=delay)
        except TimeoutError:
            return
        raise asyncio.CancelledError

    def _capacity_slot(self) -> AbstractAsyncContextManager[None]:
        if self._capacity_gate is not None:
            return self._capacity_gate.admit()
        return _unbounded_slot()

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
        batch_id = self._batch_id(task_id, stream, batch_index, batch)
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
            except (TimeoutError, httpx.TransportError) as exc:
                raise _VbasTransientCallError(lease.instance_id, exc) from exc
            except httpx.HTTPStatusError as exc:
                raise VbasAdapterError(
                    "VBas 批次 HTTP 失败: "
                    f"task_id={task_id} batch_id={batch_id} "
                    f"instance_id={lease.instance_id} "
                    f"status_code={exc.response.status_code}"
                ) from exc
            except ValueError as exc:
                raise VbasAdapterError(
                    "VBas 批次响应解析失败: "
                    f"task_id={task_id} batch_id={batch_id} "
                    f"instance_id={lease.instance_id} "
                    f"reason={_exception_detail(exc)}"
                ) from exc
        try:
            return self._parse_response(body, batch)
        except VbasAdapterError as exc:
            raise VbasAdapterError(
                "VBas 批次业务响应失败: "
                f"task_id={task_id} batch_id={batch_id} "
                f"instance_id={lease.instance_id} "
                f"reason={_exception_detail(exc)}"
            ) from exc

    @staticmethod
    def _batch_id(
        task_id: str,
        stream: VisionStream,
        batch_index: int,
        batch: list[VbasFrame],
    ) -> str:
        identity = [
            {
                "image_id": frame.image_id,
                "frame_index": frame.frame_index,
                "timestamp_ms": round(frame.timestamp_seconds * 1000),
                "points": frame.points,
            }
            for frame in batch
        ]
        digest = hashlib.sha256(
            json.dumps(
                {"task_id": task_id, "stream": stream.value, "frames": identity},
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:24]
        suffix = f"-{stream.value.lower()}-{batch_index:04d}-{digest}"
        return f"{task_id[: 200 - len(suffix)]}{suffix}"

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


@asynccontextmanager
async def _unbounded_slot() -> AsyncIterator[None]:
    yield


def _exception_detail(exc: BaseException) -> str:
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
