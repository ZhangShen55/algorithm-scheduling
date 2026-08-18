from __future__ import annotations

import logging
from dataclasses import replace
from threading import Lock
from typing import Protocol, cast
from urllib.parse import urlsplit

import httpx

from packages.platform_common.operator_audit_repository import OperatorInstanceEvent
from packages.platform_common.operator_operations import OperatorOperationsRegistry
from packages.platform_common.operator_registry import (
    CapacityLease,
    OperatorInstance,
    OperatorInstanceNotFoundError,
    OperatorLifecycle,
    OperatorRegistry,
)

logger = logging.getLogger(__name__)


def canonical_operator_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("算子服务地址必须是显式端口的 HTTP/HTTPS origin") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("算子服务地址必须是显式端口的 HTTP/HTTPS origin")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return f"{parsed.scheme.lower()}://{host}:{port}"


class OperatorHealthChecker(Protocol):
    def check(self, instance: OperatorInstance) -> bool: ...


class HttpOperatorHealthChecker:
    def __init__(
        self,
        *,
        timeout_seconds: float = 2.0,
        trusted_service_urls: dict[str, str],
        http_client: httpx.Client | None = None,
    ) -> None:
        if not 0 < timeout_seconds <= 10:
            raise ValueError("算子健康检查超时必须大于 0 且不超过 10 秒")
        self._timeout_seconds = timeout_seconds
        self._trusted_service_urls = {
            instance_id: canonical_operator_origin(service_url)
            for instance_id, service_url in trusted_service_urls.items()
        }
        self._http_client = http_client

    def check(self, instance: OperatorInstance) -> bool:
        try:
            base_url = self._trusted_service_urls.get(instance.instance_id)
            if base_url is None or canonical_operator_origin(instance.service_url) != base_url:
                return False
            if self._http_client is None:
                health = httpx.get(
                    f"{base_url}/ops/health",
                    timeout=self._timeout_seconds,
                )
            else:
                health = self._http_client.get(
                    f"{base_url}/ops/health",
                    timeout=self._timeout_seconds,
                )
            if health.status_code != 200:
                return False
            if self._http_client is None:
                metadata = httpx.get(
                    f"{base_url}/ops/metadata",
                    timeout=self._timeout_seconds,
                )
            else:
                metadata = self._http_client.get(
                    f"{base_url}/ops/metadata",
                    timeout=self._timeout_seconds,
                )
            if metadata.status_code != 200:
                return False
            payload = metadata.json()
        except (httpx.HTTPError, ValueError):
            return False
        operator_code = getattr(instance.operator_code, "value", instance.operator_code)
        expected = {
            "instance_id": instance.instance_id,
            "operator_code": operator_code,
            "capabilities": instance.capabilities,
            "model_version": instance.model_version,
            "api_version": instance.api_version,
        }
        return type(payload) is dict and payload == expected


class OperatorAudit(Protocol):
    def record_registration(self, instance: OperatorInstance) -> None: ...

    def get_desired_lifecycle(self, instance_id: str) -> OperatorLifecycle: ...

    def record_heartbeat_summary(
        self,
        instance_id: str,
        *,
        inflight: int,
        model_ready: bool,
        min_interval_seconds: float,
    ) -> bool: ...

    def record_lifecycle(
        self,
        instance_id: str,
        lifecycle: OperatorLifecycle,
        *,
        source: str,
        reason: str | None = None,
    ) -> bool: ...

    def record_unregistration(self, instance_id: str, *, source: str) -> bool: ...

    def list_events(
        self,
        instance_id: str,
        *,
        limit: int = 100,
    ) -> list[OperatorInstanceEvent]: ...


