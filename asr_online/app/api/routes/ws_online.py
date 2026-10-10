import asyncio
import json
import logging
import random
import string
import time
import uuid
from typing import Iterable
import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from funasr.utils.postprocess_utils import rich_transcription_postprocess

from app.core.models import (
    get_correction_scheduler,
    get_correction_model,
    get_online_model,
    get_punct_pipeline,
    get_sensevoice_model,
    get_vad_model,
)
from app.core.correction_scheduler import CorrectionResult, CorrectionSnapshot
from app.core.config import settings
from app.utils.character_utils import normalize_punct_by_context, safe_concat, count_chinese_and_words
from app.utils.asr_stats import update_stat

router = APIRouter()
logger = logging.getLogger(__name__)

_MAX_REVISION_SEGMENT_CHUNKS = 42
_VAD_MAX_END_SILENCE_MS = settings.vad_max_end_silence_ms
_VAD_MERGE_MAX_GAP_MS = settings.vad_merge_max_gap_ms
_SAMPLE_RATE = 16000
_INPUT_CHUNK_SAMPLES = int(_SAMPLE_RATE * 0.48)
_PRE_ROLL_SAMPLES = int(_SAMPLE_RATE * 0.15)
_MAX_PENDING_SAMPLES = _SAMPLE_RATE * 6
_PROGRESSIVE_CORRECTION_ENABLED = settings.progressive_correction_enabled
_PROGRESSIVE_CORRECTION_CHARS = settings.progressive_correction_chars
_PROGRESSIVE_CORRECTION_AUDIO_SAMPLES = int(
    settings.progressive_correction_audio_ms * _SAMPLE_RATE / 1000
)
_PROGRESSIVE_CORRECTION_COOLDOWN_SAMPLES = int(
    settings.progressive_correction_cooldown_ms * _SAMPLE_RATE / 1000
)
_CORRECTION_TASK_TIMEOUT_SECONDS = settings.correction_task_timeout_ms / 1000
_V3_ONLINE_MODEL_LOCK = asyncio.Lock()
_V3_VAD_MODEL_LOCK = asyncio.Lock()


def _log_telemetry(level: int, event: str, **fields):
    payload = {"name": event, **fields}
    logger.log(
        level,
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        extra={"event": event, **fields},
    )


async def _run_legacy_session(ws: WebSocket):
    await ws.accept()
    cache = {}

    # chunk 参数与原实现保持一致
    chunk_size = [4, 8, 4]
    encoder_chunk_look_back = 4
    decoder_chunk_look_back = 1
    sampling_points = 960

    is_silence_num = 0
    SILENCE_CHUNKS = 2
    chunk_idx = 0

    buffer = ""
    buffer_bg_time = None

    model_online = get_online_model()
    punctuation = get_punct_pipeline()

    try:
        while True:
            chunk = await ws.receive_bytes()
            speech_np = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            if np.any(~np.isfinite(speech_np)):
                speech_np = np.nan_to_num(speech_np)

            bg = round(chunk_idx * 0.48, 2)
            ed = round((chunk_idx + 1) * 0.48, 2)

            res = model_online.generate(
                input=speech_np,
                cache=cache,
                is_final=False,
                chunk_size=chunk_size,
                encoder_chunk_look_back=encoder_chunk_look_back,
                decoder_chunk_look_back=decoder_chunk_look_back,
            )

            if res[0]["text"] == "":
                is_silence_num += 1
            else:
                is_silence_num = 0

            if buffer == "":
                buffer_bg_time = ed
            addition = res[0]["text"]
            buffer = safe_concat(buffer, addition)

            if is_silence_num >= SILENCE_CHUNKS:
                if buffer:
                    buffer = punctuation(buffer, cache={})[0]["text"]
                    buffer = normalize_punct_by_context(buffer)
                await ws.send_json({
                    "key": f"rand_key_{''.join(random.choices(string.ascii_letters + string.digits, k=12))}",
                    "text": buffer,
                    "finished": True if buffer else False,
                    "bg": ed,
                    "ed": round(ed + 0.48, 2)
                })
                cache.clear()
                buffer = ""
                buffer_bg_time = None
            else:
                text = buffer
                if count_chinese_and_words(text) > 200:
                    text = punctuation(text, cache={})[0]["text"]
                    text = normalize_punct_by_context(text)
                    await ws.send_json({
                        "key": f"rand_key_{''.join(random.choices(string.ascii_letters + string.digits, k=12))}",
                        "text": text,
                        "finished": True,
                        "bg": buffer_bg_time,
                        "ed": round(ed + 0.48, 2)
                    })
                    cache.clear()
                    is_silence_num = 0
                    buffer = ""
                    buffer_bg_time = None
                else:
                    if count_chinese_and_words(text) > 3:
                        text = punctuation(text, cache={})[0]["text"]
                        text = normalize_punct_by_context(text)
                        await ws.send_json({
                            "key": f"rand_key_{''.join(random.choices(string.ascii_letters + string.digits, k=12))}",
                            "text": text,
                            "finished": False,
                            "bg": buffer_bg_time,
                            "ed": round(ed + 0.48, 2)
                        })
                    else:
                        await ws.send_json({
                            "key": f"rand_key_{''.join(random.choices(string.ascii_letters + string.digits, k=12))}",
                            "text": text,
                            "finished": False,
                            "bg": buffer_bg_time,
                            "ed": round(ed + 0.48, 2)
                        })
            chunk_idx += 1

    except WebSocketDisconnect:
        pass
    finally:
        update_stat("online")
        if ws.client_state == "CONNECTED":
            await ws.close()


