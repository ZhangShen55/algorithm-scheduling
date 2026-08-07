from __future__ import annotations

import logging
from threading import Lock
from typing import Protocol

from packages.platform_common.operator_audit_repository import OperatorInstanceEvent
from packages.platform_common.operator_registry import (
    CapacityLease,
    OperatorInstance,
    OperatorInstanceNotFoundError,
    OperatorLifecycle,
    OperatorRegistry,
)

logger = logging.getLogger(__name__)


class OperatorAudit(Protocol):
    def record_registration(self, instance: OperatorInstance) -> None: ...

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
    ) -> None:
        if heartbeat_audit_interval_seconds <= 0:
            raise ValueError("心跳审计间隔必须大于 0")
        self._realtime = realtime_registry
        self._audit = audit_repository
        self._heartbeat_audit_interval_seconds = heartbeat_audit_interval_seconds
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
        self._audit.record_registration(instance)
        registered = self._realtime.register(instance)
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
            model_ready=model_ready,
        )
        try:
            self._audit.record_heartbeat_summary(
                instance_id,
                inflight=inflight,
                model_ready=model_ready,
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
