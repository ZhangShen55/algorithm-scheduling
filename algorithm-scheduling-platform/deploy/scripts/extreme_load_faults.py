#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol

_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
_COMPOSE_IDENTITY = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}")
_CAMPAIGN_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}")
_PLATFORM_PROJECT = "algorithm-scheduling-platform"
_OPERATOR_PROJECT = "algorithm-operators"
_FAULT_KINDS = frozenset(
    {
        "single_operator",
        "gpu_group",
        "platform_service",
        "kafka",
        "redis",
    }
)
_OPERATOR_CODES = (
    "asr_offline",
    "asr_online",
    "ocr",
    "vbas",
    "facerec",
    "screen_det",
    "ppt_slice",
)
_GPU_OPERATOR_CODES = _OPERATOR_CODES[:-1]
_PLATFORM_SERVICES = (
    "control-service",
    "orchestrator-service",
    "vision-orchestrator-service",
    "online-gateway-service",
)


class PlanValidationError(ValueError):
    pass


def _finite_timeout(document: Mapping[str, object], field_name: str) -> float:
    raw = document.get(field_name)
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise PlanValidationError(f"{field_name} 格式错误")
    try:
        return float(raw)
    except ValueError as exc:
        raise PlanValidationError(f"{field_name} 格式错误") from exc


class FaultAction(StrEnum):
    STOP_START = "stop_start"
    RESTART = "restart"


class RecoveryAction(StrEnum):
    ENSURE_RUNNING = "ensure_running"


@dataclass(frozen=True, slots=True)
class ContainerTarget:
    container_id: str
    compose_project: str
    compose_service: str

    def validate(self) -> None:
        if _CONTAINER_ID.fullmatch(self.container_id) is None:
            raise PlanValidationError("故障目标必须使用 64 位小写完整容器 ID")
        if _COMPOSE_IDENTITY.fullmatch(self.compose_project) is None:
            raise PlanValidationError("Compose project 必须是精确身份，不能包含通配符")
        if _COMPOSE_IDENTITY.fullmatch(self.compose_service) is None:
            raise PlanValidationError("Compose service 必须是精确身份，不能包含通配符")

    def to_dict(self) -> dict[str, str]:
        return {
            "container_id": self.container_id,
            "compose_project": self.compose_project,
            "compose_service": self.compose_service,
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, object]) -> ContainerTarget:
        target = cls(
            container_id=str(document.get("container_id", "")),
            compose_project=str(document.get("compose_project", "")),
            compose_service=str(document.get("compose_service", "")),
        )
        target.validate()
        return target


@dataclass(frozen=True, slots=True)
class ContainerIdentity:
    container_id: str
    compose_project: str
    compose_service: str
    running: bool


def _validate_fault_scope(
    kind: str,
    action: FaultAction,
    targets: Sequence[ContainerTarget],
) -> None:
    services = {target.compose_service for target in targets}
    projects = {target.compose_project for target in targets}
    gpu_services = {
        f"{code.replace('_', '-')}-gpu{gpu_index}"
        for code in _GPU_OPERATOR_CODES
        for gpu_index in range(3)
    }
    cpu_services = {f"ppt-slice-cpu{index}" for index in range(3)}
    if kind == "single_operator":
        if action is not FaultAction.STOP_START or len(targets) != 1:
            raise PlanValidationError("单算子故障必须精确 stop/start 一个实例")
        if not services <= gpu_services | cpu_services:
            raise PlanValidationError("单算子故障目标不属于当前七算子 21 实例")
        if projects != {_OPERATOR_PROJECT}:
            raise PlanValidationError("单算子故障目标 Compose project 不属于算子权威")
        return
    if kind == "gpu_group":
        if action is not FaultAction.STOP_START or len(targets) != 6:
            raise PlanValidationError("单 GPU 故障必须精确 stop/start 六个实例")
        expected_groups = (
            {f"{code.replace('_', '-')}-gpu{index}" for code in _GPU_OPERATOR_CODES}
            for index in range(3)
        )
        if services not in expected_groups:
            raise PlanValidationError("单 GPU 故障目标必须是同一卡的六类算子实例")
        if projects != {_OPERATOR_PROJECT}:
            raise PlanValidationError("单 GPU 故障目标 Compose project 不属于算子权威")
        return
    if action is not FaultAction.RESTART or len(targets) != 1:
        raise PlanValidationError("平台和中间件故障必须精确 restart 一个实例")
    expected_services = {
        "platform_service": set(_PLATFORM_SERVICES),
        "kafka": {"kafka"},
        "redis": {"redis"},
    }
    if next(iter(services)) not in expected_services[kind]:
        raise PlanValidationError(f"{kind} 故障目标不属于当前权威服务")
    if projects != {_PLATFORM_PROJECT}:
        raise PlanValidationError(f"{kind} 故障目标 Compose project 不属于平台权威")


