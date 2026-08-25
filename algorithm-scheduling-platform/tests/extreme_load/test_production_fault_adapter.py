from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from deploy.scripts.extreme_load_faults import (
    ContainerIdentity,
    ContainerTarget,
    FaultCheck,
    FaultScenario,
    PlanValidationError,
)
from scripts.extreme_load import production_fault_adapter
from scripts.extreme_load.catalog import (
    CampaignCatalog,
    CampaignPhase,
    CaseSpec,
    FixtureDescriptor,
    FixtureKind,
    FixtureManifest,
)
from scripts.extreme_load.plan import CampaignPlan, build_campaign_plan
from scripts.extreme_load.system_probes import CommandResult
from scripts.run_extreme_load_campaign import _load_adapter_factories

_GIT_SHA = "a" * 40
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


def _case(case_id: str, load: dict[str, object]) -> CaseSpec:
    return CaseSpec(
        case_id=case_id,
        phase=CampaignPhase.RECOVERY,
        load=load,
        fixture_ids=("external-fixture-manifest",),
        expected="故障与恢复语义均通过",
        timeout_seconds=3600.0,
        guardrails=("maintenance_lock",),
        cleanup=("ensure_running",),
        evidence_path=f"campaign/phase-5-recovery/{case_id.lower()}.json",
    )


def _plan(case: CaseSpec) -> CampaignPlan:
    fixture = FixtureDescriptor(
        fixture_id="online-image",
        kind=FixtureKind.ONLINE_IMAGE,
        path="/external/online-image.jpg",
        size_bytes=1024,
        sha256="b" * 64,
    )
    return build_campaign_plan(
        release_tag="release-20260825",
        git_sha=_GIT_SHA,
        seed=20260825,
        control_origin="http://192.168.29.11:18100",
        gateway_origin="http://192.168.29.11:18103",
        fixture_manifest=FixtureManifest(schema_version=1, fixtures=(fixture,)),
        catalog=CampaignCatalog(schema_version=1, cases=(case,)),
    )


def _targets() -> dict[str, ContainerTarget]:
    services = [
        *(f"{code.replace('_', '-')}-gpu{gpu}" for gpu in range(3) for code in _GPU_OPERATOR_CODES),
        *(f"ppt-slice-cpu{index}" for index in range(3)),
        *_PLATFORM_SERVICES,
        "kafka",
        "redis",
    ]
    targets: dict[str, ContainerTarget] = {}
    for index, service in enumerate(services, start=1):
        project = (
            "algorithm-operators"
            if service not in {*_PLATFORM_SERVICES, "kafka", "redis"}
            else "algorithm-scheduling-platform"
        )
        targets[service] = ContainerTarget(f"{index:064x}", project, service)
    return targets


def _settings(tmp_path: Path) -> production_fault_adapter.FaultAdapterSettings:
    del tmp_path
    remote_release = Path(
        f"/data/reports/milestone-2b/releases/release-20260825/{_GIT_SHA}"
    )
    return production_fault_adapter.FaultAdapterSettings(
        target_hostname="192.168.29.11",
        ssh_user="root",
        ssh_port=22,
        delegated_lock_holder_pid=4242,
        delegated_lock_path=remote_release.parent / ".operator-lifecycle.lock",
        semantic_probe_path="/opt/algorithm-platform/deploy/scripts/extreme_load_fault_probe.py",
        semantic_probe_release_root=str(remote_release),
        semantic_probe_evidence_root=str(
            remote_release / "campaign" / "phase-5-recovery" / "fault-probes"
        ),
        probe_poll_seconds=0.01,
        single_operator_services={
            "asr_offline": "asr-offline-gpu0",
            "asr_online": "asr-online-gpu0",
            "ocr": "ocr-gpu0",
            "vbas": "vbas-gpu0",
            "facerec": "facerec-gpu0",
            "screen_det": "screen-det-gpu0",
            "ppt_slice": "ppt-slice-cpu0",
        },
        targets=_targets(),
    )


def _witness_media() -> production_fault_adapter.FaultWitnessMedia:
    return production_fault_adapter.FaultWitnessMedia(
        short_teacher_video_url="http://192.168.29.12:5555/course/short-T.mp4",
        long_teacher_video_url="http://192.168.29.12:5555/course/long-T.mp4",
        long_slides_video_url="http://192.168.29.12:5555/course/long-P.mp4",
    )


