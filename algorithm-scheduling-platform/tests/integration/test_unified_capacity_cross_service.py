from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from control_service.app.api.control import create_control_app
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from online_gateway_service.app.api.routes import create_online_gateway_app
from online_gateway_service.app.core.config import OnlineGatewaySettings
from online_gateway_service.app.infrastructure.capacity import (
    OnlineCapacityLeaseClient,
    OnlineWorkContext,
)
from orchestrator_service.app.domain.errors import CapacityUnavailableError
from orchestrator_service.app.domain.ppt_work import PptImageWork, PptWorkLimits
from orchestrator_service.app.infrastructure.control_client import ControlLeaseClient
from orchestrator_service.app.infrastructure.ppt_text import OcrAdapter, PptTextPipeline
from redis import Redis
from vision_orchestrator_service.app.infrastructure.cache import VisionStream
from vision_orchestrator_service.app.infrastructure.capacity import (
    CapacityLeaseHttpClient,
)
from vision_orchestrator_service.app.infrastructure.capacity import (
    WorkContext as VisionWorkContext,
)
from vision_orchestrator_service.app.infrastructure.vbas import (
    VbasBatchClient,
    VbasBatchConfig,
    VbasFrame,
)

from packages.platform_common.operator_registry import (
    CapacityLeaseNotFoundError,
    OperatorCode,
    OperatorInstance,
    WorkContext,
)
from packages.platform_common.redis_operator_registry import RedisOperatorRegistry
from packages.platform_common.repository import (
    NodeResultWrite,
    NodeWorkItemRecord,
    NodeWorkItemWrite,
    WorkItemProgress,
)
from packages.platform_contracts.status import NodeStatus

pytestmark = pytest.mark.integration
TEST_REDIS_URL = "redis://127.0.0.1:6379/15"


