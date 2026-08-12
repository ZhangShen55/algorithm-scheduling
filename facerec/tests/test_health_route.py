import asyncio

from app.core.readiness import FaceRecReadiness
from app.router import ops
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.operator_registry_client import install_operator_runtime


class FakeReadiness:
    def __init__(self, *, database: bool, arcface: bool, dlib: bool) -> None:
        self.database = database
        self.arcface = arcface
        self.dlib = dlib

    async def check(self) -> bool:
        return self.database and self.arcface and self.dlib

    def database_ready(self) -> bool:
        return self.database

    def embedding_model_ready(self) -> bool:
        return self.arcface

    def dlib_workers_ready(self) -> bool:
        return self.dlib


class FakeCollection:
    async def find_one(self) -> None:
        return None


class FakeDatabase:
    def __getitem__(self, name: str) -> FakeCollection:
        assert name == "persons"
        return FakeCollection()


def _client(monkeypatch, *, ready: bool) -> TestClient:
    monkeypatch.setattr(
        ops,
        "readiness",
        FakeReadiness(database=ready, arcface=ready, dlib=ready),
    )
    monkeypatch.setattr(ops, "db", FakeDatabase())
    app = FastAPI()
    app.include_router(ops.router)
    return TestClient(app)


def test_ops_health_returns_http_200_when_healthy(monkeypatch) -> None:
    response = _client(monkeypatch, ready=True).get("/ops/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_ops_health_returns_http_200_when_degraded(monkeypatch) -> None:
    response = _client(monkeypatch, ready=False).get("/ops/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


def test_ops_health_is_degraded_when_dlib_worker_initialization_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        ops,
        "readiness",
        FakeReadiness(database=True, arcface=True, dlib=False),
    )
    monkeypatch.setattr(ops, "db", FakeDatabase())
    app = FastAPI()
    app.include_router(ops.router)

    response = TestClient(app).get("/ops/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["components"]["dlib_workers"]["status"] == "down"


class FakeMongo:
    async def command(self, command: object) -> dict[str, int]:
        assert command == {"ping": 1}
        return {"ok": 1}


class FakeArcFace:
    initialized = True


def test_operator_status_reports_model_not_ready_when_dlib_workers_failed() -> None:
    readiness = FaceRecReadiness(
        FakeMongo(),
        FakeArcFace(),
        dlib_workers_ready=False,
        timeout_seconds=0.1,
    )
    assert asyncio.run(readiness.check()) is False
    app = FastAPI()
    install_operator_runtime(
        app,
        operator_code="facerec",
        capabilities=["recognize"],
        default_port=8003,
        model_ready_provider=readiness.model_ready,
        registration_enabled=False,
    )

    response = TestClient(app).get("/ops/status")

    assert response.status_code == 200
    assert response.json()["model_ready"] is False