@dataclass
class _FakeGuard:
    release_root: Path
    held: bool = True

    def __enter__(self) -> _FakeGuard:
        if not self.held:
            raise ValueError("delegated maintenance lock is not held")
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def held_for(self, release_root: Path) -> bool:
        return self.held and release_root == self.release_root


@dataclass
class _FakeRuntime:
    identities: dict[str, ContainerIdentity]
    failed_checks: set[tuple[str, str]] = field(default_factory=set)
    actions: list[tuple[str, str]] = field(default_factory=list)
    check_evidence: list[Mapping[str, object]] = field(default_factory=list)
    lock_probe: Callable[[], bool] | None = None

    def _require_local_lock(self) -> None:
        if self.lock_probe is not None:
            assert self.lock_probe()

    def inspect(self, container_id: str) -> ContainerIdentity:
        return self.identities[container_id]

    def _set_running(self, container_id: str, running: bool) -> None:
        current = self.identities[container_id]
        self.identities[container_id] = replace(current, running=running)

    def stop(self, container_id: str, timeout_seconds: float) -> None:
        del timeout_seconds
        self._require_local_lock()
        self.actions.append(("stop", container_id))
        self._set_running(container_id, False)

    def start(self, container_id: str, timeout_seconds: float) -> None:
        del timeout_seconds
        self._require_local_lock()
        self.actions.append(("start", container_id))
        self._set_running(container_id, True)

    def restart(self, container_id: str, timeout_seconds: float) -> None:
        del timeout_seconds
        self._require_local_lock()
        self.actions.append(("restart", container_id))
        self._set_running(container_id, True)

    def verify(self, scenario: FaultScenario, check: FaultCheck, phase: str) -> bool:
        if check.probe == "containers_stopped":
            passed = all(
                not self.identities[item.container_id].running for item in scenario.targets
            )
        elif check.probe == "containers_running":
            passed = all(self.identities[item.container_id].running for item in scenario.targets)
        else:
            passed = (phase, check.name) not in self.failed_checks
        self.check_evidence.append(
            {"phase": phase, "probe": check.probe, "passed": passed, "evidence_refs": []}
        )
        return passed


@dataclass
class _RecordingCommandRunner:
    identities: dict[str, ContainerIdentity]
    remote_lock_held: bool = True
    lose_lock_after_stop: bool = False
    calls: list[tuple[str, ...]] = field(default_factory=list)
    call_times: list[datetime] = field(default_factory=list)

    def run(self, argv: Sequence[str], *, timeout_seconds: float) -> CommandResult:
        assert 0 < timeout_seconds <= 30
        command = tuple(argv)
        self.calls.append(command)
        self.call_times.append(datetime.now(UTC))
        argv = command
        if "--lock-only" in argv:
            challenge = argv[argv.index("--challenge") + 1]
            release_root = argv[argv.index("--release-root") + 1]
            lock_path = argv[argv.index("--lock-path") + 1]
            holder_pid = int(argv[argv.index("--lock-holder-pid") + 1])
            return CommandResult(
                0,
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "held" if self.remote_lock_held else "lost",
                        "challenge": challenge,
                        "release_root": release_root,
                        "lock_path": lock_path,
                        "holder_pid": holder_pid,
                    }
                ),
                "",
            )
        if argv[:2] == ("docker", "inspect"):
            identity = self.identities[argv[2]]
            return CommandResult(
                0,
                json.dumps(
                    [
                        {
                            "Id": identity.container_id,
                            "Config": {
                                "Labels": {
                                    "com.docker.compose.project": identity.compose_project,
                                    "com.docker.compose.service": identity.compose_service,
                                }
                            },
                            "State": {"Running": identity.running},
                        }
                    ]
                ),
                "",
            )
        if argv[0] == "docker":
            container_id = argv[-1]
            running = argv[1] != "stop"
            self.identities[container_id] = replace(self.identities[container_id], running=running)
            if argv[1] == "stop" and self.lose_lock_after_stop:
                self.remote_lock_held = False
            return CommandResult(0, "", "")
        values = {
            name: argv[argv.index(name) + 1]
            for name in (
                "--campaign-id",
                "--case-id",
                "--scenario-id",
                "--phase",
                "--check-index",
                "--challenge",
            )
        }
        target_values = [argv[index + 1] for index, item in enumerate(argv) if item == "--target"]
        targets = []
        for value in target_values:
            project, service, container_id = value.split(":", maxsplit=2)
            targets.append(
                {
                    "container_id": container_id,
                    "compose_project": project,
                    "compose_service": service,
                }
            )
        fault_window = None
        if "--fault-window-token" in argv:
            fault_window = {
                "token": argv[argv.index("--fault-window-token") + 1],
                "opened_at": argv[argv.index("--fault-window-opened-at") + 1],
            }
        return CommandResult(
            0,
            json.dumps(
                {
                    "schema_version": 1,
                    "campaign_id": values["--campaign-id"],
                    "case_id": values["--case-id"],
                    "scenario_id": values["--scenario-id"],
                    "phase": values["--phase"],
                    "check_index": int(values["--check-index"]),
                    "challenge": values["--challenge"],
                    "fault_window": fault_window,
                    "status": "passed",
                    "targets": targets,
                    "lock_binding": {
                        "holder_pid": int(argv[argv.index("--lock-holder-pid") + 1]),
                        "lock_path": argv[argv.index("--lock-path") + 1],
                        "release_root": argv[argv.index("--release-root") + 1],
                    },
                    "evidence_refs": [f"release:fault/{values['--phase']}.json#sha256:{'c' * 64}"],
                }
            ),
            "",
        )


