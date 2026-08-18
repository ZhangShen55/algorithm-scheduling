import asyncio
from collections.abc import Iterator
from contextlib import asynccontextmanager, suppress
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
from packages.operator_registry_client.runtime import _wrap_lifespan


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
        management_token="registry-test-token",
    )


def test_registry_package_exposes_reusable_fastapi_runtime_installer() -> None:
    assert hasattr(registry_package, "install_operator_runtime")


def test_runtime_installer_exposes_identity_status_and_drain_without_changing_business_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLATFORM_INSTANCE_ID", "ocr-gpu0")
    monkeypatch.setenv("PLATFORM_MODEL_VERSION", "ocr-v6")
    monkeypatch.setenv("PLATFORM_API_VERSION", "v1")
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
        metadata = client.get("/ops/metadata")
        before = client.get("/ops/status")
        business = client.get("/inference")
        drained = client.post("/ops/drain")
        rejected = client.get("/inference")

    assert metadata.status_code == 200
    assert metadata.json() == {
        "instance_id": "ocr-gpu0",
        "operator_code": "ocr",
        "capabilities": ["ocr"],
        "model_version": "ocr-v6",
        "api_version": "v1",
    }
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
async def test_registry_wrapper_runs_pre_shutdown_hook_before_network_stop() -> None:
    events: list[str] = []
    app = FastAPI()

    @asynccontextmanager
    async def service_lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        events.append("service-start")
        try:
            yield
        finally:
            events.append("service-stop")

    class RegistryClient:
        async def start(self) -> None:
            events.append("registry-start")

        async def stop(self) -> None:
            events.append("registry-stop")

        async def aclose(self) -> None:
            events.append("registry-close")

    app.router.lifespan_context = service_lifespan
    _wrap_lifespan(
        app,
        RegistryClient(),  # type: ignore[arg-type]
        before_registry_shutdown=lambda: events.append("pre-shutdown"),
    )

    async with app.router.lifespan_context(app):
        events.append("application")

    assert events == [
        "service-start",
        "registry-start",
        "application",
        "pre-shutdown",
        "registry-stop",
        "registry-close",
        "service-stop",
    ]


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
    assert [
        request.headers["x-operator-registry-token"] for request in requests
    ] == ["registry-test-token", "registry-test-token"]
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
    assert [
        request.headers["x-operator-registry-token"] for request in requests
    ] == ["registry-test-token", "registry-test-token"]


@pytest.mark.asyncio
async def test_normal_stop_allows_same_instance_to_restart_online_and_lease() -> None:
    desired_state = "ONLINE"
    live = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal desired_state, live
        if request.url.path.endswith("/register"):
            return httpx.Response(201, json={"instance_id": "ocr-gpu0"})
        if request.url.path.endswith("/heartbeat"):
            live = desired_state == "ONLINE"
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path.endswith("/lifecycle"):
            desired_state = request.read().decode()
            if '"DRAINING"' in desired_state:
                desired_state = "DRAINING"
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path.endswith("/unregister"):
            live = False
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path.endswith("/lease"):
            status_code = 200 if desired_state == "ONLINE" and live else 503
            return httpx.Response(status_code, json={"status": "ok"})
        raise AssertionError(f"unexpected request: {request.url.path}")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    registry_client = OperatorRegistryClient(
        client_config(),
        status_provider=lambda: OperatorRuntimeStatus(inflight=0, model_ready=True),
        http_client=http_client,
    )

    try:
        await registry_client.start()
        await registry_client.stop()
        await registry_client.start()
        lease = await http_client.post(
            "http://control/internal/operator-instances/lease",
            json={"capability": "ocr", "ttl_seconds": 30},
        )

        assert desired_state == "ONLINE"
        assert lease.status_code == 200
    finally:
        await registry_client.stop()
        await registry_client.aclose()


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
async def test_client_start_retries_registration_and_initial_heartbeat_until_recovery() -> None:
    requests: list[str] = []
    registration_attempts = 0
    heartbeat_attempts = 0
    registration_failed = asyncio.Event()
    heartbeat_failed = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal heartbeat_attempts, registration_attempts
        requests.append(request.url.path)
        if request.url.path.endswith("/register"):
            registration_attempts += 1
            if registration_attempts == 1:
                registration_failed.set()
                return httpx.Response(503, json={"detail": "control unavailable"})
            return httpx.Response(201, json={"instance_id": "ocr-gpu0"})
        if request.url.path.endswith("/heartbeat"):
            heartbeat_attempts += 1
            if heartbeat_attempts == 1:
                heartbeat_failed.set()
                return httpx.Response(503, json={"detail": "control unavailable"})
        return httpx.Response(200, json={"status": "ok"})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    registry_client = OperatorRegistryClient(
        replace(client_config(), heartbeat_interval_seconds=0.05),
        status_provider=lambda: OperatorRuntimeStatus(inflight=0, model_ready=True),
        http_client=http_client,
    )
    start_task = asyncio.create_task(registry_client.start())

    try:
        await asyncio.wait_for(registration_failed.wait(), timeout=0.5)
        await asyncio.sleep(0)
        assert not start_task.done()
        await asyncio.wait_for(heartbeat_failed.wait(), timeout=0.5)
        await asyncio.sleep(0)
        assert not start_task.done()
        await asyncio.wait_for(start_task, timeout=0.5)
        assert requests[:5] == [
            "/api/operator-instances/register",
            "/api/operator-instances/register",
            "/api/operator-instances/heartbeat",
            "/api/operator-instances/register",
            "/api/operator-instances/heartbeat",
        ]
    finally:
        if not start_task.done():
            start_task.cancel()
            with suppress(asyncio.CancelledError):
                await start_task
        with suppress(httpx.HTTPError):
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


