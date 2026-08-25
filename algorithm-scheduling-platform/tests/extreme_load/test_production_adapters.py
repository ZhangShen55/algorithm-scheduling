from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts.extreme_load import media_download, production_adapters
from scripts.extreme_load.catalog import (
    CampaignCatalog,
    CampaignPhase,
    CaseSpec,
    FixtureDescriptor,
    FixtureKind,
    FixtureManifest,
)
from scripts.extreme_load.media_download import CommandResult
from scripts.extreme_load.plan import CampaignPlan, build_campaign_plan
from scripts.extreme_load.production_adapters import (
    RUNTIME_CONFIG_ENV,
    media_download_factory,
    metrics_factory,
)
from scripts.extreme_load.runtime_metrics import RuntimeMetricsAdapter
from scripts.extreme_load.system_probes import SshCommandRunner

_URL_SECRET = "runtime-url-secret"


def _secure_write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def _source_evidence(tmp_path: Path, *, mode: int = 0o600) -> Path:
    path = tmp_path / "source-resources.json"
    path.write_text(
        json.dumps(
            {
                "evidence_id": "source-resource-snapshot-1",
                "collected_at": "2026-08-23T09:00:00+08:00",
                "cpu_percent": 12.5,
                "memory_percent": 34.5,
                "network_transmit_bytes_per_second": 8192.0,
                "open_connections": 7,
            }
        ),
        encoding="utf-8",
    )
    path.chmod(mode)
    return path


