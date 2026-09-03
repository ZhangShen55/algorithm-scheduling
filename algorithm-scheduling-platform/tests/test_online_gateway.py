import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from online_gateway_service.app.api.routes import create_online_gateway_app
from online_gateway_service.app.core.config import (
    Base64Config,
    BodyConfig,
    HttpConfig,
    OnlineGatewaySettings,
)
from online_gateway_service.app.infrastructure.capacity import (
    OnlineCapacityLeaseClient,
    OnlineCapacityLeaseError,
    OnlineCapacityWaitTimeoutError,
    OnlineWorkContext,
)
from online_gateway_service.app.main import app
from starlette.websockets import WebSocketDisconnect

from packages.platform_common.config import PlatformSettings
from packages.platform_common.metrics import PlatformMetrics
from packages.platform_common.operator_registry import CapacityLease

MINIMAL_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
MINIMAL_PNG_DATA_URI = f"data:image/png;base64,{MINIMAL_PNG_BASE64}"


def test_online_gateway_exposes_vbas_request_level_proxy() -> None:
    route_paths = {route.path for route in app.routes}

    assert route_paths >= {
        "/online/vbas/teacher",
        "/online/vbas/student",
        "/online/vbas/person-count",
    }


def test_online_gateway_exposes_face_recognition_proxy() -> None:
    route_paths = {route.path for route in app.routes}

    assert "/api/online/face/recognize" in route_paths


def test_online_gateway_exposes_image_quality_detect_all_proxy() -> None:
    route_paths = {route.path for route in app.routes}

    assert "/api/online/image-quality/detect" in route_paths


def test_online_gateway_exposes_realtime_asr_websocket() -> None:
    route_paths = {route.path for route in app.routes}

    assert "/api/online/asr/stream" in route_paths


def test_online_gateway_exposes_single_image_ocr_proxy() -> None:
    route_paths = {route.path for route in app.routes}

    assert "/api/online/ocr/recognize" in route_paths


@pytest.mark.parametrize(
    ("request_body", "expected_formula", "expected_image_id"),
    [
        ({"image": MINIMAL_PNG_BASE64}, False, None),
        (
            {
                "image_id": "ppt-image-001",
                "image": MINIMAL_PNG_DATA_URI,
                "enable_formula": True,
            },
            True,
            "ppt-image-001",
        ),
    ],
)
def test_online_ocr_adapts_single_image_and_preserves_operator_response(
    request_body: dict[str, object],
    expected_formula: bool,
    expected_image_id: str | None,
) -> None:
    contexts: list[OnlineWorkContext] = []
    forwarded: list[dict[str, object]] = []

    class LeaseClient:
        @asynccontextmanager
        async def acquire(self, capability: str, *, ttl_seconds: int = 60, **kwargs):
            assert capability == "ocr"
            assert ttl_seconds == 60
            contexts.append(kwargs["work_context"])
            yield CapacityLease(
                lease_id="lease-ocr-online",
                instance_id="ocr-gpu0",
                capability=capability,
                service_url="http://ocr-gpu0:8866",
                expires_at=datetime.now(UTC) + timedelta(seconds=60),
            )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ocr/prediction"
        body = json.loads(request.content)
        forwarded.append(body)
        return httpx.Response(
            200,
            json={
                "key": body["key"],
                "value": ["识别文本"],
                "formula_results": [[]],
                "err_no": 0,
                "err_msg": "",
            },
        )

    online_app = create_online_gateway_app()
    online_app.state.online_lease_client = LeaseClient()
    operator_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    online_app.state.online_http_client = operator_http
    try:
        with TestClient(online_app) as client:
            response = client.post("/api/online/ocr/recognize", json=request_body)
    finally:
        asyncio.run(operator_http.aclose())

    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert response.json()["data"]["value"] == ["识别文本"]
    assert forwarded[0]["enable_formula"] is expected_formula
    assert len(forwarded[0]["key"]) == 1
    if expected_image_id is None:
        assert forwarded[0]["key"][0].startswith("online-ocr-")
    else:
        assert forwarded[0]["key"] == [expected_image_id]
    assert forwarded[0]["value"] == [request_body["image"]]
    assert len(contexts) == 1
    assert contexts[0].source_service == "online-gateway-service"
    assert contexts[0].work_type == "online_ocr"
    assert contexts[0].trace_id


