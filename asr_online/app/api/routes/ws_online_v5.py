import json
import logging
from dataclasses import dataclass

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.core.cumulative_text import normalized
from app.core.qwen_stream import (
    QwenBackendPool,
    QwenStreamClient,
    QwenStreamError,
)
from app.utils.asr_stats import update_stat


logger = logging.getLogger(__name__)
_SAMPLE_RATE = 16000
_QWEN_CHUNK_SAMPLES = round(settings.qwen_stream_chunk_seconds * _SAMPLE_RATE)
_QWEN_CHUNK_BYTES = _QWEN_CHUNK_SAMPLES * 2
_MAX_FRAME_SAMPLES = settings.qwen_stream_max_segment_seconds * _SAMPLE_RATE
_MAX_SEGMENT_SAMPLES = settings.qwen_stream_max_segment_seconds * _SAMPLE_RATE
_BACKEND_POOL = QwenBackendPool(
    settings.qwen_stream_base_urls,
    capacity_per_backend=settings.qwen_stream_backend_capacity,
    total_capacity=settings.qwen_stream_max_sessions,
)


@dataclass
class Segment:
    generation: int
    start_sample: int
    text: str = ""

    @property
    def key(self) -> str:
        return f"segment-{self.generation:06d}"


def _telemetry(event: str, **fields) -> None:
    logger.info(
        json.dumps({"name": event, **fields}, ensure_ascii=True, separators=(",", ":")),
        extra={"event": event, **fields},
    )


def _suffix_after_frozen(frozen: str, cumulative: str) -> str:
    if not frozen:
        return cumulative
    if cumulative.startswith(frozen):
        return cumulative[len(frozen):].lstrip()

    frozen_normalized, _ = normalized(frozen)
    current_normalized, offsets = normalized(cumulative)
    anchor = frozen_normalized[-12:]
    if anchor:
        positions = [
            index
            for index in range(len(current_normalized) - len(anchor) + 1)
            if current_normalized.startswith(anchor, index)
        ]
        if len(positions) == 1:
            end = positions[0] + len(anchor)
            cut = offsets[end - 1] if offsets else 0
            return cumulative[cut:].lstrip()

    common = 0
    for left, right in zip(frozen, cumulative):
        if left != right:
            break
        common += 1
    threshold = min(4, len(frozen))
    if common >= threshold:
        return cumulative[common:].lstrip()
    return ""


