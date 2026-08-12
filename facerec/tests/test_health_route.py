from app.router import ops
from fastapi import FastAPI
from fastapi.testclient import TestClient


class FakeReadiness:
    def __init__(self, ready: bool) -> None:
        self.ready = ready

    async def check(self) -> bool:
        return self.ready

    def database_ready(self) -> bool:
        return self.ready

    def embedding_model_ready(self) -> bool:
        return self.ready


class FakeCollection:
    async def find_one(self) -> None:
        return None


class FakeDatabase:
    def __getitem__(self, name: str) -> FakeCollection:
        assert name == "persons"
        return FakeCollection()


def _client(monkeypatch, *, ready: bool) -> TestClient:
    monkeypatch.setattr(ops, "readiness", FakeReadiness(ready))
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
