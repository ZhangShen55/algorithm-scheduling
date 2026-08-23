from __future__ import annotations

import io
import json
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts.extreme_load.catalog import (
    CampaignCatalog,
    CampaignPhase,
    CaseSpec,
    FixtureDescriptor,
    FixtureKind,
    FixtureManifest,
)
from scripts.extreme_load.core import HttpRequestSpec, LoadResult, ResultCategory
from scripts.extreme_load.execution import (
    CampaignCaseExecutor,
    CaseRunOutcome,
    _http_outcome,
)
from scripts.extreme_load.online_images import ScheduledImageRequest
from scripts.extreme_load.plan import CampaignPlan, build_campaign_plan, read_case_evidence
from scripts.extreme_load.realtime_asr import AsrSessionResult


def _case(kind: str, **load: object) -> CaseSpec:
    case_id = str(load.pop("case_id", f"TEST-{kind.replace('_', '-').upper()}"))
    fixture_ids = (
        ("realtime-audio",)
        if kind in {"realtime_asr", "realtime_asr_reconnect"}
        else ("external-fixture-manifest",)
    )
    return CaseSpec(
        case_id=case_id,
        phase=CampaignPhase.ONLINE,
        load={"kind": kind, **load},
        fixture_ids=fixture_ids,
        expected="execution",
        timeout_seconds=30,
        guardrails=("evidence",),
        cleanup=("drain",),
        evidence_path=f"campaign/phase-3-online/{case_id.lower()}.json",
    )