class HostRoutingTransport(httpx.AsyncBaseTransport):
    def __init__(self, applications: dict[str, FastAPI]) -> None:
        self._transports = {
            host: httpx.ASGITransport(app=application)
            for host, application in applications.items()
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = self._transports.get(request.url.host)
        if transport is None:
            raise httpx.ConnectError(
                f"测试请求访问了未声明的主机: {request.url.host}",
                request=request,
            )
        return await transport.handle_async_request(request)

    async def aclose(self) -> None:
        await asyncio.gather(
            *(transport.aclose() for transport in self._transports.values())
        )


class InMemoryPptRepository:
    def __init__(self) -> None:
        self.items: dict[int, dict[str, dict[str, Any] | None]] = {}
        self.completed_nodes: dict[int, NodeResultWrite] = {}

    def create_node_work_items(
        self,
        task_node_id: int,
        items: list[NodeWorkItemWrite],
    ) -> list[NodeWorkItemRecord]:
        node_items = self.items.setdefault(task_node_id, {})
        for item in items:
            node_items.setdefault(item.item_key, item.result)
        return self.list_node_work_items(task_node_id)

    def complete_node_work_item(
        self,
        task_node_id: int,
        item_key: str,
        result: dict[str, Any],
        *,
        reason: str,
    ) -> WorkItemProgress:
        del reason
        self.items[task_node_id][item_key] = result
        values = self.items[task_node_id].values()
        return WorkItemProgress(
            completed_count=sum(value is not None for value in values),
            total_count=len(self.items[task_node_id]),
        )

    def list_node_work_items(self, task_node_id: int) -> list[NodeWorkItemRecord]:
        return [
            NodeWorkItemRecord(
                id=index + 1,
                task_node_id=task_node_id,
                item_key=item_key,
                ordinal=index,
                status=(
                    NodeStatus.COMPLETED
                    if result is not None
                    else NodeStatus.PENDING
                ),
                reason="已完成" if result is not None else "等待处理",
                result=result,
            )
            for index, (item_key, result) in enumerate(
                self.items.get(task_node_id, {}).items()
            )
        ]

    def complete_node(
        self,
        node_id: int,
        result: NodeResultWrite,
        *,
        reason: str,
    ) -> object:
        del reason
        self.completed_nodes[node_id] = result
        return object()


@pytest.fixture
def redis_registry() -> Iterator[RedisOperatorRegistry]:
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    key_prefix = f"algorithm-platform:test:unified-capacity:{uuid4().hex}:"
    try:
        client.ping()
    except Exception as exc:
        client.close()
        pytest.skip(f"Redis 集成测试环境不可用: {exc}")
    registry = RedisOperatorRegistry(
        client,
        heartbeat_ttl_seconds=30,
        key_prefix=key_prefix,
    )
    try:
        yield registry
    finally:
        keys = list(client.scan_iter(match=f"{key_prefix}*", count=100))
        if keys:
            client.delete(*keys)
        client.close()


def _register_ready(
    registry: RedisOperatorRegistry,
    *,
    instance_id: str,
    operator_code: OperatorCode,
    capabilities: list[str],
    service_url: str,
    capacity: int = 1,
    inflight: int = 0,
) -> None:
    registry.register(
        OperatorInstance(
            instance_id=instance_id,
            operator_code=operator_code,
            capabilities=capabilities,
            service_url=service_url,
            declared_capacity=capacity,
        )
    )
    registry.heartbeat(
        instance_id,
        inflight=inflight,
        model_ready=True,
    )


def _control_app(registry: RedisOperatorRegistry) -> FastAPI:
    return create_control_app(
        repository=object(),
        operator_registry=registry,
    )


@asynccontextmanager
async def _gateway_runtime(
    registry: RedisOperatorRegistry,
    operator_apps: dict[str, FastAPI],
    *,
    decoded_limit: int = 52_428_800,
    body_limit: int = 75_497_472,
    lease_ttl_seconds: int = 60,
) -> AsyncIterator[
    tuple[httpx.AsyncClient, httpx.AsyncClient, httpx.AsyncClient]
]:
    control = _control_app(registry)
    settings = OnlineGatewaySettings.model_validate(
        {
            "control": {"base_url": "http://control.test"},
            "leases": {
                "request_ttl_seconds": lease_ttl_seconds,
                "websocket_ttl_seconds": lease_ttl_seconds,
            },
            "http": {"hard_timeout_seconds": 5.0},
            "base64": {"max_decoded_bytes": decoded_limit},
            "body": {"max_bytes": body_limit},
        }
    )
    gateway = create_online_gateway_app(settings)
    await gateway.state.online_http_client.aclose()
    routed_http = httpx.AsyncClient(
        transport=HostRoutingTransport(
            {"control.test": control, **operator_apps}
        )
    )
    gateway.state.online_http_client = routed_http
    gateway.state.online_lease_client = OnlineCapacityLeaseClient(
        routed_http,
        control_service_url="http://control.test",
    )
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway),
            base_url="http://gateway.test",
        ) as gateway_http,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=control),
            base_url="http://control.test",
        ) as control_http,
    ):
        try:
            yield gateway_http, control_http, routed_http
        finally:
            await routed_http.aclose()


def _ocr_response(image_id: str) -> dict[str, Any]:
    return {
        "key": [image_id],
        "value": [json.dumps([{"text": "数学"}], ensure_ascii=False)],
        "formula_results": [[]],
        "err_no": 0,
        "err_msg": "",
    }


