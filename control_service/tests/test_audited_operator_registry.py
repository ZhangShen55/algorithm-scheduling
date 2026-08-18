from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest

from control_service.app.infrastructure.audited_operator_registry import (
    AuditedOperatorRegistry,
    HttpOperatorHealthChecker,
)
from packages.platform_common.operator_registry import (
    CapacityLease,
    CapacityUnavailableError,
    OperatorCode,
    OperatorInstance,
    OperatorLifecycle,
)


def _instance(service_url: str) -> OperatorInstance:
    return OperatorInstance(
        instance_id="vbas-gpu0",
        operator_code=OperatorCode.VBAS,
        capabilities=["teacher_behavior"],
        service_url=service_url,
        declared_capacity=1,
        model_ready=False,
    )


class RealtimeRegistry:
    def __init__(self) -> None:
        self.instance: OperatorInstance | None = None

    def register(self, instance: OperatorInstance) -> OperatorInstance:
        self.instance = instance
        return instance

    def heartbeat(
        self,
        instance_id: str,
        *,
        inflight: int,
        model_ready: bool,
    ) -> OperatorInstance:
        assert self.instance is not None and self.instance.instance_id == instance_id
        self.instance = replace(
            self.instance,
            inflight=inflight,
            model_ready=model_ready,
        )
        return self.instance

    def lease(self, capability: str, ttl_seconds: int) -> CapacityLease:
        assert self.instance is not None
        if (
            not self.instance.model_ready
            or self.instance.lifecycle is not OperatorLifecycle.ONLINE
        ):
            raise CapacityUnavailableError(capability)
        return CapacityLease(
            lease_id="lease-001",
            instance_id=self.instance.instance_id,
            capability=capability,
            service_url=self.instance.service_url,
            expires_at=datetime.now(UTC),
        )

    def set_lifecycle(
        self,
        instance_id: str,
        lifecycle: OperatorLifecycle,
    ) -> OperatorInstance:
        assert self.instance is not None and self.instance.instance_id == instance_id
        self.instance = replace(self.instance, lifecycle=lifecycle)
        return self.instance

    def unregister(self, instance_id: str) -> None:
        assert self.instance is not None and self.instance.instance_id == instance_id
        self.instance = None

    def list_instances(self) -> list[OperatorInstance]:
        return [] if self.instance is None else [self.instance]


class RecordingAudit:
    def record_registration(self, instance: OperatorInstance) -> None:
        del instance

    def record_heartbeat_summary(self, *args: object, **kwargs: object) -> bool:
        del args, kwargs
        return True

    def get_desired_lifecycle(self, instance_id: str) -> OperatorLifecycle:
        del instance_id
        return OperatorLifecycle.ONLINE


class StaticHealthChecker:
    def __init__(self, healthy: bool) -> None:
        self.healthy = healthy
        self.instances: list[OperatorInstance] = []

    def check(self, instance: OperatorInstance) -> bool:
        self.instances.append(instance)
        return self.healthy


def test_unhealthy_service_url_never_becomes_leaseable() -> None:
    realtime = RealtimeRegistry()
    health = StaticHealthChecker(False)
    registry = AuditedOperatorRegistry(
        realtime,  # type: ignore[arg-type]
        RecordingAudit(),  # type: ignore[arg-type]
        heartbeat_audit_interval_seconds=60,
        health_checker=health,
    )
    instance = _instance("http://wrong-operator:8981")

    registry.register(instance)
    heartbeat = registry.heartbeat(
        instance.instance_id,
        inflight=0,
        model_ready=True,
    )

    assert heartbeat.model_ready is False
    assert health.instances == [instance]
    with pytest.raises(CapacityUnavailableError):
        registry.lease("teacher_behavior", 30)


