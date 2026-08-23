from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace

import pytest

from deploy.scripts.extreme_load_faults import (
    ContainerIdentity,
    ContainerTarget,
    FaultAction,
    FaultCheck,
    FaultPlan,
    FaultScenario,
    FaultSequenceRunner,
    MaintenanceLock,
    PlanValidationError,
    RecoveryAction,
    build_gpu_group_scenario,
    build_kafka_scenario,
    build_platform_scenarios,
    build_redis_scenario,
    build_single_operator_scenarios,
    main,
)


def _target(service: str, number: int = 1) -> ContainerTarget:
    platform_services = {
        "control-service",
        "orchestrator-service",
        "vision-orchestrator-service",
        "online-gateway-service",
        "kafka",
        "redis",
    }
    project = (
        "algorithm-scheduling-platform" if service in platform_services else "algorithm-operators"
    )
    return ContainerTarget(
        container_id=f"{number:064x}",
        compose_project=project,
        compose_service=service,
    )


def _scenario(*, service: str = "ocr-gpu0") -> FaultScenario:
    return FaultScenario(
        scenario_id="FAULT-001",
        kind="single_operator",
        action=FaultAction.STOP_START,
        recovery_action=RecoveryAction.ENSURE_RUNNING,
        targets=(_target(service),),
        disruption_checks=(
            FaultCheck("精确实例已停止", 5.0, "containers_stopped"),
            FaultCheck("实例 TTL 后离线", 30.0),
        ),
        recovery_checks=(
            FaultCheck("精确实例已恢复", 5.0, "containers_running"),
            FaultCheck("实例重新健康注册", 60.0),
        ),
        action_timeout_seconds=10.0,
        timeout_seconds=120.0,
    )


@dataclass
class FakeRuntime:
    identities: dict[str, ContainerIdentity]
    failed_checks: set[str] = field(default_factory=set)
    fail_restart_after_stop: set[str] = field(default_factory=set)
    actions: list[tuple[str, str]] = field(default_factory=list)

    def inspect(self, container_id: str) -> ContainerIdentity:
        return self.identities[container_id]

    def stop(self, container_id: str, timeout_seconds: float) -> None:
        self.actions.append(("stop", container_id))
        self._set_running(container_id, False)

    def start(self, container_id: str, timeout_seconds: float) -> None:
        self.actions.append(("start", container_id))
        self._set_running(container_id, True)

    def restart(self, container_id: str, timeout_seconds: float) -> None:
        self.actions.append(("restart", container_id))
        self._set_running(container_id, False)
        if container_id in self.fail_restart_after_stop:
            raise RuntimeError("restart failed after stop")
        self._set_running(container_id, True)

    def _set_running(self, container_id: str, running: bool) -> None:
        current = self.identities[container_id]
        self.identities[container_id] = ContainerIdentity(
            container_id=current.container_id,
            compose_project=current.compose_project,
            compose_service=current.compose_service,
            running=running,
        )

    def verify(self, scenario: FaultScenario, check: FaultCheck, phase: str) -> bool:
        self.actions.append((f"verify:{phase}", check.name))
        if check.probe == "containers_stopped":
            return all(
                not self.identities[target.container_id].running for target in scenario.targets
            )
        if check.probe == "containers_running":
            return all(self.identities[target.container_id].running for target in scenario.targets)
        return check.name not in self.failed_checks


def _identity(target: ContainerTarget) -> ContainerIdentity:
    return ContainerIdentity(
        container_id=target.container_id,
        compose_project=target.compose_project,
        compose_service=target.compose_service,
        running=True,
    )


