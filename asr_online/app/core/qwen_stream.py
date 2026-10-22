"""Adapter for the official Qwen3-ASR stateful streaming HTTP demo."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx


@dataclass(frozen=True)
class QwenStreamUpdate:
    text: str
    language: str


class QwenStreamError(RuntimeError):
    """Raised when the Qwen streaming service cannot satisfy the session."""


@dataclass
class QwenBackendLease:
    index: int
    base_url: str
    _release: Callable[["QwenBackendLease"], Awaitable[None]]
    released: bool = False

    async def release(self) -> None:
        if self.released:
            return
        await self._release(self)
        self.released = True


class QwenBackendPool:
    """在固定 Qwen 后端上预留有状态会话。"""

    def __init__(
        self,
        base_urls: tuple[str, ...] | list[str],
        *,
        capacity_per_backend: int = 1,
        total_capacity: int | None = None,
    ):
        normalized = tuple(dict.fromkeys(url.rstrip("/") for url in base_urls))
        if not normalized or any(not url.startswith(("http://", "https://")) for url in normalized):
            raise ValueError("backend URLs must contain at least one HTTP(S) URL")
        if capacity_per_backend < 1:
            raise ValueError("capacity_per_backend must be positive")
        available_capacity = len(normalized) * capacity_per_backend
        if total_capacity is not None and total_capacity < 1:
            raise ValueError("total_capacity must be positive")
        self.base_urls = normalized
        self.capacity_per_backend = capacity_per_backend
        self.capacity = min(total_capacity or available_capacity, available_capacity)
        self.active = [0] * len(self.base_urls)
        self._cursor = 0
        self._lock = asyncio.Lock()

    async def try_acquire(self) -> QwenBackendLease | None:
        async with self._lock:
            if sum(self.active) >= self.capacity:
                return None
            for offset in range(len(self.base_urls)):
                index = (self._cursor + offset) % len(self.base_urls)
                if self.active[index] < self.capacity_per_backend:
                    self.active[index] += 1
                    self._cursor = (index + 1) % len(self.base_urls)
                    return QwenBackendLease(
                        index=index,
                        base_url=self.base_urls[index],
                        _release=self._release,
                    )
        return None

    async def _release(self, lease: QwenBackendLease) -> None:
        async with self._lock:
            if self.active[lease.index] <= 0:
                raise RuntimeError("qwen backend released without a lease")
            self.active[lease.index] -= 1


class QwenStreamClient:
    def __init__(
        self,
        base_url: str,
        *,
        connect_timeout_seconds: float,
        chunk_timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.chunk_timeout_seconds = chunk_timeout_seconds
        timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=chunk_timeout_seconds,
            write=chunk_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)
        self.session_id: str | None = None

    async def start(self) -> str:
        self.session_id = None
        try:
            response = await self._client.post(f"{self.base_url}/api/start")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("stream start response must be an object")
            session_id = str(payload.get("session_id", "") or "")
        except (httpx.HTTPError, ValueError) as exc:
            raise QwenStreamError(f"stream start failed: {type(exc).__name__}") from exc
        if not session_id:
            raise QwenStreamError("stream start returned no session_id")
        self.session_id = session_id
        return session_id

    async def chunk(self, pcm_float32: bytes) -> QwenStreamUpdate:
        if self.session_id is None:
            raise QwenStreamError("stream chunk submitted before start")
        try:
            response = await self._client.post(
                f"{self.base_url}/api/chunk",
                params={"session_id": self.session_id},
                content=pcm_float32,
                headers={"Content-Type": "application/octet-stream"},
                timeout=self.chunk_timeout_seconds,
            )
            response.raise_for_status()
            return _parse_update(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise QwenStreamError(f"stream chunk failed: {type(exc).__name__}") from exc

    async def finish(self) -> QwenStreamUpdate:
        if self.session_id is None:
            return QwenStreamUpdate("", "")
        session_id = self.session_id
        self.session_id = None
        try:
            response = await self._client.post(
                f"{self.base_url}/api/finish",
                params={"session_id": session_id},
                timeout=self.chunk_timeout_seconds,
            )
            response.raise_for_status()
            return _parse_update(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise QwenStreamError(f"stream finish failed: {type(exc).__name__}") from exc

    async def close(self) -> None:
        await self._client.aclose()


def _parse_update(payload: object) -> QwenStreamUpdate:
    if not isinstance(payload, dict):
        raise ValueError("stream response must be an object")
    text = str(payload.get("text", "") or "")
    language = str(payload.get("language", "") or "")
    prefix = "language "
    if text.lower().startswith(prefix) and "<asr_text>" in text:
        text = text.split("<asr_text>", 1)[1]
    return QwenStreamUpdate(text=text, language=language)
