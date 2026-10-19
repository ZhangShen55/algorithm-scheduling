import asyncio
import time
import uuid
from dataclasses import dataclass, field

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.core.cumulative_text import CumulativeText
from app.core.models import get_online_model, get_v4_punctuation, get_v4_scheduler
from app.core.v4_correction import Request, Snapshot, SerialRunner, extract_text, telemetry
from app.utils.asr_stats import update_stat
from app.utils.character_utils import count_chinese_and_words, safe_concat


_SAMPLE_RATE = 16000
_SILENCE_CHUNKS = 2
_MAX_SEGMENT_SAMPLES = settings.v4_max_segment_seconds * _SAMPLE_RATE
_INTERVAL_SAMPLES = settings.v4_correction_interval_ms * 16
_FINAL_BUDGET_SECONDS = settings.v4_final_budget_ms / 1000
_PARTIAL_PUNCTUATION_UNITS = settings.v4_partial_punctuation_chars
_PARTIAL_PUNCTUATION_SAMPLES = settings.v4_partial_punctuation_interval_ms * 16
_MAX_BUFFER_SAMPLES = 20 * _SAMPLE_RATE
_ONLINE_RUNNER = SerialRunner()


@dataclass
class Segment:
    generation: int
    start: int
    text: CumulativeText = field(default_factory=CumulativeText)
    pcm: list = field(default_factory=list)
    end: int = 0
    state: str = 'OPEN'
    revision: int = 0
    last_admitted: int = 0
    last_watermark: int = 0
    retry_at: int = 0
    pending: bool = False
    deferred: object = None
    punctuation_source: str = ''
    punctuation_text: str = ''
    punctuation_end: int = 0
    silence: int = 0

    @property
    def key(self):
        return f'segment-{self.generation:06d}'