def _lock(tmp_path, campaign_id: str = "campaign-001"):  # type: ignore[no-untyped-def]
    path = tmp_path / "maintenance.lock"
    path.write_text(json.dumps({"campaign_id": campaign_id}), encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.mark.parametrize(
    "target",
    (
        ContainerTarget("short", "project", "service"),
        ContainerTarget("A" * 64, "project", "service"),
        ContainerTarget("a" * 64, "project*", "service"),
        ContainerTarget("a" * 64, "project", "service*"),
    ),
)
def test_fault_target_requires_exact_full_id_and_compose_identity(
    target: ContainerTarget,
) -> None:
    with pytest.raises(PlanValidationError):
        target.validate()


def test_fault_kind_cannot_be_used_to_target_an_unrelated_service() -> None:
    with pytest.raises(PlanValidationError, match="21 实例"):
        replace(_scenario(), targets=(_target("unrelated-service"),)).validate()

    target = _target("ocr-gpu0")
    with pytest.raises(PlanValidationError, match="Compose project"):
        replace(
            _scenario(),
            targets=(
                ContainerTarget(
                    target.container_id,
                    "unrelated-project",
                    target.compose_service,
                ),
            ),
        ).validate()


def test_execute_requires_current_private_maintenance_lock(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scenario = _scenario()
    target = scenario.targets[0]
    runtime = FakeRuntime({target.container_id: _identity(target)})
    runner = FaultSequenceRunner(runtime)

    with pytest.raises(PlanValidationError, match="维护锁"):
        runner.run(
            FaultPlan("campaign-001", (scenario,)),
            dry_run=False,
            maintenance_lock=None,
        )

    lock_path = _lock(tmp_path)
    lock_path.chmod(0o644)
    with pytest.raises(PlanValidationError, match="0600"):
        with MaintenanceLock(lock_path, "campaign-001"):
            pass

    lock_path.chmod(0o600)
    second_link = tmp_path / "maintenance.link"
    os.link(lock_path, second_link)
    with pytest.raises(PlanValidationError, match="硬链接"):
        with MaintenanceLock(lock_path, "campaign-001"):
            pass


def test_default_dry_run_never_calls_mutating_runtime() -> None:
    scenario = _scenario()
    target = scenario.targets[0]
    runtime = FakeRuntime({target.container_id: _identity(target)})

    result = FaultSequenceRunner(runtime).run(
        FaultPlan("campaign-001", (scenario,)),
        dry_run=True,
        maintenance_lock=None,
    )

    assert result.dry_run
    assert result.scenarios[0].status == "DRY_RUN"
    assert runtime.actions == []


def test_compose_identity_mismatch_fails_before_stop(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scenario = _scenario()
    target = scenario.targets[0]
    runtime = FakeRuntime(
        {
            target.container_id: ContainerIdentity(
                container_id=target.container_id,
                compose_project=target.compose_project,
                compose_service="wrong-service",
                running=True,
            )
        }
    )

    with MaintenanceLock(_lock(tmp_path), "campaign-001") as lock:
        with pytest.raises(PlanValidationError, match="Compose 身份"):
            FaultSequenceRunner(runtime).run(
                FaultPlan("campaign-001", (scenario,)),
                dry_run=False,
                maintenance_lock=lock,
            )
    assert runtime.actions == []


def test_nonrunning_target_is_rejected_without_changing_original_state(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scenario = _scenario()
    target = scenario.targets[0]
    identity = _identity(target)
    runtime = FakeRuntime(
        {
            target.container_id: ContainerIdentity(
                identity.container_id,
                identity.compose_project,
                identity.compose_service,
                False,
            )
        }
    )

    with MaintenanceLock(_lock(tmp_path), "campaign-001") as lock:
        with pytest.raises(PlanValidationError, match="当前未运行"):
            FaultSequenceRunner(runtime).run(
                FaultPlan("campaign-001", (scenario,)),
                dry_run=False,
                maintenance_lock=lock,
            )

    assert runtime.actions == []
    assert not runtime.identities[target.container_id].running


def test_failed_restart_still_executes_exact_running_recovery(tmp_path) -> None:  # type: ignore[no-untyped-def]
    targets = {
        service: _target(service, index + 40)
        for index, service in enumerate(
            (
                "control-service",
                "orchestrator-service",
                "vision-orchestrator-service",
                "online-gateway-service",
            )
        )
    }
    scenario = build_platform_scenarios(targets)[0]
    target = scenario.targets[0]
    runtime = FakeRuntime(
        {target.container_id: _identity(target)},
        fail_restart_after_stop={target.container_id},
    )

    with MaintenanceLock(_lock(tmp_path), "campaign-001") as lock:
        result = FaultSequenceRunner(runtime).run(
            FaultPlan("campaign-001", (scenario,)),
            dry_run=False,
            maintenance_lock=lock,
        )

    assert result.scenarios[0].status == "DISRUPTION_FAILED"
    assert result.scenarios[0].recovered
    assert runtime.identities[target.container_id].running
    assert ("restart", target.container_id) in runtime.actions
    assert ("start", target.container_id) in runtime.actions


def test_plan_requires_explicit_recovery_action_and_total_timeout_budget() -> None:
    scenario = _scenario()
    document = scenario.to_dict()
    document.pop("recovery_action")

    with pytest.raises(PlanValidationError, match="recovery_action"):
        FaultScenario.from_dict(document)
    with pytest.raises(PlanValidationError, match="超时总和"):
        replace(scenario, timeout_seconds=119).validate()
    with pytest.raises(PlanValidationError, match="动作超时"):
        replace(scenario, action_timeout_seconds=True).validate()
    with pytest.raises(PlanValidationError, match="检查超时"):
        FaultCheck("invalid", True).validate()


def test_recovery_failure_stops_following_scenario_but_still_starts_target(tmp_path) -> None:  # type: ignore[no-untyped-def]
    first = _scenario(service="ocr-gpu0")
    second = FaultScenario(
        scenario_id="FAULT-002",
        kind="single_operator",
        action=FaultAction.STOP_START,
        recovery_action=RecoveryAction.ENSURE_RUNNING,
        targets=(_target("vbas-gpu0", 2),),
        disruption_checks=(
            FaultCheck("VBas 精确实例已停止", 5, "containers_stopped"),
            FaultCheck("VBas TTL 后离线", 30),
        ),
        recovery_checks=(
            FaultCheck("VBas 精确实例已恢复", 5, "containers_running"),
            FaultCheck("VBas 重新健康注册", 60),
        ),
        action_timeout_seconds=10,
        timeout_seconds=120,
    )
    identities = {
        target.container_id: _identity(target) for target in (*first.targets, *second.targets)
    }
    runtime = FakeRuntime(identities, failed_checks={"实例重新健康注册"})

    with MaintenanceLock(_lock(tmp_path), "campaign-001") as lock:
        result = FaultSequenceRunner(runtime).run(
            FaultPlan("campaign-001", (first, second)),
            dry_run=False,
            maintenance_lock=lock,
        )

    assert result.scenarios[0].status == "RECOVERY_FAILED"
    assert result.scenarios[1].status == "BLOCKED_BY_PREVIOUS_RECOVERY"
    assert ("stop", first.targets[0].container_id) in runtime.actions
    assert ("start", first.targets[0].container_id) in runtime.actions
    assert ("stop", second.targets[0].container_id) not in runtime.actions


def test_scenario_factories_lock_expected_scope() -> None:
    operator_codes = (
        "asr_offline",
        "asr_online",
        "ocr",
        "vbas",
        "facerec",
        "screen_det",
        "ppt_slice",
    )
    operator_targets = {
        code: _target(
            "ppt-slice-cpu0" if code == "ppt_slice" else f"{code.replace('_', '-')}-gpu0",
            index + 1,
        )
        for index, code in enumerate(operator_codes)
    }
    single = build_single_operator_scenarios(operator_targets)
    gpu_codes = (
        "asr_offline",
        "asr_online",
        "ocr",
        "vbas",
        "facerec",
        "screen_det",
    )
    gpu_scenarios = tuple(
        build_gpu_group_scenario(
            gpu_index,
            {
                code: _target(
                    f"{code.replace('_', '-')}-gpu{gpu_index}",
                    index + 20 + gpu_index * 10,
                )
                for index, code in enumerate(gpu_codes)
            },
        )
        for gpu_index in range(3)
    )
    platforms = build_platform_scenarios(
        {
            name: _target(name, index + 40)
            for index, name in enumerate(
                (
                    "control-service",
                    "orchestrator-service",
                    "vision-orchestrator-service",
                    "online-gateway-service",
                )
            )
        }
    )

    assert len(single) == 7
    assert [scenario.scenario_id for scenario in gpu_scenarios] == [
        "fault-gpu-0",
        "fault-gpu-1",
        "fault-gpu-2",
    ]
    assert all(len(scenario.targets) == 6 for scenario in gpu_scenarios)
    assert len(platforms) == 4
    assert any(
        "Outbox" in check.name
        for check in build_kafka_scenario(_target("kafka", 60)).recovery_checks
    )
    assert any(
        "注册" in check.name for check in build_redis_scenario(_target("redis", 61)).recovery_checks
    )


def test_cli_defaults_to_dry_run(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    scenario = _scenario()
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(FaultPlan("campaign-001", (scenario,)).to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )

    assert main(["--plan", str(plan_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["dry_run"] is True
    assert output["scenarios"][0]["status"] == "DRY_RUN"
