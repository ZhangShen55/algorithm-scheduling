import asyncio
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import packages.operator_registry_client as registry_package
from packages.operator_registry_client.client import (
    OperatorRegistryClient,
    OperatorRegistryClientConfig,
    OperatorRuntimeStatus,
)


@pytest.fixture
def captured_requests() -> Iterator[tuple[list[httpx.Request], httpx.AsyncClient]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/register"):
            return httpx.Response(201, json={"instance_id": "ocr-gpu0"})
        return httpx.Response(200, json={"status": "ok"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    yield requests, client


def client_config() -> OperatorRegistryClientConfig:
    return OperatorRegistryClientConfig(
        control_service_url="http://control-service:8000",
        instance_id="ocr-gpu0",
        operator_code="ocr",
        capabilities=["ocr"],
        service_url="http://ocr-gpu0:18002",
        declared_capacity=2,
        model_version="v6",
        api_version="v1",
        labels={"gpu": "0"},
        heartbeat_interval_seconds=5,
    )


def test_registry_package_exposes_reusable_fastapi_runtime_installer() -> None:
    assert hasattr(registry_package, "install_operator_runtime")


def test_runtime_installer_exposes_status_and_drain_without_changing_business_routes() -> None:
    install_operator_runtime = registry_package.install_operator_runtime
    app = FastAPI()

    @app.get("/inference")
    async def inference() -> dict[str, str]:
        return {"result": "ok"}

    runtime = install_operator_runtime(
        app,
        operator_code="ocr",
        capabilities=["ocr"],
        default_port=8866,
        declared_capacity=2,
        registration_enabled=False,
    )

    with TestClient(app) as client:
        before = client.get("/ops/status")
        business = client.get("/inference")
        drained = client.post("/ops/drain")
        rejected = client.get("/inference")

    assert before.json() == {
        "lifecycle": "ONLINE",
        "model_ready": True,
        "inflight": 0,
        "declared_capacity": 2,
    }
    assert business.json() == {"result": "ok"}
    assert drained.json()["lifecycle"] == "DRAINING"
    assert rejected.status_code == 503
    assert runtime.status().inflight == 0


def test_runtime_uses_configured_capacity_and_background_inflight_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLATFORM_DECLARED_CAPACITY", "15")
    active_tasks = 3
    app = FastAPI()

    runtime = registry_package.install_operator_runtime(
        app,
        operator_code="ppt_slice",
        capabilities=["ppt_slice"],
        default_port=9001,
        declared_capacity=1,
        inflight_provider=lambda: active_tasks,
        registration_enabled=False,
    )

    assert runtime.status().declared_capacity == 15
    assert runtime.status().inflight == 3
    assert runtime.heartbeat_status().inflight == 3


@pytest.mark.asyncio
async def test_client_registers_and_sends_runtime_heartbeat(
    captured_requests: tuple[list[httpx.Request], httpx.AsyncClient],
) -> None:
    requests, http_client = captured_requests
    registry_client = OperatorRegistryClient(
        client_config(),
        status_provider=lambda: OperatorRuntimeStatus(inflight=1, model_ready=True),
        http_client=http_client,
    )

    await registry_client.register()
    await registry_client.heartbeat()
    await registry_client.aclose()

    assert [request.url.path for request in requests] == [
        "/api/operator-instances/register",
        "/api/operator-instances/heartbeat",
    ]
    assert '"inflight":1' in requests[1].content.decode()


@pytest.mark.asyncio
async def test_client_drains_then_unregisters_on_shutdown(
    captured_requests: tuple[list[httpx.Request], httpx.AsyncClient],
) -> None:
    requests, http_client = captured_requests
    registry_client = OperatorRegistryClient(
        client_config(),
        status_provider=lambda: OperatorRuntimeStatus(inflight=0, model_ready=True),
        http_client=http_client,
    )

    await registry_client.drain()
    await registry_client.unregister()
    await registry_client.aclose()

    assert [request.url.path for request in requests] == [
        "/api/operator-instances/lifecycle",
        "/api/operator-instances/unregister",
    ]


@pytest.mark.asyncio
async def test_client_start_waits_for_first_successful_heartbeat(
    captured_requests: tuple[list[httpx.Request], httpx.AsyncClient],
) -> None:
    requests, http_client = captured_requests
    registry_client = OperatorRegistryClient(
        client_config(),
        status_provider=lambda: OperatorRuntimeStatus(inflight=0, model_ready=True),
        http_client=http_client,
    )

    try:
        await registry_client.start()
        assert [request.url.path for request in requests[:2]] == [
            "/api/operator-instances/register",
            "/api/operator-instances/heartbeat",
        ]
    finally:
        await registry_client.stop()
        await registry_client.aclose()


@pytest.mark.asyncio
async def test_background_heartbeat_retries_after_transient_http_failure() -> None:
    heartbeat_attempts = 0
    recovered = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal heartbeat_attempts
        if request.url.path.endswith("/register"):
            return httpx.Response(201, json={"instance_id": "ocr-gpu0"})
        if request.url.path.endswith("/heartbeat"):
            heartbeat_attempts += 1
            if heartbeat_attempts == 2:
                return httpx.Response(503, json={"detail": "temporary unavailable"})
            if heartbeat_attempts >= 3:
                recovered.set()
        return httpx.Response(200, json={"status": "ok"})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    registry_client = OperatorRegistryClient(
        replace(client_config(), heartbeat_interval_seconds=0.01),
        status_provider=lambda: OperatorRuntimeStatus(inflight=0, model_ready=True),
        http_client=http_client,
    )

    try:
        await registry_client.start()
        await asyncio.wait_for(recovered.wait(), timeout=0.5)
        assert heartbeat_attempts >= 3
    finally:
        with suppress(httpx.HTTPError):
            await registry_client.stop()
        await registry_client.aclose()