def _runtime(settings: production_fault_adapter.FaultAdapterSettings) -> _FakeRuntime:
    return _FakeRuntime(
        {
            target.container_id: ContainerIdentity(
                target.container_id,
                target.compose_project,
                target.compose_service,
                True,
            )
            for target in settings.targets.values()
        }
    )


def _adapter(
    tmp_path: Path,
    case: CaseSpec,
    *,
    runtime: _FakeRuntime | None = None,
    guard: _FakeGuard | None = None,
) -> tuple[production_fault_adapter.ProductionFaultStageAdapter, _FakeRuntime]:
    release_root = (
        tmp_path
        / "release-20260825"
        / _GIT_SHA
        / "attempts"
        / "full-campaign-test"
    )
    settings = _settings(tmp_path)
    selected_runtime = runtime or _runtime(settings)
    selected_guard = guard or _FakeGuard(release_root)

    def runtime_factory(_case_id: str, lock_probe: Callable[[], bool]) -> _FakeRuntime:
        selected_runtime.lock_probe = lock_probe
        return selected_runtime

    adapter = production_fault_adapter.ProductionFaultStageAdapter(
        _plan(case),
        release_root,
        settings,
        runtime_factory=runtime_factory,
        lock_guard_factory=lambda _root: selected_guard,
    )
    return adapter, selected_runtime


def _all_cases() -> tuple[CaseSpec, ...]:
    cases = [
        _case(
            f"RECOVERY-OPERATOR-{code.replace('_', '-').upper()}",
            {"kind": "single_operator_fault", "operator": code.replace("_", "-")},
        )
        for code in _OPERATOR_CODES
    ]
    cases.extend(
        _case(f"RECOVERY-GPU-{gpu}", {"kind": "gpu_group_fault", "gpu": gpu}) for gpu in range(3)
    )
    platform_values = ("control", "orchestrator", "vision", "online-gateway")
    cases.extend(
        _case(
            f"RECOVERY-PLATFORM-{service.upper()}",
            {"kind": "platform_fault", "service": service},
        )
        for service in platform_values
    )
    cases.extend(
        _case(
            f"RECOVERY-{service.upper()}",
            {"kind": "middleware_fault", "service": service},
        )
        for service in ("kafka", "redis")
    )
    return tuple(cases)


def test_cli_dynamically_loads_production_fault_factory() -> None:
    factories = _load_adapter_factories(
        ["fault=scripts.extreme_load.production_fault_adapter:fault_factory"]
    )

    assert factories == {"fault": production_fault_adapter.fault_factory}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _all_cases(), ids=lambda case: case.case_id)
