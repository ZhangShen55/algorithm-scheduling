from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from control_service.app.api.control import create_control_app
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from packages.platform_common.config import PlatformSettings
from packages.platform_common.operator_registry import (
    ActiveCapacityLease,
    CapacityLease,
    CapacityLeaseContextConflictError,
    CapacityLeaseNotFoundError,
    CapacityUnavailableError,
    LeaseContextStatus,
    OperatorActiveLeases,
    OperatorCode,
    OperatorInstance,
    OperatorInstanceNotFoundError,
    WorkContext,
)

REGISTRY_TOKEN = "registry-test-token"
REGISTRY_HEADERS = {"X-Operator-Registry-Token": REGISTRY_TOKEN}


class FakeOperatorRegistry:
    def __init__(self) -> None:
        self.instances: dict[str, OperatorInstance] = {}
        self.no_capacity = False
        self.missing_release = False
        self.missing_context_lease = False
        self.conflicting_context_lease = False
        self.last_work_context: WorkContext | None = None

    def register(self, instance: OperatorInstance) -> OperatorInstance:
        self.instances[instance.instance_id] = instance
        return instance

    def heartbeat(
        self,
        instance_id: str,
        *,
        inflight: int,
        model_ready: bool,
    ) -> OperatorInstance:
        try:
            return self.instances[instance_id]
        except KeyError as exc:
            raise OperatorInstanceNotFoundError(instance_id) from exc

    def unregister(self, instance_id: str) -> None:
        if self.instances.pop(instance_id, None) is None:
            raise OperatorInstanceNotFoundError(instance_id)

    def list_instances(self) -> list[OperatorInstance]:
        return list(self.instances.values())

    def lease(
        self,
        capability: str,
        ttl_seconds: int,
        work_context: WorkContext | None = None,
    ) -> CapacityLease:
        if self.no_capacity:
            raise CapacityUnavailableError(capability)
        self.last_work_context = work_context
        return CapacityLease(
            lease_id="lease-001",
            instance_id="vbas-gpu0",
            capability=capability,
            service_url="http://127.0.0.1:19001",
            expires_at=datetime.now(UTC),
            work_context=work_context,
        )

    def bind_lease_context(
        self,
        lease_id: str,
        work_context: WorkContext,
    ) -> CapacityLease:
        if self.missing_context_lease:
            raise CapacityLeaseNotFoundError(lease_id)
        if self.conflicting_context_lease:
            raise CapacityLeaseContextConflictError(lease_id)
        self.last_work_context = work_context
        return CapacityLease(
            lease_id=lease_id,
            instance_id="vbas-gpu0",
            capability="teacher_behavior",
            service_url="http://127.0.0.1:19001",
            expires_at=datetime.now(UTC),
            work_context=work_context,
        )

    def list_active_leases(self, instance_id: str) -> OperatorActiveLeases:
        if instance_id == "missing":
            raise OperatorInstanceNotFoundError(instance_id)
        now = datetime.now(UTC)
        return OperatorActiveLeases(
            instance_id=instance_id,
            active_lease_count=1,
            reported_inflight=2,
            attribution_difference=1,
            leases=(
                ActiveCapacityLease(
                    lease_id="lease-001",
                    instance_id=instance_id,
                    capability="teacher_behavior",
                    service_url="http://127.0.0.1:19001",
                    acquired_at=now,
                    expires_at=now,
                    context_status=(
                        LeaseContextStatus.BOUND
                        if self.last_work_context is not None
                        else LeaseContextStatus.UNBOUND
                    ),
                    work_context=self.last_work_context,
                ),
            ),
        )

    def release(self, lease_id: str) -> None:
        if self.missing_release:
            raise CapacityLeaseNotFoundError(lease_id)

    def renew(self, lease_id: str, ttl_seconds: int) -> CapacityLease:
        if lease_id != "lease-001":
            raise CapacityLeaseNotFoundError(lease_id)
        return CapacityLease(
            lease_id=lease_id,
            instance_id="vbas-gpu0",
            capability="teacher_behavior",
            service_url="http://127.0.0.1:19001",
            expires_at=datetime.now(UTC),
        )


