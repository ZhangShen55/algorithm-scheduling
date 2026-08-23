from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from scripts.extreme_load.catalog import (
    CampaignCatalog,
    CampaignPhase,
    CaseSpec,
    FixtureDescriptor,
    FixtureKind,
    FixtureManifest,
)
from scripts.extreme_load.execution import CampaignCaseExecutor
from scripts.extreme_load.media_download import (
    CommandResult,
    DownloadSample,
    MediaDownloadResult,
    RemoteDownloadDocument,
    SourceResourceEvidence,
    SshMediaDownloadAdapter,
)
from scripts.extreme_load.plan import CampaignPlan, build_campaign_plan, read_case_evidence
from scripts.extreme_load.report import validate_public_payload

_MEDIA_SECRET = "download-secret"
_SOURCE_EVIDENCE = SourceResourceEvidence(
    evidence_id="source-resource-snapshot-1",
    collected_at="2026-08-23T09:00:00+08:00",
    cpu_percent=12.5,
    memory_percent=34.5,
    network_transmit_bytes_per_second=8_192.0,
    open_connections=7,
)


def _fixture(fixture_id: str = "long-teacher") -> FixtureDescriptor:
    return FixtureDescriptor(
        fixture_id=fixture_id,
        kind=FixtureKind.LONG_COURSE,
        path=(
            f"http://media-user:{_MEDIA_SECRET}@192.168.29.12:5555/"
            f"course/{fixture_id}.mp4?token=fixture-token"
        ),
        size_bytes=4_096,
        duration_seconds=2_700.0,
        sha256="a" * 64,
    )


def _remote_document(
    concurrency: int,
    fixture_ids: Sequence[str],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "concurrency": concurrency,
        "wall_elapsed_seconds": 2.0,
        "target_network_receive_bytes": 16_384,
        "aggregate_bytes_per_second": concurrency * 2_048.0,
        "samples": [
            {
                "request_index": index,
                "fixture_id": fixture_ids[index % len(fixture_ids)],
                "succeeded": True,
                "size_bytes": 4_096,
                "connect_seconds": 0.05 + index / 1_000,
                "elapsed_seconds": 1.0 + index / 100,
                "error_type": None,
            }
            for index in range(concurrency)
        ],
    }


