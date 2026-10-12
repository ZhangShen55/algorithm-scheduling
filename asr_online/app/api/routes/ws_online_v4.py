import asyncio
import json
import logging
import time
import uuid
from typing import Any

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.core.correction_scheduler import CorrectionResult, CorrectionSnapshot
from app.core.models import (
    get_correction_scheduler,
    get_online_model,
    get_punct_pipeline,
)
from app.utils.asr_stats import update_stat
from app.utils.character_utils import (
    count_chinese_and_words,
    normalize_punct_by_context,
    safe_concat,
)


logger = logging.getLogger(__name__)
_SAMPLE_RATE = 16000
_SILENCE_CHUNKS = 2
_MAX_TEXT_UNITS = 200
_FINAL_BUDGET_SECONDS = settings.v4_final_budget_ms / 1000
_MAX_SEGMENT_SAMPLES = settings.v4_max_segment_seconds * _SAMPLE_RATE
_PARTIAL_PUNCTUATION_UNITS = settings.v4_partial_punctuation_chars
_PARTIAL_PUNCTUATION_SAMPLES = int(
    settings.v4_partial_punctuation_interval_ms * _SAMPLE_RATE / 1000
)
_CORRECTION_QUEUE_CAPACITY = settings.v4_correction_queue_capacity


def _extract_text(result: Any) -> str:
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return str(result[0].get("text", "") or "")
    if isinstance(result, dict):
        return str(result.get("text", "") or "")
    return ""


def _merge_streaming_text(previous: str, current: str) -> str:
    if not previous:
        return current or ""
    if not current:
        return previous
    if current.startswith(previous):
        return current
    if previous.endswith(current):
        return previous
    for overlap in range(min(len(previous), len(current)), 0, -1):
        if previous[-overlap:] == current[:overlap]:
            return previous + current[overlap:]
    return safe_concat(previous, current)


def _telemetry(level: int, event: str, **fields: Any) -> None:
    payload = {"name": event, **fields}
    logger.log(
        level,
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        extra={"event": event, **fields},
    )


class _SerialModelRunner:
    """Run one shared model at a time without blocking the event loop."""

    def __init__(self) -> None:
        self._loop = None
        self._lock = None
        self._pending = 0

    def _lock_for_current_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            if self._pending:
                raise RuntimeError("模型执行入口不能在运行中切换事件循环")
            self._loop = loop
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def has_backlog(self) -> bool:
        return self._pending > 0

    async def run(self, function, *args, **kwargs):
        lock = self._lock_for_current_loop()
        queued_at = time.monotonic()
        self._pending += 1
        try:
            async with lock:
                started_at = time.monotonic()
                result = await asyncio.to_thread(function, *args, **kwargs)
            return result, started_at - queued_at, time.monotonic() - started_at
        finally:
            self._pending -= 1


_ONLINE_RUNNER = _SerialModelRunner()
_PUNCTUATION_RUNNER = _SerialModelRunner()