@dataclass(frozen=True, slots=True)
class FaultCheck:
    name: str
    timeout_seconds: float
    probe: str = "external_evidence"

    def validate(self) -> None:
        if not self.name:
            raise PlanValidationError("故障检查名称不能为空")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0 < self.timeout_seconds <= 3600
        ):
            raise PlanValidationError("故障检查超时必须位于 0–3600 秒")
        if self.probe not in {
            "external_evidence",
            "containers_stopped",
            "containers_running",
        }:
            raise PlanValidationError(f"不支持的故障检查探针: {self.probe}")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "timeout_seconds": self.timeout_seconds,
            "probe": self.probe,
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, object]) -> FaultCheck:
        timeout = _finite_timeout(document, "timeout_seconds")
        check = cls(
            name=str(document.get("name", "")),
            timeout_seconds=timeout,
            probe=str(document.get("probe", "external_evidence")),
        )
        check.validate()
        return check


@dataclass(frozen=True, slots=True)
class FaultScenario:
    scenario_id: str
    kind: str
    action: FaultAction
    recovery_action: RecoveryAction
    targets: tuple[ContainerTarget, ...]
    disruption_checks: tuple[FaultCheck, ...]
    recovery_checks: tuple[FaultCheck, ...]
    action_timeout_seconds: float
    timeout_seconds: float

    def validate(self) -> None:
        if _COMPOSE_IDENTITY.fullmatch(self.scenario_id) is None:
            raise PlanValidationError("故障场景 ID 不是安全单段标识")
        if self.kind not in _FAULT_KINDS:
            raise PlanValidationError(f"不支持的故障场景类型: {self.kind}")
        if not self.targets:
            raise PlanValidationError("故障场景必须包含精确目标")
        for target in self.targets:
            target.validate()
        _validate_fault_scope(self.kind, self.action, self.targets)
        target_ids = [target.container_id for target in self.targets]
        if len(target_ids) != len(set(target_ids)):
            raise PlanValidationError("同一故障场景不能包含重复容器 ID")
        target_identities = [
            (target.compose_project, target.compose_service) for target in self.targets
        ]
        if len(target_identities) != len(set(target_identities)):
            raise PlanValidationError("同一故障场景不能包含重复 Compose 身份")
        if not self.disruption_checks or not self.recovery_checks:
            raise PlanValidationError("故障场景必须同时声明故障期和恢复期检查")
        for check in (*self.disruption_checks, *self.recovery_checks):
            check.validate()
        disruption_probes = {check.probe for check in self.disruption_checks}
        recovery_probes = {check.probe for check in self.recovery_checks}
        if self.action is FaultAction.STOP_START and "containers_stopped" not in disruption_probes:
            raise PlanValidationError("stop_start 场景必须显式检查精确容器已停止")
        if "containers_running" not in recovery_probes:
            raise PlanValidationError("故障场景必须显式检查精确容器已恢复运行")
        if (
            "external_evidence" not in disruption_probes
            or "external_evidence" not in recovery_probes
        ):
            raise PlanValidationError("故障与恢复都必须包含业务语义外部证据")
        if self.recovery_action is not RecoveryAction.ENSURE_RUNNING:
            raise PlanValidationError("故障场景必须声明精确恢复为 ensure_running")
        if (
            isinstance(self.action_timeout_seconds, bool)
            or not isinstance(self.action_timeout_seconds, (int, float))
            or not 1 <= self.action_timeout_seconds <= 300
        ):
            raise PlanValidationError("故障动作超时必须位于 1–300 秒")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0 < self.timeout_seconds <= 7200
        ):
            raise PlanValidationError("故障场景超时必须位于 0–7200 秒")
        maximum_budget = (
            self.action_timeout_seconds * len(self.targets) * 2
            + sum(check.timeout_seconds for check in self.disruption_checks)
            + sum(check.timeout_seconds for check in self.recovery_checks)
        )
        if maximum_budget > self.timeout_seconds:
            raise PlanValidationError("故障动作、检查和精确恢复超时总和超过场景超时")

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "kind": self.kind,
            "action": self.action.value,
            "recovery_action": self.recovery_action.value,
            "targets": [target.to_dict() for target in self.targets],
            "disruption_checks": [check.to_dict() for check in self.disruption_checks],
            "recovery_checks": [check.to_dict() for check in self.recovery_checks],
            "action_timeout_seconds": self.action_timeout_seconds,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, object]) -> FaultScenario:
        raw_targets = document.get("targets")
        raw_disruption = document.get("disruption_checks")
        raw_recovery = document.get("recovery_checks")
        if not isinstance(raw_targets, list):
            raise PlanValidationError("targets 必须是数组")
        if not isinstance(raw_disruption, list) or not isinstance(raw_recovery, list):
            raise PlanValidationError("故障期和恢复期检查必须是数组")
        try:
            action = FaultAction(str(document.get("action", "")))
            recovery_action = RecoveryAction(str(document.get("recovery_action", "")))
        except ValueError as exc:
            raise PlanValidationError("故障场景 action/recovery_action 格式错误") from exc
        action_timeout = _finite_timeout(document, "action_timeout_seconds")
        timeout = _finite_timeout(document, "timeout_seconds")
        scenario = cls(
            scenario_id=str(document.get("scenario_id", "")),
            kind=str(document.get("kind", "")),
            action=action,
            recovery_action=recovery_action,
            targets=tuple(
                ContainerTarget.from_dict(item) for item in raw_targets if isinstance(item, Mapping)
            ),
            disruption_checks=tuple(
                FaultCheck.from_dict(item) for item in raw_disruption if isinstance(item, Mapping)
            ),
            recovery_checks=tuple(
                FaultCheck.from_dict(item) for item in raw_recovery if isinstance(item, Mapping)
            ),
            action_timeout_seconds=action_timeout,
            timeout_seconds=timeout,
        )
        if len(scenario.targets) != len(raw_targets):
            raise PlanValidationError("targets 包含非对象项")
        if len(scenario.disruption_checks) != len(raw_disruption) or len(
            scenario.recovery_checks
        ) != len(raw_recovery):
            raise PlanValidationError("故障检查包含非对象项")
        scenario.validate()
        return scenario