async def test_adapter_covers_each_authoritative_fault_case(
    tmp_path: Path,
    case: CaseSpec,
) -> None:
    adapter, runtime = _adapter(tmp_path, case)

    outcome = await adapter.execute(case)

    assert outcome.status == "passed"
    assert outcome.recovery_succeeded is True
    target_evidence = outcome.evidence["targets"]
    assert isinstance(target_evidence, list)
    assert all(len(item["container_id"]) == 64 for item in target_evidence)
    if case.load["kind"] == "gpu_group_fault":
        assert len(target_evidence) == 6
        assert [action for action, _ in runtime.actions].count("stop") == 6
        assert [action for action, _ in runtime.actions].count("start") == 6
    elif case.load["kind"] == "single_operator_fault":
        assert [action for action, _ in runtime.actions] == ["stop", "start"]
    else:
        assert [action for action, _ in runtime.actions] == ["restart", "start"]


@pytest.mark.asyncio
async def test_adapter_requires_current_local_campaign_lock_before_mutation(
    tmp_path: Path,
) -> None:
    case = _all_cases()[0]
    settings = _settings(tmp_path)
    runtime = _runtime(settings)
    adapter, _ = _adapter(
        tmp_path,
        case,
        runtime=runtime,
        guard=_FakeGuard(tmp_path / "wrong-release", held=False),
    )

    outcome = await adapter.execute(case)

    assert outcome.status == "blocked"
    assert "维护锁" in outcome.reason
    assert outcome.recovery_succeeded is None
    assert runtime.actions == []


@pytest.mark.asyncio
async def test_attempt_layout_holds_local_lock_separately_from_remote_canonical_lock(
    tmp_path: Path,
) -> None:
    case = _all_cases()[0]
    settings = _settings(tmp_path)
    runtime = _runtime(settings)
    release_root = (
        tmp_path
        / "release-20260825"
        / _GIT_SHA
        / "attempts"
        / "full-campaign-test"
    )
    release_root.mkdir(parents=True)

    def runtime_factory(_case_id: str, lock_probe: Callable[[], bool]) -> _FakeRuntime:
        runtime.lock_probe = lock_probe
        return runtime

    adapter = production_fault_adapter.ProductionFaultStageAdapter(
        _plan(case),
        release_root,
        settings,
        runtime_factory=runtime_factory,
    )

    outcome = await adapter.execute(case)

    local_lock = release_root / ".campaign-fault.lock"
    assert outcome.status == "passed"
    assert outcome.evidence["local_release_layout"] == "attempt"
    assert (
        outcome.evidence["maintenance_lock_binding"]
        == "local_attempt_and_remote_canonical"
    )
    assert local_lock.is_file()
    assert settings.delegated_lock_path != local_lock
    assert json.loads(local_lock.read_text(encoding="utf-8")) == {
        "attempt_root": str(release_root),
        "campaign_id": _plan(case).campaign_id,
        "schema_version": 1,
    }


@pytest.mark.asyncio
async def test_legacy_direct_release_layout_remains_controlled_compatible(
    tmp_path: Path,
) -> None:
    case = _all_cases()[0]
    settings = _settings(tmp_path)
    runtime = _runtime(settings)
    release_root = tmp_path / "release-20260825" / _GIT_SHA
    release_root.mkdir(parents=True)

    def runtime_factory(_case_id: str, lock_probe: Callable[[], bool]) -> _FakeRuntime:
        runtime.lock_probe = lock_probe
        return runtime

    adapter = production_fault_adapter.ProductionFaultStageAdapter(
        _plan(case),
        release_root,
        settings,
        runtime_factory=runtime_factory,
    )

    outcome = await adapter.execute(case)

    assert outcome.status == "passed"
    assert outcome.evidence["local_release_layout"] == "legacy_direct"
    assert (release_root / ".campaign-fault.lock").is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt_id", ("bad attempt", "attempt@1"))
