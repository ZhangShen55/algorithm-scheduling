from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol

from websockets.asyncio.client import connect as websocket_connect

from .core import NorthboundTargets, ReproducibleIdentity, ResultCategory


class AsrSocket(Protocol):
    async def send(self, message: bytes | str) -> None: ...

    async def recv(self) -> bytes | str: ...


ConnectCallable = Callable[
    [str, dict[str, str]],
    AbstractAsyncContextManager[AsrSocket],
]
SleepCallable = Callable[[float], Awaitable[None]]
ClockCallable = Callable[[], float]

ASR_ONLINE_SAMPLE_RATE_HZ = 16_000
ASR_ONLINE_SAMPLE_WIDTH_BYTES = 2
ASR_ONLINE_CHANNELS = 1
ASR_ONLINE_CHUNK_SAMPLES = 7_680
ASR_ONLINE_CHUNK_DURATION_SECONDS = (
    ASR_ONLINE_CHUNK_SAMPLES / ASR_ONLINE_SAMPLE_RATE_HZ
)
ASR_ONLINE_CHUNK_BYTES = (
    ASR_ONLINE_CHUNK_SAMPLES
    * ASR_ONLINE_SAMPLE_WIDTH_BYTES
    * ASR_ONLINE_CHANNELS
)
ASR_ONLINE_TAIL_SILENCE_CHUNKS = 6


def _default_connect(
    url: str,
    headers: dict[str, str],
) -> AbstractAsyncContextManager[AsrSocket]:
    return websocket_connect(url, additional_headers=headers)


@dataclass(frozen=True)
class AudioStreamFixture:
    pcm: bytes
    sample_rate_hz: int
    sample_width_bytes: int
    channels: int
    chunk_duration_seconds: float

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0 or self.sample_width_bytes <= 0 or self.channels <= 0:
            raise ValueError("PCM 音频参数必须为正数")
        if self.chunk_duration_seconds <= 0:
            raise ValueError("实时音频分块周期必须为正数")
        if not self.pcm or len(self.pcm) % self.frame_width != 0:
            raise ValueError("PCM 内容必须按采样帧对齐")
        if self.chunk_bytes <= 0 or self.chunk_bytes % self.frame_width != 0:
            raise ValueError("实时分块必须按采样帧对齐")

    @property
    def frame_width(self) -> int:
        return self.sample_width_bytes * self.channels

    @property
    def chunk_bytes(self) -> int:
        return round(
            self.sample_rate_hz
            * self.frame_width
            * self.chunk_duration_seconds
        )

    @property
    def media_duration_seconds(self) -> float:
        return len(self.pcm) / self.frame_width / self.sample_rate_hz

    def chunks(self) -> tuple[bytes, ...]:
        chunks = []
        for offset in range(0, len(self.pcm), self.chunk_bytes):
            chunk = self.pcm[offset : offset + self.chunk_bytes]
            chunks.append(chunk.ljust(self.chunk_bytes, b"\x00"))
        return tuple(chunks)


def build_asr_online_fixture(
    *,
    pcm: bytes,
    sample_rate_hz: int,
    sample_width_bytes: int,
    channels: int,
) -> AudioStreamFixture:
    if (
        sample_rate_hz != ASR_ONLINE_SAMPLE_RATE_HZ
        or sample_width_bytes != ASR_ONLINE_SAMPLE_WIDTH_BYTES
        or channels != ASR_ONLINE_CHANNELS
    ):
        raise ValueError("实时 ASR 必须使用 16 kHz 单声道 signed 16-bit PCM")
    return AudioStreamFixture(
        pcm=pcm,
        sample_rate_hz=sample_rate_hz,
        sample_width_bytes=sample_width_bytes,
        channels=channels,
        chunk_duration_seconds=ASR_ONLINE_CHUNK_DURATION_SECONDS,
    )


@dataclass(frozen=True)
class AsrSessionSpec:
    session_id: str
    trace_id: str
    reconnect_of: str | None = None


def build_session_specs(
    identity: ReproducibleIdentity,
    case_id: str,
    session_count: int,
) -> tuple[AsrSessionSpec, ...]:
    if session_count <= 0:
        raise ValueError("session_count 必须为正数")
    return tuple(
        AsrSessionSpec(
            session_id=identity.request_id(case_id, index),
            trace_id=identity.trace_id(case_id, index),
        )
        for index in range(session_count)
    )


def build_reconnect_specs(
    identity: ReproducibleIdentity,
    case_id: str,
    originals: Sequence[AsrSessionSpec],
) -> tuple[AsrSessionSpec, ...]:
    if not originals:
        raise ValueError("重连用例至少需要一个原会话")
    offset = len(originals)
    return tuple(
        AsrSessionSpec(
            session_id=identity.request_id(f"{case_id}-RECONNECT", offset + index),
            trace_id=identity.trace_id(f"{case_id}-RECONNECT", offset + index),
            reconnect_of=original.session_id,
        )
        for index, original in enumerate(originals)
    )