@dataclass(frozen=True, slots=True)
class FaultPlan:
    campaign_id: str
    scenarios: tuple[FaultScenario, ...]

    def validate(self) -> None:
        if _CAMPAIGN_ID.fullmatch(self.campaign_id) is None:
            raise PlanValidationError("Campaign ID 不是安全单段标识")
        if not self.scenarios:
            raise PlanValidationError("故障计划至少需要一个场景")
        for scenario in self.scenarios:
            scenario.validate()
        ids = [scenario.scenario_id for scenario in self.scenarios]
        if len(ids) != len(set(ids)):
            raise PlanValidationError("故障场景 ID 不能重复")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "campaign_id": self.campaign_id,
            "scenarios": [scenario.to_dict() for scenario in self.scenarios],
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, object]) -> FaultPlan:
        if type(document.get("schema_version")) is not int or document.get("schema_version") != 1:
            raise PlanValidationError("故障计划 schema_version 必须为 1")
        raw_scenarios = document.get("scenarios")
        if not isinstance(raw_scenarios, list):
            raise PlanValidationError("scenarios 必须是数组")
        plan = cls(
            campaign_id=str(document.get("campaign_id", "")),
            scenarios=tuple(
                FaultScenario.from_dict(item) for item in raw_scenarios if isinstance(item, Mapping)
            ),
        )
        if len(plan.scenarios) != len(raw_scenarios):
            raise PlanValidationError("scenarios 包含非对象项")
        plan.validate()
        return plan