async def test_noncanonical_attempt_layout_blocks_before_mutation(
    tmp_path: Path,
    attempt_id: str,
) -> None:
    case = _all_cases()[0]
    settings = _settings(tmp_path)
    runtime = _runtime(settings)
    release_root = tmp_path / "release-20260825" / _GIT_SHA / "attempts" / attempt_id
    adapter = production_fault_adapter.ProductionFaultStageAdapter(
        _plan(case),
        release_root,
        settings,
        runtime_factory=lambda _case_id, _lock_probe: runtime,
        lock_guard_factory=lambda _root: _FakeGuard(release_root),
    )

    outcome = await adapter.execute(case)

    assert outcome.status == "blocked"
    assert outcome.evidence == {"configuration_state": "release_mismatch"}
    assert runtime.actions == []


def test_local_attempt_lock_is_exclusive_and_detects_inode_rebinding(tmp_path: Path) -> None:
    release_root = (
        tmp_path / "release-20260825" / _GIT_SHA / "attempts" / "attempt-1"
    )
    release_root.mkdir(parents=True)
    first = production_fault_adapter._LocalCampaignLockGuard(
        release_root,
        "campaign-1",
    )

    with first:
        assert first.held_for(release_root)
        with pytest.raises(ValueError, match="其他故障执行者"):
            with production_fault_adapter._LocalCampaignLockGuard(
                release_root,
                "campaign-1",
            ):
                pass
        first.lock_path.unlink()
        first.lock_path.write_text("{}", encoding="utf-8")
        first.lock_path.chmod(0o600)
        assert not first.held_for(release_root)


@pytest.mark.asyncio
async def test_remote_delegated_lock_must_bind_remote_release_tag(tmp_path: Path) -> None:
    case = _all_cases()[0]
    settings = replace(
        _settings(tmp_path),
        delegated_lock_path=Path(
            "/data/reports/milestone-2b/releases/other/.operator-lifecycle.lock"
        ),
    )
    runtime = _runtime(settings)
    release_root = (
        tmp_path / "release-20260825" / _GIT_SHA / "attempts" / "attempt-1"
    )
    adapter = production_fault_adapter.ProductionFaultStageAdapter(
        _plan(case),
        release_root,
        settings,
        runtime_factory=lambda _case_id, _lock_probe: runtime,
        lock_guard_factory=lambda _root: _FakeGuard(release_root),
    )

    outcome = await adapter.execute(case)

    assert outcome.status == "blocked"
    assert outcome.evidence == {"configuration_state": "release_mismatch"}
    assert runtime.actions == []


@pytest.mark.asyncio
async def test_compose_identity_drift_blocks_before_any_container_change(tmp_path: Path) -> None:
    case = _all_cases()[0]
    settings = _settings(tmp_path)
    runtime = _runtime(settings)
    target = settings.targets[settings.single_operator_services["asr_offline"]]
    runtime.identities[target.container_id] = replace(
        runtime.identities[target.container_id],
        compose_service="asr-offline-gpu1",
    )
    adapter, _ = _adapter(tmp_path, case, runtime=runtime)

    outcome = await adapter.execute(case)

    assert outcome.status == "blocked"
    assert "身份" in outcome.reason
    assert runtime.actions == []


@pytest.mark.asyncio
async def test_recovery_semantic_failure_is_not_reported_as_success(tmp_path: Path) -> None:
    case = _all_cases()[0]
    settings = _settings(tmp_path)
    runtime = _runtime(settings)
    runtime.failed_checks = {("recovery", "asr_offline 原实例重新健康注册且可分配租约")}
    adapter, _ = _adapter(tmp_path, case, runtime=runtime)

    outcome = await adapter.execute(case)

    assert outcome.status == "failed"
    assert outcome.recovery_succeeded is False
    assert [action for action, _ in runtime.actions] == ["stop", "start"]