def realtime_asr_session_tiers() -> tuple[int, ...]:
    return (1, 10, 24, 30, 60, 90, 150)


@dataclass(frozen=True)
class AsrSessionLevel:
    session_count: int
    within_declared_capacity: bool


def realtime_asr_session_ladder(
    declared_capacity: int = 30,
) -> tuple[AsrSessionLevel, ...]:
    if declared_capacity <= 0:
        raise ValueError("声明 ASR 容量必须为正数")
    return tuple(
        AsrSessionLevel(
            session_count=count,
            within_declared_capacity=count <= declared_capacity,
        )
        for count in realtime_asr_session_tiers()
    )


@dataclass(frozen=True)
class AsrSessionResult:
    session_id: str
    trace_id: str
    category: ResultCategory
    sent_chunk_count: int
    message_digests: tuple[str, ...]
    finished_message_count: int = 0
    sent_media_chunk_count: int = 0
    sent_tail_silence_chunk_count: int = 0
    planned_media_duration_seconds: float = 0.0
    sent_media_duration_seconds: float = 0.0
    send_elapsed_seconds: float = 0.0
    realtime_factor: float = 0.0
    max_positive_schedule_drift_seconds: float = 0.0

    @property
    def chunk_counts_consistent(self) -> bool:
        return self.sent_chunk_count == (
            self.sent_media_chunk_count + self.sent_tail_silence_chunk_count
        )


