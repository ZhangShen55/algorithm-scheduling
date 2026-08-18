from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx


@dataclass(frozen=True, slots=True)
class OperatorRuntimeStatus:
    inflight: int
    model_ready: bool


@dataclass(frozen=True, slots=True)
class OperatorRegistryClientConfig:
    control_service_url: str
    instance_id: str
    operator_code: str
    capabilities: list[str]
    service_url: str
    declared_capacity: int
    management_token: str
    model_version: str | None = None
    api_version: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    heartbeat_interval_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.declared_capacity <= 0:
            raise ValueError("算子声明容量必须大于 0")
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("心跳间隔必须大于 0")
        if not self.management_token.strip():
            raise ValueError("算子注册管理令牌不能为空")


class OperatorRegistryClient:
    def __init__(
        self,
        config: OperatorRegistryClientConfig,
        *,
        status_provider: Callable[[], OperatorRuntimeStatus],
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._status_provider = status_provider
        self._http = http_client or httpx.AsyncClient(
            base_url=config.control_service_url,
            timeout=10,
        )
        self._stop_event = asyncio.Event()
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def register(self) -> None:
        response = await self._http.post(
            f"{self._config.control_service_url}/api/operator-instances/register",
            headers=self._management_headers,
            json={
                "instance_id": self._config.instance_id,
                "operator_code": self._config.operator_code,
                "capabilities": self._config.capabilities,
                "service_url": self._config.service_url,
                "model_version": self._config.model_version,
                "api_version": self._config.api_version,
                "declared_capacity": self._config.declared_capacity,
                "labels": self._config.labels,
            },
        )
        response.raise_for_status()

    async def heartbeat(self) -> None:
        runtime = self._status_provider()
        response = await self._http.post(
            f"{self._config.control_service_url}/api/operator-instances/heartbeat",
            headers=self._management_headers,
            json={
                "instance_id": self._config.instance_id,
                "inflight": runtime.inflight,
                "model_ready": runtime.model_ready,
            },
        )
        response.raise_for_status()

    async def drain(self) -> None:
        response = await self._http.post(
            f"{self._config.control_service_url}/api/operator-instances/lifecycle",
            headers=self._management_headers,
            json={
                "instance_id": self._config.instance_id,
                "lifecycle": "DRAINING",
            },
        )
        response.raise_for_status()

    async def unregister(self) -> None:
        response = await self._http.post(
            f"{self._config.control_service_url}/api/operator-instances/unregister",
            headers=self._management_headers,
            json={"instance_id": self._config.instance_id},
        )
        response.raise_for_status()

    @property
    def _management_headers(self) -> dict[str, str]:
        return {"X-Operator-Registry-Token": self._config.management_token}

    async def start(self) -> None:
        self._stop_event.clear()
        while True:
            try:
                await self.register()
                await self.heartbeat()
            except httpx.HTTPError:
                await asyncio.sleep(self._config.heartbeat_interval_seconds)
                continue
            break
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name=f"operator-heartbeat-{self._config.instance_id}",
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._heartbeat_task is not None:
            await self._heartbeat_task
            self._heartbeat_task = None
        await self.unregister()

    async def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._config.heartbeat_interval_seconds,
                )
            # Python 3.10 keeps this distinct from the built-in TimeoutError.
            except asyncio.TimeoutError:  # noqa: UP041
                pass
            if self._stop_event.is_set():
                return
            try:
                await self.heartbeat()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    continue
                try:
                    await self.register()
                    await self.heartbeat()
                except httpx.HTTPError:
                    continue
            except httpx.HTTPError:
                continue

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> OperatorRegistryClient:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        try:
            await self.stop()
        finally:
            await self.aclose()