def _runtime_config(
    tmp_path: Path,
    *,
    enabled: bool,
    evidence_path: Path,
    target_hostname: str = "192.168.29.11",
    mode: int = 0o600,
) -> Path:
    path = tmp_path / "extreme-load-runtime.toml"
    path.write_text(
        "\n".join(
            (
                "schema_version = 1",
                "",
                "[media_download]",
                f"enabled = {'true' if enabled else 'false'}",
                f"target_hostname = {json.dumps(target_hostname)}",
                'ssh_user = "root"',
                "ssh_port = 22",
                "download_timeout_seconds = 1800",
                "source_resource_evidence_path = " + json.dumps(str(evidence_path)),
                "",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(mode)
    return path


def _metrics_runtime_config(
    tmp_path: Path,
    *,
    enabled: bool,
    mode: int = 0o600,
) -> Path:
    path = tmp_path / "extreme-load-metrics-runtime.toml"
    path.write_text(
        "\n".join(
            (
                "schema_version = 1",
                "",
                "[runtime_metrics]",
                f"enabled = {'true' if enabled else 'false'}",
                'target_hostname = "192.168.29.11"',
                'ssh_user = "root"',
                "ssh_port = 22",
                'filesystem_paths = ["/", "/data/course", "/data/result"]',
                'compose_projects = ["algorithm-platform", "algorithm-operators"]',
                'kafka_compose_project = "algorithm-platform"',
                'kafka_compose_service = "kafka"',
                "kafka_consumer_groups = "
                '["algorithm-orchestrator", "algorithm-orchestrator-visual-events", '
                '"vision-orchestrator"]',
                "kafka_probe_timeout_seconds = 20.0",
                "kafka_probe_attempts = 2",
                "kafka_probe_retry_delay_seconds = 0.25",
                "regular_seconds = 5.0",
                "burst_seconds = 0.5",
                "probe_timeout_seconds = 5.0",
                "probe_attempts = 2",
                "probe_retry_delay_seconds = 0.25",
                "restart_loop_threshold = 3",
                "restart_loop_window_seconds = 60.0",
                'database_services = ["postgres", "kafka", "redis", "mongodb"]',
                'critical_container_services = ["control-service", "online-gateway-service"]',
                "",
                "[runtime_metrics.expected_gpu_by_pid]",
                '"42" = "GPU-expected"',
                "",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(mode)
    return path


def _fixture(fixture_id: str) -> FixtureDescriptor:
    filename = {
        "long-teacher": "T.mp4",
        "long-student": "S.mp4",
        "long-slides": "P.mp4",
    }[fixture_id]
    return FixtureDescriptor(
        fixture_id=fixture_id,
        kind=FixtureKind.LONG_COURSE,
        path=(
            f"http://media-user:{_URL_SECRET}@192.168.29.12:5555/"
            f"course/{filename}?token=fixture-token"
        ),
        size_bytes=4096,
        duration_seconds=2700.0,
        sha256="a" * 64,
    )


def _case(
    *,
    kind: str = "media_download",
    concurrency: int = 3,
    fixture_ids: tuple[str, ...] = ("long-teacher", "long-student", "long-slides"),
) -> CaseSpec:
    return CaseSpec(
        case_id="BASE-MEDIA-DOWNLOAD-3",
        phase=CampaignPhase.BASELINE,
        load={"kind": kind, "concurrency": concurrency},
        fixture_ids=fixture_ids,
        expected="execution",
        timeout_seconds=3600.0,
        guardrails=("evidence",),
        cleanup=("drain",),
        evidence_path="campaign/phase-0-baseline/media-download-3.json",
    )


def _plan(
    case: CaseSpec | None = None,
    *,
    control_hostname: str = "192.168.29.11",
    fixtures: tuple[FixtureDescriptor, ...] | None = None,
) -> CampaignPlan:
    selected_case = case or _case()
    manifest = FixtureManifest(
        schema_version=1,
        fixtures=fixtures
        or tuple(
            _fixture(fixture_id)
            for fixture_id in (
                "long-teacher",
                "long-student",
                "long-slides",
            )
        ),
    )
    return build_campaign_plan(
        release_tag="release-1",
        git_sha="b" * 40,
        seed=11,
        control_origin=f"http://{control_hostname}:18100",
        gateway_origin=f"http://{control_hostname}:18103",
        fixture_manifest=manifest,
        catalog=CampaignCatalog(schema_version=1, cases=(selected_case,)),
    )


class RecordingRunner:
    def __init__(self, *, raised: Exception | None = None) -> None:
        self.raised = raised
        self.calls: list[tuple[tuple[str, ...], bytes, float]] = []

    async def run(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes,
        timeout_seconds: float,
    ) -> CommandResult:
        self.calls.append((tuple(argv), stdin, timeout_seconds))
        if self.raised is not None:
            raise self.raised
        payload = json.loads(stdin)
        concurrency = int(payload["concurrency"])
        fixture_ids = [str(item["fixture_id"]) for item in payload["fixtures"]]
        document = {
            "schema_version": 1,
            "concurrency": concurrency,
            "wall_elapsed_seconds": 2.0,
            "target_network_receive_bytes": 16384,
            "aggregate_bytes_per_second": concurrency * 2048.0,
            "samples": [
                {
                    "request_index": index,
                    "fixture_id": fixture_ids[index % len(fixture_ids)],
                    "succeeded": True,
                    "size_bytes": 4096,
                    "connect_seconds": 0.05,
                    "elapsed_seconds": 1.0,
                    "error_type": None,
                }
                for index in range(concurrency)
            ],
        }
        return CommandResult(0, json.dumps(document).encode(), b"")


def _install_runner(monkeypatch: pytest.MonkeyPatch, runner: RecordingRunner) -> None:
    monkeypatch.setattr(media_download, "AsyncSubprocessRunner", lambda: runner)


@pytest.mark.asyncio
async def test_missing_runtime_config_blocks_without_constructing_ssh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(RUNTIME_CONFIG_ENV, raising=False)
    runner = RecordingRunner()
    _install_runner(monkeypatch, runner)

    adapter = media_download_factory(_plan(), tmp_path / "release")
    outcome = await adapter.execute(_case())

    assert outcome.status == "blocked"
    assert RUNTIME_CONFIG_ENV in outcome.reason
    assert outcome.evidence == {"configuration_state": "config_missing"}
    assert runner.calls == []


@pytest.mark.asyncio
async def test_metrics_factory_missing_or_disabled_config_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(RUNTIME_CONFIG_ENV, raising=False)
    missing = metrics_factory(_plan(), tmp_path / "release")
    missing_assessment = await missing.assess(_case(), "before")
    assert missing_assessment.level.value == "STOP"
    assert RUNTIME_CONFIG_ENV in missing_assessment.reasons[0]

    config = _metrics_runtime_config(tmp_path, enabled=False)
    monkeypatch.setenv(RUNTIME_CONFIG_ENV, str(config))
    disabled = metrics_factory(_plan(), tmp_path / "release")
    disabled_outcome = await disabled.execute(_case())
    assert disabled_outcome.status == "blocked"
    assert disabled_outcome.evidence == {"configuration_state": "disabled"}


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout_seconds", (14.99, 30.01))
async def test_metrics_factory_rejects_kafka_timeout_outside_independent_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timeout_seconds: float,
) -> None:
    config = _metrics_runtime_config(tmp_path, enabled=True)
    content = config.read_text(encoding="utf-8").replace(
        "kafka_probe_timeout_seconds = 20.0",
        f"kafka_probe_timeout_seconds = {timeout_seconds}",
    )
    config.write_text(content, encoding="utf-8")
    config.chmod(0o600)
    monkeypatch.setenv(RUNTIME_CONFIG_ENV, str(config))

    adapter = metrics_factory(_plan(), tmp_path / "release")
    assessment = await adapter.assess(_case(), "before")

    assert assessment.level.value == "STOP"
    assert "运行时指标配置" in assessment.reasons[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("original", "replacement"),
    (
        ("probe_attempts = 2", "probe_attempts = 0"),
        ("probe_attempts = 2", "probe_attempts = 3"),
        ("probe_retry_delay_seconds = 0.25", "probe_retry_delay_seconds = -0.1"),
        ("probe_retry_delay_seconds = 0.25", "probe_retry_delay_seconds = 5.1"),
    ),
)
async def test_metrics_factory_rejects_probe_retry_values_outside_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original: str,
    replacement: str,
) -> None:
    config = _metrics_runtime_config(tmp_path, enabled=True)
    config.write_text(
        config.read_text(encoding="utf-8").replace(original, replacement),
        encoding="utf-8",
    )
    config.chmod(0o600)
    monkeypatch.setenv(RUNTIME_CONFIG_ENV, str(config))

    adapter = metrics_factory(_plan(), tmp_path / "release")
    assessment = await adapter.assess(_case(), "before")

    assert assessment.level.value == "STOP"
    assert "运行时指标配置" in assessment.reasons[0]


def test_metrics_factory_assembles_explicit_read_only_probe_surfaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _metrics_runtime_config(tmp_path, enabled=True)
    monkeypatch.setenv(RUNTIME_CONFIG_ENV, str(config))

    adapter = metrics_factory(_plan(), tmp_path / "release")

    assert isinstance(adapter, RuntimeMetricsAdapter)
    assert adapter.schedule.regular_seconds == 5.0
    assert adapter.schedule.burst_seconds == 0.5
    assert adapter.probe_attempts == 2
    assert adapter.probe_retry_delay_seconds == 0.25
    assert adapter.database_services == ("postgres", "kafka", "redis", "mongodb")
    assert adapter.expected_gpu_by_pid == {42: "GPU-expected"}
    remote_runner = adapter.target_host_probe.runner
    assert isinstance(remote_runner, SshCommandRunner)
    assert remote_runner.enabled is True
    assert remote_runner.target.host == "192.168.29.11"
    assert adapter.target_host_probe.directory_paths == ("/data/course", "/data/result")
    assert adapter.control_probe.include_kafka_lag is False
    assert adapter.control_probe.kafka_lag_source is None
    kafka_probe = adapter.kafka_lag_probe
    assert isinstance(kafka_probe, production_adapters.KafkaLagProbe)
    assert kafka_probe.consumer_groups == (
        "algorithm-orchestrator",
        "algorithm-orchestrator-visual-events",
        "vision-orchestrator",
    )
    assert kafka_probe.attempts == 2
    assert kafka_probe.retry_delay_seconds == 0.25
    assert kafka_probe.timeout_seconds == 20.0


@pytest.mark.asyncio
async def test_metrics_factory_requires_external_owner_0600_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _metrics_runtime_config(tmp_path, enabled=True, mode=0o644)
    monkeypatch.setenv(RUNTIME_CONFIG_ENV, str(config))

    adapter = metrics_factory(_plan(), tmp_path / "release")
    assessment = await adapter.assess(_case(), "before")

    assert assessment.level.value == "STOP"
    assert "0600 TOML" in assessment.reasons[0]


@pytest.mark.asyncio
async def test_disabled_runtime_config_does_not_require_or_read_source_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_evidence = tmp_path / "does-not-exist.json"
    config = _runtime_config(tmp_path, enabled=False, evidence_path=missing_evidence)
    monkeypatch.setenv(RUNTIME_CONFIG_ENV, str(config))
    runner = RecordingRunner()
    _install_runner(monkeypatch, runner)

    outcome = await media_download_factory(_plan(), tmp_path / "release").execute(_case())

    assert outcome.status == "blocked"
    assert "enabled=true" in outcome.reason
    assert outcome.evidence == {"configuration_state": "disabled"}
    assert runner.calls == []


@pytest.mark.asyncio
async def test_missing_or_insecure_source_evidence_blocks_without_ssh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _source_evidence(tmp_path, mode=0o644)
    config = _runtime_config(tmp_path, enabled=True, evidence_path=evidence)
    monkeypatch.setenv(RUNTIME_CONFIG_ENV, str(config))
    runner = RecordingRunner()
    _install_runner(monkeypatch, runner)

    outcome = await media_download_factory(_plan(), tmp_path / "release").execute(_case())

    assert outcome.status == "blocked"
    assert "源端资源证据" in outcome.reason
    assert outcome.evidence == {"configuration_state": "source_evidence_unavailable"}
    assert runner.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe_kind", ("mode", "symlink"))
async def test_runtime_config_must_be_external_owner_0600_regular_toml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_kind: str,
) -> None:
    evidence = _source_evidence(tmp_path)
    config = _runtime_config(tmp_path, enabled=True, evidence_path=evidence)
    if unsafe_kind == "mode":
        config.chmod(0o644)
        selected = config
    else:
        selected = tmp_path / "runtime-link.toml"
        selected.symlink_to(config)
    monkeypatch.setenv(RUNTIME_CONFIG_ENV, str(selected))
    runner = RecordingRunner()
    _install_runner(monkeypatch, runner)

    outcome = await media_download_factory(_plan(), tmp_path / "release").execute(_case())

    assert outcome.status == "blocked"
    assert "0600 TOML" in outcome.reason
    assert outcome.evidence == {"configuration_state": "config_invalid"}
    assert runner.calls == []


@pytest.mark.asyncio
async def test_runtime_config_inside_workspace_is_blocked_without_ssh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    evidence = _source_evidence(tmp_path)
    config = _runtime_config(workspace, enabled=True, evidence_path=evidence)
    monkeypatch.setattr(production_adapters, "_WORKSPACE_ROOT", workspace.resolve())
    monkeypatch.setenv(RUNTIME_CONFIG_ENV, str(config))
    runner = RecordingRunner()
    _install_runner(monkeypatch, runner)

    outcome = await media_download_factory(_plan(), tmp_path / "release").execute(_case())

    assert outcome.status == "blocked"
    assert "0600 TOML" in outcome.reason
    assert outcome.evidence == {"configuration_state": "config_invalid"}
    assert runner.calls == []


@pytest.mark.asyncio
async def test_valid_config_executes_media_stage_without_url_in_argv_or_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _source_evidence(tmp_path)
    config = _runtime_config(tmp_path, enabled=True, evidence_path=evidence)
    monkeypatch.setenv(RUNTIME_CONFIG_ENV, str(config))
    runner = RecordingRunner()
    _install_runner(monkeypatch, runner)

    outcome = await media_download_factory(_plan(), tmp_path / "release").execute(_case())

    assert outcome.status == "passed"
    assert len(runner.calls) == 1
    argv, stdin, timeout_seconds = runner.calls[0]
    argv_text = " ".join(argv)
    assert timeout_seconds == 1830
    assert _URL_SECRET not in argv_text
    assert "fixture-token" not in argv_text
    assert "192.168.29.12:5555" not in argv_text
    assert _URL_SECRET in stdin.decode()
    public_text = outcome.reason + json.dumps(outcome.evidence, ensure_ascii=False)
    assert _URL_SECRET not in public_text
    assert "fixture-token" not in public_text
    assert "192.168.29.12:5555" not in public_text
    assert outcome.evidence["attempt_count"] == 3
    assert outcome.evidence["source_resources"] == {
        "evidence_id": "source-resource-snapshot-1",
        "collected_at": "2026-08-23T09:00:00+08:00",
        "cpu_percent": 12.5,
        "memory_percent": 34.5,
        "network_transmit_bytes_per_second": 8192.0,
        "open_connections": 7,
    }
    assert outcome.evidence["target_hostname"] == "192.168.29.11"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "reason_fragment"),
    (
        (_case(kind="mixed"), "media_download"),
        (_case(concurrency=2), "1/3/10/30"),
        (_case(fixture_ids=("long-teacher",)), "T/S/P"),
    ),
)
async def test_invalid_stage_case_fails_before_ssh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: CaseSpec,
    reason_fragment: str,
) -> None:
    evidence = _source_evidence(tmp_path)
    config = _runtime_config(tmp_path, enabled=True, evidence_path=evidence)
    monkeypatch.setenv(RUNTIME_CONFIG_ENV, str(config))
    runner = RecordingRunner()
    _install_runner(monkeypatch, runner)
    plan = _plan(case)

    outcome = await media_download_factory(plan, tmp_path / "release").execute(case)

    assert outcome.status == "failed"
    assert reason_fragment in outcome.reason
    assert runner.calls == []


