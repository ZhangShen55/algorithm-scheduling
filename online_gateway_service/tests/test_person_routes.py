from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from app.api.routes import create_online_gateway_app
from app.core.config import HttpConfig, OnlineGatewaySettings
from app.infrastructure.persons import FacePersonClient
from fastapi.testclient import TestClient

JsonObject = dict[str, Any]


class RecordingPersonClient:
    def __init__(self, response: JsonObject) -> None:
        self.response = response
        self.calls: list[tuple[str, object]] = []

    async def create(self, request_body: JsonObject) -> JsonObject:
        self.calls.append(("create", request_body))
        return self.response

    async def create_batch(self, request_body: JsonObject) -> JsonObject:
        self.calls.append(("batch", request_body))
        return self.response

    async def list(self, *, skip: int, limit: int) -> JsonObject:
        self.calls.append(("list", {"skip": skip, "limit": limit}))
        return self.response

    async def search(self, request_body: JsonObject) -> JsonObject:
        self.calls.append(("search", request_body))
        return self.response

    async def delete(self, request_body: JsonObject) -> JsonObject:
        self.calls.append(("delete", request_body))
        return self.response


class LeaseClientMustNotBeUsed:
    def acquire(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("人物管理请求不应申请推理容量租约")


@pytest.mark.parametrize(
    ("method", "path", "request_body", "expected_call"),
    [
        (
            "POST",
            "/api/online/face/persons",
            {"photo": "data:image/png;base64,AA==", "name": "张三", "number": "T001"},
            (
                "create",
                {
                    "photo": "data:image/png;base64,AA==",
                    "name": "张三",
                    "number": "T001",
                },
            ),
        ),
        (
            "POST",
            "/api/online/face/persons/batch",
            {"persons": [{"photo": "AA==", "name": "张三", "number": "T001"}]},
            (
                "batch",
                {
                    "persons": [
                        {"photo": "AA==", "name": "张三", "number": "T001"}
                    ]
                },
            ),
        ),
        (
            "GET",
            "/api/online/face/persons?skip=20&limit=30",
            None,
            ("list", {"skip": 20, "limit": 30}),
        ),
        (
            "POST",
            "/api/online/face/persons/search",
            {"name": "张三", "number": "T001"},
            ("search", {"name": "张三", "number": "T001"}),
        ),
        (
            "DELETE",
            "/api/online/face/persons/delete",
            {"id": "person-001", "name": "张三", "number": "T001"},
            (
                "delete",
                {"id": "person-001", "name": "张三", "number": "T001"},
            ),
        ),
    ],
)
def test_person_routes_preserve_contract_without_capacity_lease(
    method: str,
    path: str,
    request_body: JsonObject | None,
    expected_call: tuple[str, object],
) -> None:
    upstream = {
        "status_code": 400,
        "message": "FaceRec 业务校验失败",
        "data": {"accepted": False},
    }
    person_client = RecordingPersonClient(upstream)
    app = create_online_gateway_app()
    app.state.face_person_client = person_client
    app.state.online_lease_client = LeaseClientMustNotBeUsed()

    with TestClient(app) as client:
        response = client.request(method, path, json=request_body)

    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert response.json()["data"] == upstream
    assert person_client.calls == [expected_call]


def test_person_client_maps_methods_paths_and_query_parameters() -> None:
    requests: list[tuple[str, str, JsonObject | None, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append(
            (
                request.method,
                request.url.path,
                body,
                dict(request.url.params),
            )
        )
        return httpx.Response(
            200,
            json={"status_code": 200, "message": "ok", "data": {}},
        )

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = FacePersonClient(
                http,
                base_url="http://facerec:8000/",
                hard_timeout_seconds=1,
            )
            await client.create({"photo": "AA==", "name": "甲", "number": "1"})
            await client.create_batch({"persons": []})
            await client.list(skip=10, limit=25)
            await client.search({"name": "甲"})
            await client.delete({"id": "person-1"})

    asyncio.run(exercise())

    assert requests == [
        (
            "POST",
            "/persons",
            {"photo": "AA==", "name": "甲", "number": "1"},
            {},
        ),
        ("POST", "/persons/batch", {"persons": []}, {}),
        ("GET", "/persons", None, {"skip": "10", "limit": "25"}),
        ("POST", "/persons/search", {"name": "甲"}, {}),
        ("DELETE", "/persons/delete", {"id": "person-1"}, {}),
    ]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, json={"detail": "unavailable"}),
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json=[{"id": "person-1"}]),
    ],
)
def test_person_route_maps_upstream_failures_to_business_error(
    response: httpx.Response,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return response

    app = create_online_gateway_app()
    operator_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.state.face_person_client = FacePersonClient(
        operator_http,
        base_url="http://facerec:8000",
        hard_timeout_seconds=1,
    )
    try:
        with TestClient(app) as client:
            result = client.post(
                "/api/online/face/persons/search",
                json={"number": "T001"},
            )
    finally:
        asyncio.run(operator_http.aclose())

    assert result.status_code == 200
    assert result.json() == {
        "code": 50000,
        "message": "人脸库管理调用失败",
        "data": None,
    }


def test_person_route_enforces_finite_hard_timeout() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={})

    app = create_online_gateway_app(
        OnlineGatewaySettings(http=HttpConfig(hard_timeout_seconds=0.01))
    )
    operator_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.state.face_person_client = FacePersonClient(
        operator_http,
        base_url="http://facerec:8000",
        hard_timeout_seconds=0.01,
    )
    try:
        with TestClient(app) as client:
            result = client.get("/api/online/face/persons")
    finally:
        asyncio.run(operator_http.aclose())

    assert result.json()["code"] == 50000


def test_gateway_closes_its_owned_http_client_on_shutdown() -> None:
    app = create_online_gateway_app()
    owned_client = app.state.online_http_client

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert not owned_client.is_closed

    assert owned_client.is_closed