class RecordingRunner:
    def __init__(self, *, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[tuple[tuple[str, ...], bytes, float]] = []

    async def run(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes,
        timeout_seconds: float,
    ) -> CommandResult:
        call = (tuple(argv), stdin, timeout_seconds)
        self.calls.append(call)
        payload: dict[str, Any] = json.loads(stdin)
        fixture_ids = [str(item["fixture_id"]) for item in payload["fixtures"]]
        stdout = json.dumps(
            _remote_document(int(payload["concurrency"]), fixture_ids)
        ).encode()
        return CommandResult(self.returncode, stdout, self.stderr)


class StaticRunner:
    def __init__(self, result: CommandResult) -> None:
        self.result = result
        self.calls = 0

    async def run(
        self,
        _argv: Sequence[str],
        *,
        stdin: bytes,
        timeout_seconds: float,
    ) -> CommandResult:
        assert stdin
        assert timeout_seconds > 0
        self.calls += 1
        return self.result


@pytest.mark.asyncio
async def test_missing_source_evidence_blocks_without_running_command() -> None:
    runner = RecordingRunner()
    adapter = SshMediaDownloadAdapter(
        target_hostname="192.168.29.11",
        ssh_user="root",
        enabled=True,
        command_runner=runner,
    )

    result = await adapter.run((_fixture(),), concurrency=1)

    assert result.status == "blocked"
    assert "源端" in result.reason
    assert runner.calls == []


@pytest.mark.asyncio
async def test_remote_execution_is_disabled_by_default() -> None:
    runner = RecordingRunner()
    adapter = SshMediaDownloadAdapter(
        target_hostname="192.168.29.11",
        ssh_user="root",
        source_evidence=_SOURCE_EVIDENCE,
        command_runner=runner,
    )

    result = await adapter.run((_fixture(),), concurrency=1)

    assert result.status == "blocked"
    assert "默认关闭" in result.reason
    assert runner.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", (1, 3, 10, 30))
async def test_success_records_download_metrics_without_exposing_urls(
    concurrency: int,
) -> None:
    fixtures = (_fixture("long-teacher"), _fixture("long-student"))
    runner = RecordingRunner()
    adapter = SshMediaDownloadAdapter(
        target_hostname="192.168.29.11",
        ssh_user="root",
        enabled=True,
        source_evidence=_SOURCE_EVIDENCE,
        command_runner=runner,
    )

    result = await adapter.run(fixtures, concurrency=concurrency)

    assert result.status == "passed"
    assert result.attempts == concurrency
    assert result.successes == concurrency
    assert len(runner.calls) == 1
    argv, stdin, timeout_seconds = runner.calls[0]
    argv_text = " ".join(argv)
    assert timeout_seconds == 1_830
    assert "StrictHostKeyChecking=yes" in argv
    assert all(fixture.path not in argv_text for fixture in fixtures)
    assert _MEDIA_SECRET not in argv_text
    payload = json.loads(stdin)
    assert payload["concurrency"] == concurrency
    assert [item["url"] for item in payload["fixtures"]] == [
        fixture.path for fixture in fixtures
    ]

    evidence = result.to_evidence()
    validate_public_payload(evidence)
    assert evidence["attempt_count"] == concurrency
    assert evidence["success_count"] == concurrency
    assert evidence["failure_count"] == 0
    assert evidence["failure_rate"] == 0.0
    assert evidence["target_network_receive_bytes"] == 16_384
    assert evidence["aggregate_bytes_per_second"] == concurrency * 2_048.0
    assert evidence["source_resources"] == {
        "evidence_id": "source-resource-snapshot-1",
        "collected_at": "2026-08-23T09:00:00+08:00",
        "cpu_percent": 12.5,
        "memory_percent": 34.5,
        "network_transmit_bytes_per_second": 8_192.0,
        "open_connections": 7,
    }
    files = evidence["files"]
    assert isinstance(files, list)
    assert [item["connect_seconds"] for item in files] == [
        0.05 + index / 1_000 for index in range(concurrency)
    ]
    assert all(item["bytes_per_second"] > 0 for item in files)
    public_text = json.dumps(evidence, ensure_ascii=False)
    assert all(fixture.path not in public_text for fixture in fixtures)
    assert _MEDIA_SECRET not in public_text
    assert "fixture-token" not in public_text


@pytest.mark.asyncio
async def test_command_failure_does_not_leak_stderr_credentials() -> None:
    runner = RecordingRunner(
        returncode=23,
        stderr=b"password=stderr-secret url=http://media/private.mp4",
    )
    adapter = SshMediaDownloadAdapter(
        target_hostname="192.168.29.11",
        ssh_user="root",
        enabled=True,
        source_evidence=_SOURCE_EVIDENCE,
        command_runner=runner,
    )

    result = await adapter.run((_fixture(),), concurrency=1)

    assert result.status == "failed"
    public_text = json.dumps(result.to_evidence(), ensure_ascii=False) + result.reason
    assert "stderr-secret" not in public_text
    assert "private.mp4" not in public_text


@pytest.mark.asyncio
async def test_invalid_remote_document_fails_closed() -> None:
    runner = StaticRunner(CommandResult(0, b'{"schema_version": 1}', b""))
    adapter = SshMediaDownloadAdapter(
        target_hostname="192.168.29.11",
        ssh_user="root",
        enabled=True,
        source_evidence=_SOURCE_EVIDENCE,
        command_runner=runner,
    )

    result = await adapter.run((_fixture(),), concurrency=1)

    assert result.status == "failed"
    assert "证据无效" in result.reason
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_mismatched_remote_concurrency_fails_closed() -> None:
    stdout = json.dumps(_remote_document(3, ("long-teacher",))).encode()
    runner = StaticRunner(CommandResult(0, stdout, b""))
    adapter = SshMediaDownloadAdapter(
        target_hostname="192.168.29.11",
        ssh_user="root",
        enabled=True,
        source_evidence=_SOURCE_EVIDENCE,
        command_runner=runner,
    )

    result = await adapter.run((_fixture(),), concurrency=1)

    assert result.status == "failed"
    assert "并发档位" in result.reason
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_mismatched_remote_fixture_binding_fails_closed() -> None:
    document = _remote_document(1, ("unrequested-fixture",))
    runner = StaticRunner(CommandResult(0, json.dumps(document).encode(), b""))
    adapter = SshMediaDownloadAdapter(
        target_hostname="192.168.29.11",
        ssh_user="root",
        enabled=True,
        source_evidence=_SOURCE_EVIDENCE,
        command_runner=runner,
    )

    result = await adapter.run((_fixture(),), concurrency=1)

    assert result.status == "failed"
    assert "fixture 绑定" in result.reason
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_invalid_requested_concurrency_does_not_run_command() -> None:
    runner = RecordingRunner()
    adapter = SshMediaDownloadAdapter(
        target_hostname="192.168.29.11",
        ssh_user="root",
        enabled=True,
        source_evidence=_SOURCE_EVIDENCE,
        command_runner=runner,
    )

    with pytest.raises(ValueError, match="1/3/10/30"):
        await adapter.run((_fixture(),), concurrency=2)
    assert runner.calls == []


def _media_case(concurrency: int = 3) -> CaseSpec:
    return CaseSpec(
        case_id=f"BASE-MEDIA-DOWNLOAD-{concurrency}",
        phase=CampaignPhase.BASELINE,
        load={"kind": "media_download", "concurrency": concurrency},
        fixture_ids=("long-teacher", "long-student", "long-slides"),
        expected="execution",
        timeout_seconds=30,
        guardrails=("evidence",),
        cleanup=("drain",),
        evidence_path=f"campaign/phase-0-baseline/media-download-{concurrency}.json",
    )


def _plan(case: CaseSpec) -> CampaignPlan:
    manifest = FixtureManifest(
        schema_version=1,
        fixtures=(
            _fixture("long-teacher"),
            _fixture("long-student"),
            _fixture("long-slides"),
        ),
    )
    return build_campaign_plan(
        release_tag="release-1",
        git_sha="b" * 40,
        seed=11,
        control_origin="http://192.168.29.11:18100",
        gateway_origin="http://192.168.29.11:18103",
        fixture_manifest=manifest,
        catalog=CampaignCatalog(schema_version=1, cases=(case,)),
    )


class FakeMediaDownloadAdapter:
    def __init__(self, target_hostname: str) -> None:
        self.target_hostname = target_hostname
        self.calls: list[tuple[tuple[str, ...], int]] = []

    async def run(
        self,
        fixtures: Sequence[FixtureDescriptor],
        *,
        concurrency: int,
    ) -> MediaDownloadResult:
        fixture_ids = tuple(fixture.fixture_id for fixture in fixtures)
        self.calls.append((fixture_ids, concurrency))
        samples = tuple(
            DownloadSample(
                request_index=index,
                fixture_id=fixture_ids[index % len(fixture_ids)],
                succeeded=True,
                size_bytes=4_096,
                connect_seconds=0.1,
                elapsed_seconds=1.0 + index / 10,
                error_type=None,
            )
            for index in range(concurrency)
        )
        document = RemoteDownloadDocument(
            schema_version=1,
            concurrency=concurrency,
            wall_elapsed_seconds=2.0,
            target_network_receive_bytes=16_384,
            aggregate_bytes_per_second=concurrency * 2_048.0,
            samples=samples,
        )
        return MediaDownloadResult(
            "passed",
            "媒体下载基线完成",
            concurrency,
            _SOURCE_EVIDENCE,
            document,
        )


@pytest.mark.asyncio
async def test_executor_without_adapter_publishes_blocked_evidence(tmp_path: Path) -> None:
    case = _media_case()
    plan = _plan(case)
    release_root = tmp_path / "release"

    path = await CampaignCaseExecutor(plan, release_root).execute(case.case_id)

    evidence = read_case_evidence(release_root, plan, case)
    assert path.is_file()
    assert evidence["status"] == "blocked"
    assert "显式远程适配器" in evidence["reason"]
    assert evidence["request_count"] == 0
    assert json.dumps(evidence).find(_MEDIA_SECRET) == -1


@pytest.mark.asyncio
async def test_executor_calls_injected_adapter_and_publishes_metrics(tmp_path: Path) -> None:
    case = _media_case()
    plan = _plan(case)
    release_root = tmp_path / "release"
    adapter = FakeMediaDownloadAdapter("192.168.29.11")
    executor = CampaignCaseExecutor(
        plan,
        release_root,
        media_download_adapter=adapter,
    )

    await executor.execute(case.case_id)

    evidence = read_case_evidence(release_root, plan, case)
    assert adapter.calls == [
        (("long-teacher", "long-student", "long-slides"), 3)
    ]
    assert evidence["status"] == "passed"
    assert evidence["request_count"] == 3
    assert evidence["categories"] == {"failure": 0, "success": 3}
    assert evidence["latency_seconds"] == [1.0, 1.1, 1.2]
    assert evidence["extra"]["target_network_receive_bytes"] == 16_384
    assert evidence["extra"]["source_resources"]["cpu_percent"] == 12.5
    public_text = json.dumps(evidence, ensure_ascii=False)
    assert _MEDIA_SECRET not in public_text
    assert "fixture-token" not in public_text


@pytest.mark.asyncio
async def test_executor_target_mismatch_blocks_without_invoking_adapter(tmp_path: Path) -> None:
    case = _media_case()
    plan = _plan(case)
    release_root = tmp_path / "release"
    adapter = FakeMediaDownloadAdapter("192.168.29.99")
    executor = CampaignCaseExecutor(
        plan,
        release_root,
        media_download_adapter=adapter,
    )

    await executor.execute(case.case_id)

    evidence = read_case_evidence(release_root, plan, case)
    assert evidence["status"] == "blocked"
    assert "目标主机" in evidence["reason"]
    assert adapter.calls == []