async def run_v5_session(ws: WebSocket) -> None:
    await ws.accept()
    if not settings.qwen_stream_enabled:
        await ws.close(code=1011, reason="qwen stream is disabled")
        return
    lease = await _BACKEND_POOL.try_acquire()
    if lease is None:
        await ws.close(code=1013, reason="qwen stream session capacity exhausted")
        return

    client: QwenStreamClient | None = None
    pending_pcm = bytearray()
    pending_samples = 0
    received_samples = 0
    last_finished_sample = 0
    session_start_sample = 0
    generation = 0
    segment: Segment | None = None
    frozen_text = ""
    silence_chunks = 0
    close_code: int | None = None
    close_reason: str | None = None

    async def emit(text: str, finished: bool) -> None:
        nonlocal last_finished_sample
        end = round(received_samples / _SAMPLE_RATE, 2)
        bg = (
            round(segment.start_sample / _SAMPLE_RATE, 2)
            if segment is not None
            else round(last_finished_sample / _SAMPLE_RATE, 2)
        )
        await ws.send_json(
            {
                "key": segment.key if segment else f"segment-{generation + 1:06d}",
                "text": text,
                "finished": finished,
                "bg": bg,
                "ed": end,
            }
        )
        if finished:
            last_finished_sample = received_samples

    try:
        client = QwenStreamClient(
            lease.base_url,
            connect_timeout_seconds=settings.qwen_stream_connect_timeout_ms / 1000,
            chunk_timeout_seconds=settings.qwen_stream_chunk_timeout_ms / 1000,
        )
        session_id = await client.start()
        _telemetry(
            "v5_stream_started",
            session_id=session_id,
            model="Qwen3-ASR-0.6B",
            backend_index=lease.index,
            backend_url=lease.base_url,
            chunk_samples=_QWEN_CHUNK_SAMPLES,
            pool_capacity=_BACKEND_POOL.capacity,
            backend_capacity=_BACKEND_POOL.capacity_per_backend,
        )
        while True:
            try:
                chunk = await ws.receive_bytes()
            except WebSocketDisconnect:
                break
            if not chunk:
                continue
            if len(chunk) % 2:
                close_code, close_reason = 1003, "PCM16 payload must contain whole samples"
                break
            samples = len(chunk) // 2
            if samples > _MAX_FRAME_SAMPLES:
                close_code, close_reason = 1009, "PCM frame exceeds maximum segment size"
                break
            pending_pcm.extend(chunk)
            pending_samples += samples

            while pending_samples >= _QWEN_CHUNK_SAMPLES:
                block = bytes(pending_pcm[:_QWEN_CHUNK_BYTES])
                del pending_pcm[:_QWEN_CHUNK_BYTES]
                pending_samples -= _QWEN_CHUNK_SAMPLES
                received_samples += _QWEN_CHUNK_SAMPLES

                audio = np.frombuffer(block, dtype="<i2").astype(np.float32) / 32768.0
                rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
                update = await client.chunk(audio.astype("<f4", copy=False).tobytes())
                suffix = _suffix_after_frozen(frozen_text, update.text)
                if suffix and segment is None:
                    generation += 1
                    segment = Segment(
                        generation=generation,
                        start_sample=max(
                            last_finished_sample,
                            received_samples - _QWEN_CHUNK_SAMPLES,
                        ),
                    )
                    # 首字之前的静音属于语音起点前的铺垫，
                    # 不计入新打开的字幕段。
                    silence_chunks = 0
                if suffix and segment is not None:
                    segment.text = suffix

                if rms < settings.qwen_stream_silence_rms:
                    silence_chunks += 1
                else:
                    silence_chunks = 0

                silence_finished = (
                    segment is not None
                    and silence_chunks >= settings.qwen_stream_silence_chunks
                )
                length_finished = segment is not None and len(segment.text) > 200
                segment_at_limit = (
                    segment is not None
                    and received_samples - segment.start_sample >= _MAX_SEGMENT_SAMPLES
                )
                idle_session_at_limit = (
                    segment is None
                    and received_samples - session_start_sample >= _MAX_SEGMENT_SAMPLES
                )
                if (
                    silence_finished
                    or length_finished
                    or segment_at_limit
                    or idle_session_at_limit
                ):
                    final_update = await client.finish()
                    final_suffix = _suffix_after_frozen(frozen_text, final_update.text)
                    if final_suffix and segment is None:
                        generation += 1
                        segment = Segment(
                            generation=generation,
                            start_sample=session_start_sample,
                        )
                    if final_suffix and segment is not None:
                        segment.text = final_suffix

                    if segment is not None:
                        await emit(segment.text, True)
                    _telemetry(
                        "v5_segment_finished",
                        segment_key=segment.key if segment is not None else None,
                        language=final_update.language or update.language,
                        start_sample=(
                            segment.start_sample if segment is not None else session_start_sample
                        ),
                        end_sample=received_samples,
                        trigger=(
                            "silence"
                            if silence_finished
                            else (
                                "length"
                                if length_finished
                                else (
                                    "segment_limit"
                                    if segment_at_limit
                                    else "session_limit"
                                )
                            )
                        ),
                    )
                    session_id = await client.start()
                    _telemetry(
                        "v5_stream_restarted",
                        session_id=session_id,
                        start_sample=received_samples,
                    )
                    session_start_sample = received_samples
                    frozen_text = ""
                    segment = None
                    silence_chunks = 0
                else:
                    await emit(segment.text if segment is not None else "", False)
    except QwenStreamError as exc:
        close_code, close_reason = 1011, str(exc)
        _telemetry("v5_stream_failed", error_type=type(exc).__name__)
    finally:
        try:
            if client is not None:
                await client.finish()
        except QwenStreamError as exc:
            _telemetry("v5_finish_failed", error_type=type(exc).__name__)
        try:
            if client is not None:
                await client.close()
        finally:
            await lease.release()
        update_stat("online")
        _telemetry(
            "v5_stream_closed",
            received_seconds=round(received_samples / _SAMPLE_RATE, 3),
            finished_segments=generation if segment is None else generation - 1,
        )
        if ws.client_state.name == "CONNECTED" and ws.application_state.name == "CONNECTED":
            if close_code is None:
                await ws.close()
            else:
                await ws.close(code=close_code, reason=close_reason)
