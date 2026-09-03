import asyncio
import base64
import json
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes import create_online_gateway_app
from app.core.config import HttpConfig, OnlineGatewaySettings
from app.infrastructure.capacity import (
    CapacityLease,
    ControlLeaseProtocolError,
    ControlServiceUnavailableError,
    OnlineCapacityWaitTimeoutError,
)

VALID_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="


class Lease:
    @asynccontextmanager
    async def acquire(self, capability: str, **kwargs: object):
        assert kwargs.get("capacity_pool") == "online"
        yield CapacityLease(
            lease_id="lease-1", instance_id="vbas-1", capability=capability,
            service_url="http://vbas", expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )


class SequentialLease:
    def __init__(self, service_urls: list[str]) -> None:
        self._service_urls = iter(service_urls)
        self.acquired: list[str] = []
        self.released: list[str] = []
        self.excluded: list[set[str]] = []

    @asynccontextmanager
    async def acquire(self, capability: str, **kwargs: object):
        assert kwargs.get("deadline") is not None
        self.excluded.append(set(kwargs.get("excluded_instance_ids", set())))
        service_url = next(self._service_urls)
        instance_id = service_url.split("//", 1)[1].split(":", 1)[0]
        self.acquired.append(instance_id)
        try:
            yield CapacityLease(
                lease_id=f"lease-{instance_id}",
                instance_id=instance_id,
                capability=capability,
                service_url=service_url,
                expires_at=datetime.now(UTC) + timedelta(minutes=1),
            )
        finally:
            self.released.append(instance_id)