class MaintenanceLock:
    """验证既有锁的身份和权限；故障入口不得临时伪造一把新锁。"""

    def __init__(self, path: Path, campaign_id: str) -> None:
        self.path = path
        self.campaign_id = campaign_id
        self._descriptor: int | None = None

    @property
    def acquired(self) -> bool:
        return self._descriptor is not None

    def __enter__(self) -> MaintenanceLock:
        try:
            metadata = os.lstat(self.path)
        except FileNotFoundError as exc:
            raise PlanValidationError("Campaign 维护锁不存在") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise PlanValidationError("Campaign 维护锁必须是普通文件且不能是软链接")
        if metadata.st_nlink != 1:
            raise PlanValidationError("Campaign 维护锁必须只有一个硬链接")
        if metadata.st_uid != os.geteuid():
            raise PlanValidationError("Campaign 维护锁必须属于当前用户")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise PlanValidationError("Campaign 维护锁权限必须为 0600")
        descriptor = os.open(self.path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise PlanValidationError("Campaign 维护锁在打开期间发生替换")
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o600
            ):
                raise PlanValidationError("Campaign 维护锁打开后的身份或权限无效")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise PlanValidationError("Campaign 维护锁已被其他执行者持有") from exc
            os.lseek(descriptor, 0, os.SEEK_SET)
            if opened.st_size > 64 * 1024:
                raise PlanValidationError("Campaign 维护锁内容超过 64 KiB")
            raw = os.read(descriptor, 64 * 1024)
            after_read = os.fstat(descriptor)
            if (
                after_read.st_size != opened.st_size
                or after_read.st_mtime_ns != opened.st_mtime_ns
                or after_read.st_ctime_ns != opened.st_ctime_ns
                or after_read.st_nlink != 1
            ):
                raise PlanValidationError("Campaign 维护锁在读取期间发生修改")
            try:
                document = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PlanValidationError("Campaign 维护锁内容不是合法 JSON") from exc
            if not isinstance(document, dict) or document.get("campaign_id") != self.campaign_id:
                raise PlanValidationError("Campaign 维护锁不属于当前 Campaign")
        except Exception:
            os.close(descriptor)
            raise
        self._descriptor = descriptor
        return self

    def __exit__(self, *_: object) -> None:
        if self._descriptor is None:
            return
        try:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self._descriptor)
            self._descriptor = None