@pytest.mark.asyncio
async def test_control_hostname_mismatch_blocks_before_ssh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _source_evidence(tmp_path)
    config = _runtime_config(tmp_path, enabled=True, evidence_path=evidence)
    monkeypatch.setenv(RUNTIME_CONFIG_ENV, str(config))
    runner = RecordingRunner()
    _install_runner(monkeypatch, runner)

    outcome = await media_download_factory(
        _plan(control_hostname="192.168.29.99"),
        tmp_path / "release",
    ).execute(_case())

    assert outcome.status == "blocked"
    assert "Control hostname" in outcome.reason
    assert runner.calls == []


@pytest.mark.asyncio
async def test_adapter_exception_is_sanitized_even_when_message_contains_fixture_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _source_evidence(tmp_path)
    config = _runtime_config(tmp_path, enabled=True, evidence_path=evidence)
    monkeypatch.setenv(RUNTIME_CONFIG_ENV, str(config))
    secret_url = _fixture("long-teacher").path
    runner = RecordingRunner(raised=RuntimeError(secret_url))
    _install_runner(monkeypatch, runner)

    outcome = await media_download_factory(_plan(), tmp_path / "release").execute(_case())

    assert outcome.status == "failed"
    public_text = outcome.reason + json.dumps(outcome.evidence, ensure_ascii=False)
    assert secret_url not in public_text
    assert _URL_SECRET not in public_text
    assert outcome.evidence == {"error_type": "RuntimeError"}


