import asyncio
import itertools
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from funasr.utils.postprocess_utils import rich_transcription_postprocess

from app.utils.character_utils import normalize_punct_by_context


logger = logging.getLogger(__name__)
CorrectionKind = Literal["intermediate", "final"]


def _extract_text(result: Any) -> str:
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return str(result[0].get("text", "")).strip()
    if isinstance(result, dict):
        return str(result.get("text", "")).strip()
    return ""


@dataclass(frozen=True)
class CorrectionSnapshot:
    connection_id: str
    segment_key: str
    revision_id: int
    text_version: int
    start_sample: int
    end_sample: int
    audio: np.ndarray = field(repr=False, compare=False)
    kind: CorrectionKind = "intermediate"
    trigger: str = "unknown"
    created_at: float = field(default_factory=time.monotonic, compare=False)


@dataclass(frozen=True)
class CorrectionResult:
    snapshot: CorrectionSnapshot
    text: str
    queue_seconds: float
    inference_seconds: float
    error: str | None = None


@dataclass
class _SessionState:
    results: asyncio.Queue
    active: CorrectionSnapshot | None = None
    pending: CorrectionSnapshot | None = None


class CorrectionScheduler:
    """Bounded SenseVoice scheduler with one active task per WebSocket session."""

    def __init__(
        self,
        models: list[Any],
        queue_capacity: int,
        submit_timeout_seconds: float,
    ) -> None:
        if not models:
            raise ValueError("correction scheduler 至少需要一个模型实例")
        if queue_capacity < len(models):
            raise ValueError("correction queue 容量不能小于 worker 数")
        if submit_timeout_seconds <= 0:
            raise ValueError("correction submit timeout 必须为正数")
        self._models = list(models)
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(queue_capacity)
        self._submit_timeout_seconds = submit_timeout_seconds
        self._sessions: dict[str, _SessionState] = {}
        self._workers: list[asyncio.Task] = []
        self._sequence = itertools.count()
        self._lock = asyncio.Lock()
        self._accepting = False
        self._metrics = {
            "submitted": 0,
            "completed": 0,
            "coalesced": 0,
            "queue_full": 0,
            "stale": 0,
            "failed": 0,
        }

    @property
    def worker_count(self) -> int:
        return len(self._models)

    async def start(self) -> None:
        if self._workers:
            return
        self._accepting = True
        self._workers = [
            asyncio.create_task(self._worker_loop(index, model))
            for index, model in enumerate(self._models)
        ]

    async def close(self) -> None:
        self._accepting = False
        async with self._lock:
            self._sessions.clear()
        workers, self._workers = self._workers, []
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
        self._models.clear()

    async def register_session(self, connection_id: str) -> asyncio.Queue:
        async with self._lock:
            if not self._accepting:
                raise RuntimeError("correction scheduler 尚未启动")
            results: asyncio.Queue = asyncio.Queue()
            self._sessions[connection_id] = _SessionState(results=results)
            return results

    async def unregister_session(self, connection_id: str) -> None:
        async with self._lock:
            self._sessions.pop(connection_id, None)

    async def submit(self, snapshot: CorrectionSnapshot) -> bool:
        async with self._lock:
            if not self._accepting:
                return False
            state = self._sessions.get(snapshot.connection_id)
            if state is None:
                return False
            if state.active is not None:
                pending = state.pending
                if pending is None or self._prefer(snapshot, pending):
                    state.pending = snapshot
                self._metrics["coalesced"] += 1
                self._log("correction_coalesced", snapshot)
                return True
            state.active = snapshot
            if self._try_enqueue(snapshot):
                return True
            if snapshot.kind == "intermediate":
                state.active = None
                self._metrics["queue_full"] += 1
                self._log("correction_queue_full", snapshot)
                return False

        try:
            await asyncio.wait_for(
                self._enqueue(snapshot), timeout=self._submit_timeout_seconds
            )
            return True
        except asyncio.TimeoutError:
            async with self._lock:
                state = self._sessions.get(snapshot.connection_id)
                if state is not None and state.active is snapshot:
                    state.active = None
            self._metrics["queue_full"] += 1
            self._log("correction_final_enqueue_timeout", snapshot)
            return False

    def metrics_snapshot(self) -> dict[str, int]:
        return {
            **self._metrics,
            "queue_depth": self._queue.qsize(),
            "sessions": len(self._sessions),
            "workers": len(self._workers),
            "inflight": sum(
                state.active is not None for state in self._sessions.values()
            ),
        }

    @staticmethod
    def _prefer(new: CorrectionSnapshot, current: CorrectionSnapshot) -> bool:
        if new.kind == "final" and current.kind != "final":
            return True
        if new.end_sample != current.end_sample:
            return new.end_sample > current.end_sample
        return new.revision_id > current.revision_id

    def _queue_item(self, snapshot: CorrectionSnapshot) -> tuple[int, int, CorrectionSnapshot]:
        priority = 0 if snapshot.kind == "final" else 1
        return priority, next(self._sequence), snapshot

    def _try_enqueue(self, snapshot: CorrectionSnapshot) -> bool:
        try:
            self._queue.put_nowait(self._queue_item(snapshot))
        except asyncio.QueueFull:
            return False
        self._metrics["submitted"] += 1
        self._log("correction_submitted", snapshot)
        return True

    async def _enqueue(self, snapshot: CorrectionSnapshot) -> None:
        await self._queue.put(self._queue_item(snapshot))
        self._metrics["submitted"] += 1
        self._log("correction_submitted", snapshot)

    async def _worker_loop(self, worker_index: int, model: Any) -> None:
        while True:
            _, _, snapshot = await self._queue.get()
            try:
                if not await self._is_active(snapshot):
                    self._metrics["stale"] += 1
                    continue
                started = time.monotonic()
                queue_seconds = started - snapshot.created_at
                text = ""
                error = None
                try:
                    raw = await asyncio.to_thread(
                        model.generate,
                        input=snapshot.audio,
                        cache={},
                        language="zh",
                        use_itn=True,
                        batch_size_s=60,
                    )
                    text = _extract_text(raw)
                    if text:
                        text = normalize_punct_by_context(
                            rich_transcription_postprocess(text)
                        )
                except Exception as exc:  # model failures must become bounded results
                    error = f"{type(exc).__name__}: {exc}"
                    self._metrics["failed"] += 1
                inference_seconds = time.monotonic() - started
                result = CorrectionResult(
                    snapshot=snapshot,
                    text=text,
                    queue_seconds=queue_seconds,
                    inference_seconds=inference_seconds,
                    error=error,
                )
                await self._complete(snapshot, result)
                self._metrics["completed"] += 1
                self._log(
                    "correction_completed" if error is None else "correction_failed",
                    snapshot,
                    worker=worker_index,
                    queue_ms=round(queue_seconds * 1000, 3),
                    inference_ms=round(inference_seconds * 1000, 3),
                    error_type=None if error is None else error.split(":", 1)[0],
                )
            finally:
                self._queue.task_done()

    async def _is_active(self, snapshot: CorrectionSnapshot) -> bool:
        async with self._lock:
            state = self._sessions.get(snapshot.connection_id)
            return state is not None and state.active is snapshot

    async def _complete(
        self, snapshot: CorrectionSnapshot, result: CorrectionResult
    ) -> None:
        next_snapshot = None
        result_queue = None
        async with self._lock:
            state = self._sessions.get(snapshot.connection_id)
            if state is None or state.active is not snapshot:
                self._metrics["stale"] += 1
                return
            result_queue = state.results
            state.active = None
            if state.pending is not None:
                next_snapshot = state.pending
                state.pending = None
                state.active = next_snapshot
                if not self._try_enqueue(next_snapshot):
                    state.active = None
                    self._metrics["queue_full"] += 1
                    self._log("correction_queue_full", next_snapshot)
        if result_queue is not None:
            result_queue.put_nowait(result)

    def _log(self, event: str, snapshot: CorrectionSnapshot, **fields: Any) -> None:
        telemetry = {
            "name": event,
            "connection_id": snapshot.connection_id,
            "segment_key": snapshot.segment_key,
            "revision_id": snapshot.revision_id,
            "correction_kind": snapshot.kind,
            "correction_trigger": snapshot.trigger,
            "start_sample": snapshot.start_sample,
            "end_sample": snapshot.end_sample,
            "queue_depth": self._queue.qsize(),
            "inflight": sum(
                state.active is not None for state in self._sessions.values()
            ),
            **fields,
        }
        logger.info(
            json.dumps(telemetry, ensure_ascii=True, separators=(",", ":")),
            extra={
                "event": event,
                **telemetry,
            },
        )