@router.websocket("/v1.0.1/seacraft_asr_online")
async def websocket_endpoint_v1(ws: WebSocket):
    await _run_legacy_session(ws)


def _extract_text(result) -> str:
    if not result or not isinstance(result, list) or not isinstance(result[0], dict):
        return ""
    return result[0].get("text", "") or ""


def _format_final_text(text: str, punctuation) -> str:
    if not text:
        return ""
    if punctuation is not None:
        text = _extract_text(punctuation(text, cache={}))
    return normalize_punct_by_context(text)


def _merge_streaming_text(previous: str, current: str) -> str:
    """合并带 look-back 重叠的在线输出，避免重复追加已返回的前缀。"""
    if not previous:
        return current or ""
    if not current:
        return previous
    if current.startswith(previous):
        return current
    if previous.endswith(current):
        return previous

    max_overlap = min(len(previous), len(current))
    for overlap in range(max_overlap, 0, -1):
        if previous[-overlap:] == current[:overlap]:
            return previous + current[overlap:]
    return safe_concat(previous, current)


def _vad_event_samples(
    events: Iterable[Iterable[float]],
    cache_origin_sample: int,
    lower_bound_sample: int,
    upper_bound_sample: int,
):
    """Convert continuous-cache VAD timestamps to session sample positions."""
    starts = []
    ends = []
    for event in events or ():
        if not isinstance(event, (list, tuple)) or len(event) != 2:
            continue
        start_ms, end_ms = event
        if start_ms >= 0 and end_ms >= 0 and end_ms <= start_ms:
            continue
        if start_ms >= 0:
            starts.append(
                max(
                    lower_bound_sample,
                    min(
                        upper_bound_sample,
                        cache_origin_sample
                        + int(round(start_ms * _SAMPLE_RATE / 1000)),
                    ),
                )
            )
        if end_ms >= 0:
            ends.append(
                max(
                    lower_bound_sample,
                    min(
                        upper_bound_sample,
                        cache_origin_sample
                        + int(round(end_ms * _SAMPLE_RATE / 1000)),
                    ),
                )
            )
    return starts, ends


