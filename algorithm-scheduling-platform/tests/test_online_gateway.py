import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.platform_common.config import PlatformSettings
from packages.platform_common.operator_registry import CapacityLease
from services.online_gateway_service.api import create_online_gateway_app
from services.online_gateway_service.capacity import OnlineCapacityLeaseError
from services.online_gateway_service.main import app


def test_online_gateway_exposes_vbas_request_level_proxy() -> None:
    route_paths = {route.path for route in app.routes}

    assert "/api/online/vbas/analyze" in route_paths


def test_online_gateway_exposes_face_recognition_proxy() -> None:
    route_paths = {route.path for route in app.routes}

    assert "/api/online/face/recognize" in route_paths


def test_online_gateway_exposes_image_quality_detect_all_proxy() -> None:
    route_paths = {route.path for route in app.routes}

    assert "/api/online/image-quality/detect" in route_paths


def test_online_gateway_exposes_realtime_asr_websocket() -> None:
    route_paths = {route.path for route in app.routes}

    assert "/api/online/asr/stream" in route_paths


def test_online_vbas_proxies_complete_base64_request_through_one_lease(
    tmp_path: Path,
) -> None:
    acquired: list[str] = []
    released: list[str] = []
    forwarded: list[tuple[str, dict[str, object]]] = []

    class LeaseClient:
        @asynccontextmanager
        async def acquire(self, capability: str, *, ttl_seconds: int = 60):
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
                "StoragePath": "data:image/jpeg;base64,AA==",
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
            response = client.post("/api/online/vbas/analyze", json=request_body)
    finally:
        asyncio.run(online_app.state.online_http_client.aclose())

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "VBas 在线分析完成",
        "data": {
            "StatusObject": {"StatusString": "success", "StatusCode": 0},
            "DataList": [],
        },
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
        async def acquire(self, capability: str, *, ttl_seconds: int = 60):
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
        "photo": "data:image/jpeg;base64,AA==",
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
        async def acquire(self, capability: str, *, ttl_seconds: int = 60):
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
        "image": "data:image/jpeg;base64,AA==",
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
        async def acquire(self, capability: str, *, ttl_seconds: int = 60):
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
            {"ImageId": "image-ok", "StoragePath": "AA=="},
            {"ImageId": "image-failed", "StoragePath": "AQ=="},
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
            body = client.post("/api/online/vbas/analyze", json=request_body).json()
    finally:
        asyncio.run(online_app.state.online_http_client.aclose())

    assert lease_count == 1
    assert forwarded_bodies == [request_body]
    assert [
        item["StatusObject"]["ImageId"] for item in body["data"]["DataList"]
    ] == ["image-ok", "image-failed"]
    assert body["data"]["DataList"][1]["StatusObject"]["StatusCode"] == 400


def test_realtime_asr_keeps_one_sticky_lease_for_the_websocket_session(
    tmp_path: Path,
) -> None:
    acquired: list[str] = []
    released: list[str] = []
    connected_urls: list[str] = []
    upstream_messages: list[bytes | str] = []

    class LeaseClient:
        @asynccontextmanager
        async def acquire(self, capability: str, *, ttl_seconds: int = 60):
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


def test_online_http_returns_bounded_business_error_when_capacity_is_unavailable(
    tmp_path: Path,
) -> None:
    class LeaseClient:
        @asynccontextmanager
        async def acquire(self, capability: str, *, ttl_seconds: int = 60):
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
            json={"photo": "data:image/jpeg;base64,AA=="},
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
        async def acquire(self, capability: str, *, ttl_seconds: int = 60):
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
                    json={"image": "AA=="},
                ),
                client.post(
                    "/api/online/image-quality/detect",
                    json={"image": "AQ=="},
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
        async def acquire(self, capability: str, *, ttl_seconds: int = 60):
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