@pytest.mark.parametrize(
    "request_body",
    [
        {},
        {"image": ""},
        {"image": "not-base64"},
        {"image": "AA==", "enable_formula": 1},
        {"image": "AA==", "enable_formula": "false"},
        {"image": "AA==", "image_id": ""},
    ],
)
def test_online_ocr_rejects_invalid_request_before_leasing(
    request_body: dict[str, object],
) -> None:
    class LeaseClient:
        @asynccontextmanager
        async def acquire(self, capability: str, **kwargs):
            del capability, kwargs
            raise AssertionError("参数错误不应申请容量租约")
            yield

    online_app = create_online_gateway_app()
    online_app.state.online_lease_client = LeaseClient()
    with TestClient(online_app) as client:
        response = client.post("/api/online/ocr/recognize", json=request_body)

    assert response.status_code == 200
    assert response.json()["code"] == 40001


def test_online_ocr_enforces_body_and_decoded_size_before_leasing() -> None:
    lease_count = 0

    class LeaseClient:
        @asynccontextmanager
        async def acquire(self, capability: str, **kwargs):
            nonlocal lease_count
            del capability, kwargs
            lease_count += 1
            yield

    decoded_limit_app = create_online_gateway_app(
        OnlineGatewaySettings(
            body=BodyConfig(max_bytes=1_024),
            base64=Base64Config(max_decoded_bytes=2),
        )
    )
    decoded_limit_app.state.online_lease_client = LeaseClient()
    with TestClient(decoded_limit_app) as client:
        decoded_response = client.post(
            "/api/online/ocr/recognize",
            json={"image": "AAEC"},
        )

    body_limit_app = create_online_gateway_app(
        OnlineGatewaySettings(body=BodyConfig(max_bytes=32))
    )
    body_limit_app.state.online_lease_client = LeaseClient()
    with TestClient(body_limit_app) as client:
        body_response = client.post(
            "/api/online/ocr/recognize",
            json={"image": "A" * 64},
        )

    assert decoded_response.json()["code"] == 40001
    assert body_response.json()["code"] == 40001
    assert lease_count == 0


@pytest.mark.asyncio
async def test_online_capacity_client_renews_context_and_releases() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    expires_at = datetime.now(UTC) + timedelta(seconds=1)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if request.url.path.endswith("/lease"):
            return httpx.Response(
                200,
                json={
                    "lease_id": "lease-online-renew",
                    "instance_id": "ocr-gpu0",
                    "capability": "ocr",
                    "service_url": "http://ocr-gpu0:8866",
                    "expires_at": expires_at.isoformat(),
                    "work_context": body["work_context"],
                },
            )
        if request.url.path.endswith("/renew"):
            return httpx.Response(
                200,
                json={
                    "lease_id": "lease-online-renew",
                    "instance_id": "ocr-gpu0",
                    "capability": "ocr",
                    "service_url": "http://ocr-gpu0:8866",
                    "expires_at": expires_at.isoformat(),
                },
            )
        return httpx.Response(200, json={"status": "RELEASED"})

    context = OnlineWorkContext(
        source_service="online-gateway-service",
        work_type="online_ocr",
        work_id="online-ocr-001",
        trace_id="trace-001",
    )
    metrics = PlatformMetrics()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OnlineCapacityLeaseClient(
            http,
            control_service_url="http://control",
            metrics=metrics,
        )
        async with client.acquire(
            "ocr",
            ttl_seconds=1,
            work_context=context,
            renew_interval_seconds=0.01,
        ):
            await asyncio.sleep(0.025)

    assert calls[0] == (
        "/internal/operator-instances/lease",
        {
            "capability": "ocr",
            "ttl_seconds": 1,
            "work_context": context.as_dict(),
            "capacity_pool": "online",
        },
    )
    assert sum(path.endswith("/renew") for path, _ in calls) >= 2
    assert calls[-1] == (
        "/internal/operator-instances/release",
        {"lease_id": "lease-online-renew"},
    )
    rendered = metrics.render().decode("utf-8")
    assert (
        'algorithm_capacity_lease_events_total{capability="ocr",'
        'instance_id="none",outcome="requested"} 1.0' in rendered
    )
    assert (
        'algorithm_capacity_lease_events_total{capability="ocr",'
        'instance_id="ocr-gpu0",outcome="acquired"} 1.0' in rendered
    )
    assert (
        'algorithm_capacity_lease_events_total{capability="ocr",'
        'instance_id="ocr-gpu0",outcome="released"} 1.0' in rendered
    )


