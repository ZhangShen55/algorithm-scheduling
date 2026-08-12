import asyncio
from typing import Any

import pytest


def _readiness(*args: object, **kwargs: object) -> Any:
    try:
        from app.core.readiness import FaceRecReadiness
    except ModuleNotFoundError:
        pytest.fail("FaceRec readiness 尚未实现")
    return FaceRecReadiness(*args, **kwargs)


class FakeDatabase:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def command(self, command: object) -> dict[str, int]:
        assert command == {"ping": 1}
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {"ok": 1}


class FakeEmbeddingModel:
    def __init__(self, initialized: bool = True) -> None:
        self.initialized = initialized


def test_readiness_requires_mongodb_and_existing_arcface_model() -> None:
    database = FakeDatabase()
    readiness = _readiness(
        database,
        FakeEmbeddingModel(),
        dlib_workers_ready=True,
        timeout_seconds=0.1,
    )

    assert asyncio.run(readiness.check()) is True
    assert readiness.model_ready() is True
    assert database.calls == 1


def test_readiness_is_false_when_mongodb_ping_fails() -> None:
    readiness = _readiness(
        FakeDatabase(RuntimeError("MongoDB 密码错误")),
        FakeEmbeddingModel(),
        dlib_workers_ready=True,
        timeout_seconds=0.1,
    )

    assert asyncio.run(readiness.check()) is False
    assert readiness.model_ready() is False


def test_readiness_is_false_when_arcface_model_is_unavailable() -> None:
    readiness = _readiness(
        FakeDatabase(),
        FakeEmbeddingModel(initialized=False),
        dlib_workers_ready=True,
        timeout_seconds=0.1,
    )

    assert asyncio.run(readiness.check()) is False
    assert readiness.model_ready() is False


def test_readiness_is_false_until_all_dlib_workers_are_ready() -> None:
    readiness = _readiness(
        FakeDatabase(),
        FakeEmbeddingModel(),
        dlib_workers_ready=False,
        timeout_seconds=0.1,
    )

    assert asyncio.run(readiness.check()) is False
    assert readiness.model_ready() is False
    assert readiness.dlib_workers_ready() is False

    readiness.set_dlib_workers_ready(True)

    assert asyncio.run(readiness.check()) is True
    assert readiness.model_ready() is True