class UnavailableOperatorRegistry:
    @staticmethod
    def _raise() -> None:
        raise RedisConnectionError("redis unavailable")

    def register(self, instance: OperatorInstance) -> OperatorInstance:
        del instance
        self._raise()

    def heartbeat(
        self,
        instance_id: str,
        *,
        inflight: int,
        model_ready: bool,
    ) -> OperatorInstance:
        del instance_id, inflight, model_ready
        self._raise()

    def unregister(self, instance_id: str) -> None:
        del instance_id
        self._raise()

    def list_instances(self) -> list[OperatorInstance]:
        self._raise()

    def set_lifecycle(self, instance_id: str, lifecycle: Any) -> OperatorInstance:
        del instance_id, lifecycle
        self._raise()

    def lease(
        self,
        capability: str,
        ttl_seconds: int,
        work_context: WorkContext | None = None,
    ) -> CapacityLease:
        del capability, ttl_seconds, work_context
        self._raise()

    def bind_lease_context(
        self,
        lease_id: str,
        work_context: WorkContext,
    ) -> CapacityLease:
        del lease_id, work_context
        self._raise()

    def list_active_leases(self, instance_id: str) -> OperatorActiveLeases:
        del instance_id
        self._raise()

    def release(self, lease_id: str) -> None:
        del lease_id
        self._raise()

    def renew(self, lease_id: str, ttl_seconds: int) -> CapacityLease:
        del lease_id, ttl_seconds
        self._raise()


def test_operator_registration_and_listing_use_real_http_status(tmp_path: Path) -> None:
    registry = FakeOperatorRegistry()
    settings = PlatformSettings(
        course_root=tmp_path / "course",
        result_root=tmp_path / "result",
        operator_registry_token=REGISTRY_TOKEN,
        trusted_operator_service_urls={
            "vbas-gpu0": "http://127.0.0.1:19001",
        },
    )
    app = create_control_app(operator_registry=registry, settings=settings)
    registration = {
        "instance_id": "vbas-gpu0",
        "operator_code": "vbas",
        "capabilities": ["teacher_behavior", "student_behavior"],
        "service_url": "http://127.0.0.1:19001",
        "model_version": "v1",
        "api_version": "v1",
        "declared_capacity": 2,
        "labels": {"gpu": "0"},
    }

    with TestClient(app) as client:
        registered = client.post(
            "/api/operator-instances/register",
            json=registration,
            headers=REGISTRY_HEADERS,
        )
        listed = client.get("/api/operator-instances")

    assert registered.status_code == 201
    assert registered.json()["instance_id"] == "vbas-gpu0"
    assert listed.status_code == 200
    assert listed.json()[0]["operator_code"] == "vbas"


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_operator_registration_requires_management_token(
    tmp_path: Path,
    token: str | None,
) -> None:
    registry = FakeOperatorRegistry()
    settings = PlatformSettings(
        course_root=tmp_path / "course",
        result_root=tmp_path / "result",
        operator_registry_token=REGISTRY_TOKEN,
        trusted_operator_service_urls={
            "vbas-gpu0": "http://vbas-gpu0:8981",
        },
    )
    app = create_control_app(operator_registry=registry, settings=settings)
    headers = {} if token is None else {"X-Operator-Registry-Token": token}

    with TestClient(app) as client:
        response = client.post(
            "/api/operator-instances/register",
            headers=headers,
            json={
                "instance_id": "vbas-gpu0",
                "operator_code": "vbas",
                "capabilities": ["teacher_behavior"],
                "service_url": "http://vbas-gpu0:8981",
                "declared_capacity": 1,
            },
        )

    assert response.status_code == 401
    assert registry.instances == {}


def test_operator_heartbeat_requires_management_token(tmp_path: Path) -> None:
    registry = FakeOperatorRegistry()
    registry.instances["vbas-gpu0"] = OperatorInstance(
        instance_id="vbas-gpu0",
        operator_code="vbas",  # type: ignore[arg-type]
        capabilities=["teacher_behavior"],
        service_url="http://vbas-gpu0:8981",
        declared_capacity=1,
    )
    settings = PlatformSettings(
        course_root=tmp_path / "course",
        result_root=tmp_path / "result",
        operator_registry_token=REGISTRY_TOKEN,
        trusted_operator_service_urls={
            "vbas-gpu0": "http://vbas-gpu0:8981",
        },
    )
    app = create_control_app(operator_registry=registry, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/operator-instances/heartbeat",
            json={"instance_id": "vbas-gpu0", "inflight": 0, "model_ready": True},
        )

    assert response.status_code == 401