async def _run_revision_session(ws: WebSocket, correction_mode: str = "seaco"):
    """在线临时结果，句末对完整 PCM 做一次最终识别。"""
    await ws.accept()
    online_cache = {}
    vad_cache = {}
    vad_cache_origin_sample = 0
    model_online = get_online_model()
    model_vad = get_vad_model()
    use_sensevoice = correction_mode == "sensevoice"
    model_correction = (
        get_sensevoice_model() if use_sensevoice else get_correction_model()
    )
    punctuation = None if use_sensevoice else get_punct_pipeline()

    chunk_size = [4, 8, 4]
    encoder_chunk_look_back = 4
    decoder_chunk_look_back = 1
    vad_chunk_ms = 200
    segment_seq = 0
    segment_key = None
    segment_text = ""
    segment_vad_start_sample = None
    segment_audio_start_sample = None
    segment_vad_confirmed = False
    candidate_end_sample = None
    pcm_buffer = []
    pcm_buffer_start_sample = 0
    received_samples = 0
    last_finished_ed = 0.0
    connection_closed = False

    async def run_sync(function, *args, **kwargs):
        return await asyncio.to_thread(function, *args, **kwargs)

    async def send_result(text: str, finished: bool, bg: float, ed: float, key: str):
        nonlocal connection_closed
        if connection_closed or ws.client_state.name != "CONNECTED":
            connection_closed = True
            return False
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
        return True

    def reset_segment():
        nonlocal online_cache, segment_key, segment_text
        nonlocal segment_vad_start_sample, segment_audio_start_sample
        nonlocal segment_vad_confirmed, candidate_end_sample
        online_cache.clear()
        online_cache = {}
        segment_key = None
        segment_text = ""
        segment_vad_start_sample = None
        segment_audio_start_sample = None
        segment_vad_confirmed = False
        candidate_end_sample = None

    def append_pcm(speech_np, start_sample: int):
        nonlocal pcm_buffer_start_sample
        if not pcm_buffer:
            pcm_buffer_start_sample = start_sample
        pcm_buffer.append(speech_np.copy())

    def pcm_slice(start_sample: int, end_sample: int):
        if not pcm_buffer:
            return np.empty(0, dtype=np.float32)
        buffer = np.concatenate(pcm_buffer)
        start = max(0, min(len(buffer), start_sample - pcm_buffer_start_sample))
        end = max(start, min(len(buffer), end_sample - pcm_buffer_start_sample))
        return buffer[start:end].copy()

    def trim_pcm_before(keep_from_sample: int):
        nonlocal pcm_buffer_start_sample, pcm_buffer
        if not pcm_buffer:
            return
        buffer = np.concatenate(pcm_buffer)
        cut = max(pcm_buffer_start_sample, keep_from_sample)
        offset = max(0, min(len(buffer), cut - pcm_buffer_start_sample))
        if offset >= len(buffer):
            pcm_buffer = []
            pcm_buffer_start_sample = received_samples
        elif offset > 0:
            pcm_buffer = [buffer[offset:].copy()]
            pcm_buffer_start_sample = cut

    def reset_vad_at_current_position():
        nonlocal vad_cache, vad_cache_origin_sample
        vad_cache.clear()
        vad_cache = {}
        vad_cache_origin_sample = received_samples

    async def finalize_segment(end_sample: int, force_vad_reset: bool = False):
        nonlocal segment_key, segment_text
        nonlocal segment_vad_start_sample, segment_audio_start_sample
        nonlocal last_finished_ed
        if segment_key is None or segment_audio_start_sample is None:
            return True

        end_sample = max(
            segment_audio_start_sample,
            min(received_samples, end_sample),
        )
        segment_audio = pcm_slice(segment_audio_start_sample, end_sample)
        if not segment_audio.size or not np.any(segment_audio):
            trim_pcm_before(max(0, end_sample - _PRE_ROLL_SAMPLES))
            reset_segment()
            if force_vad_reset:
                reset_vad_at_current_position()
            return True
        final_text = ""
        if model_correction is not None and segment_audio.size:
            if use_sensevoice:
                final_result = await run_sync(
                    model_correction.generate,
                    input=segment_audio,
                    cache={},
                    language="zh",
                    use_itn=True,
                    batch_size_s=60,
                )
                final_text = _extract_text(final_result)
                if final_text:
                    final_text = rich_transcription_postprocess(final_text)
            else:
                final_result = await run_sync(
                    model_correction.generate,
                    input=segment_audio,
                    is_final=True,
                    spk_model=None,
                )
                final_text = _extract_text(final_result)
        if not final_text and segment_audio.size:
            final_result = await run_sync(
                model_online.generate,
                input=segment_audio,
                cache={},
                is_final=True,
                chunk_size=chunk_size,
                encoder_chunk_look_back=encoder_chunk_look_back,
                decoder_chunk_look_back=decoder_chunk_look_back,
            )
            final_text = _extract_text(final_result)
        final_text = (
            normalize_punct_by_context(final_text)
            if use_sensevoice
            else _format_final_text(final_text, punctuation)
        ) or segment_text

        # A VAD end may refer to an earlier position in the current input
        # block. Final segments stay monotonic without expanding to the block
        # end that was already shown by a temporary response.
        final_ed = max(last_finished_ed, round(end_sample / _SAMPLE_RATE, 2))
        sent = await send_result(
            final_text,
            True,
            round(
                (
                    segment_vad_start_sample
                    if segment_vad_start_sample is not None
                    else segment_audio_start_sample
                )
                / _SAMPLE_RATE,
                2,
            ),
            final_ed,
            segment_key,
        )
        if sent:
            last_finished_ed = final_ed
        # Keep the tail of the completed segment as the next segment's
        # pre-roll; PCM after the VAD end remains pending for the next start.
        trim_pcm_before(max(0, end_sample - _PRE_ROLL_SAMPLES))
        reset_segment()
        if force_vad_reset:
            reset_vad_at_current_position()
        return sent

    try:
        while True:
            chunk = await ws.receive_bytes()
            speech_np = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            if np.any(~np.isfinite(speech_np)):
                speech_np = np.nan_to_num(speech_np)
            if speech_np.size == 0:
                continue

            chunk_start_sample = received_samples
            received_samples += speech_np.size
            chunk_end_sample = received_samples
            append_pcm(speech_np, chunk_start_sample)

            res = await run_sync(
                model_online.generate,
                input=speech_np,
                cache=online_cache,
                is_final=False,
                chunk_size=chunk_size,
                encoder_chunk_look_back=encoder_chunk_look_back,
                decoder_chunk_look_back=decoder_chunk_look_back,
            )
            addition = _extract_text(res)

            vad_events = []
            if model_vad is not None:
                vad_res = await run_sync(
                    model_vad.generate,
                    input=speech_np,
                    cache=vad_cache,
                    is_final=False,
                    chunk_size=vad_chunk_ms,
                    max_end_silence_time=_VAD_MAX_END_SILENCE_MS,
                )
                if vad_res and isinstance(vad_res[0], dict):
                    vad_events = vad_res[0].get("value", []) or []
            start_samples, end_samples = _vad_event_samples(
                vad_events,
                vad_cache_origin_sample,
                pcm_buffer_start_sample,
                chunk_end_sample,
            )

            valid_end_samples = [
                sample
                for sample in end_samples
                if segment_vad_start_sample is None
                or sample > segment_vad_start_sample
            ]
            effective_start_samples = list(start_samples)
            candidate_resolved = False
            candidate_merge_start = None
            candidate_finalized = False

            # Resolve a previous VAD end before assigning this chunk's online
            # text. A start within the short gap belongs to the same segment;
            # a later start begins a new segment after the old one is finalized.
            if candidate_end_sample is not None:
                merge_limit = candidate_end_sample + int(
                    _VAD_MERGE_MAX_GAP_MS * _SAMPLE_RATE / 1000
                )
                merge_start = next(
                    (
                        sample
                        for sample in start_samples
                        if candidate_end_sample <= sample <= merge_limit
                    ),
                    None,
                )
                next_start = next(
                    (sample for sample in start_samples if sample > merge_limit),
                    None,
                )
                if merge_start is not None:
                    candidate_end_sample = None
                    candidate_resolved = True
                    candidate_merge_start = merge_start
                    effective_start_samples = []
                elif next_start is not None or chunk_end_sample > merge_limit:
                    sent = await finalize_segment(candidate_end_sample)
                    if not sent:
                        break
                    candidate_finalized = True
                    effective_start_samples = [
                        sample for sample in start_samples if sample > merge_limit
                    ]
                else:
                    effective_start_samples = []

            if effective_start_samples and segment_key is None:
                vad_start_sample = effective_start_samples[0]
                if not segment_vad_confirmed:
                    segment_vad_start_sample = vad_start_sample
                    segment_audio_start_sample = max(
                        pcm_buffer_start_sample,
                        vad_start_sample - _PRE_ROLL_SAMPLES,
                    )
                    segment_vad_confirmed = True
                if segment_audio_start_sample is None:
                    segment_audio_start_sample = max(
                        pcm_buffer_start_sample,
                        vad_start_sample - _PRE_ROLL_SAMPLES,
                    )
                elif segment_audio_start_sample < pcm_buffer_start_sample:
                    segment_audio_start_sample = pcm_buffer_start_sample
                if segment_key is None:
                    segment_seq += 1
                    segment_key = f"segment-{segment_seq:06d}"
                    segment_text = ""

            if effective_start_samples and segment_key is not None and candidate_end_sample is None:
                # A new start following a finalized segment needs a fresh key;
                # an in-window start has already cleared candidate_end_sample
                # and intentionally keeps the old segment key.
                if (
                    segment_vad_start_sample is not None
                    and effective_start_samples[0] > segment_vad_start_sample
                ):
                    if not segment_vad_confirmed:
                        segment_vad_start_sample = effective_start_samples[0]
                        segment_audio_start_sample = max(
                            pcm_buffer_start_sample,
                            effective_start_samples[0] - _PRE_ROLL_SAMPLES,
                        )
                        segment_vad_confirmed = True

            if segment_key is None and addition:
                segment_vad_start_sample = chunk_start_sample
                segment_audio_start_sample = chunk_start_sample
                segment_vad_confirmed = False
                segment_seq += 1
                segment_key = f"segment-{segment_seq:06d}"
                segment_text = ""

            if addition:
                segment_text = _merge_streaming_text(segment_text, addition)

            bg = round(
                segment_vad_start_sample / _SAMPLE_RATE
                if segment_vad_start_sample is not None
                else chunk_start_sample / _SAMPLE_RATE,
                2,
            )
            ed = round(chunk_end_sample / _SAMPLE_RATE, 2)
            if segment_key is None:
                sent = await send_result("", False, ed, ed, f"segment-{segment_seq + 1:06d}")
            else:
                sent = await send_result(segment_text, False, bg, ed, segment_key)
            if not sent:
                break

            if valid_end_samples and segment_key is not None:
                # A delayed VAD start can replace an online-text fallback
                # start in this same batch. Re-check ends against the updated
                # start so an old end at that boundary cannot create a
                # zero-duration finished segment.
                valid_end_samples = [
                    sample
                    for sample in valid_end_samples
                    if segment_vad_start_sample is None
                    or sample > segment_vad_start_sample
                ]
                if candidate_finalized:
                    valid_end_samples = [
                        sample
                        for sample in valid_end_samples
                        if effective_start_samples
                        and sample > effective_start_samples[0]
                    ]
                if candidate_resolved:
                    valid_end_samples = [
                        sample
                        for sample in valid_end_samples
                        if candidate_merge_start is not None
                        and sample > candidate_merge_start
                    ]
                if valid_end_samples and candidate_end_sample is None:
                    candidate_end_sample = valid_end_samples[0]

            if segment_audio_start_sample is not None and (
                chunk_end_sample - segment_audio_start_sample
                >= _MAX_REVISION_SEGMENT_CHUNKS * _INPUT_CHUNK_SAMPLES
            ):
                sent = await finalize_segment(chunk_end_sample, force_vad_reset=True)
                if not sent:
                    break
            elif segment_audio_start_sample is None:
                trim_pcm_before(max(pcm_buffer_start_sample, chunk_end_sample - _MAX_PENDING_SAMPLES))
                if chunk_end_sample - vad_cache_origin_sample >= _MAX_PENDING_SAMPLES:
                    trim_pcm_before(max(0, chunk_end_sample - _PRE_ROLL_SAMPLES))
                    reset_vad_at_current_position()
    except WebSocketDisconnect:
        pass
    finally:
        online_cache.clear()
        vad_cache.clear()
        pcm_buffer.clear()
        update_stat("online")
        if ws.client_state == "CONNECTED":
            await ws.close()