@pytest.mark.asyncio
async def test_online_capacity_renewal_failure_cancels_work_and_releases() -> None:
    calls: list[str] = []
    expires_at = datetime.now(UTC) + timedelta(seconds=1)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/lease"):
            return httpx.Response(
                200,
                json={
                    "lease_id": "lease-renew-failure",
                    "instance_id": "ocr-gpu0",
                    "capability": "ocr",
                    "service_url": "http://ocr-gpu0:8866",
                    "expires_at": expires_at.isoformat(),
                },
            )
        if request.url.path.endswith("/renew"):
            return httpx.Response(503, json={"detail": "redis unavailable"})
        return httpx.Response(200, json={"status": "RELEASED"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OnlineCapacityLeaseClient(http, control_service_url="http://control")
        with pytest.raises(OnlineCapacityLeaseError, match="续租失败"):
            async with client.acquire(
                "ocr",
                ttl_seconds=1,
                renew_interval_seconds=0.01,
            ):
                await asyncio.sleep(1)

    assert calls[-1].endswith("/release")


@pytest.mark.asyncio
async def test_online_capacity_client_counts_capacity_wait_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/operator-instances/lease"
        return httpx.Response(503, json={"detail": "暂无可用算子容量"})

    metrics = PlatformMetrics()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OnlineCapacityLeaseClient(
            http,
            control_service_url="http://control",
            metrics=metrics,
            acquire_wait_timeout_seconds=0.02,
            acquire_retry_interval_seconds=0.001,
        )
        with pytest.raises(OnlineCapacityWaitTimeoutError, match="超过"):
            async with client.acquire("detect_all"):
                raise AssertionError("没有租约时不得进入算子调用")

    rendered = metrics.render().decode("utf-8")
    assert (
        'algorithm_capacity_lease_events_total{capability="detect_all",'
        'instance_id="none",outcome="requested"} 1.0' in rendered
    )
    assert (
        'algorithm_capacity_lease_events_total{capability="detect_all",'
        'instance_id="none",outcome="timeout"} 1.0' in rendered
    )
    assert "algorithm_capacity_recovery_events_total" in rendered
    assert 'capacity_pool="online"' in rendered


def test_online_ocr_timeout_and_upstream_errors_release_the_lease() -> None:
    released: list[str] = []

    class LeaseClient:
        @asynccontextmanager
        async def acquire(self, capability: str, **kwargs):
            del kwargs
            assert capability == "ocr"
            try:
                yield CapacityLease(
                    lease_id="lease-ocr-timeout",
                    instance_id="ocr-gpu0",
                    capability="ocr",
                    service_url="http://ocr-gpu0:8866",
                    expires_at=datetime.now(UTC) + timedelta(seconds=60),
                )
            finally:
                released.append("lease-ocr-timeout")

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={})

    online_app = create_online_gateway_app(
        OnlineGatewaySettings(http=HttpConfig(hard_timeout_seconds=0.01))
    )
    online_app.state.online_lease_client = LeaseClient()
    operator_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    online_app.state.online_http_client = operator_http
    try:
        with TestClient(online_app) as client:
            response = client.post(
                "/api/online/ocr/recognize",
                json={"image": MINIMAL_PNG_BASE64},
            )
    finally:
        asyncio.run(operator_http.aclose())

    assert response.status_code == 200
    assert response.json()["code"] == 50000
    assert released == ["lease-ocr-timeout"]


def test_online_vbas_proxies_complete_base64_request_through_one_lease(
    tmp_path: Path,
) -> None:
    acquired: list[str] = []
    released: list[str] = []
    forwarded: list[tuple[str, dict[str, object]]] = []

    class LeaseClient:
        @asynccontextmanager
        async def acquire(self, capability: str, *, ttl_seconds: int = 60, **kwargs):
            assert kwargs["work_context"].source_service == "online-gateway-service"
            acquired.append(capability)
            assert ttl_seconds == 60
            try:
                yield CapacityLease(
                    lease_id="lease-vbas-1",
                    instance_id="vbas-gpu0",
                    capability=capability,
                    service_url="http://vbas-gpu0:8981",
                    expires_at=datetime.now(UTC) + timedelta(seconds=60),
                )
            finally:
                released.append("lease-vbas-1")

    def handler(request: httpx.Request) -> httpx.Response:
        forwarded.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "StatusObject": {"StatusString": "success", "StatusCode": 0},
                "DataList": [],
            },
        )

    request_body = {
        "task_id": "online-001",
        "batch_id": "batch-001",
        "stream_type": "student",
        "ImageList": [
            {
                "ImageId": "student-001",
                "StoragePath": MINIMAL_PNG_DATA_URI,
            }
        ],
    }
    online_app = create_online_gateway_app(
        PlatformSettings(
            service_name="online-gateway-service",
            course_root=tmp_path / "course",
            result_root=tmp_path / "result",
        )
    )
    online_app.state.online_lease_client = LeaseClient()
    online_app.state.online_http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    try:
        with TestClient(online_app) as client:
            response = client.post("/online/vbas/student", json=request_body)
    finally:
        asyncio.run(online_app.state.online_http_client.aclose())

    assert response.status_code == 200
    assert response.json() == {
        "StatusObject": {"StatusString": "success", "StatusCode": 0},
        "DataList": [],
    }
    assert acquired == ["student_behavior"]
    assert released == ["lease-vbas-1"]
    assert forwarded == [("/ImageDetect/student/v1.0.0", request_body)]


