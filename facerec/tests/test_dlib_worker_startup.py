import asyncio
import os
import queue
import time

import pytest
from app.core import ai_engine, dlib_worker
from app.main import MAX_WORKERS, _begin_dlib_shutdown, app, lifespan
from app.router import ops
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.operator_registry_client.runtime import _wrap_lifespan


class StatusQueue:
    def __init__(self) -> None:
        self.messages: list[tuple[int, bool, str | None]] = []

    def put(self, message: tuple[int, bool, str | None]) -> None:
        self.messages.append(message)


def test_dlib_worker_initializer_reports_and_raises_model_load_failure(
    monkeypatch,
) -> None:
    status_queue = StatusQueue()
    monkeypatch.setattr(dlib_worker.dlib, "get_frontal_face_detector", lambda: object())

    def fail_to_load(_: str) -> object:
        raise RuntimeError("shape predictor 损坏")

    monkeypatch.setattr(dlib_worker.dlib, "shape_predictor", fail_to_load)

    with pytest.raises(RuntimeError, match="shape predictor 损坏"):
        dlib_worker.init_worker(status_queue, object(), "predictor.dat")

    assert len(status_queue.messages) == 1
    assert status_queue.messages[0][1:3] == (False, "shape predictor 损坏")


def test_collect_dlib_worker_status_requires_every_worker() -> None:
    status_queue: queue.Queue[tuple[int, bool, str | None, dict[str, object]]] = (
        queue.Queue()
    )
    status_queue.put((101, True, None, {"pid": 101}))
    status_queue.put((102, True, None, {"pid": 102}))

    dlib_worker.collect_startup_status(
        status_queue,
        expected_workers=2,
        timeout_seconds=0.1,
    )


def test_collect_dlib_worker_status_rejects_any_failed_worker() -> None:
    status_queue: queue.Queue[tuple[int, bool, str | None, dict[str, object]]] = (
        queue.Queue()
    )
    status_queue.put((101, True, None, {"pid": 101}))
    status_queue.put((102, False, "shape predictor 缺失", {"pid": 102}))

    with pytest.raises(RuntimeError, match="shape predictor 缺失"):
        dlib_worker.collect_startup_status(
            status_queue,
            expected_workers=2,
            timeout_seconds=0.1,
        )


def test_lifespan_prewarms_all_real_workers_and_cleans_up() -> None:
    with TestClient(app) as client:
        health = client.get("/ops/health")
        status = client.get("/ops/status")

        assert health.status_code == 200
        assert health.json()["status"] == "healthy"
        assert health.json()["components"]["dlib_workers"]["status"] == "up"
        assert status.json()["model_ready"] is True
        assert ai_engine.GLOBAL_PROCESS_POOL is not None

    assert ai_engine.GLOBAL_PROCESS_POOL is None
    assert ops.readiness.dlib_workers_ready() is False


def test_lifespan_body_exception_cleans_pool_and_all_workers() -> None:
    worker_pids: list[int] = []

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="application body failed"):
            async with lifespan(app):
                assert ai_engine.GLOBAL_PROCESS_POOL is not None
                worker_pids.extend(ai_engine.GLOBAL_PROCESS_POOL._processes)
                assert len(worker_pids) == MAX_WORKERS
                raise RuntimeError("application body failed")

    asyncio.run(exercise())

    assert ai_engine.GLOBAL_PROCESS_POOL is None
    assert ops.readiness.dlib_workers_ready() is False
    deadline = time.monotonic() + 5.0
    while any(_process_exists(pid) for pid in worker_pids) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert all(not _process_exists(pid) for pid in worker_pids)


def test_registry_wrapped_body_exception_revokes_readiness_before_registry_stop() -> None:
    class RegistryClient:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            assert ai_engine.GLOBAL_PROCESS_POOL is None
            assert ops.readiness.dlib_workers_ready() is False

        async def aclose(self) -> None:
            return None

    wrapped_app = FastAPI(lifespan=lifespan)
    _wrap_lifespan(
        wrapped_app,
        RegistryClient(),  # type: ignore[arg-type]
        before_registry_shutdown=_begin_dlib_shutdown,
    )

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="application body failed"):
            async with wrapped_app.router.lifespan_context(wrapped_app):
                assert ai_engine.GLOBAL_PROCESS_POOL is not None
                assert ops.readiness.dlib_workers_ready() is True
                raise RuntimeError("application body failed")

    asyncio.run(exercise())


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