@pytest.mark.parametrize(
    "service_url",
    [
        "http://127.0.0.1:19001",
        "http://169.254.169.254:80",
        "http://unconfigured-operator:8981",
    ],
)
def test_registration_rejects_service_url_outside_exact_instance_authority(
    tmp_path: Path,
    service_url: str,
) -> None:
    registry = FakeOperatorRegistry()
    settings = PlatformSettings(
        course_root=tmp_path / "course",
        result_root=tmp_path / "result",
        operator_registry_token=REGISTRY_TOKEN,
        trusted_operator_service_urls={
            "vbas-gpu0": "http://vbas-gpu0:8981",
        },
    )
    app = create_control_app(operator_registry=registry, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/operator-instances/register",
            headers=REGISTRY_HEADERS,
            json={
                "instance_id": "vbas-gpu0",
                "operator_code": "vbas",
                "capabilities": ["teacher_behavior"],
                "service_url": service_url,
                "declared_capacity": 1,
            },
        )

    assert response.status_code == 403
    assert registry.instances == {}


@pytest.mark.parametrize(
    "trusted_url",
    ["http://vbas-gpu0:8981", "http://127.0.0.1:19001"],
)
def test_registration_accepts_exact_configured_docker_or_local_origin(
    tmp_path: Path,
    trusted_url: str,
) -> None:
    registry = FakeOperatorRegistry()
    settings = PlatformSettings(
        course_root=tmp_path / "course",
        result_root=tmp_path / "result",
        operator_registry_token=REGISTRY_TOKEN,
        trusted_operator_service_urls={"vbas-gpu0": trusted_url},
    )
    app = create_control_app(operator_registry=registry, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/operator-instances/register",
            headers=REGISTRY_HEADERS,
            json={
                "instance_id": "vbas-gpu0",
                "operator_code": "vbas",
                "capabilities": ["teacher_behavior"],
                "service_url": trusted_url,
                "declared_capacity": 1,
            },
        )

    assert response.status_code == 201


def test_release_missing_lease_is_idempotent(tmp_path: Path) -> None:
    registry = FakeOperatorRegistry()
    registry.missing_release = True
    settings = PlatformSettings(
        course_root=tmp_path / "course",
        result_root=tmp_path / "result",
    )
    app = create_control_app(operator_registry=registry, settings=settings)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/internal/operator-instances/release",
            json={"lease_id": "missing-lease"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "lease_id": "missing-lease",
        "status": "ALREADY_RELEASED",
    }


def test_legacy_operator_code_is_rejected(tmp_path: Path) -> None:
    registry = FakeOperatorRegistry()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(operator_registry=registry, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/operator-instances/register",
            headers=REGISTRY_HEADERS,
            json={
                "instance_id": "legacy-instance",
                "operator_code": "tias",
                "capabilities": ["teacher_behavior"],
                "service_url": "http://127.0.0.1:19001",
                "declared_capacity": 1,
            },
        )

    assert response.status_code == 422
    assert registry.instances == {}


def test_retired_text_analysis_operator_code_is_rejected_and_absent_from_openapi(
    tmp_path: Path,
) -> None:
    registry = FakeOperatorRegistry()
    settings = PlatformSettings(
        course_root=tmp_path / "course",
        result_root=tmp_path / "result",
        operator_registry_token=REGISTRY_TOKEN,
        trusted_operator_service_urls={
            "text-analysis-cpu0": "http://text-analysis-cpu0:8000",
        },
    )
    app = create_control_app(operator_registry=registry, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/operator-instances/register",
            headers=REGISTRY_HEADERS,
            json={
                "instance_id": "text-analysis-cpu0",
                "operator_code": "text_analysis",
                "capabilities": ["extract_keywords", "course_overviews"],
                "service_url": "http://text-analysis-cpu0:8000",
                "declared_capacity": 1,
            },
        )
        openapi = client.get("/openapi.json").json()

    assert response.status_code == 422
    assert registry.instances == {}
    assert "text_analysis" not in openapi["components"]["schemas"]["OperatorCode"][
        "enum"
    ]
    assert {code.value for code in OperatorCode} == {
        "asr_offline",
        "asr_online",
        "ppt_slice",
        "ocr",
        "vbas",
        "facerec",
        "screen_det",
    }


