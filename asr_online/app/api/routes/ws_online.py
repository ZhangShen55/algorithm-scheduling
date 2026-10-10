import asyncio
import random
import string
from typing import Iterable
import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from funasr.utils.postprocess_utils import rich_transcription_postprocess

from app.core.models import (
    get_correction_model,
    get_online_model,
    get_punct_pipeline,
    get_sensevoice_model,
    get_vad_model,
)
from app.core.config import settings
from app.utils.character_utils import normalize_punct_by_context, safe_concat, count_chinese_and_words
from app.utils.asr_stats import update_stat

router = APIRouter()

_MAX_REVISION_SEGMENT_CHUNKS = 42
_VAD_MAX_END_SILENCE_MS = settings.vad_max_end_silence_ms
_VAD_MERGE_MAX_GAP_MS = settings.vad_merge_max_gap_ms
_SAMPLE_RATE = 16000
_INPUT_CHUNK_SAMPLES = int(_SAMPLE_RATE * 0.48)
_PRE_ROLL_SAMPLES = int(_SAMPLE_RATE * 0.15)
_MAX_PENDING_SAMPLES = _SAMPLE_RATE * 6


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


@router.websocket("/v1.0.2/seacraft_asr_online")
async def websocket_endpoint_v2(ws: WebSocket):
    await _run_revision_session(ws)


@router.websocket("/v1.0.3/seacraft_asr_online")
async def websocket_endpoint_v3(ws: WebSocket):
    await _run_revision_session(ws, correction_mode="sensevoice")