@pytest.mark.asyncio
async def test_online_ocr_crosses_gateway_control_and_contract_operator(
    redis_registry: RedisOperatorRegistry,
) -> None:
    requests: list[dict[str, Any]] = []
    mode = "block"
    started = asyncio.Event()
    unblock = asyncio.Event()
    ocr = FastAPI()

    @ocr.post("/ocr/prediction")
    async def recognize(request: Request) -> Any:
        body = await request.json()
        requests.append(body)
        if mode == "invalid":
            return PlainTextResponse("not-json")
        if mode == "block":
            started.set()
            await unblock.wait()
        return _ocr_response(body["key"][0])

    _register_ready(
        redis_registry,
        instance_id="ocr-contract-0",
        operator_code=OperatorCode.OCR,
        capabilities=["ocr"],
        service_url="http://ocr.test",
    )
    valid_image = base64.b64encode(b"image").decode()

    async with _gateway_runtime(
        redis_registry,
        {"ocr.test": ocr},
        decoded_limit=8,
        body_limit=256,
    ) as (gateway, control, _):
        request_task = asyncio.create_task(
            gateway.post(
                "/api/online/ocr/recognize",
                json={"image_id": "frame-001", "image": valid_image},
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        active = (
            await control.get(
                "/ops/operator-instances/ocr-contract-0/active-leases"
            )
        ).json()
        assert active["active_lease_count"] == 1
        assert active["leases"][0]["context_status"] == "BOUND"
        assert active["leases"][0]["work_context"]["source_service"] == (
            "online-gateway-service"
        )
        assert active["leases"][0]["work_context"]["work_type"] == "online_ocr"

        unblock.set()
        response = await request_task
        assert response.status_code == 200
        assert response.json()["code"] == 0
        assert response.json()["data"] == _ocr_response("frame-001")
        assert requests[0]["enable_formula"] is False
        assert redis_registry.list_active_leases(
            "ocr-contract-0"
        ).active_lease_count == 0

        mode = "success"
        generated = await gateway.post(
            "/api/online/ocr/recognize",
            json={"image": valid_image, "enable_formula": True},
        )
        generated_key = generated.json()["data"]["key"][0]
        assert generated.json()["code"] == 0
        assert generated_key.startswith("online-ocr-")
        assert requests[-1]["key"] == [generated_key]
        assert requests[-1]["enable_formula"] is True

        for payload in (
            {"image": "%%%"},
            {"image": base64.b64encode(b"x" * 9).decode()},
            {"image": "A" * 300},
        ):
            invalid = await gateway.post(
                "/api/online/ocr/recognize",
                json=payload,
            )
            assert invalid.status_code == 200
            assert invalid.json()["code"] == 40001
            assert redis_registry.list_active_leases(
                "ocr-contract-0"
            ).active_lease_count == 0

        held = redis_registry.lease("ocr", 30)
        unavailable = await gateway.post(
            "/api/online/ocr/recognize",
            json={"image": valid_image},
        )
        assert unavailable.json()["code"] == 50301
        redis_registry.release(held.lease_id)

        mode = "invalid"
        upstream_failure = await gateway.post(
            "/api/online/ocr/recognize",
            json={"image": valid_image},
        )
        assert upstream_failure.json()["code"] == 50000
        assert redis_registry.list_active_leases(
            "ocr-contract-0"
        ).active_lease_count == 0


@pytest.mark.asyncio
async def test_online_and_ppt_ocr_share_one_pool_without_losing_offline_work(
    redis_registry: RedisOperatorRegistry,
    tmp_path: Path,
) -> None:
    started = asyncio.Event()
    unblock = asyncio.Event()
    should_block = True
    ocr = FastAPI()

    @ocr.post("/ocr/prediction")
    async def recognize(request: Request) -> dict[str, Any]:
        body = await request.json()
        if should_block:
            started.set()
            await unblock.wait()
        return _ocr_response(body["key"][0])

    _register_ready(
        redis_registry,
        instance_id="ocr-shared-0",
        operator_code=OperatorCode.OCR,
        capabilities=["ocr"],
        service_url="http://ocr.test",
    )
    image_path = tmp_path / "slide.jpg"
    image_path.write_bytes(b"image")
    repository = InMemoryPptRepository()

    async with _gateway_runtime(
        redis_registry,
        {"ocr.test": ocr},
    ) as (gateway, control, routed_http):
        lease_client = ControlLeaseClient(control, default_ttl_seconds=30)
        pipeline = PptTextPipeline(
            repository,
            lease_client,
            OcrAdapter(routed_http),
            PptWorkLimits(batch_size=1, max_concurrency=1),
            lease_ttl_seconds=30,
        )
        online_task = asyncio.create_task(
            gateway.post(
                "/api/online/ocr/recognize",
                json={"image": base64.b64encode(b"image").decode()},
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)

        with pytest.raises(CapacityUnavailableError):
            await pipeline.run_ocr(
                task_id="course-shared",
                node_id=11,
                work=[PptImageWork("ppt-001", image_path, 0)],
            )
        assert repository.items[11]["ppt-001"] is None
        assert 11 not in repository.completed_nodes

        should_block = False
        unblock.set()
        assert (await online_task).json()["code"] == 0
        offline_result = await pipeline.run_ocr(
            task_id="course-shared",
            node_id=11,
            work=[PptImageWork("ppt-001", image_path, 0)],
        )
        assert offline_result["ppt-001"]["text"] == "数学"
        assert repository.completed_nodes[11].result == offline_result
        assert redis_registry.list_active_leases(
            "ocr-shared-0"
        ).active_lease_count == 0


def test_deterministic_instance_preference_is_allowed_until_capacity_is_full(
    redis_registry: RedisOperatorRegistry,
) -> None:
    _register_ready(
        redis_registry,
        instance_id="ocr-gpu0",
        operator_code=OperatorCode.OCR,
        capabilities=["ocr"],
        service_url="http://ocr-gpu0.test",
        capacity=2,
    )
    _register_ready(
        redis_registry,
        instance_id="ocr-gpu1",
        operator_code=OperatorCode.OCR,
        capabilities=["ocr"],
        service_url="http://ocr-gpu1.test",
        capacity=1,
    )

    first = redis_registry.lease("ocr", 30)
    second = redis_registry.lease("ocr", 30)
    third = redis_registry.lease("ocr", 30)

    assert [first.instance_id, second.instance_id, third.instance_id] == [
        "ocr-gpu0",
        "ocr-gpu0",
        "ocr-gpu1",
    ]


@pytest.mark.asyncio
async def test_ppt_items_use_independent_leases_and_multiple_instances(
    redis_registry: RedisOperatorRegistry,
    tmp_path: Path,
) -> None:
    ocr_started = asyncio.Event()
    unblock_ocr = asyncio.Event()
    ocr_hosts: list[str] = []
    counter_lock = asyncio.Lock()
    operators = FastAPI()

    @operators.post("/ocr/prediction")
    async def recognize(request: Request) -> dict[str, Any]:
        body = await request.json()
        async with counter_lock:
            ocr_hosts.append(str(request.url.hostname))
            if len(ocr_hosts) == 2:
                ocr_started.set()
        await unblock_ocr.wait()
        return _ocr_response(body["key"][0])

    for index in range(2):
        _register_ready(
            redis_registry,
            instance_id=f"ocr-contract-{index}",
            operator_code=OperatorCode.OCR,
            capabilities=["ocr"],
            service_url=f"http://ocr-{index}.test",
        )
    applications = {f"ocr-{index}.test": operators for index in range(2)}
    work: list[PptImageWork] = []
    for index in range(2):
        path = tmp_path / f"slide-{index}.jpg"
        path.write_bytes(f"image-{index}".encode())
        work.append(PptImageWork(f"ppt-{index}", path, index))
    repository = InMemoryPptRepository()

    async with _gateway_runtime(
        redis_registry,
        applications,
    ) as (_, control, routed_http):
        pipeline = PptTextPipeline(
            repository,
            ControlLeaseClient(control, default_ttl_seconds=30),
            OcrAdapter(routed_http),
            PptWorkLimits(batch_size=2, max_concurrency=2),
            lease_ttl_seconds=30,
        )
        ocr_task = asyncio.create_task(
            pipeline.run_ocr(
                task_id="course-ppt",
                node_id=21,
                work=work,
                trace_id="trace-ppt",
            )
        )
        await asyncio.wait_for(ocr_started.wait(), timeout=1)
        ocr_snapshots = [
            redis_registry.list_active_leases(f"ocr-contract-{index}")
            for index in range(2)
        ]
        assert [snapshot.active_lease_count for snapshot in ocr_snapshots] == [1, 1]
        assert {
            snapshot.leases[0].work_context.item_id
            for snapshot in ocr_snapshots
            if snapshot.leases[0].work_context is not None
        } == {"ppt-0", "ppt-1"}
        assert all(
            snapshot.leases[0].work_context is not None
            and snapshot.leases[0].work_context.work_type == "ppt_ocr_item"
            for snapshot in ocr_snapshots
        )
        unblock_ocr.set()
        ocr_results = await ocr_task

    assert set(ocr_hosts) == {"ocr-0.test", "ocr-1.test"}
    assert list(ocr_results) == ["ppt-0", "ppt-1"]
    assert all(
        redis_registry.list_active_leases(f"ocr-contract-{index}").active_lease_count
        == 0
        for index in range(2)
    )


@pytest.mark.asyncio
async def test_control_reports_heartbeat_difference_without_using_it_for_admission(
    redis_registry: RedisOperatorRegistry,
) -> None:
    _register_ready(
        redis_registry,
        instance_id="ocr-observation-0",
        operator_code=OperatorCode.OCR,
        capabilities=["ocr"],
        service_url="http://ocr.test",
        inflight=1,
    )
    control = _control_app(redis_registry)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=control),
        base_url="http://control.test",
    ) as client:
        empty = (
            await client.get(
                "/ops/operator-instances/ocr-observation-0/active-leases"
            )
        ).json()
        assert empty["active_lease_count"] == 0
        assert empty["reported_inflight"] == 1
        assert empty["attribution_difference"] == 1

        lease = redis_registry.lease("ocr", 30)
        redis_registry.heartbeat(
            "ocr-observation-0",
            inflight=0,
            model_ready=True,
        )
        active = (
            await client.get(
                "/ops/operator-instances/ocr-observation-0/active-leases"
            )
        ).json()
        assert active["active_lease_count"] == 1
        assert active["reported_inflight"] == 0
        assert active["attribution_difference"] == -1
        assert active["leases"][0]["context_status"] == "UNBOUND"
        redis_registry.release(lease.lease_id)

        redis_registry.heartbeat(
            "ocr-observation-0",
            inflight=1,
            model_ready=True,
        )
        replacement = redis_registry.lease("ocr", 30)
        assert replacement.instance_id == "ocr-observation-0"
        redis_registry.release(replacement.lease_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "instance_id",
        "operator_code",
        "capability",
        "gateway_path",
        "operator_path",
        "payload",
    ),
    (
        (
            "vbas-full-0",
            OperatorCode.VBAS,
            "teacher_behavior",
            "/api/online/vbas/analyze",
            "/ImageDetect/teacher/v1.0.0",
            {
                "stream_type": "teacher",
                "ImageList": [{"ImageId": "frame-1", "StoragePath": "AA=="}],
            },
        ),
        (
            "facerec-full-0",
            OperatorCode.FACEREC,
            "recognize",
            "/api/online/face/recognize",
            "/recognize",
            {"photo": "AA=="},
        ),
        (
            "screen-full-0",
            OperatorCode.SCREEN_DET,
            "detect_all",
            "/api/online/image-quality/detect",
            "/detect_all",
            {"image": "AA=="},
        ),
        (
            "ocr-full-0",
            OperatorCode.OCR,
            "ocr",
            "/api/online/ocr/recognize",
            "/ocr/prediction",
            {"image": "AA=="},
        ),
    ),
)
async def test_concurrent_online_http_returns_50301_while_operator_waits(
    redis_registry: RedisOperatorRegistry,
    instance_id: str,
    operator_code: OperatorCode,
    capability: str,
    gateway_path: str,
    operator_path: str,
    payload: dict[str, Any],
) -> None:
    started = asyncio.Event()
    unblock = asyncio.Event()
    operator = FastAPI()

    async def wait_inside_operator() -> dict[str, Any]:
        started.set()
        await unblock.wait()
        return {"status": "ok"}

    operator.post(operator_path)(wait_inside_operator)
    _register_ready(
        redis_registry,
        instance_id=instance_id,
        operator_code=operator_code,
        capabilities=[capability],
        service_url=f"http://{instance_id}.test",
    )
    async with _gateway_runtime(
        redis_registry,
        {f"{instance_id}.test": operator},
    ) as (gateway, _, __):
        first_request = asyncio.create_task(gateway.post(gateway_path, json=payload))
        await asyncio.wait_for(started.wait(), timeout=1)
        assert redis_registry.list_active_leases(instance_id).active_lease_count == 1

        response = await gateway.post(gateway_path, json=payload)
        assert response.status_code == 200
        assert response.json()["code"] == 50301
        assert redis_registry.list_active_leases(instance_id).active_lease_count == 1

        unblock.set()
        assert (await first_request).json()["code"] == 0

    assert redis_registry.list_active_leases(instance_id).active_lease_count == 0