def test_healthy_service_url_becomes_leaseable_after_first_heartbeat() -> None:
    realtime = RealtimeRegistry()
    health = StaticHealthChecker(True)
    registry = AuditedOperatorRegistry(
        realtime,  # type: ignore[arg-type]
        RecordingAudit(),  # type: ignore[arg-type]
        heartbeat_audit_interval_seconds=60,
        health_checker=health,
    )
    instance = _instance("http://vbas-gpu0:8981")

    registry.register(instance)
    heartbeat = registry.heartbeat(
        instance.instance_id,
        inflight=0,
        model_ready=True,
    )
    lease = registry.lease("teacher_behavior", 30)

    assert heartbeat.model_ready is True
    assert lease.service_url == instance.service_url
    assert health.instances == [instance]


def test_http_health_checker_rejects_healthy_wrong_operator_identity() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/ops/health":
            return httpx.Response(200, json={"status": "alive"})
        if request.url.path == "/ops/metadata":
            return httpx.Response(
                200,
                json={
                    "instance_id": "ocr-gpu0",
                    "operator_code": "ocr",
                    "capabilities": ["ocr"],
                    "model_version": "ocr-v6",
                    "api_version": "v1",
                },
            )
        raise AssertionError(request.url.path)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        checker = HttpOperatorHealthChecker(
            trusted_service_urls={"vbas-gpu0": "http://wrong-operator:8981"},
            http_client=client,
        )
        healthy = checker.check(_instance("http://wrong-operator:8981"))

    assert healthy is False
    assert paths == ["/ops/health", "/ops/metadata"]


def test_http_health_checker_never_probes_untrusted_persisted_service_url() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, json={"status": "alive"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        checker = HttpOperatorHealthChecker(
            trusted_service_urls={"vbas-gpu0": "http://vbas-gpu0:8981"},
            http_client=client,
        )
        healthy = checker.check(_instance("http://rebinding.attacker:8981"))

    assert healthy is False
    assert requested_urls == []


def test_draining_intent_survives_unregister_and_reregistration() -> None:
    class DurableAudit(RecordingAudit):
        def __init__(self) -> None:
            self.desired_lifecycle: OperatorLifecycle | None = None

        def record_registration(self, instance: OperatorInstance) -> None:
            if self.desired_lifecycle is None:
                self.desired_lifecycle = instance.lifecycle

        def get_desired_lifecycle(self, instance_id: str) -> OperatorLifecycle:
            del instance_id
            assert self.desired_lifecycle is not None
            return self.desired_lifecycle

        def record_lifecycle(
            self,
            instance_id: str,
            lifecycle: OperatorLifecycle,
            *,
            source: str,
            reason: str | None = None,
        ) -> bool:
            del instance_id, source, reason
            changed = self.desired_lifecycle is not lifecycle
            self.desired_lifecycle = lifecycle
            return changed

        def record_unregistration(self, instance_id: str, *, source: str) -> bool:
            del instance_id, source
            return True

    realtime = RealtimeRegistry()
    audit = DurableAudit()
    registry = AuditedOperatorRegistry(
        realtime,  # type: ignore[arg-type]
        audit,  # type: ignore[arg-type]
        heartbeat_audit_interval_seconds=60,
        health_checker=StaticHealthChecker(True),
    )
    instance = _instance("http://vbas-gpu0:8981")

    registry.register(instance)
    registry.heartbeat(instance.instance_id, inflight=0, model_ready=True)
    registry.set_lifecycle(instance.instance_id, OperatorLifecycle.DRAINING)
    registry.unregister(instance.instance_id)

    registry.register(instance)
    heartbeat = registry.heartbeat(
        instance.instance_id,
        inflight=0,
        model_ready=True,
    )

    assert audit.desired_lifecycle is OperatorLifecycle.DRAINING
    assert heartbeat.lifecycle is OperatorLifecycle.DRAINING
    with pytest.raises(CapacityUnavailableError):
        registry.lease("teacher_behavior", 30)

    registry.set_lifecycle(instance.instance_id, OperatorLifecycle.ONLINE)
    lease = registry.lease("teacher_behavior", 30)
    assert lease.instance_id == instance.instance_id