def test_unknown_heartbeat_returns_http_404(tmp_path: Path) -> None:
    registry = FakeOperatorRegistry()
    settings = PlatformSettings(
        course_root=tmp_path / "course",
        result_root=tmp_path / "result",
        operator_registry_token=REGISTRY_TOKEN,
    )
    app = create_control_app(operator_registry=registry, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/operator-instances/heartbeat",
            headers=REGISTRY_HEADERS,
            json={"instance_id": "missing", "inflight": 0, "model_ready": True},
        )

    assert response.status_code == 404


def test_capacity_unavailable_returns_http_503(tmp_path: Path) -> None:
    registry = FakeOperatorRegistry()
    registry.no_capacity = True
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(operator_registry=registry, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/internal/operator-instances/lease",
            json={"capability": "ocr", "ttl_seconds": 60},
        )

    assert response.status_code == 503
    assert "ocr" in response.json()["detail"]


@pytest.mark.parametrize("invalid_capacity", [0, -1, True, 1.5, "2"])
def test_registration_rejects_non_positive_or_non_integer_capacity(
    tmp_path: Path,
    invalid_capacity: object,
) -> None:
    registry = FakeOperatorRegistry()
    settings = PlatformSettings(
        course_root=tmp_path / "course",
        result_root=tmp_path / "result",
        operator_registry_token=REGISTRY_TOKEN,
        trusted_operator_service_urls={"ocr-gpu0": "http://ocr-gpu0:8866"},
    )
    app = create_control_app(operator_registry=registry, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/operator-instances/register",
            headers=REGISTRY_HEADERS,
            json={
                "instance_id": "ocr-gpu0",
                "operator_code": "ocr",
                "capabilities": ["ocr"],
                "service_url": "http://ocr-gpu0:8866",
                "declared_capacity": invalid_capacity,
            },
        )

    assert response.status_code == 422
    assert registry.instances == {}


def test_capacity_lease_accepts_optional_work_context_without_breaking_legacy_request(
    tmp_path: Path,
) -> None:
    registry = FakeOperatorRegistry()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(operator_registry=registry, settings=settings)
    context = {
        "source_service": "online-gateway-service",
        "work_type": "online_ocr",
        "work_id": "request-001",
        "trace_id": "trace-001",
    }

    with TestClient(app) as client:
        legacy = client.post(
            "/internal/operator-instances/lease",
            json={"capability": "ocr", "ttl_seconds": 60},
        )
        contextual = client.post(
            "/internal/operator-instances/lease",
            json={"capability": "ocr", "ttl_seconds": 60, "work_context": context},
        )

    assert legacy.status_code == 200
    assert legacy.json()["work_context"] is None
    assert contextual.status_code == 200
    assert contextual.json()["work_context"] == {
        **context,
        "task_id": None,
        "node_id": None,
        "item_id": None,
    }
    assert registry.last_work_context == WorkContext(**context)


def test_capacity_lease_context_binding_maps_success_not_found_and_conflict(
    tmp_path: Path,
) -> None:
    registry = FakeOperatorRegistry()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(operator_registry=registry, settings=settings)
    payload = {
        "lease_id": "lease-001",
        "work_context": {
            "source_service": "orchestrator-service",
            "work_type": "node",
            "work_id": "node-7",
            "task_id": "course-001",
            "node_id": "7",
        },
    }

    with TestClient(app) as client:
        bound = client.post("/internal/operator-instances/lease/context", json=payload)
        registry.missing_context_lease = True
        missing = client.post("/internal/operator-instances/lease/context", json=payload)
        registry.missing_context_lease = False
        registry.conflicting_context_lease = True
        conflict = client.post("/internal/operator-instances/lease/context", json=payload)

    assert bound.status_code == 200
    assert bound.json()["work_context"]["task_id"] == "course-001"
    assert missing.status_code == 404
    assert conflict.status_code == 409


@pytest.mark.parametrize(
    "work_context",
    [
        {
            "source_service": "orchestrator-service",
            "work_type": "node",
            "work_id": "node-7",
            "request_body": "not-allowed",
        },
        {
            "source_service": "orchestrator-service",
            "work_type": "node",
            "work_id": "x" * 201,
        },
    ],
)
def test_capacity_lease_rejects_unbounded_or_extra_work_context(
    tmp_path: Path,
    work_context: dict[str, str],
) -> None:
    registry = FakeOperatorRegistry()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(operator_registry=registry, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/internal/operator-instances/lease",
            json={"capability": "ocr", "work_context": work_context},
        )

    assert response.status_code == 422
    assert registry.last_work_context is None


