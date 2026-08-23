from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

GiB = 1024**3


class GuardrailLevel(StrEnum):
    CLEAR = "CLEAR"
    WARNING = "WARNING"
    STOP = "STOP"


class GuardrailState(StrEnum):
    RUNNING = "RUNNING"
    WARNING = "WARNING"
    STOP_NEW_LOAD = "STOP_NEW_LOAD"
    PRESERVING_EVIDENCE = "PRESERVING_EVIDENCE"
    RECOVERING = "RECOVERING"
    DRAINING = "DRAINING"
    RECOVERED = "RECOVERED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class StorageObservation:
    name: str
    total_bytes: int
    free_bytes: int

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("存储观测名称不能为空")
        if type(self.total_bytes) is not int or self.total_bytes <= 0:
            raise ValueError("存储总空间必须是正整数")
        if type(self.free_bytes) is not int or not 0 <= self.free_bytes <= self.total_bytes:
            raise ValueError("存储剩余空间必须位于 0 到总空间之间")

    @property
    def free_ratio(self) -> float:
        return self.free_bytes / self.total_bytes


@dataclass(frozen=True, slots=True)
class GuardrailPolicy:
    warning_free_ratio: float = 0.15
    warning_free_bytes: int = 150 * GiB
    stop_free_ratio: float = 0.10
    stop_free_bytes: int = 100 * GiB

    def __post_init__(self) -> None:
        for name in ("warning_free_ratio", "stop_free_ratio"):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 < value < 1:
                raise ValueError(f"{name} 必须位于 0 与 1 之间")
        if self.stop_free_ratio >= self.warning_free_ratio:
            raise ValueError("停止百分比阈值必须低于警戒阈值")
        if (
            type(self.warning_free_bytes) is not int
            or type(self.stop_free_bytes) is not int
            or self.stop_free_bytes <= 0
            or self.warning_free_bytes <= self.stop_free_bytes
        ):
            raise ValueError("绝对空间阈值必须为递增的正整数")


@dataclass(frozen=True, slots=True)
class GuardrailObservation:
    storage: tuple[StorageObservation, ...] = ()
    gpu_critical_errors: tuple[str, ...] = ()
    host_oom: bool = False
    restart_loop_containers: tuple[str, ...] = ()
    database_health: Mapping[str, bool] = field(default_factory=dict)
    evidence_writable: bool = True
    maintenance_lock_owned: bool = True
    gpu_assignment_valid: bool = True

    def __post_init__(self) -> None:
        for name in (
            "host_oom",
            "evidence_writable",
            "maintenance_lock_owned",
            "gpu_assignment_valid",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} 必须是布尔值")
        if any(type(value) is not bool for value in self.database_health.values()):
            raise ValueError("数据库健康状态必须是布尔值")
        if any(not name for name in self.database_health):
            raise ValueError("数据库健康状态名称不能为空")
        storage_names = [item.name for item in self.storage]
        if len(storage_names) != len(set(storage_names)):
            raise ValueError("同一护栏观测不能包含重复存储名称")
        for field_name in ("gpu_critical_errors", "restart_loop_containers"):
            values = getattr(self, field_name)
            if any(not value for value in values):
                raise ValueError(f"{field_name} 不能包含空值")


@dataclass(frozen=True, slots=True)
class GuardrailAssessment:
    level: GuardrailLevel
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.level is GuardrailLevel.CLEAR and self.reasons:
            raise ValueError("CLEAR 护栏评估不能包含原因")
        if self.level is not GuardrailLevel.CLEAR and not self.reasons:
            raise ValueError("WARNING/STOP 护栏评估必须包含原因")
        if any(not reason for reason in self.reasons):
            raise ValueError("护栏原因不能为空")

    @property
    def may_generate_load(self) -> bool:
        return self.level is not GuardrailLevel.STOP

    @property
    def may_start_next_step(self) -> bool:
        return self.level is GuardrailLevel.CLEAR