class RealtimeAsrRunner:
    def __init__(
        self,
        targets: NorthboundTargets,
        *,
        connect: ConnectCallable = _default_connect,
        sleep: SleepCallable = asyncio.sleep,
        clock: ClockCallable = time.monotonic,
        max_concurrency: int = 150,
        session_timeout_seconds: float = 14_400,
        final_response_grace_seconds: float = 5.0,
        tail_silence_chunk_count: int = ASR_ONLINE_TAIL_SILENCE_CHUNKS,
        max_schedule_drift_seconds: float = ASR_ONLINE_CHUNK_DURATION_SECONDS,
    ) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency 必须为正数")
        if not math.isfinite(session_timeout_seconds) or session_timeout_seconds <= 0:
            raise ValueError("session_timeout_seconds 必须为有界正数")
        if (
            not math.isfinite(final_response_grace_seconds)
            or final_response_grace_seconds <= 0
        ):
            raise ValueError("final_response_grace_seconds 必须为有界正数")
        if (
            type(tail_silence_chunk_count) is not int
            or not 0 <= tail_silence_chunk_count <= 12
        ):
            raise ValueError("tail_silence_chunk_count 必须为 0 到 12 的整数")
        if not math.isfinite(max_schedule_drift_seconds) or max_schedule_drift_seconds <= 0:
            raise ValueError("max_schedule_drift_seconds 必须为有界正数")
        self._url = targets.gateway_websocket_url("/api/online/asr/stream")
        self._connect = connect
        self._sleep = sleep
        self._clock = clock
        self._max_concurrency = max_concurrency
        self._session_timeout_seconds = session_timeout_seconds
        self._final_response_grace_seconds = final_response_grace_seconds
        self._tail_silence_chunk_count = tail_silence_chunk_count
        self._max_schedule_drift_seconds = max_schedule_drift_seconds

    async def run_sessions(
        self,
        specs: Sequence[AsrSessionSpec],
        fixture: AudioStreamFixture,
    ) -> tuple[AsrSessionResult, ...]:
        if not specs:
            raise ValueError("ASR 阶梯至少需要一个会话")
        if len({spec.session_id for spec in specs}) != len(specs) or len(
            {spec.trace_id for spec in specs}
        ) != len(specs):
            raise ValueError("ASR 会话或追踪 ID 重复")
        results: list[AsrSessionResult] = []
        for start in range(0, len(specs), self._max_concurrency):
            batch = specs[start : start + self._max_concurrency]
            results.extend(
                await asyncio.gather(
                    *(self._run_session(spec, fixture) for spec in batch)
                )
            )
        return tuple(results)

    async def _run_session(
        self,
        spec: AsrSessionSpec,
        fixture: AudioStreamFixture,
    ) -> AsrSessionResult:
        messages: list[bytes | str] = []
        category = ResultCategory.SUCCESS
        media_chunks = fixture.chunks()
        tail_silence_chunks = (
            bytes(fixture.chunk_bytes),
        ) * self._tail_silence_chunk_count
        chunks = (*media_chunks, *tail_silence_chunks)
        sent = 0
        sent_media = 0
        sent_tail_silence = 0
        complete_utterance = asyncio.Event()
        overload = asyncio.Event()
        receiver_error: BaseException | None = None
        max_positive_drift = 0.0
        send_started_at = self._clock()
        send_finished_at = send_started_at
        try:
            async with asyncio.timeout(self._session_timeout_seconds):
                async with self._connect(
                    self._url,
                    {"X-Trace-ID": spec.trace_id},
                ) as socket:
                    receiving = asyncio.create_task(
                        self._receive(socket, messages, complete_utterance, overload),
                        name=f"asr-receiver-{spec.session_id}",
                    )
                    # WebSocket 建连耗时不属于媒体分块的实时节拍漂移。
                    send_started_at = self._clock()
                    send_finished_at = send_started_at
                    try:
                        for index, chunk in enumerate(chunks):
                            if overload.is_set():
                                break
                            deadline = (
                                send_started_at
                                + index * fixture.chunk_duration_seconds
                            )
                            delay = deadline - self._clock()
                            if delay > 0:
                                await self._sleep(delay)
                            max_positive_drift = max(
                                max_positive_drift,
                                max(0.0, self._clock() - deadline),
                            )
                            if max_positive_drift > self._max_schedule_drift_seconds:
                                category = ResultCategory.LOAD_GENERATOR_FAILURE
                                break
                            receiver_error = self._receiver_error(receiving)
                            if receiver_error is not None or overload.is_set():
                                break
                            await socket.send(chunk)
                            send_finished_at = self._clock()
                            sent += 1
                            if index < len(media_chunks):
                                sent_media += 1
                            else:
                                sent_tail_silence += 1
                            await asyncio.sleep(0)
                            receiver_error = self._receiver_error(receiving)
                            if receiver_error is not None or overload.is_set():
                                break
                        # 现有协议没有 EOS；尾部静音只用于触发完整语句边界，随后仍有界等待。
                        if (
                            category is ResultCategory.SUCCESS
                            and not complete_utterance.is_set()
                            and not receiving.done()
                        ):
                            try:
                                await asyncio.wait_for(
                                    complete_utterance.wait(),
                                    timeout=self._final_response_grace_seconds,
                                )
                            except TimeoutError:
                                pass
                    finally:
                        settled_error = await self._settle_receiver(receiving)
                        if receiver_error is None:
                            receiver_error = settled_error
        except TimeoutError:
            category = ResultCategory.TIMEOUT
        except Exception:
            category = ResultCategory.CONNECTION_FAILURE
        if receiver_error is not None:
            category = ResultCategory.CONNECTION_FAILURE
        if any(_is_capacity_message(message) for message in messages):
            category = ResultCategory.OVERLOAD
        send_elapsed = max(0.0, send_finished_at - send_started_at)
        scheduled_span = max(0.0, (sent - 1) * fixture.chunk_duration_seconds)
        return AsrSessionResult(
            session_id=spec.session_id,
            trace_id=spec.trace_id,
            category=category,
            sent_chunk_count=sent,
            message_digests=tuple(_message_digest(message) for message in messages),
            finished_message_count=sum(
                1 for message in messages if _is_finished_message(message)
            ),
            sent_media_chunk_count=sent_media,
            sent_tail_silence_chunk_count=sent_tail_silence,
            planned_media_duration_seconds=fixture.media_duration_seconds,
            sent_media_duration_seconds=min(
                fixture.media_duration_seconds,
                sent_media * fixture.chunk_duration_seconds,
            ),
            send_elapsed_seconds=send_elapsed,
            realtime_factor=(send_elapsed / scheduled_span if scheduled_span > 0 else 0.0),
            max_positive_schedule_drift_seconds=max_positive_drift,
        )

    @staticmethod
    def _receiver_error(receiving: asyncio.Task[None]) -> BaseException | None:
        if not receiving.done():
            return None
        try:
            receiving.result()
        except BaseException as error:
            return error
        return None

    @staticmethod
    async def _settle_receiver(receiving: asyncio.Task[None]) -> BaseException | None:
        cancelled_by_runner = not receiving.done()
        if cancelled_by_runner:
            receiving.cancel()
        try:
            await receiving
        except asyncio.CancelledError as error:
            return None if cancelled_by_runner else error
        except Exception as error:
            return error
        return None

    @staticmethod
    async def _receive(
        socket: AsrSocket,
        messages: list[bytes | str],
        complete_utterance: asyncio.Event,
        overload: asyncio.Event,
    ) -> None:
        while True:
            try:
                message = await socket.recv()
            except (StopAsyncIteration, EOFError):
                return
            messages.append(message)
            if _is_capacity_message(message):
                overload.set()
                complete_utterance.set()
            elif _is_finished_message(message):
                complete_utterance.set()


def _message_digest(message: bytes | str) -> str:
    raw = message if isinstance(message, bytes) else message.encode()
    return hashlib.sha256(raw).hexdigest()


def _is_capacity_message(message: bytes | str) -> bool:
    try:
        raw = message.decode() if isinstance(message, bytes) else message
        body = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return False
    return isinstance(body, dict) and body.get("code") == 50301


def _is_finished_message(message: bytes | str) -> bool:
    try:
        raw = message.decode() if isinstance(message, bytes) else message
        body = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return False
    return isinstance(body, dict) and body.get("finished") is True
