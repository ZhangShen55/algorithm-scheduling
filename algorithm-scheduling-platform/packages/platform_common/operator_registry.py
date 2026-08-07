from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol


class OperatorCode(StrEnum):
    ASR_OFFLINE = "asr_offline"
    ASR_ONLINE = "asr_online"
    PPT_SLICE = "ppt_slice"
    OCR = "ocr"
    TEXT_ANALYSIS = "text_analysis"
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


@dataclass(frozen=True, slots=True)
class CapacityLease:
    lease_id: str
    instance_id: str
    capability: str
    service_url: str
    expires_at: datetime


class OperatorRegistry(Protocol):
    def register(self, instance: OperatorInstance) -> OperatorInstance: ...

    def heartbeat(
        self,
        instance_id: str,
        *,
        inflight: int,
        model_ready: bool,
    ) -> OperatorInstance: ...

    def unregister(self, instance_id: str) -> None: ...

    def list_instances(self) -> list[OperatorInstance]: ...

    def set_lifecycle(
        self,
        instance_id: str,
        lifecycle: OperatorLifecycle,
    ) -> OperatorInstance: ...

    def lease(self, capability: str, ttl_seconds: int) -> CapacityLease: ...

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
    ) -> OperatorInstance:
        self._unavailable()
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

    def lease(self, capability: str, ttl_seconds: int) -> CapacityLease:
        self._unavailable()
        raise CapacityUnavailableError(capability)

    def renew(self, lease_id: str, ttl_seconds: int) -> CapacityLease:
        self._unavailable()
        raise CapacityLeaseNotFoundError(lease_id)

    def release(self, lease_id: str) -> None:
        self._unavailable()