def _fault_config(
    tmp_path: Path,
    *,
    mutate: Callable[[dict[str, str], dict[str, str]], None] | None = None,
) -> Path:
    targets = _targets()
    operator_ids = {
        service: target.container_id
        for service, target in targets.items()
        if target.compose_project == "algorithm-operators"
    }
    platform_ids = {
        service: target.container_id
        for service, target in targets.items()
        if target.compose_project == "algorithm-scheduling-platform"
    }
    if mutate is not None:
        mutate(operator_ids, platform_ids)
    remote_release = Path(
        f"/data/reports/milestone-2b/releases/release-20260825/{_GIT_SHA}"
    )
    lock_path = remote_release.parent / ".operator-lifecycle.lock"
    lines = [
        "schema_version = 1",
        "",
        "[fault]",
        "enabled = true",
        'target_hostname = "192.168.29.11"',
        'ssh_user = "root"',
        "ssh_port = 22",
        "delegated_lock_holder_pid = 4242",
        f"delegated_lock_path = {json.dumps(str(lock_path))}",
        (
            'semantic_probe_path = "'
            '/opt/algorithm-platform/deploy/scripts/extreme_load_fault_probe.py"'
        ),
        (
            "semantic_probe_release_root = "
            + json.dumps(str(remote_release))
        ),
        (
            "semantic_probe_evidence_root = "
            + json.dumps(
                str(
                    remote_release
                    / "campaign"
                    / "phase-5-recovery"
                    / "fault-probes"
                )
            )
        ),
        "probe_poll_seconds = 1.0",
        "",
        "[fault.single_operator_services]",
        'asr_offline = "asr-offline-gpu0"',
        'asr_online = "asr-online-gpu0"',
        'ocr = "ocr-gpu0"',
        'vbas = "vbas-gpu0"',
        'facerec = "facerec-gpu0"',
        'screen_det = "screen-det-gpu0"',
        'ppt_slice = "ppt-slice-cpu0"',
        "",
        "[fault.operator_container_ids]",
        *(f'{service} = "{container_id}"' for service, container_id in operator_ids.items()),
        "",
        "[fault.platform_container_ids]",
        *(f'{service} = "{container_id}"' for service, container_id in platform_ids.items()),
        "",
    ]
    path = tmp_path / "fault-runtime.toml"
    path.write_text("\n".join(lines), encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    (
        lambda operators, _platform: operators.__setitem__("ocr-gpu0", "short"),
        lambda operators, _platform: operators.__setitem__("ocr-*", operators["ocr-gpu0"]),
        lambda _operators, platform: platform.__setitem__("text-analysis", "f" * 64),
        lambda _operators, platform: platform.__setitem__("redis", platform["kafka"]),
    ),
)
async def test_factory_blocks_incomplete_broad_or_duplicate_inventory_without_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[dict[str, str], dict[str, str]], None],
) -> None:
    case = _all_cases()[0]
    config = _fault_config(tmp_path, mutate=mutate)
    monkeypatch.setenv(production_fault_adapter.RUNTIME_CONFIG_ENV, str(config))
    constructed = False

    def forbidden_runtime(*_: object) -> object:
        nonlocal constructed
        constructed = True
        raise AssertionError("invalid inventory must not construct a runtime")

    monkeypatch.setattr(production_fault_adapter, "_production_runtime", forbidden_runtime)
    adapter = production_fault_adapter.fault_factory(
        _plan(case),
        tmp_path / "release-20260825" / _GIT_SHA,
    )

    outcome = await adapter.execute(case)

    assert outcome.status == "blocked"
    assert outcome.evidence == {"configuration_state": "config_invalid"}
    assert constructed is False


def test_runtime_config_delegated_lock_fields_are_remote_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _fault_config(tmp_path)
    monkeypatch.setenv(production_fault_adapter.RUNTIME_CONFIG_ENV, str(config))

    settings = production_fault_adapter._load_settings()

    assert settings.delegated_lock_holder_pid == 4242
    assert settings.delegated_lock_path == Path(
        "/data/reports/milestone-2b/releases/release-20260825/.operator-lifecycle.lock"
    )
    assert tmp_path not in settings.delegated_lock_path.parents


@pytest.mark.asyncio
async def test_factory_accepts_attempt_layout_with_separate_remote_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _all_cases()[0]
    plan = _plan(case)
    config = _fault_config(tmp_path)
    monkeypatch.setenv(production_fault_adapter.RUNTIME_CONFIG_ENV, str(config))
    settings = _settings(tmp_path)
    runtime = _runtime(settings)

    def runtime_factory(
        _settings: production_fault_adapter.FaultAdapterSettings,
        _plan: CampaignPlan,
        _case_id: str,
        lock_probe: Callable[[], bool],
    ) -> _FakeRuntime:
        runtime.lock_probe = lock_probe
        return runtime

    monkeypatch.setattr(production_fault_adapter, "_production_runtime", runtime_factory)
    release_root = (
        tmp_path
        / "release-20260825"
        / _GIT_SHA
        / "attempts"
        / "factory-attempt"
    )
    release_root.mkdir(parents=True)
    adapter = production_fault_adapter.fault_factory(plan, release_root)

    outcome = await adapter.execute(case)

    assert outcome.status == "passed"
    assert outcome.evidence["local_release_layout"] == "attempt"
    assert (release_root / ".campaign-fault.lock").is_file()