class FaultRuntime(Protocol):
    def inspect(self, container_id: str) -> ContainerIdentity: ...

    def stop(self, container_id: str, timeout_seconds: float) -> None: ...

    def start(self, container_id: str, timeout_seconds: float) -> None: ...

    def restart(self, container_id: str, timeout_seconds: float) -> None: ...

    def verify(self, scenario: FaultScenario, check: FaultCheck, phase: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class ScenarioRunResult:
    scenario_id: str
    status: str
    recovered: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "status": self.status,
            "recovered": self.recovered,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class FaultPlanRunResult:
    campaign_id: str
    dry_run: bool
    scenarios: tuple[ScenarioRunResult, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "campaign_id": self.campaign_id,
            "dry_run": self.dry_run,
            "scenarios": [scenario.to_dict() for scenario in self.scenarios],
        }


class FaultSequenceRunner:
    def __init__(
        self,
        runtime: FaultRuntime,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runtime = runtime
        self._monotonic_clock = monotonic_clock

    def _verify_identity(
        self,
        target: ContainerTarget,
        *,
        require_running: bool,
    ) -> ContainerIdentity:
        actual = self._runtime.inspect(target.container_id)
        if actual.container_id != target.container_id:
            raise PlanValidationError("Docker inspect 返回的容器 ID 与计划不一致")
        if (
            actual.compose_project != target.compose_project
            or actual.compose_service != target.compose_service
        ):
            raise PlanValidationError(
                "故障目标 Compose 身份与 Docker inspect 不一致: "
                f"{target.compose_project}/{target.compose_service}"
            )
        if type(actual.running) is not bool:
            raise PlanValidationError("Docker inspect 的 running 状态不是布尔值")
        if require_running and not actual.running:
            raise PlanValidationError("故障目标当前未运行，拒绝改变其原始状态")
        return actual

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self._monotonic_clock()
        if remaining <= 0:
            raise TimeoutError("故障场景超过总超时")
        return remaining

    @staticmethod
    def _add_reason(current: str, reason: str) -> str:
        return f"{current}；{reason}" if current else reason

    def _verify_check(
        self,
        scenario: FaultScenario,
        check: FaultCheck,
        phase: str,
        deadline: float,
    ) -> bool:
        bounded_check = replace(
            check,
            timeout_seconds=min(check.timeout_seconds, self._remaining(deadline)),
        )
        return self._runtime.verify(scenario, bounded_check, phase)

    def _restore_running(self, target: ContainerTarget, timeout_seconds: float) -> None:
        self._verify_identity(target, require_running=False)
        self._runtime.start(target.container_id, timeout_seconds)
        self._verify_identity(target, require_running=True)

    def _run_one(self, scenario: FaultScenario) -> ScenarioRunResult:
        deadline = self._monotonic_clock() + scenario.timeout_seconds
        for target in scenario.targets:
            self._verify_identity(target, require_running=True)
        attempted: list[ContainerTarget] = []
        disruption_ok = False
        recovery_ok = True
        execution_reason = ""
        try:
            if scenario.action is FaultAction.STOP_START:
                for target in scenario.targets:
                    attempted.append(target)
                    self._runtime.stop(
                        target.container_id,
                        min(scenario.action_timeout_seconds, self._remaining(deadline)),
                    )
            else:
                for target in scenario.targets:
                    attempted.append(target)
                    self._runtime.restart(
                        target.container_id,
                        min(scenario.action_timeout_seconds, self._remaining(deadline)),
                    )
            disruption_results = [
                self._verify_check(scenario, check, "disruption", deadline)
                for check in scenario.disruption_checks
            ]
            disruption_ok = all(disruption_results)
            if not disruption_ok:
                execution_reason = "故障期检查未全部通过"
        except Exception as exc:  # 恢复优先，原异常只进入受控结果。
            execution_reason = f"故障动作或检查失败: {type(exc).__name__}: {exc}"
        finally:
            for target in reversed(attempted):
                try:
                    self._restore_running(target, scenario.action_timeout_seconds)
                except Exception as exc:  # 后续恢复检查仍需运行并保留双重原因。
                    recovery_ok = False
                    execution_reason = self._add_reason(
                        execution_reason,
                        f"精确恢复失败: {type(exc).__name__}: {exc}",
                    )

        for check in scenario.recovery_checks:
            try:
                if not self._verify_check(scenario, check, "recovery", deadline):
                    recovery_ok = False
                    execution_reason = self._add_reason(
                        execution_reason,
                        f"恢复检查失败: {check.name}",
                    )
            except Exception as exc:
                recovery_ok = False
                execution_reason = self._add_reason(
                    execution_reason,
                    f"恢复检查异常: {type(exc).__name__}: {exc}",
                )

        if not recovery_ok:
            status = "RECOVERY_FAILED"
        elif not disruption_ok:
            status = "DISRUPTION_FAILED"
        else:
            status = "PASS"
        return ScenarioRunResult(
            scenario_id=scenario.scenario_id,
            status=status,
            recovered=recovery_ok,
            reason=execution_reason or "故障与恢复检查均通过",
        )

    def run(
        self,
        plan: FaultPlan,
        *,
        dry_run: bool = True,
        maintenance_lock: MaintenanceLock | None,
    ) -> FaultPlanRunResult:
        plan.validate()
        if dry_run:
            return FaultPlanRunResult(
                campaign_id=plan.campaign_id,
                dry_run=True,
                scenarios=tuple(
                    ScenarioRunResult(
                        scenario_id=scenario.scenario_id,
                        status="DRY_RUN",
                        recovered=False,
                        reason="仅校验精确目标、检查和恢复计划，未执行容器操作",
                    )
                    for scenario in plan.scenarios
                ),
            )
        if (
            maintenance_lock is None
            or not maintenance_lock.acquired
            or maintenance_lock.campaign_id != plan.campaign_id
        ):
            raise PlanValidationError("执行故障注入必须持有当前 Campaign 维护锁")

        results: list[ScenarioRunResult] = []
        previous_recovered = True
        for scenario in plan.scenarios:
            if not previous_recovered:
                results.append(
                    ScenarioRunResult(
                        scenario.scenario_id,
                        "BLOCKED_BY_PREVIOUS_RECOVERY",
                        False,
                        "前一故障未完成恢复，序列门禁禁止继续",
                    )
                )
                continue
            result = self._run_one(scenario)
            results.append(result)
            previous_recovered = result.recovered
        return FaultPlanRunResult(plan.campaign_id, False, tuple(results))


def _operator_checks(operator_code: str) -> tuple[tuple[FaultCheck, ...], tuple[FaultCheck, ...]]:
    return (
        (
            FaultCheck(f"{operator_code} 精确目标已停止", 30, "containers_stopped"),
            FaultCheck(f"{operator_code} TTL 后离线且剩余实例接管", 180),
        ),
        (
            FaultCheck(f"{operator_code} 精确目标已恢复运行", 30, "containers_running"),
            FaultCheck(f"{operator_code} 原实例重新健康注册且可分配租约", 300),
        ),
    )


def build_single_operator_scenarios(
    targets: Mapping[str, ContainerTarget],
) -> tuple[FaultScenario, ...]:
    if set(targets) != set(_OPERATOR_CODES):
        raise PlanValidationError("单实例轮换必须精确覆盖七类算子")
    scenarios: list[FaultScenario] = []
    for index, operator_code in enumerate(_OPERATOR_CODES, start=1):
        disruption, recovery = _operator_checks(operator_code)
        scenarios.append(
            FaultScenario(
                scenario_id=f"fault-operator-{index:02d}-{operator_code.replace('_', '-')}",
                kind="single_operator",
                action=FaultAction.STOP_START,
                recovery_action=RecoveryAction.ENSURE_RUNNING,
                targets=(targets[operator_code],),
                disruption_checks=disruption,
                recovery_checks=recovery,
                action_timeout_seconds=30,
                timeout_seconds=600,
            )
        )
        scenarios[-1].validate()
    return tuple(scenarios)


def build_gpu_group_scenario(
    gpu_index: int,
    targets: Mapping[str, ContainerTarget],
) -> FaultScenario:
    if gpu_index not in {0, 1, 2}:
        raise PlanValidationError("GPU 组只允许 GPU0/GPU1/GPU2")
    if set(targets) != set(_GPU_OPERATOR_CODES):
        raise PlanValidationError("单 GPU 故障必须精确包含六类 GPU 算子")
    scenario = FaultScenario(
        scenario_id=f"fault-gpu-{gpu_index}",
        kind="gpu_group",
        action=FaultAction.STOP_START,
        recovery_action=RecoveryAction.ENSURE_RUNNING,
        targets=tuple(targets[code] for code in _GPU_OPERATOR_CODES),
        disruption_checks=(
            FaultCheck(
                f"GPU{gpu_index} 六个精确目标已停止",
                60,
                "containers_stopped",
            ),
            FaultCheck(f"GPU{gpu_index} 六实例 TTL 后离线且其他两卡接管", 300),
        ),
        recovery_checks=(
            FaultCheck(
                f"GPU{gpu_index} 六个精确目标已恢复运行",
                60,
                "containers_running",
            ),
            FaultCheck(f"GPU{gpu_index} 六实例重新健康注册且 GPU 归属正确", 600),
        ),
        action_timeout_seconds=30,
        timeout_seconds=1800,
    )
    scenario.validate()
    return scenario


def build_platform_scenarios(
    targets: Mapping[str, ContainerTarget],
) -> tuple[FaultScenario, ...]:
    if set(targets) != set(_PLATFORM_SERVICES):
        raise PlanValidationError("平台服务轮换必须精确覆盖四个平台服务")
    checks = {
        "control-service": "任务事实保留且算子重新心跳恢复",
        "orchestrator-service": "Outbox、Kafka offset 与 DAG 幂等恢复",
        "vision-orchestrator-service": "视觉事件消费和聚合恢复且结果不重复",
        "online-gateway-service": "在线请求恢复且实时 ASR WebSocket 可重连",
    }
    scenarios = tuple(
        FaultScenario(
            scenario_id=f"fault-platform-{index:02d}",
            kind="platform_service",
            action=FaultAction.RESTART,
            recovery_action=RecoveryAction.ENSURE_RUNNING,
            targets=(targets[service],),
            disruption_checks=(FaultCheck(f"{service} 重启期间行为被如实记录", 180),),
            recovery_checks=(
                FaultCheck(f"{service} 精确目标已恢复运行", 30, "containers_running"),
                FaultCheck(checks[service], 600),
            ),
            action_timeout_seconds=30,
            timeout_seconds=900,
        )
        for index, service in enumerate(_PLATFORM_SERVICES, start=1)
    )
    for scenario in scenarios:
        scenario.validate()
    return scenarios


def build_kafka_scenario(target: ContainerTarget) -> FaultScenario:
    scenario = FaultScenario(
        scenario_id="fault-kafka",
        kind="kafka",
        action=FaultAction.RESTART,
        recovery_action=RecoveryAction.ENSURE_RUNNING,
        targets=(target,),
        disruption_checks=(FaultCheck("Kafka 重启期间已接受任务的 Outbox 事实保留", 300),),
        recovery_checks=(
            FaultCheck("Kafka 精确目标已恢复运行", 30, "containers_running"),
            FaultCheck("Outbox 重发、Consumer 恢复且 DAG 不重复", 900),
        ),
        action_timeout_seconds=30,
        timeout_seconds=1500,
    )
    scenario.validate()
    return scenario


def build_redis_scenario(target: ContainerTarget) -> FaultScenario:
    scenario = FaultScenario(
        scenario_id="fault-redis",
        kind="redis",
        action=FaultAction.RESTART,
        recovery_action=RecoveryAction.ENSURE_RUNNING,
        targets=(target,),
        disruption_checks=(FaultCheck("Redis 重启期间新租约受控拒绝且不超卖", 300),),
        recovery_checks=(
            FaultCheck("Redis 精确目标已恢复运行", 30, "containers_running"),
            FaultCheck("算子重新注册、旧租约回收且容量恢复", 900),
        ),
        action_timeout_seconds=30,
        timeout_seconds=1500,
    )
    scenario.validate()
    return scenario


class DockerCliRuntime:
    """只接受无 shell 的精确 ID 命令；语义恢复证据未接线时失败关闭。"""

    @staticmethod
    def _run(arguments: Sequence[str], *, timeout: float) -> str:
        completed = subprocess.run(
            list(arguments),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "Docker 命令失败")
        return completed.stdout

    def _inspect(self, container_id: str, *, timeout_seconds: float) -> ContainerIdentity:
        if _CONTAINER_ID.fullmatch(container_id) is None:
            raise PlanValidationError("Docker inspect 只接受完整容器 ID")
        output = self._run(
            ("docker", "inspect", container_id),
            timeout=timeout_seconds,
        )
        document = json.loads(output)
        if not isinstance(document, list) or len(document) != 1:
            raise RuntimeError("Docker inspect 没有返回唯一容器")
        item = document[0]
        if not isinstance(item, dict):
            raise RuntimeError("Docker inspect 容器结果不是对象")
        config = item.get("Config")
        state = item.get("State")
        if not isinstance(config, dict) or not isinstance(state, dict):
            raise RuntimeError("Docker inspect 缺少 Config 或 State")
        labels = config.get("Labels")
        if not isinstance(labels, dict):
            raise RuntimeError("Docker inspect 缺少 Compose labels")
        return ContainerIdentity(
            container_id=str(item.get("Id", "")),
            compose_project=str(labels.get("com.docker.compose.project", "")),
            compose_service=str(labels.get("com.docker.compose.service", "")),
            running=state.get("Running") is True,
        )

    def inspect(self, container_id: str) -> ContainerIdentity:
        return self._inspect(container_id, timeout_seconds=30)

    def stop(self, container_id: str, timeout_seconds: float) -> None:
        self._run(
            (
                "docker",
                "stop",
                "--time",
                str(max(1, int(timeout_seconds) - 1)),
                container_id,
            ),
            timeout=timeout_seconds,
        )

    def start(self, container_id: str, timeout_seconds: float) -> None:
        self._run(("docker", "start", container_id), timeout=timeout_seconds)

    def restart(self, container_id: str, timeout_seconds: float) -> None:
        self._run(
            (
                "docker",
                "restart",
                "--time",
                str(max(1, int(timeout_seconds) - 1)),
                container_id,
            ),
            timeout=timeout_seconds,
        )

    def verify(self, scenario: FaultScenario, check: FaultCheck, phase: str) -> bool:
        if check.probe in {"containers_stopped", "containers_running"}:
            deadline = time.monotonic() + check.timeout_seconds
            expected_running = check.probe == "containers_running"
            for target in scenario.targets:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"容器状态检查超时: {check.name}")
                actual = self._inspect(
                    target.container_id,
                    timeout_seconds=min(30, remaining),
                )
                if actual.running is not expected_running:
                    return False
            return True
        # 业务语义必须由 Campaign 观测器提供，不能仅凭容器恢复伪报通过。
        return False


class _DryRunRuntime:
    def inspect(self, container_id: str) -> ContainerIdentity:
        raise RuntimeError("dry-run 不应调用 Docker inspect")

    def stop(self, container_id: str, timeout_seconds: float) -> None:
        raise RuntimeError("dry-run 不应停止容器")

    def start(self, container_id: str, timeout_seconds: float) -> None:
        raise RuntimeError("dry-run 不应启动容器")

    def restart(self, container_id: str, timeout_seconds: float) -> None:
        raise RuntimeError("dry-run 不应重启容器")

    def verify(self, scenario: FaultScenario, check: FaultCheck, phase: str) -> bool:
        raise RuntimeError("dry-run 不应运行恢复检查")


def _read_plan(path: Path) -> FaultPlan:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError as exc:
        raise PlanValidationError("故障计划不存在") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PlanValidationError("故障计划必须是普通文件且不能是软链接")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise PlanValidationError("故障计划在打开期间发生替换")
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > 2 * 1024 * 1024:
            raise PlanValidationError("故障计划不是普通文件或超过 2 MiB")
        raw = os.read(descriptor, 2 * 1024 * 1024 + 1)
        after_read = os.fstat(descriptor)
        if len(raw) > 2 * 1024 * 1024:
            raise PlanValidationError("故障计划超过 2 MiB")
        if (
            after_read.st_size != opened.st_size
            or after_read.st_mtime_ns != opened.st_mtime_ns
            or after_read.st_ctime_ns != opened.st_ctime_ns
        ):
            raise PlanValidationError("故障计划在读取期间发生修改")
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanValidationError("故障计划不是合法 JSON") from exc
    finally:
        os.close(descriptor)
    if not isinstance(document, dict):
        raise PlanValidationError("故障计划必须是 JSON 对象")
    return FaultPlan.from_dict(document)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="极限负载 Campaign 受控故障计划")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--maintenance-lock", type=Path)
    parser.add_argument("--confirm-campaign-id")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    plan = _read_plan(args.plan)
    if not args.execute:
        result = FaultSequenceRunner(_DryRunRuntime()).run(
            plan,
            dry_run=True,
            maintenance_lock=None,
        )
    else:
        if args.confirm_campaign_id != plan.campaign_id:
            raise PlanValidationError("执行故障必须精确确认 Campaign ID")
        if args.maintenance_lock is None:
            raise PlanValidationError("执行故障必须提供当前 Campaign 维护锁")
        with MaintenanceLock(args.maintenance_lock, plan.campaign_id) as lock:
            result = FaultSequenceRunner(DockerCliRuntime()).run(
                plan,
                dry_run=False,
                maintenance_lock=lock,
            )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if all(item.status in {"DRY_RUN", "PASS"} for item in result.scenarios) else 1


if __name__ == "__main__":
    raise SystemExit(main())