def evaluate_guardrails(
    observation: GuardrailObservation,
    policy: GuardrailPolicy,
) -> GuardrailAssessment:
    stop_reasons: list[str] = []
    warning_reasons: list[str] = []
    for storage in observation.storage:
        if (
            storage.free_bytes < policy.stop_free_bytes
            or storage.free_ratio < policy.stop_free_ratio
        ):
            stop_reasons.append(
                f"{storage.name} 剩余空间达到停止红线: "
                f"{storage.free_bytes} bytes/{storage.free_ratio:.2%}"
            )
        elif (
            storage.free_bytes < policy.warning_free_bytes
            or storage.free_ratio < policy.warning_free_ratio
        ):
            warning_reasons.append(
                f"{storage.name} 剩余空间达到警戒线: "
                f"{storage.free_bytes} bytes/{storage.free_ratio:.2%}"
            )

    stop_reasons.extend(f"GPU 严重错误: {error}" for error in observation.gpu_critical_errors)
    if observation.host_oom:
        stop_reasons.append("宿主机发生 OOM")
    stop_reasons.extend(f"容器连续重启: {name}" for name in observation.restart_loop_containers)
    stop_reasons.extend(
        f"关键数据库不健康: {name}"
        for name, healthy in observation.database_health.items()
        if not healthy
    )
    if not observation.evidence_writable:
        stop_reasons.append("证据无法原子写入")
    if not observation.maintenance_lock_owned:
        stop_reasons.append("Campaign 维护锁所有权丢失")
    if not observation.gpu_assignment_valid:
        stop_reasons.append("发现 GPU 跨卡归属")

    if stop_reasons:
        return GuardrailAssessment(GuardrailLevel.STOP, tuple(stop_reasons))
    if warning_reasons:
        return GuardrailAssessment(GuardrailLevel.WARNING, tuple(warning_reasons))
    return GuardrailAssessment(GuardrailLevel.CLEAR, ())


@dataclass(frozen=True, slots=True)
class GuardrailTransition:
    previous: GuardrailState
    current: GuardrailState
    reason: str


class GuardrailController:
    """只管理安全顺序；具体停止、取证和恢复动作由受控执行器注入。"""

    def __init__(self) -> None:
        self._state = GuardrailState.RUNNING
        self._history: list[GuardrailTransition] = []

    @property
    def state(self) -> GuardrailState:
        return self._state

    @property
    def history(self) -> tuple[GuardrailTransition, ...]:
        return tuple(self._history)

    @property
    def may_generate_load(self) -> bool:
        return self._state in {GuardrailState.RUNNING, GuardrailState.WARNING}

    @property
    def may_start_next_step(self) -> bool:
        return self._state is GuardrailState.RUNNING

    def _transition(
        self,
        expected: set[GuardrailState],
        current: GuardrailState,
        reason: str,
    ) -> None:
        if self._state not in expected:
            allowed = ", ".join(sorted(item.value for item in expected))
            raise RuntimeError(f"护栏状态 {self._state.value} 不能执行该动作，期望: {allowed}")
        previous = self._state
        self._state = current
        self._history.append(GuardrailTransition(previous, current, reason))

    def apply(self, assessment: GuardrailAssessment) -> None:
        if assessment.level is GuardrailLevel.STOP:
            if self._state in {GuardrailState.RUNNING, GuardrailState.WARNING}:
                self._transition(
                    {GuardrailState.RUNNING, GuardrailState.WARNING},
                    GuardrailState.STOP_NEW_LOAD,
                    "；".join(assessment.reasons),
                )
            return
        if assessment.level is GuardrailLevel.WARNING:
            if self._state is GuardrailState.RUNNING:
                self._transition(
                    {GuardrailState.RUNNING},
                    GuardrailState.WARNING,
                    "；".join(assessment.reasons),
                )
            return
        if self._state is GuardrailState.WARNING:
            self._transition(
                {GuardrailState.WARNING},
                GuardrailState.RUNNING,
                "警戒条件已解除",
            )

    def evidence_preserved(self) -> None:
        self._transition(
            {GuardrailState.STOP_NEW_LOAD},
            GuardrailState.PRESERVING_EVIDENCE,
            "停止新负载并保留现场证据",
        )

    def evidence_preservation_failed(self, reason: str) -> None:
        if not reason:
            raise ValueError("证据保留失败原因不能为空")
        self._transition(
            {GuardrailState.STOP_NEW_LOAD, GuardrailState.PRESERVING_EVIDENCE},
            GuardrailState.FAILED,
            reason,
        )

    def recovery_started(self) -> None:
        self._transition(
            {GuardrailState.PRESERVING_EVIDENCE},
            GuardrailState.RECOVERING,
            "开始精确恢复",
        )

    def recovery_completed(self) -> None:
        self._transition(
            {GuardrailState.RECOVERING},
            GuardrailState.DRAINING,
            "精确恢复完成，继续排空已接受工作",
        )

    def recovery_failed(self, reason: str) -> None:
        if not reason:
            raise ValueError("恢复失败原因不能为空")
        self._transition(
            {GuardrailState.RECOVERING},
            GuardrailState.FAILED,
            reason,
        )

    def drain_completed(self) -> None:
        self._transition(
            {GuardrailState.DRAINING},
            GuardrailState.RECOVERED,
            "已接受工作完成排空",
        )

    def drain_failed(self, reason: str) -> None:
        if not reason:
            raise ValueError("排空失败原因不能为空")
        self._transition(
            {GuardrailState.DRAINING},
            GuardrailState.FAILED,
            reason,
        )
