import queue

import pytest
from app.core import ai_engine
from app.main import app
from app.router import ops
from fastapi.testclient import TestClient


class StatusQueue:
    def __init__(self) -> None:
        self.messages: list[tuple[int, bool, str | None]] = []

    def put(self, message: tuple[int, bool, str | None]) -> None:
        self.messages.append(message)


def test_dlib_worker_initializer_reports_and_raises_model_load_failure(
    monkeypatch,
) -> None:
    status_queue = StatusQueue()
    monkeypatch.setattr(ai_engine.dlib, "get_frontal_face_detector", lambda: object())

    def fail_to_load(_: str) -> object:
        raise RuntimeError("shape predictor 损坏")

    monkeypatch.setattr(ai_engine.dlib, "shape_predictor", fail_to_load)

    with pytest.raises(RuntimeError, match="shape predictor 损坏"):
        ai_engine._init_dlib_worker(status_queue)

    assert len(status_queue.messages) == 1
    assert status_queue.messages[0][1:] == (False, "shape predictor 损坏")


def test_collect_dlib_worker_status_requires_every_worker() -> None:
    status_queue: queue.Queue[tuple[int, bool, str | None]] = queue.Queue()
    status_queue.put((101, True, None))
    status_queue.put((102, True, None))

    ai_engine._collect_dlib_worker_status(
        status_queue,
        expected_workers=2,
        timeout_seconds=0.1,
    )


def test_collect_dlib_worker_status_rejects_any_failed_worker() -> None:
    status_queue: queue.Queue[tuple[int, bool, str | None]] = queue.Queue()
    status_queue.put((101, True, None))
    status_queue.put((102, False, "shape predictor 缺失"))

    with pytest.raises(RuntimeError, match="shape predictor 缺失"):
        ai_engine._collect_dlib_worker_status(
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