@pytest.mark.asyncio
async def test_background_heartbeat_reregisters_after_registry_loss() -> None:
    requests: list[str] = []
    heartbeat_attempts = 0
    recovered = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal heartbeat_attempts
        requests.append(request.url.path)
        if request.url.path.endswith("/register"):
            return httpx.Response(201, json={"instance_id": "ocr-gpu0"})
        if request.url.path.endswith("/heartbeat"):
            heartbeat_attempts += 1
            if heartbeat_attempts == 2:
                return httpx.Response(503, json={"detail": "temporary unavailable"})
            if heartbeat_attempts == 3:
                return httpx.Response(404, json={"detail": "instance missing"})
            if heartbeat_attempts == 4:
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
        assert requests[:6] == [
            "/api/operator-instances/register",
            "/api/operator-instances/heartbeat",
            "/api/operator-instances/heartbeat",
            "/api/operator-instances/heartbeat",
            "/api/operator-instances/register",
            "/api/operator-instances/heartbeat",
        ]
    finally:
        with suppress(httpx.HTTPError):
            await registry_client.stop()
        await registry_client.aclose()


@pytest.mark.asyncio
async def test_background_heartbeat_survives_python310_asyncio_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Python310AsyncioTimeoutError(Exception):
        pass

    heartbeat_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal heartbeat_attempts
        if request.url.path.endswith("/register"):
            return httpx.Response(201, json={"instance_id": "ocr-gpu0"})
        if request.url.path.endswith("/heartbeat"):
            heartbeat_attempts += 1
        return httpx.Response(200, json={"status": "ok"})

    original_wait_for = asyncio.wait_for
    wait_calls = 0

    async def python310_wait_for(awaitable: object, timeout: float) -> object:
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            close = getattr(awaitable, "close", None)
            if close is not None:
                close()
            raise Python310AsyncioTimeoutError
        return await original_wait_for(awaitable, timeout=timeout)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "TimeoutError", Python310AsyncioTimeoutError)
    monkeypatch.setattr(asyncio, "wait_for", python310_wait_for)
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    registry_client = OperatorRegistryClient(
        client_config(),
        status_provider=lambda: OperatorRuntimeStatus(inflight=0, model_ready=True),
        http_client=http_client,
    )

    try:
        await registry_client.start()
        for _ in range(20):
            if heartbeat_attempts >= 2:
                break
            await asyncio.sleep(0)
        assert heartbeat_attempts >= 2
    finally:
        with suppress(Python310AsyncioTimeoutError, httpx.HTTPError):
            await registry_client.stop()
        await registry_client.aclose()
