import asyncio
import time
from collections import deque
from contextlib import asynccontextmanager
from statistics import mean
from typing import AsyncIterator, Deque, Dict, List, Optional


class BatchRejectedError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class BatchAdmissionController:
    """按在线 HTTP 请求和离线 batch 分离维护单实例准入状态。"""

    def __init__(self, instance_id: str, base_url: str, max_concurrent_offline_batches: int | None = None,
                 max_concurrent_online_requests: int = 24, max_queue_online_size: int = 24,
                 capabilities: Optional[List[str]] = None, **legacy: object):
        if max_concurrent_offline_batches is None:
            max_concurrent_offline_batches = int(legacy.pop("max_concurrent_batches", 1024))
        if "max_queue_size" in legacy:
            old_queue = int(legacy.pop("max_queue_size"))
            if old_queue != 0:
                raise ValueError("旧 MaxQueueSize 已移除，请使用 MaxQueueOnlineSize")
        if legacy:
            raise TypeError(f"不支持的准入参数: {', '.join(sorted(legacy))}")
        if type(max_concurrent_offline_batches) is not int or max_concurrent_offline_batches <= 0:
            raise ValueError("MaxConcurrentOfflineBatches（旧 MaxConcurrentBatches）必须是正整数")
        if type(max_concurrent_online_requests) is not int or max_concurrent_online_requests <= 0:
            raise ValueError("MaxConcurrentOnlineRequests 必须是正整数")
        if type(max_queue_online_size) is not int or max_queue_online_size < 0:
            raise ValueError("MaxQueueOnlineSize 必须是非负整数")
        self.instance_id = instance_id
        self.base_url = base_url
        self.max_concurrent_offline_batches = max_concurrent_offline_batches
        self.max_concurrent_online_requests = max_concurrent_online_requests
        self.max_queue_online_size = max_queue_online_size
        self.capabilities = capabilities or ["student_behavior", "teacher_behavior", "person_count"]
        self.running_offline_batches = 0
        self.running_online_requests = 0
        self.queued_online_requests = 0
        self.success_count = 0
        self.failure_count = 0
        self.recent_failure_count = 0
        self.last_error: Optional[str] = None
        self._draining = False
        self._latencies_ms: List[float] = []
        self._lock = asyncio.Lock()
        self._online_waiters: Deque[asyncio.Future[None]] = deque()

    @property
    def running_batches(self) -> int:
        return self.running_offline_batches

    @running_batches.setter
    def running_batches(self, value: int) -> None:
        self.running_offline_batches = value

    @property
    def queued_batches(self) -> int:
        return self.queued_online_requests

    @asynccontextmanager
    async def admit(self, task_id: str, batch_id: str, stream_type: str, frame_count: int,
                    *, work_type: str = "offline") -> AsyncIterator[None]:
        online = work_type.lower() == "online"
        await self._enter(task_id, batch_id, stream_type, frame_count, online=online)
        start = time.perf_counter()
        try:
            yield
        except BaseException as exc:
            await asyncio.shield(self._leave((time.perf_counter() - start) * 1000, str(exc) or type(exc).__name__, online=online))
            raise
        else:
            await self._leave((time.perf_counter() - start) * 1000, None, online=online)

    async def _enter(self, task_id: str, batch_id: str, stream_type: str, frame_count: int, *, online: bool) -> None:
        del task_id, batch_id, stream_type, frame_count
        async with self._lock:
            if self._draining:
                raise BatchRejectedError(503, "VBAS 正在排空，拒绝新请求")
            if not online:
                if self.running_offline_batches >= self.max_concurrent_offline_batches:
                    raise BatchRejectedError(429, "VBAS 离线 batch 当前满载")
                self.running_offline_batches += 1
                return
            if self.running_online_requests < self.max_concurrent_online_requests and not self._online_waiters:
                self.running_online_requests += 1
                return
            if len(self._online_waiters) >= self.max_queue_online_size:
                raise BatchRejectedError(429, "VBAS 在线队列已满")
            waiter = asyncio.get_running_loop().create_future()
            self._online_waiters.append(waiter)
            self.queued_online_requests = len(self._online_waiters)
        try:
            await waiter
        except asyncio.CancelledError:
            async with self._lock:
                if waiter in self._online_waiters:
                    self._online_waiters.remove(waiter)
                    self.queued_online_requests = len(self._online_waiters)
            raise

    async def _leave(self, elapsed_ms: float, error: Optional[str], *, online: bool) -> None:
        async with self._lock:
            if online:
                self.running_online_requests = max(0, self.running_online_requests - 1)
                self._promote_next_online_locked()
            else:
                self.running_offline_batches = max(0, self.running_offline_batches - 1)
            self._latencies_ms.append(max(0.0, elapsed_ms))
            self._latencies_ms = self._latencies_ms[-200:]
            if error:
                self.failure_count += 1
                self.recent_failure_count += 1
                self.last_error = error[:500]
            else:
                self.success_count += 1
                self.recent_failure_count = 0

    def _promote_next_online_locked(self) -> None:
        while self.running_online_requests < self.max_concurrent_online_requests and self._online_waiters:
            waiter = self._online_waiters.popleft()
            if waiter.done():
                continue
            self.running_online_requests += 1
            waiter.set_result(None)
            break
        self.queued_online_requests = len(self._online_waiters)

    def set_draining(self) -> None:
        self._draining = True
        for waiter in list(self._online_waiters):
            if not waiter.done():
                waiter.set_exception(BatchRejectedError(503, "VBAS 正在排空，拒绝新请求"))
        self._online_waiters.clear()
        self.queued_online_requests = 0

    def set_up(self) -> None:
        self._draining = False

    def snapshot(self) -> Dict[str, object]:
        status = "DRAINING" if self._draining else "UP"
        if not self._draining and (self.running_offline_batches >= self.max_concurrent_offline_batches or self.running_online_requests >= self.max_concurrent_online_requests):
            status = "BUSY"
        latencies = list(self._latencies_ms)
        return {
            "instance_id": self.instance_id, "base_url": self.base_url,
            "service_version": "6.0", "model_version": "student/person/face/teacher",
            "capabilities": list(self.capabilities), "status": status,
            "max_concurrent_offline_batches": self.max_concurrent_offline_batches,
            "max_concurrent_online_requests": self.max_concurrent_online_requests,
            "max_queue_online_size": self.max_queue_online_size,
            "running_offline_batches": self.running_offline_batches,
            "running_online_requests": self.running_online_requests,
            "queued_online_requests": self.queued_online_requests,
            "available_offline_slots": max(0, self.max_concurrent_offline_batches - self.running_offline_batches),
            "available_online_slots": max(0, self.max_concurrent_online_requests - self.running_online_requests),
            "avg_latency_ms": round(mean(latencies), 2) if latencies else None,
            "p95_latency_ms": round(self._percentile(latencies, 95), 2) if latencies else None,
            "success_count": self.success_count, "failure_count": self.failure_count,
            "recent_failure_count": self.recent_failure_count, "last_error": self.last_error,
            "running_batches": self.running_offline_batches,
            "queued_batches": self.queued_online_requests,
            "available_slots": max(0, self.max_concurrent_offline_batches - self.running_offline_batches),
        }

    @staticmethod
    def _percentile(values: List[float], percentile: int) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = int(round((len(ordered) - 1) * percentile / 100))
        return ordered[max(0, min(index, len(ordered) - 1))]
