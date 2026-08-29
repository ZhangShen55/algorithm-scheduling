from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

WORK_CONTEXT_IDENTIFIER_MAX_LENGTH = 200
DEFAULT_CAPACITY_POOL = "default"
ONLINE_CAPACITY_POOL = "online"
OFFLINE_CAPACITY_POOL = "offline"


def validate_positive_int(value: object, *, field_name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name}必须是正整数")
    return value


def _validate_work_identifier(value: object, *, field_name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name}必须是字符串")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name}不能为空")
    if len(normalized) > WORK_CONTEXT_IDENTIFIER_MAX_LENGTH:
        raise ValueError(
            f"{field_name}长度不能超过 {WORK_CONTEXT_IDENTIFIER_MAX_LENGTH} 个字符"
        )
    if any(character in normalized for character in ("\x00", "\r", "\n")):
        raise ValueError(f"{field_name}不能包含控制字符")
    return normalized


class OperatorCode(StrEnum):
    ASR_OFFLINE = "asr_offline"
    ASR_ONLINE = "asr_online"
    PPT_SLICE = "ppt_slice"
    OCR = "ocr"
    VBAS = "vbas"
    FACEREC = "facerec"
    SCREEN_DET = "screen_det"


class OperatorLifecycle(StrEnum):
    ONLINE = "ONLINE"
    DRAINING = "DRAINING"
    OFFLINE = "OFFLINE"


class OperatorInstanceNotFoundError(LookupError):
    pass


class CapacityUnavailableError(RuntimeError):
    pass


class CapacityLeaseNotFoundError(LookupError):
    pass


class CapacityLeaseContextConflictError(RuntimeError):
    pass


class LeaseContextStatus(StrEnum):
    BOUND = "BOUND"
    UNBOUND = "UNBOUND"


@dataclass(frozen=True, slots=True)
class WorkContext:
    source_service: str
    work_type: str
    work_id: str
    task_id: str | None = None
    node_id: str | None = None
    item_id: str | None = None
    trace_id: str | None = None
    capacity_pool: str = DEFAULT_CAPACITY_POOL

    def __post_init__(self) -> None:
        for field_name in ("source_service", "work_type", "work_id"):
            object.__setattr__(
                self,
                field_name,
                _validate_work_identifier(
                    getattr(self, field_name),
                    field_name=field_name,
                ),
            )
        for field_name in ("task_id", "node_id", "item_id", "trace_id", "capacity_pool"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _validate_work_identifier(value, field_name=field_name),
                )

    def as_dict(self) -> dict[str, str]:
        values = {
            "source_service": self.source_service,
            "work_type": self.work_type,
            "work_id": self.work_id,
            "task_id": self.task_id,
            "node_id": self.node_id,
            "item_id": self.item_id,
            "trace_id": self.trace_id,
            "capacity_pool": self.capacity_pool,
        }
        return {key: value for key, value in values.items() if value is not None}


@dataclass(frozen=True, slots=True)
class OperatorInstance:
    instance_id: str
    operator_code: OperatorCode
    capabilities: list[str]
    service_url: str
    declared_capacity: int
    model_version: str | None = None
    api_version: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    lifecycle: OperatorLifecycle = OperatorLifecycle.ONLINE
    inflight: int = 0
    model_ready: bool = True
    last_heartbeat_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    capacity_pools: dict[str, int] = field(default_factory=dict)
    inflight_by_pool: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_positive_int(self.declared_capacity, field_name="算子声明容量")
        if not self.capacity_pools:
            object.__setattr__(
                self,
                "capacity_pools",
                {DEFAULT_CAPACITY_POOL: self.declared_capacity},
            )
        for pool, capacity in self.capacity_pools.items():
            _validate_work_identifier(pool, field_name="容量池")
            validate_positive_int(capacity, field_name=f"容量池 {pool} 声明容量")
        for pool, inflight in self.inflight_by_pool.items():
            _validate_work_identifier(pool, field_name="容量池")
            if type(inflight) is not int or inflight < 0:
                raise ValueError(f"容量池 {pool} 在途数必须是非负整数")


