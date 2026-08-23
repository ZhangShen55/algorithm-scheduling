from __future__ import annotations

import asyncio
import hashlib
import json
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

    def chunks(self) -> tuple[bytes, ...]:
        return tuple(
            self.pcm[offset : offset + self.chunk_bytes]
            for offset in range(0, len(self.pcm), self.chunk_bytes)
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


class RealtimeAsrRunner:
    def __init__(
        self,
        targets: NorthboundTargets,
        *,
        connect: ConnectCallable = _default_connect,
        sleep: SleepCallable = asyncio.sleep,
        max_concurrency: int = 150,
        session_timeout_seconds: float = 14_400,
        final_response_grace_seconds: float = 1.0,
    ) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency 必须为正数")
        if session_timeout_seconds <= 0:
            raise ValueError("session_timeout_seconds 必须为有界正数")
        if final_response_grace_seconds <= 0:
            raise ValueError("final_response_grace_seconds 必须为有界正数")
        self._url = targets.gateway_websocket_url("/api/online/asr/stream")
        self._connect = connect
        self._sleep = sleep
        self._max_concurrency = max_concurrency
        self._session_timeout_seconds = session_timeout_seconds
        self._final_response_grace_seconds = final_response_grace_seconds

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
        chunks = fixture.chunks()
        sent = 0
        try:
            async with asyncio.timeout(self._session_timeout_seconds):
                async with self._connect(
                    self._url,
                    {"X-Trace-ID": spec.trace_id},
                ) as socket:
                    receiving = asyncio.create_task(
                        self._receive(socket, messages),
                        name=f"asr-receiver-{spec.session_id}",
                    )
                    try:
                        for index, chunk in enumerate(chunks):
                            await socket.send(chunk)
                            sent += 1
                            await asyncio.sleep(0)
                            if index < len(chunks) - 1:
                                await self._sleep(fixture.chunk_duration_seconds)
                        # 算子没有显式 EOF 上行帧；末块后保留有限窗口，避免刚生成的字幕
                        # 在客户端主动断连时被 receiver 取消而丢失。
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(receiving),
                                timeout=self._final_response_grace_seconds,
                            )
                        except TimeoutError:
                            pass
                    finally:
                        if not receiving.done():
                            receiving.cancel()
                        await asyncio.gather(receiving, return_exceptions=True)
        except TimeoutError:
            category = ResultCategory.TIMEOUT
        except Exception:
            category = ResultCategory.CONNECTION_FAILURE
        if any(_is_capacity_message(message) for message in messages):
            category = ResultCategory.OVERLOAD
        return AsrSessionResult(
            session_id=spec.session_id,
            trace_id=spec.trace_id,
            category=category,
            sent_chunk_count=sent,
            message_digests=tuple(_message_digest(message) for message in messages),
            finished_message_count=sum(
                1 for message in messages if _is_finished_message(message)
            ),
        )

    @staticmethod
    async def _receive(socket: AsrSocket, messages: list[bytes | str]) -> None:
        while True:
            try:
                message = await socket.recv()
            except (StopAsyncIteration, EOFError):
                return
            messages.append(message)


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
