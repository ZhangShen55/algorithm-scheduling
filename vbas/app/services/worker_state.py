import asyncio
import time
from contextlib import asynccontextmanager
from statistics import mean
from typing import AsyncIterator, Dict, List, Optional


class BatchRejectedError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class BatchAdmissionController:
    def __init__(
            self,
            instance_id: str,
            base_url: str,
            max_concurrent_batches: int,
            max_queue_size: int,
            capabilities: Optional[List[str]] = None):
        self.instance_id = instance_id
        self.base_url = base_url
        self.max_concurrent_batches = max(1, int(max_concurrent_batches))
        self.max_queue_size = max(0, int(max_queue_size))
        self.capabilities = capabilities or ["student_behavior", "teacher_behavior", "teacher_head_pose"]
        self.running_batches = 0
        self.queued_batches = 0
        self.success_count = 0
        self.failure_count = 0
        self.recent_failure_count = 0
        self.last_error: Optional[str] = None
        self._draining = False
        self._latencies_ms: List[float] = []
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def admit(
            self,
            task_id: str,
            batch_id: str,
            stream_type: str,
            frame_count: int) -> AsyncIterator[None]:
        await self._enter(task_id, batch_id, stream_type, frame_count)
        start = time.perf_counter()
        try:
            yield
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            await self._leave(elapsed_ms, error=str(exc))
            raise
        else:
            elapsed_ms = (time.perf_counter() - start) * 1000
            await self._leave(elapsed_ms, error=None)

    async def _enter(self, task_id: str, batch_id: str, stream_type: str, frame_count: int) -> None:
        async with self._lock:
            if self._draining:
                raise BatchRejectedError(503, "TIAS 正在排空，拒绝新批次")
            if self.running_batches >= self.max_concurrent_batches:
                if self.max_queue_size <= 0:
                    raise BatchRejectedError(429, "TIAS 当前满载")
                if self.queued_batches >= self.max_queue_size:
                    raise BatchRejectedError(503, "TIAS 本地队列已满")
                raise BatchRejectedError(503, "TIAS 本地排队暂未启用")
            self.running_batches += 1

    async def _leave(self, elapsed_ms: float, error: Optional[str]) -> None:
        async with self._lock:
            self.running_batches = max(0, self.running_batches - 1)
            self._latencies_ms.append(max(0.0, elapsed_ms))
            self._latencies_ms = self._latencies_ms[-200:]
            if error:
                self.failure_count += 1
                self.recent_failure_count += 1
                self.last_error = error[:500]
            else:
                self.success_count += 1
                self.recent_failure_count = 0

    def set_draining(self) -> None:
        self._draining = True

    def set_up(self) -> None:
        self._draining = False

    def snapshot(self) -> Dict[str, object]:
        latencies = list(self._latencies_ms)
        status = "DRAINING" if self._draining else "UP"
        if not self._draining and self.running_batches >= self.max_concurrent_batches:
            status = "BUSY"
        return {
            "instance_id": self.instance_id,
            "base_url": self.base_url,
            "service_version": "6.0",
            "model_version": "student/person/face/teacher",
            "capabilities": list(self.capabilities),
            "status": status,
            "max_concurrent_batches": self.max_concurrent_batches,
            "running_batches": self.running_batches,
            "queued_batches": self.queued_batches,
            "max_queue_size": self.max_queue_size,
            "available_slots": max(0, self.max_concurrent_batches - self.running_batches),
            "avg_latency_ms": round(mean(latencies), 2) if latencies else None,
            "p95_latency_ms": round(self._percentile(latencies, 95), 2) if latencies else None,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "recent_failure_count": self.recent_failure_count,
            "last_error": self.last_error,
        }

    @staticmethod
    def _percentile(values: List[float], percentile: int) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = int(round((len(ordered) - 1) * percentile / 100))
        return ordered[max(0, min(index, len(ordered) - 1))]