@pytest.mark.asyncio
async def test_three_calling_services_renew_same_lease_across_ttl_and_release(
    redis_registry: RedisOperatorRegistry,
) -> None:
    instances = (
        (
            "asr-renew-0",
            OperatorCode.ASR_OFFLINE,
            ["asr_offline"],
            "http://asr.test",
        ),
        (
            "vbas-renew-0",
            OperatorCode.VBAS,
            ["teacher_behavior"],
            "http://vbas.test",
        ),
        ("ocr-renew-0", OperatorCode.OCR, ["ocr"], "http://ocr.test"),
    )
    for instance_id, operator_code, capabilities, service_url in instances:
        _register_ready(
            redis_registry,
            instance_id=instance_id,
            operator_code=operator_code,
            capabilities=capabilities,
            service_url=service_url,
        )
    control = _control_app(redis_registry)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=control),
        base_url="http://control.test",
    ) as client:
        orchestrator = ControlLeaseClient(client, default_ttl_seconds=1)
        orchestrator_lease = await orchestrator.acquire(
            "asr_offline",
            work_context=WorkContext(
                source_service="orchestrator-service",
                work_type="node",
                work_id="node-31",
                task_id="course-renew",
                node_id="31",
            ),
        )

        async def long_operation() -> str:
            await asyncio.sleep(1.2)
            return "done"

        operation = asyncio.create_task(
            orchestrator.run_with_renewal(
                orchestrator_lease,
                long_operation(),
                renew_interval_seconds=0.2,
                hard_timeout_seconds=3,
            )
        )
        await asyncio.sleep(1.05)
        orchestrator_snapshot = redis_registry.list_active_leases("asr-renew-0")
        assert orchestrator_snapshot.active_lease_count == 1
        assert orchestrator_snapshot.leases[0].lease_id == orchestrator_lease.lease_id
        assert orchestrator_snapshot.leases[0].acquired_at == (
            orchestrator_lease.acquired_at
        )
        assert await operation == "done"
        await orchestrator.release(orchestrator_lease.lease_id)
        assert redis_registry.list_active_leases(
            "asr-renew-0"
        ).active_lease_count == 0

        vision = CapacityLeaseHttpClient(
            client,
            control_service_url="http://control.test",
        )
        async with vision.acquire(
            "teacher_behavior",
            ttl_seconds=1,
            renew_interval_seconds=0.2,
            work_context=VisionWorkContext(
                source_service="vision-orchestrator-service",
                work_type="vbas_teacher_batch",
                work_id="batch-1",
                task_id="course-renew",
            ),
        ) as vision_lease:
            await asyncio.sleep(1.05)
            vision_snapshot = redis_registry.list_active_leases("vbas-renew-0")
            assert vision_snapshot.active_lease_count == 1
            assert vision_snapshot.leases[0].lease_id == vision_lease.lease_id
        assert redis_registry.list_active_leases(
            "vbas-renew-0"
        ).active_lease_count == 0

        online = OnlineCapacityLeaseClient(
            client,
            control_service_url="http://control.test",
        )
        async with online.acquire(
            "ocr",
            ttl_seconds=1,
            renew_interval_seconds=0.2,
            work_context=OnlineWorkContext(
                source_service="online-gateway-service",
                work_type="online_ocr",
                work_id="request-1",
            ),
        ) as online_lease:
            await asyncio.sleep(1.05)
            online_snapshot = redis_registry.list_active_leases("ocr-renew-0")
            assert online_snapshot.active_lease_count == 1
            assert online_snapshot.leases[0].lease_id == online_lease.lease_id
        assert redis_registry.list_active_leases(
            "ocr-renew-0"
        ).active_lease_count == 0

        abandoned = redis_registry.lease("ocr", 1)
        await asyncio.sleep(1.05)
        assert redis_registry.list_active_leases(
            "ocr-renew-0"
        ).active_lease_count == 0
        with pytest.raises(CapacityLeaseNotFoundError):
            redis_registry.renew(abandoned.lease_id, 1)