async def run_v4_session(ws: WebSocket) -> None:
    await ws.accept()
    model = get_online_model()
    scheduler, punctuation = get_v4_scheduler(), get_v4_punctuation()
    if scheduler is None or punctuation is None:
        await ws.close(code=1011, reason='v4 correction model is not configured')
        return
    cid = uuid.uuid4().hex
    results = scheduler.register(cid)
    inputs = asyncio.Queue(maxsize=64)
    buffered = 0
    receive_error = None
    disconnected = False
    cache = {}
    received = 0
    sequence = 0
    segment = None
    pre_pcm = []
    pre_empty = 0
    last_ed = 0.0
    last_finished = 0.0
    response_sequence = 0
    applied_since_emit = []

    async def receive():
        nonlocal buffered, receive_error, disconnected
        try:
            while True:
                chunk = await ws.receive_bytes()
                if not chunk:
                    continue
                samples = len(chunk) // 2
                if len(chunk) % 2 or samples > _MAX_BUFFER_SAMPLES:
                    receive_error = 'invalid_or_oversize_pcm'
                    break
                if buffered + samples > _MAX_BUFFER_SAMPLES or inputs.full():
                    receive_error = 'input_backlog_limit'
                    break
                buffered += samples
                inputs.put_nowait(chunk)
        except WebSocketDisconnect:
            disconnected = True
        finally:
            if receive_error:
                telemetry('v4_input_rejected', connection_id=cid, reason=receive_error)
                await ws.close(code=1013, reason=receive_error)
                disconnected = True
            await inputs.put(None)

    async def emit(text, finished=False):
        nonlocal last_ed, last_finished, response_sequence
        if disconnected:
            return
        end = max(last_ed, round(received / _SAMPLE_RATE, 2))
        bg = max(last_finished, round(segment.start / _SAMPLE_RATE, 2)) if segment else end
        await ws.send_json({'key': segment.key if segment else f'segment-{sequence+1:06d}',
                           'text': text, 'finished': finished, 'bg': bg, 'ed': end})
        response_sequence += 1
        for applied in applied_since_emit:
            telemetry('v4_correction_visible', **applied,
                      response_sequence=response_sequence, finished=finished)
        applied_since_emit.clear()
        last_ed = end
        if finished:
            last_finished = end

    def fields(snap):
        return dict(connection_id=cid, segment_key=f'segment-{snap.generation:06d}',
                    generation=snap.generation, revision_id=snap.revision,
                    start_sample=snap.start, end_sample=snap.end,
                    correction_kind=snap.kind)

    def adopt(result):
        snap = result.snapshot
        if segment is None or segment.state == 'FINISHED' or snap.generation != segment.generation:
            telemetry('v4_correction_stale', **fields(snap), reason='generation')
            return False
        if result.error:
            telemetry('v4_correction_rejected', **fields(snap), reason=result.error)
            return False
        full = snap.kind == 'final' and segment.state == 'CLOSING' and snap.end == segment.end
        reason = segment.text.adopt(result.text, snap.raw, snap.end, snap.revision, final=full)
        if reason == 'applied':
            segment.deferred = None
            segment.punctuation_source = ''
            applied_since_emit.append(fields(snap))
            telemetry('v4_correction_applied', **fields(snap),
                      queue_ms=result.queue_ms, asr_ms=result.asr_ms,
                      punctuation_ms=result.punctuation_ms, visible_end_sample=received)
            return True
        if reason == 'no_unique_suffix_anchor' and segment.state == 'OPEN':
            if segment.deferred is None or segment.deferred.snapshot.end <= snap.end:
                segment.deferred = result
        telemetry('v4_correction_rejected', **fields(snap), reason=reason)
        return False

    def request(kind, deadline=float('inf')):
        current = segment

        def freeze():
            # Freeze only at worker admission; pending holds no PCM copy.
            current.pending = False
            current.revision += 1
            current.last_admitted = current.end
            current.last_watermark = len(current.text.records)
            return Snapshot(cid, current.generation, current.revision,
                            current.start, current.end, current.text.raw,
                            current.last_watermark, np.concatenate(current.pcm).copy(), kind)

        accepted = scheduler.submit(cid, Request(current.generation, kind, freeze, deadline=deadline))
        if accepted:
            current.pending = True
        else:
            current.retry_at = received + _SAMPLE_RATE
            telemetry('v4_correction_skipped', connection_id=cid, segment_key=current.key,
                      correction_kind=kind, reason='queue_or_health')
        return accepted

    async def visible():
        source = segment.text.visible
        if segment.punctuation_source == source:
            return segment.punctuation_text
        growth = count_chinese_and_words(source) - count_chinese_and_words(segment.punctuation_source)
        due = growth >= _PARTIAL_PUNCTUATION_UNITS or received - segment.punctuation_end >= _PARTIAL_PUNCTUATION_SAMPLES
        if due and count_chinese_and_words(source) > 3:
            try:
                text, _ = await punctuation.apply(source)
            except Exception as exc:
                telemetry('v4_punctuation_failed', connection_id=cid, error_type=type(exc).__name__)
                text = source
            segment.punctuation_source, segment.punctuation_text = source, text
            segment.punctuation_end = received
            return text
        if segment.punctuation_source and source.startswith(segment.punctuation_source):
            return safe_concat(segment.punctuation_text, source[len(segment.punctuation_source):])
        return source

    async def online(audio, final=False):
        raw, queue, duration = await _ONLINE_RUNNER.run(
            model.generate, input=audio, cache=cache, is_final=final,
            chunk_size=[4,8,4], encoder_chunk_look_back=4, decoder_chunk_look_back=1)
        telemetry('v4_model_inference', model_name='online', queue_ms=queue*1000,
                  inference_ms=duration*1000, is_final=final)
        return extract_text(raw)

    async def finish(trigger):
        nonlocal segment, cache, result_wait
        segment.state = 'CLOSING'
        deadline = time.monotonic() + (0 if _ONLINE_RUNNER.has_backlog or not inputs.empty()
                                      else _FINAL_BUDGET_SECONDS)
        # Finalize only at a segment boundary. Empty audio flushes cached features.
        tail = await online(np.empty(0, dtype=np.float32), final=True)
        segment.text.append(tail, received)
        request('final', deadline)
        final_applied = False
        # The persistent result waiter may already own a result; drain it first.
        if result_wait.done():
            result = result_wait.result()
            applied = adopt(result)
            final_applied = applied and result.snapshot.kind == 'final'
            result_wait = asyncio.create_task(results.get())
        while not final_applied and time.monotonic() < deadline and not disconnected:
            done, _ = await asyncio.wait({result_wait}, timeout=max(0, deadline-time.monotonic()))
            if not done:
                break
            result = result_wait.result()
            result_wait = asyncio.create_task(results.get())
            applied = adopt(result)
            if applied and result.snapshot.kind == 'final' and result.snapshot.end == segment.end:
                final_applied = True
        text = segment.text.visible
        if segment.punctuation_source == text:
            text = segment.punctuation_text
        elif segment.punctuation_source and text.startswith(segment.punctuation_source):
            text = safe_concat(segment.punctuation_text, text[len(segment.punctuation_source):])
        if not final_applied:
            telemetry('v4_correction_fallback', connection_id=cid, segment_key=segment.key,
                      reason='final_budget', trigger=trigger,
                      covered_end_sample=segment.text.covered_end)
        await emit(text, True)
        segment.state = 'FINISHED'
        scheduler.cancel_pending(cid, segment.generation)
        segment = None
        cache = {}

    receiver = asyncio.create_task(receive())
    input_wait = asyncio.create_task(inputs.get())
    result_wait = asyncio.create_task(results.get())
    try:
        while not disconnected:
            if not scheduler.healthy:
                telemetry('v4_model_stalled', connection_id=cid)
                await ws.close(code=1011, reason='v4 model stalled; restart required')
                break
            done, _ = await asyncio.wait({input_wait, result_wait}, timeout=1,
                                         return_when=asyncio.FIRST_COMPLETED)
            if result_wait in done:
                result = result_wait.result()
                result_wait = asyncio.create_task(results.get())
                if adopt(result):
                    await emit(segment.text.visible)
            if input_wait not in done:
                continue
            chunk = input_wait.result()
            input_wait = asyncio.create_task(inputs.get())
            if chunk is None:
                break
            audio = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768
            buffered -= audio.size
            received += audio.size
            addition = await online(audio)
            if segment is None:
                pre_pcm.append(audio)
                if not addition:
                    pre_empty += 1
                    await emit('')
                    if pre_empty >= _SILENCE_CHUNKS:
                        pre_pcm, cache, pre_empty = [], {}, 0
                    continue
                sequence += 1
                start = received - sum(a.size for a in pre_pcm)
                segment = Segment(sequence, start, pcm=pre_pcm,
                                  last_admitted=start, punctuation_end=start)
                pre_pcm, pre_empty = [], 0
            else:
                segment.pcm.append(audio)
            segment.end = received
            segment.text.append(addition, received)
            segment.silence = 0 if addition else segment.silence + 1
            if segment.deferred is not None:
                adopt(segment.deferred)
            if (received - segment.start >= _MAX_SEGMENT_SAMPLES
                    or count_chinese_and_words(segment.text.raw) > 200
                    or segment.silence >= _SILENCE_CHUNKS):
                await finish('empty_results' if segment.silence >= _SILENCE_CHUNKS else 'segment_limit')
                continue
            if (not segment.pending and received >= segment.retry_at
                    and received - segment.last_admitted >= _INTERVAL_SAMPLES
                    and len(segment.text.records) > segment.last_watermark):
                request('intermediate')
            await emit(await visible())
    except WebSocketDisconnect:
        pass
    finally:
        scheduler.unregister(cid)
        for task in (receiver, input_wait, result_wait):
            task.cancel()
        await asyncio.gather(receiver, input_wait, result_wait, return_exceptions=True)
        cache.clear()
        update_stat('online')
        if ws.client_state.name == 'CONNECTED' and ws.application_state.name == 'CONNECTED':
            await ws.close()
