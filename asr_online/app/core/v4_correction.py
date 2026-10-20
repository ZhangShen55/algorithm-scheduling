"""V4-only bounded workers. Waiting deadlines never release a busy model."""
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from app.core.cumulative_text import normalized, quality_reason
from app.core.v4_itn import V4ITN
from app.utils.character_utils import normalize_punct_by_context


logger = logging.getLogger(__name__)


def extract_text(result):
    if isinstance(result, list) and result:
        result = result[0]
    return str(result.get('text', '') or '') if isinstance(result, dict) else ''


def telemetry(event, **fields):
    logger.info(json.dumps({'name': event, **fields}, ensure_ascii=True),
                extra={'event': event, **fields})


class SerialRunner:
    def __init__(self):
        self._loop = None
        self._lock = None
        self._pending = 0

    @property
    def has_backlog(self):
        return self._pending > 0

    async def run(self, function, *args, **kwargs):
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            if self._pending:
                raise RuntimeError('model runner changed loop while busy')
            self._loop, self._lock = loop, asyncio.Lock()
        queued = time.monotonic()
        self._pending += 1
        try:
            async with self._lock:
                started = time.monotonic()
                future = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
                try:
                    result = await asyncio.shield(future)
                except asyncio.CancelledError:
                    # Cancellation cannot stop torch in its thread. Keep the lock.
                    await future
                    raise
            return result, started - queued, time.monotonic() - started
        finally:
            self._pending -= 1


class V4Punctuation:
    def __init__(self, model):
        self.model = model
        self.runner = SerialRunner()

    async def apply(self, text):
        started = time.monotonic()
        # CT-Transformer adds marks rather than replacing existing ones.
        # Preserve numeric separators (3.14, 12:30) when re-punctuating a base.
        source = re.sub(r'[，。！？；：、!?;]|(?<!\d)[,.:]|[,.:](?!\d)', '', text)
        result, _, _ = await self.runner.run(self.model, source, cache={})
        candidate = normalize_punct_by_context(extract_text(result))
        # Punctuation must not change the lexical payload.
        if not normalized(candidate)[0] or normalized(text)[0] != normalized(candidate)[0]:
            candidate = text
        return candidate, time.monotonic() - started


@dataclass(frozen=True)
class Snapshot:
    connection_id: str
    generation: int
    revision: int
    start: int
    end: int
    raw: str
    watermark: int
    audio: np.ndarray = field(repr=False, compare=False)
    kind: str = 'intermediate'


@dataclass
class Result:
    snapshot: Snapshot
    text: str = ''
    alignment_text: str = ''
    error: str | None = None
    queue_ms: float = 0
    asr_ms: float = 0
    itn_ms: float = 0
    itn_status: str = 'disabled'
    itn_error_type: str | None = None
    punctuation_ms: float = 0


@dataclass
class Request:
    generation: int
    kind: str
    freeze: Callable[[], Snapshot]
    created: float = field(default_factory=time.monotonic)
    deadline: float = float('inf')


class V4CorrectionScheduler:
    def __init__(self, models, punctuation, capacity=8, max_samples=1927680,
                 stall_seconds=30, itn=None):
        if not models or capacity < 1:
            raise ValueError('v4 models and queue capacity required')
        self.models, self.punctuation = models, punctuation
        self.itn = itn or V4ITN(enabled=False)
        self.capacity, self.max_samples = capacity, max_samples
        self.stall_seconds = stall_seconds
        self.sessions = {}
        self.pending = {}
        self.active = {}
        self.workers = []
        self.wake = asyncio.Event()
        self.accepting = False

    @property
    def healthy(self):
        return all(time.monotonic() - t < self.stall_seconds for t in self.active.values())

    async def start(self):
        self.accepting = True
        self.workers = [asyncio.create_task(self._worker(model)) for model in self.models]

    async def close(self):
        self.accepting = False
        self.pending.clear()
        self.sessions.clear()
        self.wake.set()
        # Do not cancel workers or drop models while native inference is running.
        await asyncio.gather(*self.workers)
        self.workers.clear()

    def register(self, cid):
        if not self.accepting:
            raise RuntimeError('v4 scheduler is stopped')
        queue = asyncio.Queue(maxsize=2)
        self.sessions[cid] = queue
        return queue

    def unregister(self, cid):
        self.sessions.pop(cid, None)
        self.pending.pop(cid, None)

    def cancel_pending(self, cid, generation):
        request = self.pending.get(cid)
        if request is not None and request.generation == generation:
            self.pending.pop(cid)

    def submit(self, cid, request):
        if not self.accepting or cid not in self.sessions or not self.healthy:
            return False
        old = self.pending.get(cid)
        if old and old.generation == request.generation and old.kind == 'final' and request.kind != 'final':
            return False
        if old is None and len(self.pending) >= self.capacity:
            return False
        self.pending[cid] = request
        self.wake.set()
        return True

    async def _worker(self, model):
        while self.accepting:
            eligible = [(cid, r) for cid, r in self.pending.items() if cid not in self.active]
            if not eligible:
                self.wake.clear()
                await self.wake.wait()
                continue
            cid, request = min(eligible, key=lambda pair: (pair[1].kind != 'final', pair[1].created))
            del self.pending[cid]
            if time.monotonic() >= request.deadline:
                continue
            started = time.monotonic()
            self.active[cid] = started
            snapshot = None
            try:
                snapshot = request.freeze()
                if snapshot.audio.size > self.max_samples:
                    raise ValueError('v4 snapshot PCM limit exceeded')
                result = Result(snapshot, queue_ms=(started-request.created)*1000)
                telemetry('v4_correction_started', connection_id=cid,
                          generation=snapshot.generation, revision_id=snapshot.revision,
                          start_sample=snapshot.start, end_sample=snapshot.end,
                          correction_kind=snapshot.kind, watermark=snapshot.watermark)
                asr_start = time.monotonic()
                native = asyncio.create_task(asyncio.to_thread(
                    model.generate, input=snapshot.audio, cache={}))
                try:
                    raw = await asyncio.shield(native)
                except asyncio.CancelledError:
                    await native
                    raise
                result.asr_ms = (time.monotonic()-asr_start)*1000
                result.alignment_text = extract_text(raw)
                result.text = result.alignment_text
                result.error = quality_reason(snapshot.raw, result.alignment_text)
                if not result.error and cid in self.sessions:
                    result.text, result.itn_ms, result.itn_status, result.itn_error_type = \
                        await self.itn.apply(result.text)
                    result.text, seconds = await self.punctuation.apply(result.text)
                    result.punctuation_ms = seconds * 1000
            except Exception as exc:
                if snapshot is None:
                    telemetry('v4_snapshot_failed', connection_id=cid, error_type=type(exc).__name__)
                    continue
                result = Result(snapshot, error=type(exc).__name__)
            finally:
                self.active.pop(cid, None)
                self.wake.set()
            queue = self.sessions.get(cid)
            if queue is not None:
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(result)
            telemetry('v4_correction_completed', connection_id=cid,
                      generation=snapshot.generation, revision_id=snapshot.revision,
                      start_sample=snapshot.start, end_sample=snapshot.end,
                      correction_kind=snapshot.kind, queue_ms=result.queue_ms,
                      asr_ms=result.asr_ms, itn_ms=result.itn_ms,
                      itn_status=result.itn_status, itn_error_type=result.itn_error_type,
                      punctuation_ms=result.punctuation_ms, error_type=result.error,
                      pending=len(self.pending), inflight=len(self.active))
