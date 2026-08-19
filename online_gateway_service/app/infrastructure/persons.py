from __future__ import annotations

import asyncio
from typing import Any

import httpx

JsonObject = dict[str, Any]


class FacePersonClientError(RuntimeError):
    """FaceRec 人物管理接口不可用或返回了无效响应。"""


class FacePersonClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        base_url: str,
        hard_timeout_seconds: float,
    ) -> None:
        self._http_client = http_client
        self._base_url = base_url.rstrip("/")
        self._hard_timeout_seconds = hard_timeout_seconds

    async def create(self, request_body: JsonObject) -> JsonObject:
        return await self._request("POST", "/persons", json=request_body)

    async def create_batch(self, request_body: JsonObject) -> JsonObject:
        return await self._request("POST", "/persons/batch", json=request_body)

    async def list(self, *, skip: int, limit: int) -> JsonObject:
        return await self._request(
            "GET",
            "/persons",
            params={"skip": skip, "limit": limit},
        )

    async def search(self, request_body: JsonObject) -> JsonObject:
        return await self._request("POST", "/persons/search", json=request_body)

    async def delete(self, request_body: JsonObject) -> JsonObject:
        return await self._request("DELETE", "/persons/delete", json=request_body)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: JsonObject | None = None,
        params: dict[str, int] | None = None,
    ) -> JsonObject:
        try:
            response = await asyncio.wait_for(
                self._http_client.request(
                    method,
                    f"{self._base_url}{path}",
                    json=json,
                    params=params,
                ),
                timeout=self._hard_timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except (TimeoutError, httpx.HTTPError, ValueError) as exc:
            raise FacePersonClientError("FaceRec 人物管理接口调用失败") from exc
        if not isinstance(body, dict):
            raise FacePersonClientError("FaceRec 人物管理响应不是 JSON 对象")
        return body