async def run_v4_session(ws: WebSocket) -> None:
    await ws.accept()
    model_online = get_online_model()
    punctuation = get_punct_pipeline()
    scheduler = get_correction_scheduler()
    connection_id = uuid.uuid4().hex
    result_queue = (
        await scheduler.register_session(connection_id)
        if scheduler is not None
        else None
    )

    chunk_size = [4, 8, 4]
    encoder_chunk_look_back = 4
    decoder_chunk_look_back = 1

    online_cache = {}
    received_samples = 0
    segment_seq = 0
    segment_key = None
    segment_text = ""
    segment_visible_text = ""
    segment_start_sample = None
    segment_pcm: list[np.ndarray] = []
    pre_segment_pcm: list[np.ndarray] = []
    silence_chunks = 0
    text_version = 0
    correction_revision = 0
    correction_attempted = False
    candidate_snapshot = None
    candidate_result = None
    candidate_end_sample = None
    last_punctuation_source = ""
    last_punctuation_text = ""
    last_punctuation_units = 0
    last_punctuation_sample = 0
    last_sent_ed = 0.0
    last_finished_ed = 0.0
    connection_closed = False

    async def send_result(
        text: str,
        finished: bool,
        bg: float,
        ed: float,
        key: str,
    ) -> bool:
        nonlocal connection_closed, last_sent_ed
        if connection_closed or ws.client_state.name != "CONNECTED":
            connection_closed = True
            return False
        ed = max(last_sent_ed, ed)
        try:
            await ws.send_json(
                {
                    "key": key,
                    "text": text,
                    "finished": finished,
                    "bg": bg,
                    "ed": ed,
                }
            )
        except (RuntimeError, WebSocketDisconnect):
            connection_closed = True
            return False
        last_sent_ed = ed
        return True

    def segment_audio(end_sample: int) -> np.ndarray:
        if segment_start_sample is None or not segment_pcm:
            return np.empty(0, dtype=np.float32)
        audio = np.concatenate(segment_pcm)
        length = max(0, min(audio.size, end_sample - segment_start_sample))
        return audio[:length].copy()

    def reset_segment() -> None:
        nonlocal online_cache, segment_key, segment_text, segment_visible_text
        nonlocal segment_start_sample, segment_pcm, pre_segment_pcm
        nonlocal silence_chunks, text_version
        nonlocal correction_attempted, candidate_snapshot, candidate_result
        nonlocal candidate_end_sample, last_punctuation_source
        nonlocal last_punctuation_text, last_punctuation_units
        nonlocal last_punctuation_sample
        online_cache.clear()
        online_cache = {}
        segment_key = None
        segment_text = ""
        segment_visible_text = ""
        segment_start_sample = None
        segment_pcm = []
        pre_segment_pcm = []
        silence_chunks = 0
        text_version = 0
        correction_attempted = False
        candidate_snapshot = None
        candidate_result = None
        candidate_end_sample = None
        last_punctuation_source = ""
        last_punctuation_text = ""
        last_punctuation_units = 0
        last_punctuation_sample = received_samples

    def correction_matches(result: CorrectionResult) -> bool:
        snapshot = result.snapshot
        return bool(
            segment_key is not None
            and candidate_snapshot is not None
            and snapshot.connection_id == connection_id
            and snapshot.segment_key == segment_key
            and snapshot.revision_id == candidate_snapshot.revision_id
            and snapshot.text_version == text_version
            and snapshot.start_sample == segment_start_sample
            and snapshot.end_sample == candidate_end_sample
        )

    def accept_correction_result(result: CorrectionResult) -> None:
        nonlocal candidate_result
        snapshot = result.snapshot
        if not correction_matches(result):
            _telemetry(
                logging.INFO,
                "v4_correction_stale",
                connection_id=connection_id,
                segment_key=snapshot.segment_key,
                revision_id=snapshot.revision_id,
                start_sample=snapshot.start_sample,
                end_sample=snapshot.end_sample,
                stale_reason="state_mismatch",
            )
            return
        if result.error or not result.text:
            _telemetry(
                logging.WARNING,
                "v4_correction_failed",
                connection_id=connection_id,
                segment_key=snapshot.segment_key,
                revision_id=snapshot.revision_id,
                start_sample=snapshot.start_sample,
                end_sample=snapshot.end_sample,
                error_type=(
                    "empty_result"
                    if not result.error
                    else result.error.split(":", 1)[0]
                ),
            )
            return
        candidate_result = result

    def drain_results() -> None:
        if result_queue is None:
            return
        while True:
            try:
                result = result_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            accept_correction_result(result)

    async def submit_candidate(end_sample: int, trigger: str) -> bool:
        nonlocal correction_attempted, correction_revision
        nonlocal candidate_snapshot, candidate_end_sample
        if correction_attempted:
            return candidate_snapshot is not None
        correction_attempted = True
        candidate_end_sample = end_sample
        audio = segment_audio(end_sample)
        _telemetry(
            logging.INFO,
            "v4_correction_eligible",
            connection_id=connection_id,
            segment_key=segment_key,
            start_sample=segment_start_sample,
            end_sample=end_sample,
            trigger=trigger,
        )
        if (
            scheduler is None
            or segment_key is None
            or segment_start_sample is None
            or not audio.size
            or not np.any(audio)
        ):
            _telemetry(
                logging.INFO,
                "v4_correction_skipped",
                connection_id=connection_id,
                segment_key=segment_key,
                start_sample=segment_start_sample,
                end_sample=end_sample,
                skip_reason="scheduler_or_audio_unavailable",
            )
            return False
        metrics = scheduler.metrics_snapshot()
        if metrics["queue_depth"] >= _CORRECTION_QUEUE_CAPACITY:
            _telemetry(
                logging.INFO,
                "v4_correction_skipped",
                connection_id=connection_id,
                segment_key=segment_key,
                start_sample=segment_start_sample,
                end_sample=end_sample,
                skip_reason="v4_queue_limit",
                queue_depth=metrics["queue_depth"],
                inflight=metrics["inflight"],
            )
            return False
        correction_revision += 1
        candidate_snapshot = CorrectionSnapshot(
            connection_id=connection_id,
            segment_key=segment_key,
            revision_id=correction_revision,
            text_version=text_version,
            start_sample=segment_start_sample,
            end_sample=end_sample,
            audio=audio,
            kind="intermediate",
            trigger=trigger,
        )
        if not await scheduler.submit(candidate_snapshot):
            candidate_snapshot = None
            _telemetry(
                logging.INFO,
                "v4_correction_skipped",
                connection_id=connection_id,
                segment_key=segment_key,
                start_sample=segment_start_sample,
                end_sample=end_sample,
                skip_reason="scheduler_queue_full",
            )
            return False
        _telemetry(
            logging.INFO,
            "v4_correction_submitted",
            connection_id=connection_id,
            segment_key=segment_key,
            revision_id=candidate_snapshot.revision_id,
            start_sample=segment_start_sample,
            end_sample=end_sample,
            queue_depth=scheduler.metrics_snapshot()["queue_depth"],
            trigger=trigger,
        )
        return True

    async def wait_for_candidate() -> None:
        if (
            result_queue is None
            or candidate_snapshot is None
            or candidate_result is not None
            or _ONLINE_RUNNER.has_backlog
        ):
            return
        deadline = time.monotonic() + _FINAL_BUDGET_SECONDS
        while candidate_result is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                result = await asyncio.wait_for(result_queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return
            accept_correction_result(result)

    async def punctuate(text: str) -> str:
        if not text or punctuation is None:
            return text
        result, _, _ = await _PUNCTUATION_RUNNER.run(punctuation, text, cache={})
        return normalize_punct_by_context(_extract_text(result)) or text

    async def visible_partial(end_sample: int) -> str:
        nonlocal last_punctuation_source, last_punctuation_text
        nonlocal last_punctuation_units, last_punctuation_sample
        units = count_chinese_and_words(segment_text)
        should_punctuate = units > 3 and (
            units - last_punctuation_units >= _PARTIAL_PUNCTUATION_UNITS
            or end_sample - last_punctuation_sample >= _PARTIAL_PUNCTUATION_SAMPLES
        )
        if should_punctuate:
            last_punctuation_source = segment_text
            last_punctuation_text = await punctuate(segment_text)
            last_punctuation_units = units
            last_punctuation_sample = end_sample
            return last_punctuation_text
        if last_punctuation_source and segment_text.startswith(last_punctuation_source):
            return safe_concat(
                last_punctuation_text,
                segment_text[len(last_punctuation_source) :],
            )
        return segment_text

    async def finalize_segment(end_sample: int, trigger: str) -> bool:
        nonlocal last_finished_ed
        if segment_key is None or segment_start_sample is None or not segment_text:
            reset_segment()
            return True
        if not correction_attempted:
            await submit_candidate(end_sample, trigger)
        drain_results()
        await wait_for_candidate()
        drain_results()

        correction = candidate_result if candidate_result is not None else None
        if correction is not None and correction_matches(correction):
            final_text = correction.text
            _telemetry(
                logging.INFO,
                "v4_correction_applied",
                connection_id=connection_id,
                segment_key=segment_key,
                revision_id=correction.snapshot.revision_id,
                start_sample=segment_start_sample,
                end_sample=end_sample,
                queue_ms=round(correction.queue_seconds * 1000, 3),
                inference_ms=round(correction.inference_seconds * 1000, 3),
            )
        else:
            final_text = await punctuate(segment_text)
            reason = (
                "not_submitted"
                if candidate_snapshot is None
                else "not_ready_within_budget"
            )
            _telemetry(
                logging.INFO,
                "v4_correction_fallback",
                connection_id=connection_id,
                segment_key=segment_key,
                start_sample=segment_start_sample,
                end_sample=end_sample,
                fallback_reason=reason,
                online_backlog=_ONLINE_RUNNER.has_backlog,
            )

        final_bg = max(last_finished_ed, round(segment_start_sample / _SAMPLE_RATE, 2))
        final_ed = max(last_sent_ed, round(received_samples / _SAMPLE_RATE, 2))
        sent = await send_result(final_text, True, final_bg, final_ed, segment_key)
        if sent:
            last_finished_ed = final_ed
        reset_segment()
        return sent

    try:
        while True:
            chunk = await ws.receive_bytes()
            drain_results()
            speech_np = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            if np.any(~np.isfinite(speech_np)):
                speech_np = np.nan_to_num(speech_np)
            if not speech_np.size:
                continue

            chunk_start_sample = received_samples
            received_samples += speech_np.size
            chunk_end_sample = received_samples
            raw, queue_seconds, inference_seconds = await _ONLINE_RUNNER.run(
                model_online.generate,
                input=speech_np,
                cache=online_cache,
                is_final=False,
                chunk_size=chunk_size,
                encoder_chunk_look_back=encoder_chunk_look_back,
                decoder_chunk_look_back=decoder_chunk_look_back,
            )
            _telemetry(
                logging.INFO,
                "v4_model_inference",
                model_name="online",
                queue_ms=round(queue_seconds * 1000, 3),
                inference_ms=round(inference_seconds * 1000, 3),
            )
            addition = _extract_text(raw)

            if segment_key is None:
                if not addition:
                    pre_segment_pcm.append(speech_np.copy())
                    silence_chunks += 1
                    placeholder = f"segment-{segment_seq + 1:06d}"
                    if not await send_result(
                        "",
                        False,
                        round(chunk_end_sample / _SAMPLE_RATE, 2),
                        round(chunk_end_sample / _SAMPLE_RATE, 2),
                        placeholder,
                    ):
                        break
                    if silence_chunks >= _SILENCE_CHUNKS:
                        online_cache.clear()
                        online_cache = {}
                        pre_segment_pcm = []
                        silence_chunks = 0
                    continue
                segment_seq += 1
                segment_key = f"segment-{segment_seq:06d}"
                initial_pcm = [*pre_segment_pcm, speech_np.copy()]
                pcm_start_sample = chunk_end_sample - sum(
                    item.size for item in initial_pcm
                )
                segment_start_sample = max(
                    int(round(last_finished_ed * _SAMPLE_RATE)),
                    pcm_start_sample,
                )
                joined_pcm = np.concatenate(initial_pcm)
                trim_samples = max(0, segment_start_sample - pcm_start_sample)
                segment_pcm = [joined_pcm[trim_samples:].copy()]
                pre_segment_pcm = []
                segment_text = addition
                text_version = 1
                silence_chunks = 0
            else:
                segment_pcm.append(speech_np.copy())
                if addition:
                    if silence_chunks:
                        candidate_end_sample = None
                        candidate_result = None
                        _telemetry(
                            logging.INFO,
                            "v4_correction_invalidated",
                            connection_id=connection_id,
                            segment_key=segment_key,
                            invalidation_reason="speech_resumed",
                        )
                    silence_chunks = 0
                    merged = _merge_streaming_text(segment_text, addition)
                    if merged != segment_text:
                        segment_text = merged
                        text_version += 1
                else:
                    silence_chunks += 1

            if (
                received_samples - segment_start_sample >= _MAX_SEGMENT_SAMPLES
                or count_chinese_and_words(segment_text) > _MAX_TEXT_UNITS
            ):
                if not await finalize_segment(chunk_end_sample, "segment_limit"):
                    break
                continue

            if silence_chunks == 1:
                candidate_end_sample = chunk_start_sample
                await submit_candidate(candidate_end_sample, "first_empty_result")

            if silence_chunks >= _SILENCE_CHUNKS:
                end_sample = candidate_end_sample or chunk_start_sample
                if not await finalize_segment(end_sample, "second_empty_result"):
                    break
                continue

            segment_visible_text = await visible_partial(chunk_end_sample)
            if not await send_result(
                segment_visible_text,
                False,
                round(segment_start_sample / _SAMPLE_RATE, 2),
                round(chunk_end_sample / _SAMPLE_RATE, 2),
                segment_key,
            ):
                break
    except WebSocketDisconnect:
        pass
    finally:
        if scheduler is not None:
            await scheduler.unregister_session(connection_id)
        online_cache.clear()
        segment_pcm.clear()
        update_stat("online")
        if ws.client_state.name == "CONNECTED":
            await ws.close()