def test_online_face_recognition_preserves_existing_operator_contract(
    tmp_path: Path,
) -> None:
    acquired: list[str] = []
    forwarded: list[tuple[str, dict[str, object]]] = []

    class LeaseClient:
        @asynccontextmanager
        async def acquire(self, capability: str, *, ttl_seconds: int = 60, **kwargs):
            assert kwargs["work_context"].source_service == "online-gateway-service"
            del ttl_seconds
            acquired.append(capability)
            yield CapacityLease(
                lease_id="lease-face-1",
                instance_id="facerec-gpu0",
                capability=capability,
                service_url="http://facerec-gpu0:8000",
                expires_at=datetime.now(UTC) + timedelta(seconds=60),
            )

    def handler(request: httpx.Request) -> httpx.Response:
        forwarded.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "status_code": 200,
                "message": "识别成功",
                "data": {"has_face": True, "match": []},
            },
        )

    request_body = {
        "photo": MINIMAL_PNG_DATA_URI,
        "targets": ["T001"],
        "threshold": 0.4,
    }
    online_app = create_online_gateway_app(
        PlatformSettings(
            service_name="online-gateway-service",
            course_root=tmp_path / "course",
            result_root=tmp_path / "result",
        )
    )
    online_app.state.online_lease_client = LeaseClient()
    online_app.state.online_http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    try:
        with TestClient(online_app) as client:
            response = client.post("/api/online/face/recognize", json=request_body)
    finally:
        asyncio.run(online_app.state.online_http_client.aclose())

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "人脸对比完成",
        "data": {
            "status_code": 200,
            "message": "识别成功",
            "data": {"has_face": True, "match": []},
        },
    }
    assert acquired == ["recognize"]
    assert forwarded == [("/recognize", request_body)]


