import asyncio
from typing import Any


class FaceRecReadiness:
    def __init__(
        self,
        database: Any,
        embedding_model: Any,
        *,
        timeout_seconds: float = 2.0,
    ) -> None:
        self._database = database
        self._embedding_model = embedding_model
        self._timeout_seconds = timeout_seconds
        self._database_ready = False
        self._embedding_model_ready = False
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
        self._ready = self._database_ready and self._embedding_model_ready
        return self._ready

    def model_ready(self) -> bool:
        return self._ready

    def database_ready(self) -> bool:
        return self._database_ready

    def embedding_model_ready(self) -> bool:
        return self._embedding_model_ready