class AuditedOperatorRegistry:
    """Keep Redis routing state and PostgreSQL operator facts in a safe order."""

    def __init__(
        self,
        realtime_registry: OperatorRegistry,
        audit_repository: OperatorAudit,
        *,
        heartbeat_audit_interval_seconds: int,
        health_checker: OperatorHealthChecker,
    ) -> None:
        if heartbeat_audit_interval_seconds <= 0:
            raise ValueError("心跳审计间隔必须大于 0")
        self._realtime = realtime_registry
        self._audit = audit_repository
        self._heartbeat_audit_interval_seconds = heartbeat_audit_interval_seconds
        self._health_checker = health_checker
        self._audit_errors: dict[str, str] = {}
        self._audit_errors_lock = Lock()

    @property
    def last_audit_error(self) -> str | None:
        with self._audit_errors_lock:
            if not self._audit_errors:
                return None
            return "；".join(
                f"{instance_id}: {detail}"
                for instance_id, detail in sorted(self._audit_errors.items())
            )

    def register(self, instance: OperatorInstance) -> OperatorInstance:
        # Persist routing intent before making an instance available to new work.
        registered_instance = replace(instance, model_ready=False)
        self._audit.record_registration(registered_instance)
        registered_instance = replace(
            registered_instance,
            lifecycle=self._audit.get_desired_lifecycle(instance.instance_id),
        )
        registered = self._realtime.register(registered_instance)
        self._clear_audit_error(instance.instance_id)
        return registered

    def heartbeat(
        self,
        instance_id: str,
        *,
        inflight: int,
        model_ready: bool,
    ) -> OperatorInstance:
        instance = self._realtime.heartbeat(
            instance_id,
            inflight=inflight,
            model_ready=False,
        )
        if model_ready:
            try:
                healthy = self._health_checker.check(instance)
            except Exception:
                healthy = False
                logger.warning(
                    "算子健康检查异常: instance_id=%s",
                    instance_id,
                    exc_info=True,
                )
            if healthy:
                instance = self._realtime.heartbeat(
                    instance_id,
                    inflight=inflight,
                    model_ready=True,
                )
        try:
            self._audit.record_heartbeat_summary(
                instance_id,
                inflight=inflight,
                model_ready=instance.model_ready,
                min_interval_seconds=self._heartbeat_audit_interval_seconds,
            )
        except Exception as exc:
            # Redis TTL is the realtime contract. A transient audit outage must not
            # make an otherwise healthy operator stop heartbeating.
            self._set_audit_error(instance_id, str(exc))
            logger.warning(
                "算子心跳审计写入失败: instance_id=%s",
                instance_id,
                exc_info=True,
            )
        else:
            self._clear_audit_error(instance_id)
        return instance

    def unregister(self, instance_id: str) -> None:
        # Stop new routing first. If PostgreSQL fails, the instance remains safe.
        realtime_present = True
        try:
            self._realtime.set_lifecycle(instance_id, OperatorLifecycle.OFFLINE)
        except OperatorInstanceNotFoundError:
            realtime_present = False
        self._audit.record_unregistration(instance_id, source="operator")
        if realtime_present:
            try:
                self._realtime.unregister(instance_id)
            except OperatorInstanceNotFoundError:
                # A concurrent idempotent retry may already have removed Redis state.
                pass
        self._clear_audit_error(instance_id)

    def list_instances(self) -> list[OperatorInstance]:
        return self._realtime.list_instances()

    def active_lease_count(self, instance_id: str) -> int:
        realtime = cast(OperatorOperationsRegistry, self._realtime)
        return realtime.active_lease_count(instance_id)

    def set_lifecycle(
        self,
        instance_id: str,
        lifecycle: OperatorLifecycle,
    ) -> OperatorInstance:
        reason = f"平台设置算子生命周期为 {lifecycle.value}"
        if lifecycle is OperatorLifecycle.ONLINE:
            # Opening routing requires a durable PostgreSQL intent first.
            self._audit.record_lifecycle(
                instance_id,
                lifecycle,
                source="control-service",
                reason=reason,
            )
            instance = self._realtime.set_lifecycle(instance_id, lifecycle)
        else:
            # Closing routing happens before its audit fact is persisted.
            instance = self._realtime.set_lifecycle(instance_id, lifecycle)
            self._audit.record_lifecycle(
                instance_id,
                lifecycle,
                source="control-service",
                reason=reason,
            )
        self._clear_audit_error(instance_id)
        return instance

    def lease(self, capability: str, ttl_seconds: int) -> CapacityLease:
        return self._realtime.lease(capability, ttl_seconds)

    def renew(self, lease_id: str, ttl_seconds: int) -> CapacityLease:
        return self._realtime.renew(lease_id, ttl_seconds)

    def release(self, lease_id: str) -> None:
        self._realtime.release(lease_id)

    def list_events(
        self,
        instance_id: str,
        *,
        limit: int = 100,
    ) -> list[OperatorInstanceEvent]:
        return self._audit.list_events(instance_id, limit=limit)

    def _set_audit_error(self, instance_id: str, detail: str) -> None:
        with self._audit_errors_lock:
            self._audit_errors[instance_id] = detail

    def _clear_audit_error(self, instance_id: str) -> None:
        with self._audit_errors_lock:
            self._audit_errors.pop(instance_id, None)