def test_online_image_quality_uses_detect_all_contract(tmp_path: Path) -> None:
    acquired: list[str] = []
    forwarded: list[tuple[str, dict[str, object]]] = []

    class LeaseClient:
        @asynccontextmanager
        async def acquire(self, capability: str, *, ttl_seconds: int = 60, **kwargs):
            assert kwargs["work_context"].source_service == "online-gateway-service"
            del ttl_seconds
            acquired.append(capability)
            yield CapacityLease(
                lease_id="lease-screen-1",
                instance_id="screen-det-gpu0",
                capability=capability,
                service_url="http://screen-det-gpu0:8880",
                expires_at=datetime.now(UTC) + timedelta(seconds=60),
            )

    def handler(request: httpx.Request) -> httpx.Response:
        forwarded.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "code": 200,
                "msg": "检测完成",
                "executed_modules": ["tilt", "screen"],
                "failed_modules": [],
                "problem_types": [],
            },
        )

    request_body = {
        "image": MINIMAL_PNG_DATA_URI,
        "include": ["tilt", "screen"],
        "screen_conf": 0.3,
    }
    online_app = create_online_gateway_app(
        PlatformSettings(
            service_name="online-gateway-service",
            course_root=tmp_path / "course",
            result_root=tmp_path / "result",
        )
    )
    online_app.state.online_lease_client = LeaseClient()
    online_app.state.online_http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    try:
        with TestClient(online_app) as client:
            response = client.post("/api/online/image-quality/detect", json=request_body)
    finally:
        asyncio.run(online_app.state.online_http_client.aclose())

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "图像质量检测完成",
        "data": {
            "code": 200,
            "msg": "检测完成",
            "executed_modules": ["tilt", "screen"],
            "failed_modules": [],
            "problem_types": [],
        },
    }
    assert acquired == ["detect_all"]
    assert forwarded == [("/detect_all", request_body)]


def test_multi_image_vbas_request_is_not_split_and_preserves_partial_results(
    tmp_path: Path,
) -> None:
    lease_count = 0
    forwarded_bodies: list[dict[str, object]] = []

    class LeaseClient:
        @asynccontextmanager
        async def acquire(self, capability: str, *, ttl_seconds: int = 60, **kwargs):
            assert kwargs["work_context"].source_service == "online-gateway-service"
            nonlocal lease_count
            del ttl_seconds
            assert capability == "student_behavior"
            lease_count += 1
            yield CapacityLease(
                lease_id="lease-vbas-multi",
                instance_id="vbas-gpu1",
                capability=capability,
                service_url="http://vbas-gpu1:8981",
                expires_at=datetime.now(UTC) + timedelta(seconds=60),
            )

    def handler(request: httpx.Request) -> httpx.Response:
        forwarded_bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "StatusObject": {"StatusString": "partial", "StatusCode": 0},
                "DataList": [
                    {
                        "StatusObject": {
                            "StatusString": "success",
                            "StatusCode": 0,
                            "ImageId": "image-ok",
                        },
                        "ResultList": [],
                    },
                    {
                        "StatusObject": {
                            "StatusString": "decode failed",
                            "StatusCode": 400,
                            "ImageId": "image-failed",
                        },
                        "ResultList": [],
                    },
                ],
            },
        )

    request_body = {
        "stream_type": "student",
        "ImageList": [
            {"ImageId": "image-ok", "StoragePath": MINIMAL_PNG_BASE64},
            {"ImageId": "image-failed", "StoragePath": MINIMAL_PNG_DATA_URI},
        ],
    }
    online_app = create_online_gateway_app(
        PlatformSettings(
            service_name="online-gateway-service",
            course_root=tmp_path / "course",
            result_root=tmp_path / "result",
        )
    )
    online_app.state.online_lease_client = LeaseClient()
    online_app.state.online_http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    try:
        with TestClient(online_app) as client:
            body = client.post("/online/vbas/student", json=request_body).json()
    finally:
        asyncio.run(online_app.state.online_http_client.aclose())

    assert lease_count == 1
    assert forwarded_bodies == [request_body]
    assert [
        item["StatusObject"]["ImageId"] for item in body["DataList"]
    ] == ["image-ok", "image-failed"]
    assert body["DataList"][1]["StatusObject"]["StatusCode"] == 400