@pytest.mark.asyncio
async def test_vision_vbas_batch_crosses_control_and_renews_one_attributed_lease(
    redis_registry: RedisOperatorRegistry,
    tmp_path: Path,
) -> None:
    started = asyncio.Event()
    unblock = asyncio.Event()
    captured_request: dict[str, Any] = {}
    vbas = FastAPI()

    @vbas.post("/ImageDetect/teacher/v1.0.0")
    async def analyze_teacher(request: Request) -> dict[str, Any]:
        captured_request.update(await request.json())
        started.set()
        await unblock.wait()
        return {
            "StatusObject": {"StatusString": "success", "StatusCode": 0},
            "DataList": [
                {
                    "StatusObject": {
                        "StatusString": "success",
                        "StatusCode": 0,
                        "ImageId": image["ImageId"],
                    },
                    "ResultList": [],
                }
                for image in captured_request["ImageList"]
            ],
        }

    _register_ready(
        redis_registry,
        instance_id="vbas-contract-0",
        operator_code=OperatorCode.VBAS,
        capabilities=["teacher_behavior", "student_behavior"],
        service_url="http://vbas.test",
    )
    frame_path = (tmp_path / "teacher.jpg").resolve()
    frame_path.write_bytes(b"image")

    async with _gateway_runtime(
        redis_registry,
        {"vbas.test": vbas},
    ) as (_, control, routed_http):
        client = VbasBatchClient(
            routed_http,
            CapacityLeaseHttpClient(
                routed_http,
                control_service_url="http://control.test",
            ),
            config=VbasBatchConfig(
                batch_size=2,
                max_concurrency=1,
                lease_ttl_seconds=1,
                request_timeout_seconds=3,
            ),
        )
        operation = asyncio.create_task(
            client.analyze(
                task_id="course-vision",
                stream=VisionStream.TEACHER,
                frames=[VbasFrame("teacher-001", frame_path, 1, 10.0)],
                trace_id="trace-vision",
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        initial = (
            await control.get(
                "/ops/operator-instances/vbas-contract-0/active-leases"
            )
        ).json()
        assert initial["active_lease_count"] == 1
        assert initial["leases"][0]["work_context"] == {
            "source_service": "vision-orchestrator-service",
            "work_type": "vbas_teacher_batch",
            "work_id": "course-vision-t-0000",
            "task_id": "course-vision",
            "node_id": None,
            "item_id": "course-vision-t-0000",
            "trace_id": "trace-vision",
        }
        lease_id = initial["leases"][0]["lease_id"]
        acquired_at = initial["leases"][0]["acquired_at"]

        await asyncio.sleep(1.05)
        renewed = (
            await control.get(
                "/ops/operator-instances/vbas-contract-0/active-leases"
            )
        ).json()
        assert renewed["active_lease_count"] == 1
        assert renewed["leases"][0]["lease_id"] == lease_id
        assert renewed["leases"][0]["acquired_at"] == acquired_at

        unblock.set()
        result = await operation

    assert result[0]["image_id"] == "teacher-001"
    assert captured_request["task_id"] == "course-vision"
    assert captured_request["ReturnHeadPose"] is False
    assert redis_registry.list_active_leases(
        "vbas-contract-0"
    ).active_lease_count == 0
