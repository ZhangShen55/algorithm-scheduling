from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from fastapi import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect


class OperatorWebSocket(Protocol):
    async def send(self, message: bytes | str) -> None: ...

    async def recv(self) -> bytes | str: ...


class AsrWebSocketConnector(Protocol):
    def connect(self, url: str) -> AbstractAsyncContextManager[OperatorWebSocket]: ...


class WebsocketsAsrConnector:
    def connect(self, url: str) -> AbstractAsyncContextManager[Any]:
        return connect(url, open_timeout=10, ping_interval=20, ping_timeout=20)


def operator_websocket_url(service_url: str) -> str:
    normalized = service_url.rstrip("/")
    if normalized.startswith("https://"):
        normalized = f"wss://{normalized.removeprefix('https://')}"
    elif normalized.startswith("http://"):
        normalized = f"ws://{normalized.removeprefix('http://')}"
    elif not normalized.startswith(("ws://", "wss://")):
        raise ValueError("实时 ASR 算子地址协议不受支持")
    return f"{normalized}/v1.0.1/seacraft_asr_online"


async def proxy_websocket(
    downstream: WebSocket,
    upstream: OperatorWebSocket,
) -> None:
    async def client_to_operator() -> None:
        while True:
            message = await downstream.receive()
            if message["type"] == "websocket.disconnect":
                return
            binary = message.get("bytes")
            text = message.get("text")
            if binary is not None:
                await upstream.send(binary)
            elif text is not None:
                await upstream.send(text)

    async def operator_to_client() -> None:
        while True:
            message = await upstream.recv()
            if isinstance(message, bytes):
                await downstream.send_bytes(message)
            else:
                await downstream.send_text(message)

    tasks = {
        asyncio.create_task(client_to_operator(), name="asr-client-to-operator"),
        asyncio.create_task(operator_to_client(), name="asr-operator-to-client"),
    }
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        try:
            task.result()
        except WebSocketDisconnect:
            return