def test_realtime_asr_keeps_one_sticky_lease_for_the_websocket_session(
    tmp_path: Path,
) -> None:
    acquired: list[str] = []
    released: list[str] = []
    connected_urls: list[str] = []
    upstream_messages: list[bytes | str] = []

    class LeaseClient:
        @asynccontextmanager
        async def acquire(self, capability: str, *, ttl_seconds: int = 60, **kwargs):
            assert kwargs["work_context"].source_service == "online-gateway-service"
            acquired.append(capability)
            assert ttl_seconds == 3_600
            try:
                yield CapacityLease(
                    lease_id="lease-asr-online-1",
                    instance_id="asr-online-gpu0",
                    capability=capability,
                    service_url="http://asr-online-gpu0:8084",
                    expires_at=datetime.now(UTC) + timedelta(seconds=3_600),
                )
            finally:
                released.append("lease-asr-online-1")

    class UpstreamWebSocket:
        def __init__(self) -> None:
            self.responses: asyncio.Queue[bytes | str] = asyncio.Queue()

        async def send(self, message: bytes | str) -> None:
            upstream_messages.append(message)
            index = len(upstream_messages)
            await self.responses.put(
                json.dumps(
                    {
                        "key": f"result-{index}",
                        "text": f"实时文本-{index}",
                        "finished": False,
                        "bg": 0.0,
                        "ed": 0.48 * index,
                    },
                    ensure_ascii=False,
                )
            )

        async def recv(self) -> bytes | str:
            return await self.responses.get()

    upstream = UpstreamWebSocket()

    class Connector:
        @asynccontextmanager
        async def connect(self, url: str):
            connected_urls.append(url)
            yield upstream

    online_app = create_online_gateway_app(
        PlatformSettings(
            service_name="online-gateway-service",
            course_root=tmp_path / "course",
            result_root=tmp_path / "result",
        )
    )
    online_app.state.online_lease_client = LeaseClient()
    online_app.state.asr_websocket_connector = Connector()

    with TestClient(online_app) as client:
        with client.websocket_connect("/api/online/asr/stream") as websocket:
            websocket.send_bytes(b"pcm-chunk-1")
            assert websocket.receive_json()["text"] == "实时文本-1"
            websocket.send_bytes(b"pcm-chunk-2")
            assert websocket.receive_json()["text"] == "实时文本-2"

    assert acquired == ["asr_online"]
    assert released == ["lease-asr-online-1"]
    assert connected_urls == [
        "ws://asr-online-gpu0:8084/v1.0.1/seacraft_asr_online"
    ]
    assert upstream_messages == [b"pcm-chunk-1", b"pcm-chunk-2"]


def test_realtime_asr_returns_capacity_error_and_1013_without_connecting_operator(
    tmp_path: Path,
) -> None:
    class LeaseClient:
        @asynccontextmanager
        async def acquire(self, capability: str, **kwargs):
            assert capability == "asr_online"
            assert kwargs["work_context"].source_service == "online-gateway-service"
            raise OnlineCapacityLeaseError("no capacity: asr_online")
            yield

    class Connector:
        @asynccontextmanager
        async def connect(self, url: str):
            raise AssertionError(f"容量不足时不应连接实时 ASR 算子: {url}")
            yield

    online_app = create_online_gateway_app(
        PlatformSettings(
            service_name="online-gateway-service",
            course_root=tmp_path / "course",
            result_root=tmp_path / "result",
        )
    )
    online_app.state.online_lease_client = LeaseClient()
    online_app.state.asr_websocket_connector = Connector()

    with TestClient(online_app) as client:
        with client.websocket_connect("/api/online/asr/stream") as websocket:
            assert websocket.receive_json() == {
                "code": 50301,
                "message": "暂无可用实时 ASR 算子容量",
                "data": None,
            }
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_json()

    assert closed.value.code == 1013