def test_runtime_template_defaults_to_disabled_and_does_not_use_dotenv() -> None:
    project_root = Path(__file__).resolve().parents[2]
    template = project_root / "deploy/templates/extreme-load-runtime.example.toml"
    content = template.read_text(encoding="utf-8")

    assert content.count("enabled = false") == 4
    assert 'target_hostname = "192.168.29.11"' in content
    assert 'ssh_user = "root"' in content
    assert "ssh_port = 22" in content
    assert RUNTIME_CONFIG_ENV in content
    assert "不使用 .env" in content
    assert "[runtime_metrics]" in content
    assert 'filesystem_paths = ["/", "/data/course", "/data/result"]' in content
    assert "regular_seconds = 5.0" in content
    assert "burst_seconds = 0.5" in content
    assert 'kafka_compose_service = "kafka"' in content
    assert '"algorithm-orchestrator-visual-events"' in content
    assert "kafka_probe_timeout_seconds = 20.0" in content
    assert "[runtime_metrics.expected_gpu_by_pid]" in content
    assert "metrics=scripts.extreme_load.production_adapters:metrics_factory" in content
    assert "fault=scripts.extreme_load.production_fault_adapter:fault_factory" in content
    assert "[fault]" in content
    assert "[fault.operator_container_ids]" in content
    assert "[fault.platform_container_ids]" in content