def test_vbas_routes_forward_raw_response_and_online_header() -> None:
    calls: list[tuple[str, dict[str, Any], str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((request.url.path, body, request.headers["x-algorithm-work-type"]))
        return httpx.Response(200, request=request, json={"TaskResult": [], "FreeCapacity": 7})

    app = create_online_gateway_app()
    app.state.online_lease_client = Lease()
    app.state.online_http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    payload = {"ImageList": [{"ImageId": "1", "StoragePath": VALID_PNG}], "AnalysisRule": {"AlgParams": {"PolygonList": []}}}
    person_count_payload = {"ImageList": [{"ImageID": "1", "Data": VALID_PNG}], "AnalysisRule": {"AlgParams": {"PolygonList": []}}}
    try:
        with TestClient(app) as client:
            for path, expected in [
                ("/online/vbas/teacher", "/ImageDetect/teacher/v1.0.0"),
                ("/online/vbas/student", "/ImageDetect/student/v1.0.0"),
                ("/online/vbas/person-count", "/AE/SyncTasks2"),
            ]:
                response = client.post(
                    path,
                    json=person_count_payload if path.endswith("person-count") else payload,
                )
                assert response.status_code == 200
                assert response.json() == {"TaskResult": [], "FreeCapacity": 7}
                assert calls[-1][0] == expected
                assert calls[-1][2] == "online"
    finally:
        import asyncio
        asyncio.run(app.state.online_http_client.aclose())


def test_person_count_accepts_real_decodable_workspace_image() -> None:
    fixture = (
        Path(__file__).resolve().parents[2]
        / "vbas/tests/teacher_person_count/frame_000068.jpg"
    )
    encoded = base64.b64encode(fixture.read_bytes()).decode("ascii")
    forwarded = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal forwarded
        forwarded = True
        body = json.loads(request.content)
        assert body["ImageList"][0]["Data"] == encoded
        return httpx.Response(200, request=request, json={"TaskResult": []})

    app = create_online_gateway_app()
    app.state.online_lease_client = Lease()
    app.state.online_http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    payload = {"ImageList": [{"ImageID": "real-frame-68", "Data": encoded}]}
    try:
        with TestClient(app) as client:
            response = client.post("/online/vbas/person-count", json=payload)
    finally:
        asyncio.run(app.state.online_http_client.aclose())

    assert response.status_code == 200
    assert response.json() == {"TaskResult": []}
    assert forwarded is True


@pytest.mark.parametrize(
    ("failures", "expected_code"),
    [
        (("connect", "connect"), 0),
        ((429, 503), 0),
        ((502, 504), 0),
        (("connect", "connect", "connect"), 50201),
        (("timeout", "timeout", "timeout"), 50401),
    ],
)
def test_vbas_route_retries_recoverable_failures_and_maps_exhaustion(
    failures: tuple[object, ...],
    expected_code: int,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        outcome = failures[calls] if calls < len(failures) else 200
        calls += 1
        if outcome == "connect":
            raise httpx.ConnectError("连接被拒绝", request=request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("读取超时", request=request)
        if isinstance(outcome, int) and outcome != 200:
            return httpx.Response(outcome, request=request, json={"detail": "暂不可用"})
        return httpx.Response(
            200,
            request=request,
            json={"TaskResult": [], "FreeCapacity": 7},
        )

    app = create_online_gateway_app()
    app.state.online_lease_client = Lease()
    app.state.online_http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    payload = {
        "ImageList": [{"ImageID": "stable-image", "Data": VALID_PNG}],
        "AnalysisRule": {"AlgParams": {"PolygonList": []}},
    }
    try:
        with TestClient(app) as client:
            response = client.post("/online/vbas/person-count", json=payload)
    finally:
        import asyncio

        asyncio.run(app.state.online_http_client.aclose())

    assert response.status_code == 200
    if expected_code == 0:
        assert response.json() == {"TaskResult": [], "FreeCapacity": 7}
        assert calls == len(failures) + 1
    else:
        assert response.json()["code"] == expected_code
        assert calls == 3


def test_vbas_route_reselects_instance_and_preserves_request_identity() -> None:
    request_bodies: list[dict[str, Any]] = []
    request_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_bodies.append(json.loads(request.content))
        request_hosts.append(str(request.url.host))
        if len(request_bodies) == 1:
            raise httpx.ConnectError("首个实例连接失败", request=request)
        return httpx.Response(200, request=request, json={"TaskResult": []})

    leases = SequentialLease(
        ["http://vbas-gpu0:8981", "http://vbas-gpu1:8981"]
    )
    app = create_online_gateway_app()
    app.state.online_lease_client = leases
    app.state.online_http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    payload = {
        "TaskID": "online-task-001",
        "ImageList": [{"ImageID": "stable-image", "Data": VALID_PNG}],
        "AnalysisRule": {"AlgParams": {"PolygonList": []}},
    }
    try:
        with TestClient(app) as client:
            response = client.post("/online/vbas/person-count", json=payload)
    finally:
        asyncio.run(app.state.online_http_client.aclose())

    assert response.json() == {"TaskResult": []}
    assert request_hosts == ["vbas-gpu0", "vbas-gpu1"]
    assert request_bodies == [payload, payload]
    assert leases.acquired == ["vbas-gpu0", "vbas-gpu1"]
    assert leases.released == leases.acquired


@pytest.mark.asyncio
async def test_cancelled_vbas_request_releases_lease_and_leaves_no_background_call() -> None:
    entered = asyncio.Event()
    released = asyncio.Event()

    class CancellableLease:
        @asynccontextmanager
        async def acquire(self, capability: str, **kwargs: object):
            del kwargs
            try:
                yield CapacityLease(
                    lease_id="lease-cancel",
                    instance_id="vbas-gpu0",
                    capability=capability,
                    service_url="http://vbas-gpu0:8981",
                    expires_at=datetime.now(UTC) + timedelta(minutes=1),
                )
            finally:
                released.set()

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        entered.set()
        await asyncio.sleep(60)
        return httpx.Response(200, json={"TaskResult": []})

    app = create_online_gateway_app()
    app.state.online_lease_client = CancellableLease()
    app.state.online_http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    payload = {"ImageList": [{"ImageID": "cancel-image", "Data": VALID_PNG}]}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://gateway.test",
    ) as client:
        request_task = asyncio.create_task(
            client.post("/online/vbas/person-count", json=payload)
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        request_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_task
        await asyncio.wait_for(released.wait(), timeout=1)

    await app.state.online_http_client.aclose()


@pytest.mark.parametrize(
    ("lease_error", "expected_code"),
    [
        (OnlineCapacityWaitTimeoutError("容量持续不可用"), 50301),
        (ControlServiceUnavailableError("Control 持续不可用"), 50302),
        (ControlLeaseProtocolError("Control 响应无效"), 50000),
    ],
)
def test_vbas_route_maps_capacity_failure_stage_precisely(
    lease_error: Exception,
    expected_code: int,
) -> None:
    class FailingLease:
        @asynccontextmanager
        async def acquire(self, capability: str, **kwargs: object):
            del capability, kwargs
            raise lease_error
            yield  # pragma: no cover

    app = create_online_gateway_app()
    app.state.online_lease_client = FailingLease()
    payload = {"ImageList": [{"ImageID": "image-1", "Data": VALID_PNG}]}

    with TestClient(app) as client:
        response = client.post("/online/vbas/person-count", json=payload)

    assert response.status_code == 200
    assert response.json()["code"] == expected_code


@pytest.mark.parametrize(
    "path",
    (
        "/online/vbas/person-count",
        "/online/vbas/teacher",
        "/online/vbas/student",
    ),
)
def test_all_vbas_routes_share_recovery_behavior(path: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, request=request, json={"TaskResult": []})

    app = create_online_gateway_app()
    app.state.online_lease_client = Lease()
    app.state.online_http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    payload = {
        "ImageList": [{"ImageID": "image-1", "Data": VALID_PNG}],
        "AnalysisRule": {"AlgParams": {"PolygonList": []}},
    }
    try:
        with TestClient(app) as client:
            response = client.post(path, json=payload)
    finally:
        asyncio.run(app.state.online_http_client.aclose())

    assert response.json() == {"TaskResult": []}
    assert calls == 2


def test_vbas_retry_excludes_the_failed_instance_from_next_lease() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, request=request, json={"TaskResult": []})

    leases = SequentialLease(
        ["http://vbas-gpu0:8981", "http://vbas-gpu1:8981"]
    )
    app = create_online_gateway_app()
    app.state.online_lease_client = leases
    app.state.online_http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    payload = {"ImageList": [{"ImageID": "image-1", "Data": VALID_PNG}]}
    try:
        with TestClient(app) as client:
            response = client.post("/online/vbas/person-count", json=payload)
    finally:
        asyncio.run(app.state.online_http_client.aclose())

    assert response.json() == {"TaskResult": []}
    assert leases.excluded == [set(), {"vbas-gpu0"}]


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    ((400, 40001), (422, 40001), (500, 50000)),
)
def test_vbas_deterministic_http_error_is_not_retried(
    status_code: int,
    expected_code: int,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, request=request)

    app = create_online_gateway_app()
    app.state.online_lease_client = Lease()
    app.state.online_http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    payload = {"ImageList": [{"ImageID": "image-1", "Data": VALID_PNG}]}
    try:
        with TestClient(app) as client:
            response = client.post("/online/vbas/person-count", json=payload)
    finally:
        asyncio.run(app.state.online_http_client.aclose())

    assert response.status_code == 200
    assert response.json()["code"] == expected_code
    assert calls == 1


def test_vbas_capacity_wait_and_operator_retries_share_one_deadline() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.03)
        return httpx.Response(503, request=request)

    app = create_online_gateway_app(
        OnlineGatewaySettings(
            http=HttpConfig(
                hard_timeout_seconds=0.05,
                operator_max_attempts=3,
                retry_base_delay_seconds=0.01,
                retry_max_delay_seconds=0.01,
            )
        )
    )
    app.state.online_lease_client = Lease()
    app.state.online_http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    payload = {"ImageList": [{"ImageID": "image-1", "Data": VALID_PNG}]}
    started_at = time.monotonic()
    try:
        with TestClient(app) as client:
            response = client.post("/online/vbas/person-count", json=payload)
    finally:
        asyncio.run(app.state.online_http_client.aclose())

    elapsed = time.monotonic() - started_at
    assert response.status_code == 200
    assert response.json()["code"] == 50401
    assert calls == 2
    assert elapsed < 0.15
