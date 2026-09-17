from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware import api_stats_middleware


class ExplodingDatabase:
    def __getitem__(self, name: str):
        raise AssertionError(f"排除端点不应访问统计数据库: {name}")


def test_ops_and_openapi_do_not_initialize_stats_database(monkeypatch) -> None:
    monkeypatch.setattr(api_stats_middleware, "db", ExplodingDatabase())
    api_stats_middleware.APIStatsMiddleware._indexes_created = False
    app = FastAPI()
    app.add_middleware(api_stats_middleware.APIStatsMiddleware)

    @app.get("/ops/status")
    async def status():
        return {"status": "ok"}

    client = TestClient(app)

    assert client.get("/ops/status").json() == {"status": "ok"}
    assert client.get("/openapi.json").status_code == 200