def _plan(tmp_path: Path, case: CaseSpec) -> CampaignPlan:
    manifest = FixtureManifest(
        schema_version=1,
        fixtures=(
            FixtureDescriptor(
                fixture_id="realtime-audio",
                kind=FixtureKind.REALTIME_AUDIO,
                path=str(tmp_path / "audio.wav"),
                size_bytes=1,
                duration_seconds=1,
                sha256="b" * 64,
            ),
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


def _request(
    request_id: str,
    task_id: str | None = None,
    *,
    rejected: bool = False,
) -> HttpRequestSpec:
    return HttpRequestSpec(
        request_id=request_id,
        method="POST",
        url="http://192.168.29.11:18100/api/course-jobs",
        json_body=None if task_id is None else {"task_id": task_id},
        expected_business_rejection=rejected,
    )


def _result(request_id: str, category: ResultCategory) -> LoadResult:
    return LoadResult(request_id, category, 0.01, 200, 0, {})


def test_http_outcome_binds_summary_fields_and_rejects_duplicate_plan_ids() -> None:
    request = _request("request-1")
    outcome = _http_outcome(
        (request,),
        (_result("request-1", ResultCategory.SUCCESS),),
        allow_overload=False,
    )

    assert outcome.status == "passed"
    assert outcome.categories == {"success": 1}
    assert outcome.latency_seconds == (0.01,)
    duplicated = _http_outcome(
        (request, request),
        (
            _result("request-1", ResultCategory.SUCCESS),
            _result("request-1", ResultCategory.SUCCESS),
        ),
        allow_overload=False,
    )
    assert duplicated.status == "failed"
    assert "计划请求 ID 重复" in duplicated.reason


@pytest.mark.asyncio
async def test_negative_mix_polls_only_successfully_accepted_positive_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case("negative_submission", ratio=0.20)
    executor = CampaignCaseExecutor(_plan(tmp_path, case), tmp_path / "release")
    requests = (
        _request("positive-1", "accepted-1"),
        _request("negative-1", "rejected-1", rejected=True),
        _request("positive-2", "accepted-2"),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        code = 40001 if body["task_id"] == "rejected-1" else 0
        return httpx.Response(200, json={"code": code})

    monkeypatch.setattr(
        "scripts.extreme_load.execution.HttpClientPool.build_client",
        lambda _self: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    polled: list[tuple[str, ...]] = []

    async def fake_poll(
        _client: httpx.AsyncClient,
        _plan: CampaignPlan,
        task_ids: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> dict[str, int]:
        assert timeout_seconds == case.timeout_seconds
        polled.append(tuple(task_ids))
        return {"success": len(task_ids)}

    monkeypatch.setattr("scripts.extreme_load.execution._poll_tasks", fake_poll)
    outcome, task_ids = await executor._run_http(case, requests, max_concurrency=3)

    assert outcome.status == "passed"
    assert task_ids == ("accepted-1", "accepted-2")
    assert polled == [("accepted-1", "accepted-2")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "expected_groups", "expected_concurrency"),
    (
        ("append_task_types", [("first", "second", "third")], [1]),
        ("completed_result_reuse", [("first",), ("second",)], [1, 1]),
    ),
)
async def test_append_and_completed_reuse_preserve_required_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    expected_groups: list[tuple[str, ...]],
    expected_concurrency: list[int],
) -> None:
    case = _case(kind)
    executor = CampaignCaseExecutor(_plan(tmp_path, case), tmp_path / "release")
    requests = tuple(_request(name, "shared-task") for name in ("first", "second", "third"))
    if kind == "completed_result_reuse":
        requests = requests[:2]

    async def fake_requests(_case: CaseSpec) -> Sequence[HttpRequestSpec]:
        return requests

    groups: list[tuple[str, ...]] = []
    concurrency: list[int] = []

    async def fake_run(
        _case: CaseSpec,
        selected: Sequence[HttpRequestSpec],
        *,
        max_concurrency: int,
        requests_per_second: float | None = None,
    ) -> tuple[CaseRunOutcome, tuple[str, ...]]:
        assert requests_per_second is None
        groups.append(tuple(item.request_id for item in selected))
        concurrency.append(max_concurrency)
        return (
            CaseRunOutcome("passed", "ok", len(selected), {"success": len(selected)}, ()),
            ("shared-task",),
        )

    monkeypatch.setattr(executor, "_offline_requests", fake_requests)
    monkeypatch.setattr(executor, "_run_http", fake_run)

    assert (await executor._run_offline_case(case)).status == "passed"
    assert groups == expected_groups
    assert concurrency == expected_concurrency


@pytest.mark.asyncio
async def test_s_stream_honors_scheduled_offsets_instead_of_one_burst(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case("s_stream", streams=100, interval=5)
    current = 100.0
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        nonlocal current
        sleeps.append(seconds)
        current += seconds

    executor = CampaignCaseExecutor(
        _plan(tmp_path, case),
        tmp_path / "release",
        sleep=fake_sleep,
        clock=lambda: current,
    )
    calls: list[tuple[float, tuple[str, ...]]] = []

    async def fake_run(
        _runner: object,
        requests: Sequence[HttpRequestSpec],
        **_kwargs: Any,
    ) -> list[LoadResult]:
        calls.append((current, tuple(request.request_id for request in requests)))
        return [_result(request.request_id, ResultCategory.SUCCESS) for request in requests]

    monkeypatch.setattr("scripts.extreme_load.execution.AsyncLoadRunner.run", fake_run)
    scheduled = tuple(
        ScheduledImageRequest(
            stream_id=f"stream-{index}",
            scheduled_offset_seconds=offset,
            request=HttpRequestSpec(
                request_id=f"frame-{index}",
                method="POST",
                url="http://192.168.29.11:18103/api/online/vbas/analyze",
            ),
        )
        for index, offset in enumerate((0.0, 5.0, 10.0))
    )

    outcome = await executor._run_scheduled_http(case, scheduled)

    assert outcome.status == "passed"
    assert sleeps == [5.0, 5.0]
    assert calls == [
        (100.0, ("frame-0",)),
        (105.0, ("frame-1",)),
        (110.0, ("frame-2",)),
    ]


def _wave_bytes() -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as stream:
        stream.setframerate(16_000)
        stream.setsampwidth(2)
        stream.setnchannels(1)
        stream.writeframes(b"a" * 6_400)
    return target.getvalue()


@pytest.mark.asyncio
async def test_asr_success_requires_a_received_final_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case("realtime_asr", sessions=1)
    executor = CampaignCaseExecutor(_plan(tmp_path, case), tmp_path / "release")

    async def fake_fixture(*_args: object, **_kwargs: object) -> bytes:
        return _wave_bytes()

    class FakeRunner:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def run_sessions(self, specs: Sequence[Any], _fixture: object):
            return tuple(
                AsrSessionResult(
                    spec.session_id,
                    spec.trace_id,
                    ResultCategory.SUCCESS,
                    1,
                    (),
                )
                for spec in specs
            )

    monkeypatch.setattr("scripts.extreme_load.execution._read_fixture_bytes", fake_fixture)
    monkeypatch.setattr("scripts.extreme_load.execution.RealtimeAsrRunner", FakeRunner)

    outcome = await executor._realtime_asr(case)

    assert outcome.status == "failed"
    assert outcome.extra == {
        "sent_chunks": 1,
        "message_digest_count": 0,
        "failed_session_count": 0,
        "missing_final_message_count": 1,
    }


@pytest.mark.asyncio
async def test_execute_publishes_atomic_0600_identity_bound_evidence(tmp_path: Path) -> None:
    case = _case("phase_gate")
    plan = _plan(tmp_path, case)
    release_root = tmp_path / "release"
    executor = CampaignCaseExecutor(plan, release_root)

    path = await executor.execute(case.case_id)

    assert path.stat().st_mode & 0o777 == 0o600
    assert read_case_evidence(release_root, plan, case)["status"] == "passed"
    with pytest.raises(FileExistsError):
        await executor.execute(case.case_id)