async def _run_progressive_session(ws: WebSocket):
    """v1.0.3 online partials plus bounded progressive SenseVoice revisions."""
    await ws.accept()
    online_cache = {}
    vad_cache = {}
    vad_cache_origin_sample = 0
    model_online = get_online_model()
    model_vad = get_vad_model()
    fallback_sensevoice = get_sensevoice_model()
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
    vad_chunk_ms = 200
    segment_seq = 0
    segment_key = None
    segment_text = ""
    segment_vad_start_sample = None
    segment_audio_start_sample = None
    segment_vad_confirmed = False
    candidate_end_sample = None
    pcm_buffer = []
    pcm_buffer_start_sample = 0
    received_samples = 0
    last_finished_ed = 0.0
    connection_closed = False

    correction_revision = 0
    text_version = 0
    latest_accepted_revision = 0
    last_correction_attempt_sample = None
    last_correction_attempt_units = 0
    submitted_corrections = {}
    accepted_corrections = {}
    last_visible_text = ""

    async def run_sync(function, *args, **kwargs):
        return await asyncio.to_thread(function, *args, **kwargs)

    async def run_model(model_name, function, *args, **kwargs):
        lock = (
            _V3_ONLINE_MODEL_LOCK
            if model_name == "online"
            else _V3_VAD_MODEL_LOCK
        )
        queued_at = time.monotonic()
        async with lock:
            started_at = time.monotonic()
            result = await run_sync(function, *args, **kwargs)
        _log_telemetry(
            logging.INFO,
            "v3_model_inference",
            model_name=model_name,
            queue_ms=round((started_at - queued_at) * 1000, 3),
            inference_ms=round((time.monotonic() - started_at) * 1000, 3),
        )
        return result

    async def send_result(text: str, finished: bool, bg: float, ed: float, key: str):
        nonlocal connection_closed
        if connection_closed or ws.client_state.name != "CONNECTED":
            connection_closed = True
            return False
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
        return True

    def reset_segment():
        nonlocal online_cache, segment_key, segment_text
        nonlocal segment_vad_start_sample, segment_audio_start_sample
        nonlocal segment_vad_confirmed, candidate_end_sample, text_version
        nonlocal latest_accepted_revision, last_correction_attempt_sample
        nonlocal last_correction_attempt_units, submitted_corrections
        nonlocal accepted_corrections, last_visible_text
        online_cache.clear()
        online_cache = {}
        segment_key = None
        segment_text = ""
        segment_vad_start_sample = None
        segment_audio_start_sample = None
        segment_vad_confirmed = False
        candidate_end_sample = None
        text_version = 0
        latest_accepted_revision = 0
        last_correction_attempt_sample = None
        last_correction_attempt_units = 0
        submitted_corrections = {}
        accepted_corrections = {}
        last_visible_text = ""

    def append_pcm(speech_np, start_sample: int):
        nonlocal pcm_buffer_start_sample
        if not pcm_buffer:
            pcm_buffer_start_sample = start_sample
        pcm_buffer.append(speech_np.copy())

    def pcm_slice(start_sample: int, end_sample: int):
        if not pcm_buffer:
            return np.empty(0, dtype=np.float32)
        buffer = np.concatenate(pcm_buffer)
        start = max(0, min(len(buffer), start_sample - pcm_buffer_start_sample))
        end = max(start, min(len(buffer), end_sample - pcm_buffer_start_sample))
        return buffer[start:end].copy()

    def trim_pcm_before(keep_from_sample: int):
        nonlocal pcm_buffer_start_sample, pcm_buffer
        if not pcm_buffer:
            return
        buffer = np.concatenate(pcm_buffer)
        cut = max(pcm_buffer_start_sample, keep_from_sample)
        offset = max(0, min(len(buffer), cut - pcm_buffer_start_sample))
        if offset >= len(buffer):
            pcm_buffer = []
            pcm_buffer_start_sample = received_samples
        elif offset > 0:
            pcm_buffer = [buffer[offset:].copy()]
            pcm_buffer_start_sample = cut

    def reset_vad_at_current_position():
        nonlocal vad_cache, vad_cache_origin_sample
        vad_cache.clear()
        vad_cache = {}
        vad_cache_origin_sample = received_samples

    async def accept_correction_result(
        result: CorrectionResult, send_intermediate: bool
    ) -> bool:
        nonlocal latest_accepted_revision, last_visible_text
        snapshot = result.snapshot
        stale_reason = None
        if connection_closed:
            stale_reason = "connection_closed"
        elif segment_key is None or snapshot.segment_key != segment_key:
            stale_reason = "segment_changed"
        elif snapshot.text_version != text_version:
            stale_reason = "text_advanced"
        elif snapshot.revision_id < latest_accepted_revision:
            stale_reason = "revision_behind"
        if stale_reason is not None:
            _log_telemetry(
                logging.INFO,
                "correction_stale",
                connection_id=connection_id,
                segment_key=snapshot.segment_key,
                revision_id=snapshot.revision_id,
                end_sample=snapshot.end_sample,
                stale_reason=stale_reason,
            )
            return True
        if result.error or not result.text:
            _log_telemetry(
                logging.WARNING,
                "correction_degraded",
                connection_id=connection_id,
                segment_key=snapshot.segment_key,
                revision_id=snapshot.revision_id,
                correction_kind=snapshot.kind,
                error_type=(
                    None
                    if not result.error
                    else result.error.split(":", 1)[0]
                ),
            )
            return True
        latest_accepted_revision = snapshot.revision_id
        accepted_corrections[snapshot.end_sample] = (
            snapshot.revision_id,
            snapshot.text_version,
            result.text,
        )
        if not send_intermediate or snapshot.kind != "intermediate":
            return True
        if result.text == last_visible_text:
            return True
        bg_sample = (
            segment_vad_start_sample
            if segment_vad_start_sample is not None
            else snapshot.start_sample
        )
        sent = await send_result(
            result.text,
            False,
            round(bg_sample / _SAMPLE_RATE, 2),
            round(snapshot.end_sample / _SAMPLE_RATE, 2),
            snapshot.segment_key,
        )
        if sent:
            last_visible_text = result.text
            _log_telemetry(
                logging.INFO,
                "correction_visible",
                connection_id=connection_id,
                segment_key=snapshot.segment_key,
                revision_id=snapshot.revision_id,
                correction_kind=snapshot.kind,
                correction_trigger=snapshot.trigger,
                start_sample=snapshot.start_sample,
                end_sample=snapshot.end_sample,
                queue_ms=round(result.queue_seconds * 1000, 3),
                inference_ms=round(result.inference_seconds * 1000, 3),
                visible_ms=round(
                    (time.monotonic() - snapshot.created_at) * 1000, 3
                ),
            )
        return sent

    async def drain_correction_results(send_intermediate: bool = True) -> bool:
        if result_queue is None:
            return True
        while True:
            try:
                result = result_queue.get_nowait()
            except asyncio.QueueEmpty:
                return True
            if not await accept_correction_result(result, send_intermediate):
                return False

    async def submit_correction(end_sample: int, kind: str, trigger: str):
        nonlocal correction_revision, last_correction_attempt_sample
        nonlocal last_correction_attempt_units
        if scheduler is None or segment_key is None:
            return None
        if kind == "intermediate" and not _PROGRESSIVE_CORRECTION_ENABLED:
            return None
        if segment_audio_start_sample is None:
            return None
        end_sample = max(
            segment_audio_start_sample,
            min(received_samples, end_sample),
        )
        existing = submitted_corrections.get(end_sample)
        if existing is not None and existing[1] == text_version:
            return existing[0]
        audio = pcm_slice(segment_audio_start_sample, end_sample)
        if not audio.size or not np.any(audio):
            return None
        correction_revision += 1
        snapshot = CorrectionSnapshot(
            connection_id=connection_id,
            segment_key=segment_key,
            revision_id=correction_revision,
            text_version=text_version,
            start_sample=segment_audio_start_sample,
            end_sample=end_sample,
            audio=audio,
            kind=kind,
            trigger=trigger,
        )
        last_correction_attempt_sample = end_sample
        last_correction_attempt_units = count_chinese_and_words(segment_text)
        if not await scheduler.submit(snapshot):
            return None
        submitted_corrections[end_sample] = (
            snapshot.revision_id,
            snapshot.text_version,
            snapshot.kind,
        )
        return snapshot.revision_id

    def cached_correction(end_sample: int):
        cached = accepted_corrections.get(end_sample)
        if cached is None or cached[1] != text_version:
            return ""
        return cached[2]

    async def wait_for_correction(end_sample: int, revision_id: int) -> str:
        if result_queue is None:
            return ""
        cached = cached_correction(end_sample)
        if cached:
            return cached
        deadline = time.monotonic() + _CORRECTION_TASK_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                result = await asyncio.wait_for(
                    result_queue.get(),
                    timeout=max(0.001, deadline - time.monotonic()),
                )
            except asyncio.TimeoutError:
                break
            await accept_correction_result(result, send_intermediate=False)
            cached = cached_correction(end_sample)
            if cached:
                return cached
            if (
                result.snapshot.revision_id == revision_id
                and result.snapshot.end_sample == end_sample
            ):
                break
        _log_telemetry(
            logging.WARNING,
            "correction_final_timeout",
            connection_id=connection_id,
            segment_key=segment_key,
            revision_id=revision_id,
            end_sample=end_sample,
        )
        return ""

    async def finalize_segment(end_sample: int, force_vad_reset: bool = False):
        nonlocal segment_key, segment_text, last_finished_ed
        if segment_key is None or segment_audio_start_sample is None:
            return True
        end_sample = max(
            segment_audio_start_sample,
            min(received_samples, end_sample),
        )
        segment_audio = pcm_slice(segment_audio_start_sample, end_sample)
        if not segment_audio.size or not np.any(segment_audio):
            trim_pcm_before(max(0, end_sample - _PRE_ROLL_SAMPLES))
            reset_segment()
            if force_vad_reset:
                reset_vad_at_current_position()
            return True

        final_text = ""
        if scheduler is not None:
            await drain_correction_results(send_intermediate=False)
            final_text = cached_correction(end_sample)
            submitted = submitted_corrections.get(end_sample)
            revision_id = submitted[0] if submitted is not None else None
            if not final_text and revision_id is None:
                revision_id = await submit_correction(
                    end_sample, "final", "segment_finalize"
                )
            if not final_text and revision_id is not None:
                final_text = await wait_for_correction(end_sample, revision_id)
        elif fallback_sensevoice is not None:
            result = await run_sync(
                fallback_sensevoice.generate,
                input=segment_audio,
                cache={},
                language="zh",
                use_itn=True,
                batch_size_s=60,
            )
            final_text = _extract_text(result)
            if final_text:
                final_text = rich_transcription_postprocess(final_text)

        if not final_text and scheduler is None:
            result = await run_model(
                "online",
                model_online.generate,
                input=segment_audio,
                cache={},
                is_final=True,
                chunk_size=chunk_size,
                encoder_chunk_look_back=encoder_chunk_look_back,
                decoder_chunk_look_back=decoder_chunk_look_back,
            )
            final_text = _extract_text(result)
        final_text = (
            normalize_punct_by_context(final_text)
            or last_visible_text
            or segment_text
        )

        final_ed = max(last_finished_ed, round(end_sample / _SAMPLE_RATE, 2))
        bg_sample = (
            segment_vad_start_sample
            if segment_vad_start_sample is not None
            else segment_audio_start_sample
        )
        sent = await send_result(
            final_text,
            True,
            round(bg_sample / _SAMPLE_RATE, 2),
            final_ed,
            segment_key,
        )
        if sent:
            last_finished_ed = final_ed
        trim_pcm_before(max(0, end_sample - _PRE_ROLL_SAMPLES))
        reset_segment()
        if force_vad_reset:
            reset_vad_at_current_position()
        return sent

    try:
        while True:
            chunk = await ws.receive_bytes()
            if not await drain_correction_results():
                break
            speech_np = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            if np.any(~np.isfinite(speech_np)):
                speech_np = np.nan_to_num(speech_np)
            if speech_np.size == 0:
                continue

            chunk_start_sample = received_samples
            received_samples += speech_np.size
            chunk_end_sample = received_samples
            append_pcm(speech_np, chunk_start_sample)

            result = await run_model(
                "online",
                model_online.generate,
                input=speech_np,
                cache=online_cache,
                is_final=False,
                chunk_size=chunk_size,
                encoder_chunk_look_back=encoder_chunk_look_back,
                decoder_chunk_look_back=decoder_chunk_look_back,
            )
            addition = _extract_text(result)

            vad_events = []
            if model_vad is not None:
                vad_result = await run_model(
                    "vad",
                    model_vad.generate,
                    input=speech_np,
                    cache=vad_cache,
                    is_final=False,
                    chunk_size=vad_chunk_ms,
                    max_end_silence_time=_VAD_MAX_END_SILENCE_MS,
                )
                if vad_result and isinstance(vad_result[0], dict):
                    vad_events = vad_result[0].get("value", []) or []
            start_samples, end_samples = _vad_event_samples(
                vad_events,
                vad_cache_origin_sample,
                pcm_buffer_start_sample,
                chunk_end_sample,
            )
            valid_end_samples = [
                sample
                for sample in end_samples
                if segment_vad_start_sample is None
                or sample > segment_vad_start_sample
            ]
            effective_start_samples = list(start_samples)
            candidate_resolved = False
            candidate_merge_start = None
            candidate_finalized = False

            if candidate_end_sample is not None:
                merge_limit = candidate_end_sample + int(
                    _VAD_MERGE_MAX_GAP_MS * _SAMPLE_RATE / 1000
                )
                merge_start = next(
                    (
                        sample
                        for sample in start_samples
                        if candidate_end_sample <= sample <= merge_limit
                    ),
                    None,
                )
                next_start = next(
                    (sample for sample in start_samples if sample > merge_limit),
                    None,
                )
                if merge_start is not None:
                    candidate_end_sample = None
                    candidate_resolved = True
                    candidate_merge_start = merge_start
                    effective_start_samples = []
                elif next_start is not None or chunk_end_sample > merge_limit:
                    sent = await finalize_segment(candidate_end_sample)
                    if not sent:
                        break
                    candidate_finalized = True
                    effective_start_samples = [
                        sample for sample in start_samples if sample > merge_limit
                    ]
                else:
                    effective_start_samples = []

            if effective_start_samples and segment_key is None:
                vad_start_sample = effective_start_samples[0]
                segment_vad_start_sample = vad_start_sample
                segment_audio_start_sample = max(
                    pcm_buffer_start_sample,
                    vad_start_sample - _PRE_ROLL_SAMPLES,
                )
                segment_vad_confirmed = True
                segment_seq += 1
                segment_key = f"segment-{segment_seq:06d}"
                segment_text = ""

            if segment_key is None and addition:
                segment_vad_start_sample = chunk_start_sample
                segment_audio_start_sample = chunk_start_sample
                segment_vad_confirmed = False
                segment_seq += 1
                segment_key = f"segment-{segment_seq:06d}"
                segment_text = ""

            text_changed = False
            if addition:
                merged_text = _merge_streaming_text(segment_text, addition)
                text_changed = merged_text != segment_text
                segment_text = merged_text
                if text_changed:
                    text_version += 1

            bg = round(
                segment_vad_start_sample / _SAMPLE_RATE
                if segment_vad_start_sample is not None
                else chunk_start_sample / _SAMPLE_RATE,
                2,
            )
            ed = round(chunk_end_sample / _SAMPLE_RATE, 2)
            if segment_key is None:
                sent = await send_result(
                    "", False, ed, ed, f"segment-{segment_seq + 1:06d}"
                )
            else:
                visible_text = (
                    segment_text
                    if text_changed
                    else last_visible_text or segment_text
                )
                sent = await send_result(
                    visible_text, False, bg, ed, segment_key
                )
                if sent and text_changed:
                    last_visible_text = segment_text
            if not sent:
                break

            if valid_end_samples and segment_key is not None:
                valid_end_samples = [
                    sample
                    for sample in valid_end_samples
                    if segment_vad_start_sample is None
                    or sample > segment_vad_start_sample
                ]
                if candidate_finalized:
                    valid_end_samples = [
                        sample
                        for sample in valid_end_samples
                        if effective_start_samples
                        and sample > effective_start_samples[0]
                    ]
                if candidate_resolved:
                    valid_end_samples = [
                        sample
                        for sample in valid_end_samples
                        if candidate_merge_start is not None
                        and sample > candidate_merge_start
                    ]
                if valid_end_samples and candidate_end_sample is None:
                    candidate_end_sample = valid_end_samples[0]
                    await submit_correction(
                        candidate_end_sample,
                        "intermediate",
                        "vad_candidate_end",
                    )

            if (
                scheduler is not None
                and segment_key is not None
                and segment_audio_start_sample is not None
                and candidate_end_sample is None
            ):
                attempt_sample = (
                    last_correction_attempt_sample
                    if last_correction_attempt_sample is not None
                    else segment_audio_start_sample
                )
                elapsed_samples = chunk_end_sample - attempt_sample
                new_units = (
                    count_chinese_and_words(segment_text)
                    - last_correction_attempt_units
                )
                if (
                    elapsed_samples >= _PROGRESSIVE_CORRECTION_COOLDOWN_SAMPLES
                    and (
                        new_units >= _PROGRESSIVE_CORRECTION_CHARS
                        or elapsed_samples >= _PROGRESSIVE_CORRECTION_AUDIO_SAMPLES
                    )
                ):
                    trigger = (
                        "stable_chars"
                        if new_units >= _PROGRESSIVE_CORRECTION_CHARS
                        else "audio_interval"
                    )
                    await submit_correction(
                        chunk_end_sample,
                        "intermediate",
                        trigger,
                    )

            if segment_audio_start_sample is not None and (
                chunk_end_sample - segment_audio_start_sample
                >= _MAX_REVISION_SEGMENT_CHUNKS * _INPUT_CHUNK_SAMPLES
            ):
                sent = await finalize_segment(
                    chunk_end_sample, force_vad_reset=True
                )
                if not sent:
                    break
            elif segment_audio_start_sample is None:
                trim_pcm_before(
                    max(
                        pcm_buffer_start_sample,
                        chunk_end_sample - _MAX_PENDING_SAMPLES,
                    )
                )
                if chunk_end_sample - vad_cache_origin_sample >= _MAX_PENDING_SAMPLES:
                    trim_pcm_before(max(0, chunk_end_sample - _PRE_ROLL_SAMPLES))
                    reset_vad_at_current_position()
    except WebSocketDisconnect:
        pass
    finally:
        if scheduler is not None:
            await scheduler.unregister_session(connection_id)
        online_cache.clear()
        vad_cache.clear()
        pcm_buffer.clear()
        update_stat("online")
        if ws.client_state == "CONNECTED":
            await ws.close()


@router.websocket("/v1.0.2/seacraft_asr_online")
async def websocket_endpoint_v2(ws: WebSocket):
    await _run_revision_session(ws)


@router.websocket("/v1.0.3/seacraft_asr_online")
async def websocket_endpoint_v3(ws: WebSocket):
    await _run_progressive_session(ws)
