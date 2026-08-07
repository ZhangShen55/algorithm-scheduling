from datetime import UTC, datetime
from pathlib import Path

from control_service.app.api.control import create_control_app
from fastapi.testclient import TestClient

from packages.platform_common.config import PlatformSettings
from packages.platform_common.operator_registry import (
    CapacityLease,
    CapacityLeaseNotFoundError,
    CapacityUnavailableError,
    OperatorInstance,
    OperatorInstanceNotFoundError,
)


class FakeOperatorRegistry:
    def __init__(self) -> None:
        self.instances: dict[str, OperatorInstance] = {}
        self.no_capacity = False

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

    def lease(self, capability: str, ttl_seconds: int) -> CapacityLease:
        if self.no_capacity:
            raise CapacityUnavailableError(capability)
        return CapacityLease(
            lease_id="lease-001",
            instance_id="vbas-gpu0",
            capability=capability,
            service_url="http://127.0.0.1:19001",
            expires_at=datetime.now(UTC),
        )

    def release(self, lease_id: str) -> None:
        return None

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


def test_operator_registration_and_listing_use_real_http_status(tmp_path: Path) -> None:
    registry = FakeOperatorRegistry()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
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
        registered = client.post("/api/operator-instances/register", json=registration)
        listed = client.get("/api/operator-instances")

    assert registered.status_code == 201
    assert registered.json()["instance_id"] == "vbas-gpu0"
    assert listed.status_code == 200
    assert listed.json()[0]["operator_code"] == "vbas"


def test_legacy_operator_code_is_rejected(tmp_path: Path) -> None:
    registry = FakeOperatorRegistry()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(operator_registry=registry, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/operator-instances/register",
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


def test_unknown_heartbeat_returns_http_404(tmp_path: Path) -> None:
    registry = FakeOperatorRegistry()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(operator_registry=registry, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/operator-instances/heartbeat",
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