@dataclass(frozen=True, slots=True)
class CapacityLease:
    lease_id: str
    instance_id: str
    capability: str
    service_url: str
    expires_at: datetime
    acquired_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    work_context: WorkContext | None = None
    capacity_pool: str = DEFAULT_CAPACITY_POOL


@dataclass(frozen=True, slots=True)
class ActiveCapacityLease:
    lease_id: str
    instance_id: str
    capability: str
    service_url: str
    acquired_at: datetime
    expires_at: datetime
    context_status: LeaseContextStatus
    work_context: WorkContext | None = None
    capacity_pool: str = DEFAULT_CAPACITY_POOL


@dataclass(frozen=True, slots=True)
class OperatorActiveLeases:
    instance_id: str
    active_lease_count: int
    reported_inflight: int
    attribution_difference: int
    leases: tuple[ActiveCapacityLease, ...]


class OperatorRegistry(Protocol):
    def register(self, instance: OperatorInstance) -> OperatorInstance: ...

    def heartbeat(
        self,
        instance_id: str,
        *,
        inflight: int,
        model_ready: bool,
        inflight_by_pool: dict[str, int] | None = None,
    ) -> OperatorInstance: ...

    def unregister(self, instance_id: str) -> None: ...

    def list_instances(self) -> list[OperatorInstance]: ...

    def set_lifecycle(
        self,
        instance_id: str,
        lifecycle: OperatorLifecycle,
    ) -> OperatorInstance: ...

    def lease(
        self,
        capability: str,
        ttl_seconds: int,
        work_context: WorkContext | None = None,
        capacity_pool: str = DEFAULT_CAPACITY_POOL,
    ) -> CapacityLease: ...

    def bind_lease_context(
        self,
        lease_id: str,
        work_context: WorkContext,
    ) -> CapacityLease: ...

    def list_active_leases(self, instance_id: str) -> OperatorActiveLeases: ...

    def renew(self, lease_id: str, ttl_seconds: int) -> CapacityLease: ...

    def release(self, lease_id: str) -> None: ...


class UnavailableOperatorRegistry:
    def _unavailable(self) -> None:
        raise RuntimeError("算子注册中心尚未配置")

    def register(self, instance: OperatorInstance) -> OperatorInstance:
        self._unavailable()
        return instance

    def heartbeat(
        self,
        instance_id: str,
        *,
        inflight: int,
        model_ready: bool,
        inflight_by_pool: dict[str, int] | None = None,
    ) -> OperatorInstance:
        self._unavailable()
        del inflight_by_pool
        raise OperatorInstanceNotFoundError(instance_id)

    def unregister(self, instance_id: str) -> None:
        self._unavailable()

    def list_instances(self) -> list[OperatorInstance]:
        self._unavailable()
        return []

    def set_lifecycle(
        self,
        instance_id: str,
        lifecycle: OperatorLifecycle,
    ) -> OperatorInstance:
        self._unavailable()
        raise OperatorInstanceNotFoundError(instance_id)

    def lease(
        self,
        capability: str,
        ttl_seconds: int,
        work_context: WorkContext | None = None,
        capacity_pool: str = DEFAULT_CAPACITY_POOL,
    ) -> CapacityLease:
        del work_context
        del capacity_pool
        self._unavailable()
        raise CapacityUnavailableError(capability)

    def bind_lease_context(
        self,
        lease_id: str,
        work_context: WorkContext,
    ) -> CapacityLease:
        del work_context
        self._unavailable()
        raise CapacityLeaseNotFoundError(lease_id)

    def list_active_leases(self, instance_id: str) -> OperatorActiveLeases:
        self._unavailable()
        raise OperatorInstanceNotFoundError(instance_id)

    def renew(self, lease_id: str, ttl_seconds: int) -> CapacityLease:
        self._unavailable()
        raise CapacityLeaseNotFoundError(lease_id)

    def release(self, lease_id: str) -> None:
        self._unavailable()
