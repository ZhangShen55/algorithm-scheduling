import asyncio
from typing import Any


class FaceRecReadiness:
    def __init__(
        self,
        database: Any,
        embedding_model: Any,
        *,
        dlib_workers_ready: bool = False,
        timeout_seconds: float = 2.0,
    ) -> None:
        self._database = database
        self._embedding_model = embedding_model
        self._timeout_seconds = timeout_seconds
        self._database_ready = False
        self._embedding_model_ready = False
        self._dlib_workers_ready = dlib_workers_ready
        self._ready = False

    async def check(self) -> bool:
        self._embedding_model_ready = self._embedding_model is not None and getattr(
            self._embedding_model,
            "initialized",
            True,
        ) is not False
        try:
            await asyncio.wait_for(
                self._database.command({"ping": 1}),
                timeout=self._timeout_seconds,
            )
            self._database_ready = True
        except Exception:
            self._database_ready = False
        self._ready = (
            self._database_ready
            and self._embedding_model_ready
            and self._dlib_workers_ready
        )
        return self._ready

    def model_ready(self) -> bool:
        return self._ready

    def database_ready(self) -> bool:
        return self._database_ready

    def embedding_model_ready(self) -> bool:
        return self._embedding_model_ready

    def set_dlib_workers_ready(self, ready: bool) -> None:
        self._dlib_workers_ready = ready
        if not ready:
            self._ready = False

    def dlib_workers_ready(self) -> bool:
        return self._dlib_workers_ready