def test_online_http_returns_bounded_business_error_when_capacity_is_unavailable(
    tmp_path: Path,
) -> None:
    class LeaseClient:
        @asynccontextmanager
        async def acquire(self, capability: str, *, ttl_seconds: int = 60, **kwargs):
            assert kwargs["work_context"].source_service == "online-gateway-service"
            del ttl_seconds
            raise OnlineCapacityLeaseError(f"no capacity: {capability}")
            yield

    online_app = create_online_gateway_app(
        PlatformSettings(
            service_name="online-gateway-service",
            course_root=tmp_path / "course",
            result_root=tmp_path / "result",
        )
    )
    online_app.state.online_lease_client = LeaseClient()

    with TestClient(online_app) as client:
        response = client.post(
            "/api/online/face/recognize",
            json={"photo": MINIMAL_PNG_DATA_URI},
        )

    assert response.status_code == 200
    assert response.json() == {
        "code": 50301,
        "message": "暂无可用人脸对比算子容量",
        "data": None,
    }


@pytest.mark.asyncio
async def test_concurrent_online_requests_can_use_different_instances(tmp_path: Path) -> None:
    leased_instances: list[str] = []
    next_instance = 0

    class LeaseClient:
        @asynccontextmanager
        async def acquire(self, capability: str, *, ttl_seconds: int = 60, **kwargs):
            assert kwargs["work_context"].source_service == "online-gateway-service"
            nonlocal next_instance
            del ttl_seconds
            assert capability == "detect_all"
            instance = next_instance
            next_instance += 1
            leased_instances.append(f"screen-det-{instance}")
            yield CapacityLease(
                lease_id=f"lease-screen-{instance}",
                instance_id=f"screen-det-{instance}",
                capability=capability,
                service_url=f"http://screen-det-{instance}:8880",
                expires_at=datetime.now(UTC) + timedelta(seconds=60),
            )

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0)
        return httpx.Response(200, json={"operator_host": request.url.host})

    online_app = create_online_gateway_app(
        PlatformSettings(
            service_name="online-gateway-service",
            course_root=tmp_path / "course",
            result_root=tmp_path / "result",
        )
    )
    online_app.state.online_lease_client = LeaseClient()
    operator_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    online_app.state.online_http_client = operator_http
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=online_app),
            base_url="http://gateway",
        ) as client:
            responses = await asyncio.gather(
                client.post(
                    "/api/online/image-quality/detect",
                    json={"image": MINIMAL_PNG_BASE64},
                ),
                client.post(
                    "/api/online/image-quality/detect",
                    json={"image": MINIMAL_PNG_DATA_URI},
                ),
            )
    finally:
        await operator_http.aclose()

    assert leased_instances == ["screen-det-0", "screen-det-1"]
    assert {response.json()["data"]["operator_host"] for response in responses} == {
        "screen-det-0",
        "screen-det-1",
    }


def test_realtime_asr_operator_disconnect_closes_session_and_releases_lease(
    tmp_path: Path,
) -> None:
    released: list[str] = []

    class LeaseClient:
        @asynccontextmanager
        async def acquire(self, capability: str, *, ttl_seconds: int = 60, **kwargs):
            assert kwargs["work_context"].source_service == "online-gateway-service"
            del ttl_seconds
            try:
                yield CapacityLease(
                    lease_id="lease-asr-disconnect",
                    instance_id="asr-online-gpu1",
                    capability=capability,
                    service_url="http://asr-online-gpu1:8084",
                    expires_at=datetime.now(UTC) + timedelta(seconds=60),
                )
            finally:
                released.append("lease-asr-disconnect")

    class DisconnectedUpstream:
        async def send(self, message: bytes | str) -> None:
            del message

        async def recv(self) -> bytes | str:
            raise RuntimeError("operator disconnected")

    class Connector:
        @asynccontextmanager
        async def connect(self, url: str):
            del url
            yield DisconnectedUpstream()

    online_app = create_online_gateway_app(
        PlatformSettings(
            service_name="online-gateway-service",
            course_root=tmp_path / "course",
            result_root=tmp_path / "result",
        )
    )
    online_app.state.online_lease_client = LeaseClient()
    online_app.state.asr_websocket_connector = Connector()

    with TestClient(online_app) as client:
        with client.websocket_connect("/api/online/asr/stream") as websocket:
            message = websocket.receive()

    assert message == {
        "type": "websocket.close",
        "code": 1011,
        "reason": "实时 ASR 算子连接中断",
    }
    assert released == ["lease-asr-disconnect"]
