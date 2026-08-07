import asyncio
import logging
from typing import Optional

import requests

from ..core.settings import settings
from .worker_state import BatchAdmissionController


logger = logging.getLogger(__name__)


class TiasRegistrationClient:
    def __init__(self, controller: BatchAdmissionController):
        self.controller = controller
        self.orchestrator_base_url = str(
            getattr(settings, "AiQualityBaseUrl", "") or ""
        ).rstrip("/")
        self.heartbeat_interval_seconds = int(getattr(settings, "HeartbeatIntervalSeconds", 5))
        self.heartbeat_timeout_seconds = int(getattr(settings, "HeartbeatTimeoutSeconds", 15))
        self.register_retry_interval_seconds = int(getattr(settings, "RegisterRetryIntervalSeconds", 5))
        self._stop_event: Optional[asyncio.Event] = None
        self._task: Optional[asyncio.Task] = None

    def start_background(self) -> None:
        if not self.orchestrator_base_url:
            logger.info("TIAS 注册跳过，未配置 AiQualityBaseUrl")
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop())

    async def stop_background(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            await self._task
        await self.unregister()

    async def _run_loop(self) -> None:
        registered = False
        while self._stop_event is not None and not self._stop_event.is_set():
            try:
                if not registered:
                    await self.register()
                    registered = True
                await self.heartbeat()
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.heartbeat_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue
            except Exception as exc:
                logger.warning(
                    "TIAS 注册心跳失败 instance_id=%s reason=%s",
                    self.controller.instance_id,
                    exc,
                )
                registered = False
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.register_retry_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    continue

    async def register(self) -> None:
        payload = self._payload()
        await asyncio.to_thread(
            self._post,
            "/api/tias/instances/register",
            payload,
        )
        logger.info(
            "TIAS 注册成功 instance_id=%s base_url=%s",
            self.controller.instance_id,
            self.controller.base_url,
        )

    async def heartbeat(self) -> None:
        await asyncio.to_thread(
            self._post,
            "/api/tias/instances/heartbeat",
            self._payload(),
        )

    async def unregister(self) -> None:
        if not self.orchestrator_base_url:
            return
        payload = {
            "instance_id": self.controller.instance_id,
            "base_url": self.controller.base_url,
            "status": "DOWN",
        }
        try:
            await asyncio.to_thread(self._post, "/api/tias/instances/unregister", payload)
        except Exception as exc:
            logger.warning(
                "TIAS 注销失败 instance_id=%s reason=%s",
                self.controller.instance_id,
                exc,
            )

    def _payload(self) -> dict:
        payload = self.controller.snapshot()
        payload["heartbeat_timeout_seconds"] = self.heartbeat_timeout_seconds
        return payload

    def _post(self, path: str, payload: dict) -> None:
        url = f"{self.orchestrator_base_url}{path}"
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
