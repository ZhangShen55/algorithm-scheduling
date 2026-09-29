import random
import string
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
from app.utils.character_utils import normalize_punct_by_context, safe_concat, count_chinese_and_words
from app.utils.asr_stats import update_stat

router = APIRouter()


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


async def _run_revision_session(ws: WebSocket, correction_mode: str = "seaco"):
    """在线临时结果，句末对完整 PCM 做一次最终识别。"""
    await ws.accept()
    online_cache = {}
    vad_cache = {}
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
    silence_chunks = 2
    silence_count = 0
    chunk_idx = 0
    segment_seq = 0
    segment_key = None
    segment_bg_time = None
    segment_text = ""
    segment_chunks = []
    vad_started = False
    vad_origin_seconds = 0.0

    async def send_result(text: str, finished: bool, bg: float, ed: float, key: str):
        await ws.send_json(
            {
                "key": key,
                "text": text,
                "finished": finished,
                "bg": bg,
                "ed": ed,
            }
        )

    def reset_segment():
        nonlocal online_cache, vad_cache, silence_count, segment_key
        nonlocal segment_bg_time, segment_text, segment_chunks, vad_started
        nonlocal vad_origin_seconds
        online_cache = {}
        vad_cache = {}
        silence_count = 0
        segment_key = None
        segment_bg_time = None
        segment_text = ""
        segment_chunks = []
        vad_started = False
        vad_origin_seconds = ed

    try:
        while True:
            chunk = await ws.receive_bytes()
            speech_np = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            if np.any(~np.isfinite(speech_np)):
                speech_np = np.nan_to_num(speech_np)
            if speech_np.size == 0:
                continue

            bg = round(chunk_idx * 0.48, 2)
            ed = round((chunk_idx + 1) * 0.48, 2)
            segment_chunks.append(speech_np.copy())

            res = model_online.generate(
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
                vad_res = model_vad.generate(
                    input=speech_np,
                    cache=vad_cache,
                    is_final=False,
                    chunk_size=vad_chunk_ms,
                )
                if vad_res and isinstance(vad_res[0], dict):
                    vad_events = vad_res[0].get("value", []) or []

            ended = False
            for event in vad_events:
                if len(event) != 2:
                    continue
                start_ms, end_ms = event
                if start_ms >= 0:
                    vad_started = True
                    if segment_bg_time is None:
                        detected_start = vad_origin_seconds + start_ms / 1000
                        # VAD 的时间戳受其内部 look-back 影响，不能晚于当前
                        # 已收到的音频；限制上界避免修订段时间轴跳到未来。
                        segment_bg_time = round(max(0.0, min(ed, detected_start)), 2)
                if end_ms >= 0:
                    ended = True
                    # 一个 VAD 结果可能同时带有下一段的起点；下一段从下一个
                    # 音频块重新建立状态，不能覆盖当前段的结束时间。
                    break

            if model_vad is None:
                silence_count = silence_count + 1 if not addition else 0
                ended = silence_count >= silence_chunks

            if segment_key is None and (addition or vad_started):
                segment_seq += 1
                segment_key = f"segment-{segment_seq:06d}"
                if segment_bg_time is None:
                    segment_bg_time = bg

            if addition:
                segment_text = _merge_streaming_text(segment_text, addition)

            if segment_key is None:
                await send_result("", False, bg, ed, f"segment-{segment_seq + 1:06d}")
            else:
                await send_result(segment_text, False, segment_bg_time, ed, segment_key)

            if ended and segment_key is not None:
                segment_audio = np.concatenate(segment_chunks)
                final_text = ""
                if model_correction is not None:
                    if use_sensevoice:
                        final_text = _extract_text(
                            model_correction.generate(
                                input=segment_audio,
                                cache={},
                                language="zh",
                                use_itn=True,
                                batch_size_s=60,
                            )
                        )
                        if final_text:
                            final_text = rich_transcription_postprocess(final_text)
                    else:
                        final_text = _extract_text(
                            model_correction.generate(
                                input=segment_audio,
                                is_final=True,
                                spk_model=None,
                            )
                        )
                if not final_text:
                    # 未配置离线模型时，用完整语音段 fresh cache 冲刷在线模型。
                    final_text = _extract_text(
                        model_online.generate(
                            input=segment_audio,
                            cache={},
                            is_final=True,
                            chunk_size=chunk_size,
                            encoder_chunk_look_back=encoder_chunk_look_back,
                            decoder_chunk_look_back=decoder_chunk_look_back,
                        )
                    ) or segment_text
                final_text = (
                    normalize_punct_by_context(final_text)
                    if use_sensevoice
                    else _format_final_text(final_text, punctuation)
                )
                final_ed = round(max(ed, segment_bg_time + len(segment_audio) / 16000), 2)
                await send_result(
                    final_text,
                    True,
                    segment_bg_time,
                    final_ed,
                    segment_key,
                )
                reset_segment()

            chunk_idx += 1
    except WebSocketDisconnect:
        pass
    finally:
        update_stat("online")
        if ws.client_state == "CONNECTED":
            await ws.close()


@router.websocket("/v1.0.2/seacraft_asr_online")
async def websocket_endpoint_v2(ws: WebSocket):
    await _run_revision_session(ws)


@router.websocket("/v1.0.3/seacraft_asr_online")
async def websocket_endpoint_v3(ws: WebSocket):
    await _run_revision_session(ws, correction_mode="sensevoice")