def test_remote_runtime_only_uses_exact_non_destructive_docker_commands() -> None:
    settings = _settings(Path("/tmp/fault-adapter-test"))
    identities = _runtime(settings).identities
    runner = _RecordingCommandRunner(identities)
    case = _all_cases()[0]
    plan = _plan(case)
    scenario = production_fault_adapter._scenario_for_case(case, settings)
    runtime = production_fault_adapter.SshFaultRuntime(
        runner,
        campaign_id=plan.campaign_id,
        case_id=case.case_id,
        semantic_probe_path=settings.semantic_probe_path,
        semantic_probe_release_root=settings.semantic_probe_release_root,
        semantic_probe_evidence_root=settings.semantic_probe_evidence_root,
        remote_lock_holder_pid=settings.delegated_lock_holder_pid,
        remote_lock_path=str(settings.delegated_lock_path),
        witness_media=_witness_media(),
        probe_poll_seconds=0.01,
        lock_probe=lambda: True,
    )
    target = scenario.targets[0]

    runtime.prepare(scenario)
    assert runtime.inspect(target.container_id).compose_service == target.compose_service
    runtime.stop(target.container_id, 10)
    assert runtime.verify(scenario, scenario.disruption_checks[0], "disruption")
    assert runtime.verify(scenario, scenario.disruption_checks[1], "disruption")
    runtime.start(target.container_id, 10)
    assert runtime.verify(scenario, scenario.recovery_checks[0], "recovery")
    assert runtime.verify(scenario, scenario.recovery_checks[1], "recovery")

    docker_calls = [argv for argv in runner.calls if argv[0] == "docker"]
    assert docker_calls
    assert {argv[1] for argv in docker_calls} <= {"inspect", "stop", "start", "restart"}
    assert all(
        len(argv[-1]) == 64 and set(argv[-1]) <= set("0123456789abcdef") for argv in docker_calls
    )
    for index, call in enumerate(runner.calls):
        if call[0] == "docker":
            assert index > 0
            assert "--lock-only" in runner.calls[index - 1]
    command_text = " ".join(" ".join(argv) for argv in runner.calls).lower()
    for forbidden in (
        "compose",
        "prune",
        "down",
        "volume",
        "/data/result",
        "rm -rf",
    ):
        assert forbidden not in command_text


def test_remote_lock_loss_blocks_before_any_docker_command() -> None:
    settings = _settings(Path("/tmp/fault-adapter-remote-lock-test"))
    runner = _RecordingCommandRunner(_runtime(settings).identities, remote_lock_held=False)
    case = _all_cases()[0]
    scenario = production_fault_adapter._scenario_for_case(case, settings)
    runtime = production_fault_adapter.SshFaultRuntime(
        runner,
        campaign_id=_plan(case).campaign_id,
        case_id=case.case_id,
        semantic_probe_path=settings.semantic_probe_path,
        semantic_probe_release_root=settings.semantic_probe_release_root,
        semantic_probe_evidence_root=settings.semantic_probe_evidence_root,
        remote_lock_holder_pid=settings.delegated_lock_holder_pid,
        remote_lock_path=str(settings.delegated_lock_path),
        witness_media=_witness_media(),
        probe_poll_seconds=0.01,
        lock_probe=lambda: True,
    )

    with pytest.raises(PlanValidationError, match="远端维护锁"):
        runtime.inspect(scenario.targets[0].container_id)
    assert not [call for call in runner.calls if call[0] == "docker"]


