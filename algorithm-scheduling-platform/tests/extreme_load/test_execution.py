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
from scripts.extreme_load.control_query import (
    ControlReadinessEvidence,
    CourseQueryObservation,
    PriorityCheckpointAssessment,
    QueryNodeObservation,
)
from scripts.extreme_load.core import HttpRequestSpec, LoadResult, ResultCategory
from scripts.extreme_load.execution import (
    CampaignCaseExecutor,
    CaseRunOutcome,
    PriorityCheckpointResult,
    _http_outcome,
)
from scripts.extreme_load.online_images import ScheduledImageRequest
from scripts.extreme_load.plan import CampaignPlan, build_campaign_plan, read_case_evidence
from scripts.extreme_load.realtime_asr import AsrSessionResult
from scripts.extreme_load.report import validate_public_payload


def _case(kind: str, **load: object) -> CaseSpec:
    case_id = str(load.pop("case_id", f"TEST-{kind.replace('_', '-').upper()}"))
    fixture_ids = (
        ("realtime-audio",)
        if kind in {"realtime_asr", "realtime_asr_reconnect"}
        else (("person-photo",) if kind.startswith("face_") else ("external-fixture-manifest",))
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
            FixtureDescriptor(
                fixture_id="person-photo",
                kind=FixtureKind.PERSON_PHOTO,
                path=str(tmp_path / "person.jpg"),
                size_bytes=1,
                duration_seconds=None,
                sha256="c" * 64,
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


def _result(
    request_id: str,
    category: ResultCategory,
    evidence: dict[str, object] | None = None,
) -> LoadResult:
    return LoadResult(request_id, category, 0.01, 200, 0, evidence or {})


def _course_observation(
    task_id: str,
    status: int,
    *,
    priority: str = "NORMAL",
    claimed_at: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> CourseQueryObservation:
    return CourseQueryObservation(
        task_id=task_id,
        task_statuses=(("PPT", status),),
        nodes=(
            QueryNodeObservation(
                task_id=task_id,
                task_type="PPT",
                task_status=status,
                node_code="PPT_SLICE",
                status=status,
                priority=priority,
                claimed_at=claimed_at,
                started_at=started_at,
                finished_at=finished_at,
                updated_at="2026-08-23T00:00:20Z",
            ),
        ),
    )


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
async def test_priority_submits_normal_before_urgent_and_proves_claim_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case("priority")
    executor = CampaignCaseExecutor(_plan(tmp_path, case), tmp_path / "release")
    normal = tuple(
        HttpRequestSpec(
            request_id=f"normal-{index}",
            method="POST",
            url="http://192.168.29.11:18100/api/course-jobs",
            json_body={"task_id": f"normal-task-{index}", "priority": "NORMAL"},
        )
        for index in range(2)
    )
    urgent = (
        HttpRequestSpec(
            request_id="urgent-0",
            method="POST",
            url="http://192.168.29.11:18100/api/course-jobs",
            json_body={"task_id": "urgent-task-0", "priority": "URGENT"},
        ),
    )

    async def fake_requests(_case: CaseSpec) -> Sequence[HttpRequestSpec]:
        return (*normal, *urgent)

    calls: list[tuple[tuple[str, ...], bool]] = []

    async def fake_run(
        _case: CaseSpec,
        selected: Sequence[HttpRequestSpec],
        *,
        max_concurrency: int,
        requests_per_second: float | None = None,
        poll_terminal: bool = True,
    ) -> tuple[CaseRunOutcome, tuple[str, ...]]:
        del max_concurrency, requests_per_second
        calls.append((tuple(item.request_id for item in selected), poll_terminal))
        task_ids = tuple(str(item.json_body["task_id"]) for item in selected if item.json_body)
        return (
            CaseRunOutcome("passed", "ok", len(selected), {"success": len(selected)}, ()),
            task_ids,
        )

    async def fake_poll(
        _client: httpx.AsyncClient,
        _plan: CampaignPlan,
        task_ids: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> dict[str, int]:
        assert timeout_seconds == case.timeout_seconds
        return {"success": len(task_ids)}

    checkpoint_observations = (
        _course_observation(
            "normal-task-0",
            50,
            claimed_at="2026-08-23T00:00:01Z",
            started_at="2026-08-23T00:00:02Z",
        ),
        _course_observation("normal-task-1", 30),
    )

    async def fake_checkpoint(
        _client: httpx.AsyncClient,
        task_ids: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> PriorityCheckpointResult:
        assert tuple(task_ids) == ("normal-task-0", "normal-task-1")
        assert timeout_seconds == case.timeout_seconds
        return PriorityCheckpointResult(
            PriorityCheckpointAssessment("ready", "ready", 2, 1, 1),
            checkpoint_observations,
        )

    async def fake_final_observations(
        _client: httpx.AsyncClient,
        _plan: CampaignPlan,
        task_ids: Sequence[str],
    ) -> tuple[tuple[CourseQueryObservation, ...], tuple[str, ...]]:
        assert tuple(task_ids) == (
            "normal-task-0",
            "normal-task-1",
            "urgent-task-0",
        )
        return (
            (
                _course_observation(
                    "normal-task-0",
                    60,
                    claimed_at="2026-08-23T00:00:01Z",
                    started_at="2026-08-23T00:00:02Z",
                    finished_at="2026-08-23T00:00:10Z",
                ),
                _course_observation(
                    "normal-task-1",
                    60,
                    claimed_at="2026-08-23T00:00:05Z",
                    started_at="2026-08-23T00:00:06Z",
                    finished_at="2026-08-23T00:00:11Z",
                ),
                _course_observation(
                    "urgent-task-0",
                    60,
                    priority="URGENT",
                    claimed_at="2026-08-23T00:00:03Z",
                    started_at="2026-08-23T00:00:04Z",
                    finished_at="2026-08-23T00:00:09Z",
                ),
            ),
            (),
        )

    monkeypatch.setattr(executor, "_offline_requests", fake_requests)
    monkeypatch.setattr(executor, "_run_http", fake_run)
    monkeypatch.setattr(executor, "_wait_priority_normal_checkpoint", fake_checkpoint)
    monkeypatch.setattr("scripts.extreme_load.execution._poll_tasks", fake_poll)
    monkeypatch.setattr(
        "scripts.extreme_load.execution._fetch_course_query_observations",
        fake_final_observations,
    )

    outcome = await executor._run_offline_case(case)

    assert outcome.status == "passed"
    assert calls == [
        (("normal-0", "normal-1"), False),
        (("urgent-0",), False),
    ]
    assert outcome.task_ids == ("normal-task-0", "normal-task-1", "urgent-task-0")
    assert outcome.extra is not None
    assert outcome.extra["submission_order"] == ("NORMAL", "URGENT")
    assert outcome.extra["claim_order_evidence_required"] is False
    claim_order = outcome.extra["claim_order"]
    assert isinstance(claim_order, dict)
    assert claim_order["status"] == "passed"


@pytest.mark.asyncio
async def test_priority_blocks_before_urgent_when_claim_timestamps_are_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case("priority")
    executor = CampaignCaseExecutor(_plan(tmp_path, case), tmp_path / "release")
    normal = HttpRequestSpec(
        request_id="normal",
        method="POST",
        url="http://192.168.29.11:18100/api/course-jobs",
        json_body={"task_id": "normal-task", "priority": "NORMAL"},
    )
    urgent = HttpRequestSpec(
        request_id="urgent",
        method="POST",
        url="http://192.168.29.11:18100/api/course-jobs",
        json_body={"task_id": "urgent-task", "priority": "URGENT"},
    )
    calls: list[tuple[str, ...]] = []

    async def fake_requests(_case: CaseSpec) -> Sequence[HttpRequestSpec]:
        return (normal, urgent)

    async def fake_run(
        _case: CaseSpec,
        selected: Sequence[HttpRequestSpec],
        **_kwargs: Any,
    ) -> tuple[CaseRunOutcome, tuple[str, ...]]:
        calls.append(tuple(item.request_id for item in selected))
        return CaseRunOutcome("passed", "ok", 1, {"success": 1}, ()), ("normal-task",)

    async def fake_checkpoint(
        _client: httpx.AsyncClient,
        _task_ids: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> PriorityCheckpointResult:
        assert timeout_seconds == case.timeout_seconds
        return PriorityCheckpointResult(
            PriorityCheckpointAssessment(
                "blocked",
                "任务查询未提供 claimed_at/started_at，不能证明真实领取顺序",
                1,
                0,
                0,
            ),
            (),
        )

    monkeypatch.setattr(executor, "_offline_requests", fake_requests)
    monkeypatch.setattr(executor, "_run_http", fake_run)
    monkeypatch.setattr(executor, "_wait_priority_normal_checkpoint", fake_checkpoint)

    outcome = await executor._run_offline_case(case)

    assert outcome.status == "blocked"
    assert calls == [("normal",)]
    assert "claimed_at/started_at" in outcome.reason


@pytest.mark.asyncio
async def test_query_proves_readiness_transitions_schedule_and_evidence_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query_case = _case("query", qps=50, interval=2, mode="herd")
    offline_case = CaseSpec(
        case_id="OFFLINE-ASR-SOURCE",
        phase=CampaignPhase.OFFLINE,
        load={"kind": "unique_submission", "combination": "asr_only"},
        fixture_ids=("external-fixture-manifest",),
        expected="accepted",
        timeout_seconds=30,
        guardrails=("evidence",),
        cleanup=("drain",),
        evidence_path="campaign/phase-2-offline/offline-asr-source.json",
    )
    plan = _plan(tmp_path, query_case).model_copy(
        update={
            "catalog": CampaignCatalog(
                schema_version=1,
                cases=(offline_case, query_case),
            )
        }
    )
    executor = CampaignCaseExecutor(plan, tmp_path / "release")
    readiness = ControlReadinessEvidence(
        True,
        200,
        "ready",
        ("postgresql", "redis", "schema"),
        (),
        "Control Service 已就绪",
    )
    readiness_calls = 0

    async def fake_readiness() -> ControlReadinessEvidence:
        nonlocal readiness_calls
        readiness_calls += 1
        return readiness

    def fake_case_evidence(
        _release_root: Path,
        _plan_value: CampaignPlan,
        case: CaseSpec,
    ) -> dict[str, object]:
        assert case is offline_case
        return {"task_ids": ["large-asr-task"]}

    def response(status: int) -> dict[str, object]:
        terminal = status == 60
        return {
            "code": 0,
            "data": {
                "task_id": "large-asr-task",
                "tasks": [
                    {
                        "task_type": "ASR",
                        "status": status,
                        "nodes": [
                            {
                                "node_code": "ASR",
                                "status": status,
                                "priority": "NORMAL",
                                "claimed_at": "2026-08-23T00:00:01Z",
                                "started_at": "2026-08-23T00:00:02Z",
                                "finished_at": (
                                    "2026-08-23T00:00:05Z" if terminal else None
                                ),
                                "updated_at": (
                                    "2026-08-23T00:00:05Z"
                                    if terminal
                                    else "2026-08-23T00:00:03Z"
                                ),
                                "result": {"segments": []},
                            }
                        ],
                    }
                ],
            },
        }

    async def fake_execute(
        _case_value: CaseSpec,
        scheduled: Sequence[Any],
    ) -> tuple[tuple[HttpRequestSpec, ...], list[LoadResult], tuple[float, ...]]:
        assert len(scheduled) == 500
        assert {item.request.work_type for item in scheduled} == {
            "large_asr_result_query"
        }
        selected = (scheduled[0], scheduled[100])
        requests = tuple(item.request for item in selected)
        results = [
            _result(
                requests[0].request_id,
                ResultCategory.SUCCESS,
                {"response": response(50), "response_size_bytes": 128},
            ),
            _result(
                requests[1].request_id,
                ResultCategory.SUCCESS,
                {"response": response(60), "response_size_bytes": 192},
            ),
        ]
        return requests, results, (0.0, 2.0)

    monkeypatch.setattr(executor, "_control_readiness", fake_readiness)
    monkeypatch.setattr(executor, "_execute_scheduled_http", fake_execute)
    monkeypatch.setattr(
        "scripts.extreme_load.execution.execution_path",
        lambda *_args: Path(__file__),
    )
    monkeypatch.setattr(
        "scripts.extreme_load.execution.read_case_evidence",
        fake_case_evidence,
    )

    outcome = await executor._query(query_case)

    assert outcome.status == "passed"
    assert readiness_calls == 2
    assert outcome.extra is not None
    assert outcome.extra["control_readiness"] == {
        "before": readiness.to_evidence(),
        "after": readiness.to_evidence(),
    }
    assert outcome.extra["node_state_transitions"] == {
        "status": "proven",
        "reason": "整数节点状态及时间事实保持合法单调迁移",
        "observed_course_count": 1,
        "observed_node_count": 1,
        "node_sample_count": 2,
        "transition_count": 1,
    }
    assert outcome.extra["mode"] == "herd"
    assert outcome.extra["requested_qps"] == 50
    assert outcome.extra["logical_poller_count"] == 100
    assert outcome.extra["large_asr_task_count"] == 1
    assert outcome.extra["response_size_sample_count"] == 2
    assert outcome.extra["response_size_bytes_total"] == 320
    assert outcome.extra["response_size_bytes_max"] == 192
    assert outcome.extra["scheduled_offsets_seconds"] == (0.0, 2.0)
    assert outcome.extra["postgresql_load_evidence"] == {
        "source": "runtime_metrics_adapter",
        "collected_by_query_executor": False,
        "reason": "PostgreSQL 主机负载由外部运行时指标采集，不由北向查询响应推断",
    }


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

        async def run_sessions(
            self,
            specs: Sequence[Any],
            _fixture: object,
        ) -> tuple[AsrSessionResult, ...]:
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


def _face_response(
    data: object,
    *,
    status_code: int = 200,
    instance_id: str | None = None,
) -> dict[str, object]:
    response: dict[str, object] = {
        "code": 0,
        "data": {"status_code": status_code, "message": "ok", "data": data},
    }
    if instance_id is not None:
        response["instance_id"] = instance_id
    return response


@pytest.mark.asyncio
async def test_face_management_executes_ordered_phases_and_sanitizes_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case("face_management", persons=500)
    executor = CampaignCaseExecutor(_plan(tmp_path, case), tmp_path / "release")
    created: list[str] = []
    phase_calls: list[tuple[str, ...]] = []

    async def fake_fixture(*_args: object, **_kwargs: object) -> bytes:
        return b"face-image"

    async def fake_run(
        _runner: object,
        requests: Sequence[HttpRequestSpec],
        **_kwargs: Any,
    ) -> list[LoadResult]:
        phase_calls.append(tuple(request.work_type for request in requests))
        results: list[LoadResult] = []
        for request in requests:
            body = request.json_body or {}
            if request.work_type == "face_person_create":
                number = str(body["number"])
                created.append(number)
                data: object = {"number": number}
            elif request.work_type == "face_person_batch_create":
                raw_persons = body["persons"]
                assert isinstance(raw_persons, list)
                numbers = [str(person["number"]) for person in raw_persons]
                created.extend(numbers)
                data = {"persons": [{"number": number} for number in numbers]}
            elif request.work_type == "face_person_list":
                assert len(created) == 500
                data = {"persons": [{"number": number} for number in created]}
            elif request.work_type == "face_person_search":
                assert len(created) == 500
                data = {"persons": [{"number": str(body["number"])}]}
            else:
                assert request.work_type == "face_person_delete"
                assert len(phase_calls) == 3
                data = {
                    "deleted_count": 1,
                    "info": [{"number": str(body["number"])}],
                }
            results.append(
                _result(
                    request.request_id,
                    ResultCategory.SUCCESS,
                    {
                        "response": _face_response(
                            data,
                            instance_id="facerec-management-0",
                        )
                    },
                )
            )
        return results

    monkeypatch.setattr("scripts.extreme_load.execution._read_fixture_bytes", fake_fixture)
    monkeypatch.setattr("scripts.extreme_load.execution.AsyncLoadRunner.run", fake_run)

    outcome = await executor._persons(case)

    assert outcome.status == "passed"
    assert len(phase_calls) == 3
    assert set(phase_calls[0]) == {"face_person_create", "face_person_batch_create"}
    assert phase_calls[1] == ("face_person_list", "face_person_search")
    assert phase_calls[2] == ("face_person_delete",)
    assert outcome.extra is not None
    assert outcome.extra["dataset_id"] == "FACE-DATASET-500"
    assert outcome.extra["retained_person_count"] == 499
    assert outcome.extra["instance_consistency"] == {
        "status": "pending_unproven",
        "reason": "管理响应不能替代三个识别实例的共享 MongoDB 观察证据",
    }
    validate_public_payload(outcome.extra)
    assert "ZmFjZS1pbWFnZQ==" not in json.dumps(outcome.extra, ensure_ascii=False)


@pytest.mark.asyncio
async def test_face_recognition_reports_person_fact_consistency_separately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case("face_recognition", persons=500)
    executor = CampaignCaseExecutor(_plan(tmp_path, case), tmp_path / "release")

    async def fake_fixture(*_args: object, **_kwargs: object) -> bytes:
        return b"face-image"

    async def fake_run(
        _runner: object,
        requests: Sequence[HttpRequestSpec],
        **_kwargs: Any,
    ) -> list[LoadResult]:
        results: list[LoadResult] = []
        for index, request in enumerate(requests):
            assert request.json_body is not None
            targets = request.json_body["targets"]
            assert isinstance(targets, list)
            expected = str(targets[0])
            if request.work_type == "online_face_recognize_deleted":
                match: list[dict[str, str]] = []
            else:
                observed = "P-wrong" if index == 0 else expected
                match = [{"number": observed}]
            results.append(
                _result(
                    request.request_id,
                    ResultCategory.SUCCESS,
                    {"response": _face_response({"match": match})},
                )
            )
        return results

    monkeypatch.setattr("scripts.extreme_load.execution._read_fixture_bytes", fake_fixture)
    monkeypatch.setattr("scripts.extreme_load.execution.AsyncLoadRunner.run", fake_run)

    outcome = await executor._persons(case)

    assert outcome.status == "failed"
    assert outcome.extra is not None
    request_summary = outcome.extra["request_summary"]
    assert isinstance(request_summary, dict)
    assert request_summary["request_count"] == 500
    assert "recognition_passes" not in request_summary
    assert request_summary["deleted_target_count"] == 1
    assert request_summary["unique_expected_number_count"] == 499
    assert outcome.extra["recognition_consistency_concurrency"] == 30
    assert outcome.extra["person_fact_consistency"] == {
        "status": "failed",
        "reason": "北向识别响应的人物事实不完整或不唯一",
        "expected_retained_number_count": 499,
        "recognized_retained_number_count": 498,
        "expected_deleted_absence_count": 1,
        "validated_deleted_absence_count": 1,
        "invalid_response_count": 1,
    }
    assert "instance_consistency" not in outcome.extra
    validate_public_payload(outcome.extra)


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