def test_operations_active_leases_distinguishes_bound_unbound_and_missing_instance(
    tmp_path: Path,
) -> None:
    registry = FakeOperatorRegistry()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(operator_registry=registry, settings=settings)

    with TestClient(app) as client:
        unbound = client.get("/ops/operator-instances/vbas-gpu0/active-leases")
        registry.last_work_context = WorkContext(
            source_service="orchestrator-service",
            work_type="node",
            work_id="node-7",
            task_id="course-001",
        )
        bound = client.get("/ops/operator-instances/vbas-gpu0/active-leases")
        missing = client.get("/ops/operator-instances/missing/active-leases")

    assert unbound.status_code == 200
    assert unbound.json()["leases"][0]["context_status"] == "UNBOUND"
    assert bound.json()["leases"][0]["context_status"] == "BOUND"
    assert bound.json()["attribution_difference"] == 1
    assert missing.status_code == 404


def test_capacity_lease_can_be_renewed(tmp_path: Path) -> None:
    registry = FakeOperatorRegistry()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(operator_registry=registry, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/internal/operator-instances/lease/renew",
            json={"lease_id": "lease-001", "ttl_seconds": 120},
        )

    assert response.status_code == 200
    assert response.json()["lease_id"] == "lease-001"
    assert response.json()["instance_id"] == "vbas-gpu0"


def test_unknown_capacity_lease_renewal_returns_http_404(tmp_path: Path) -> None:
    registry = FakeOperatorRegistry()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(operator_registry=registry, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/internal/operator-instances/lease/renew",
            json={"lease_id": "missing", "ttl_seconds": 120},
        )

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "POST",
            "/api/operator-instances/register",
            {
                "instance_id": "vbas-gpu0",
                "operator_code": "vbas",
                "capabilities": ["teacher_behavior"],
                "service_url": "http://127.0.0.1:19001",
                "declared_capacity": 1,
            },
        ),
        (
            "POST",
            "/api/operator-instances/heartbeat",
            {"instance_id": "vbas-gpu0", "inflight": 0, "model_ready": True},
        ),
        ("POST", "/api/operator-instances/unregister", {"instance_id": "vbas-gpu0"}),
        ("GET", "/api/operator-instances", None),
        (
            "POST",
            "/api/operator-instances/lifecycle",
            {"instance_id": "vbas-gpu0", "lifecycle": "DRAINING"},
        ),
        (
            "POST",
            "/internal/operator-instances/lease",
            {"capability": "teacher_behavior", "ttl_seconds": 60},
        ),
        (
            "POST",
            "/internal/operator-instances/lease/context",
            {
                "lease_id": "lease-001",
                "work_context": {
                    "source_service": "orchestrator-service",
                    "work_type": "node",
                    "work_id": "node-1",
                },
            },
        ),
        (
            "POST",
            "/internal/operator-instances/release",
            {"lease_id": "lease-001"},
        ),
        (
            "POST",
            "/internal/operator-instances/lease/renew",
            {"lease_id": "lease-001", "ttl_seconds": 60},
        ),
        ("GET", "/ops/operator-instances", None),
        ("GET", "/ops/operator-instances/vbas-gpu0/active-leases", None),
        ("POST", "/ops/operator-instances/vbas-gpu0/drain", None),
    ],
)
def test_operator_routes_return_http_503_when_registry_is_unavailable(
    tmp_path: Path,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
) -> None:
    settings = PlatformSettings(
        course_root=tmp_path / "course",
        result_root=tmp_path / "result",
        operator_registry_token=REGISTRY_TOKEN,
        trusted_operator_service_urls={
            "vbas-gpu0": "http://127.0.0.1:19001",
        },
    )
    app = create_control_app(
        operator_registry=UnavailableOperatorRegistry(),
        settings=settings,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        headers = (
            REGISTRY_HEADERS
            if path in {
                "/api/operator-instances/register",
                "/api/operator-instances/heartbeat",
                "/api/operator-instances/unregister",
                "/api/operator-instances/lifecycle",
                "/ops/operator-instances/vbas-gpu0/drain",
            }
            else None
        )
        response = client.request(method, path, json=payload, headers=headers)

    assert response.status_code == 503
    assert "暂不可用" in response.json()["detail"]