def test_platform_restart_publishes_baseline_and_action_window_witnesses() -> None:
    settings = _settings(Path("/tmp/fault-adapter-action-test"))
    identities = _runtime(settings).identities
    runner = _RecordingCommandRunner(identities)
    case = next(item for item in _all_cases() if item.case_id == "RECOVERY-PLATFORM-ONLINE-GATEWAY")
    plan = _plan(case)
    scenario = production_fault_adapter._scenario_for_case(case, settings)
    runtime = production_fault_adapter.SshFaultRuntime(
        runner,
        campaign_id=plan.campaign_id,
        case_id=case.case_id,
        semantic_probe_path=settings.semantic_probe_path,
        semantic_probe_release_root=settings.semantic_probe_release_root,
        semantic_probe_evidence_root=settings.semantic_probe_evidence_root,
        remote_lock_holder_pid=settings.delegated_lock_holder_pid,
        remote_lock_path=str(settings.delegated_lock_path),
        witness_media=_witness_media(),
        probe_poll_seconds=0.01,
        lock_probe=lambda: True,
    )

    runtime.prepare(scenario)
    runtime.restart(scenario.targets[0].container_id, 30)
    assert runtime.verify(scenario, scenario.disruption_checks[0], "disruption")
    assert runtime.verify(scenario, scenario.recovery_checks[0], "recovery")
    assert runtime.verify(scenario, scenario.recovery_checks[1], "recovery")

    docker_actions = [call[1] for call in runner.calls if call[0] == "docker"]
    assert "stop" in docker_actions
    assert "start" in docker_actions
    assert "restart" not in docker_actions
    probe_calls = [
        call
        for call in runner.calls
        if call[0] == settings.semantic_probe_path and "--phase" in call
    ]
    phases = [call[call.index("--phase") + 1] for call in probe_calls]
    assert phases == ["baseline", "action", "disruption", "recovery"]
    stop_index = next(
        index
        for index, call in enumerate(runner.calls)
        if call[:2] == ("docker", "stop")
    )
    action_call = probe_calls[1]
    opened_at = datetime.fromisoformat(
        action_call[action_call.index("--fault-window-opened-at") + 1]
    )
    assert opened_at <= runner.call_times[stop_index]
    disruption = probe_calls[2]
    recovery = probe_calls[3]
    assert "--baseline-ref" in disruption
    assert "--action-ref" in disruption
    assert "--baseline-ref" in recovery
    assert "--action-ref" in recovery


def test_restart_restores_exact_stopped_container_when_remote_lock_is_lost() -> None:
    settings = _settings(Path("/tmp/fault-adapter-compensating-start-test"))
    identities = _runtime(settings).identities
    runner = _RecordingCommandRunner(identities, lose_lock_after_stop=True)
    case = next(
        item
        for item in _all_cases()
        if item.case_id == "RECOVERY-PLATFORM-ONLINE-GATEWAY"
    )
    plan = _plan(case)
    scenario = production_fault_adapter._scenario_for_case(case, settings)
    runtime = production_fault_adapter.SshFaultRuntime(
        runner,
        campaign_id=plan.campaign_id,
        case_id=case.case_id,
        semantic_probe_path=settings.semantic_probe_path,
        semantic_probe_release_root=settings.semantic_probe_release_root,
        semantic_probe_evidence_root=settings.semantic_probe_evidence_root,
        remote_lock_holder_pid=settings.delegated_lock_holder_pid,
        remote_lock_path=str(settings.delegated_lock_path),
        witness_media=_witness_media(),
        probe_poll_seconds=0.01,
        lock_probe=lambda: True,
    )
    target = scenario.targets[0]
    runtime.prepare(scenario)

    with pytest.raises(PlanValidationError, match="远端维护锁"):
        runtime.restart(target.container_id, 30)

    assert runner.identities[target.container_id].running is True
    assert runtime.inspect(target.container_id).running is True
    docker_calls = [call for call in runner.calls if call[0] == "docker"]
    assert docker_calls[-1] == ("docker", "start", target.container_id)


def test_runtime_config_rejects_boolean_interval_and_absolute_evidence_reference() -> None:
    with pytest.raises(ValueError, match="有限数值"):
        production_fault_adapter._finite_number(True, "probe_poll_seconds")
    with pytest.raises(ValueError, match="非法证据引用"):
        production_fault_adapter.SshFaultRuntime._validate_evidence_refs(
            [f"release:/outside.json#sha256:{'d' * 64}"]
        )
