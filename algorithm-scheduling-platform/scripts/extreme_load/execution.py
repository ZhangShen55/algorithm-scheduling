from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import time
import urllib.parse
import wave
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .catalog import CaseSpec, FixtureDescriptor
from .control_query import (
    ControlReadinessEvidence,
    CourseQueryObservation,
    ObservedCourseQuery,
    PriorityCheckpointAssessment,
    QueryMode,
    ScheduledQueryRequest,
    assess_priority_normal_checkpoint,
    build_negative_query_mix,
    build_scheduled_query_requests,
    parse_course_query_response,
    query_qps_tiers,
    validate_control_readiness_response,
    validate_course_query_response,
    validate_monotonic_query_observations,
    validate_priority_claim_order,
)
from .core import (
    AsyncLoadRunner,
    HttpClientPool,
    HttpRequestSpec,
    LoadResult,
    ReproducibleIdentity,
    ResultCategory,
)
from .media_download import MediaDownloadAdapter
from .offline import (
    CourseMedia,
    TaskCombination,
    build_append_task_type_sequence,
    build_completed_result_reuse_request,
    build_idempotent_burst,
    build_negative_submission_mix,
    build_priority_sequence,
    build_unique_submission_burst,
)
from .online_images import (
    ImageKind,
    OnlineImageFixture,
    ScheduledImageRequest,
    build_image_request,
    build_invalid_image_request,
    build_mixed_image_requests,
    build_s_stream_requests,
    image_syntax_and_format_cases,
)
from .persons import (
    FaceManagementBoundary,
    build_person_dataset,
    build_person_management_requests,
    build_person_recognition_plan,
    person_dataset_id,
    recognition_expected_number,
    validate_person_management_response,
    validate_person_recognition_response,
)
from .plan import CampaignPlan, execution_path, read_case_evidence
from .realtime_asr import (
    AudioStreamFixture,
    RealtimeAsrRunner,
    build_reconnect_specs,
    build_session_specs,
)
from .report import atomic_write_report, validate_public_payload

JsonObject = dict[str, Any]
SleepCallable = Callable[[float], Awaitable[None]]
ClockCallable = Callable[[], float]
_TERMINAL_STATUSES = {60, 70, 80}
_SUCCESS_STATUS = 60


@dataclass(frozen=True, slots=True)
class CaseRunOutcome:
    status: str
    reason: str
    request_count: int
    categories: Mapping[str, int]
    latency_seconds: tuple[float, ...]
    task_ids: tuple[str, ...] = ()
    terminal_counts: Mapping[str, int] | None = None
    extra: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.status not in {"passed", "failed", "blocked", "not_run"}:
            raise ValueError("用例状态不合法")
        if not self.reason:
            raise ValueError("用例原因不能为空")


def _result_summary(results: Sequence[LoadResult]) -> tuple[dict[str, int], tuple[float, ...]]:
    categories = Counter(result.category.value for result in results)
    return dict(sorted(categories.items())), tuple(result.elapsed_seconds for result in results)


def _http_outcome(
    requests: Sequence[HttpRequestSpec],
    results: Sequence[LoadResult],
    *,
    allow_overload: bool,
) -> CaseRunOutcome:
    categories, latency = _result_summary(results)
    if len(requests) != len(results):
        return CaseRunOutcome(
            status="failed",
            reason="请求结果数量与计划不一致",
            request_count=len(results),
            categories=categories,
            latency_seconds=latency,
        )
    failures: list[str] = []
    request_ids = [request.request_id for request in requests]
    if len(request_ids) != len(set(request_ids)):
        failures.append("计划请求 ID 重复")
    result_by_id = {result.request_id: result for result in results}
    if len(result_by_id) != len(results):
        failures.append("请求结果 ID 重复")
    for request in requests:
        result = result_by_id.get(request.request_id)
        if result is None:
            failures.append(f"缺少请求结果: {request.request_id}")
            continue
        if request.expected_business_rejection:
            if result.category is not ResultCategory.BUSINESS_REJECTED:
                failures.append(f"负向请求没有得到业务拒绝: {request.request_id}")
            continue
        allowed = {ResultCategory.SUCCESS}
        if allow_overload:
            allowed.add(ResultCategory.OVERLOAD)
        if result.category not in allowed:
            failures.append(f"出现非预期请求分类: {request.request_id}={result.category.value}")
    return CaseRunOutcome(
        status="failed" if failures else "passed",
        reason=(
            "；".join(failures[:20])
            if failures
            else "北向请求结果符合当前档位合同"
        ),
        request_count=len(results),
        categories=categories,
        latency_seconds=latency,
    )


def _fixture(plan: CampaignPlan, fixture_id: str) -> FixtureDescriptor:
    fixture = next(
        (item for item in plan.fixture_manifest.fixtures if item.fixture_id == fixture_id),
        None,
    )
    if fixture is None:
        raise ValueError(f"fixture 不存在: {fixture_id}")
    return fixture


async def _read_fixture_bytes(
    fixture: FixtureDescriptor,
    *,
    max_bytes: int,
) -> bytes:
    if fixture.size_bytes > max_bytes:
        raise ValueError(f"fixture 超过当前负载机安全读取上限: {fixture.fixture_id}")
    parsed = urllib.parse.urlsplit(fixture.path)
    if parsed.scheme in {"http", "https"}:
        async with httpx.AsyncClient(timeout=300, follow_redirects=False) as client:
            response = await client.get(fixture.path)
            response.raise_for_status()
            content = response.content
    else:
        content = await asyncio.to_thread(Path(fixture.path).read_bytes)
    if len(content) != fixture.size_bytes:
        raise ValueError(f"fixture 大小与 manifest 不一致: {fixture.fixture_id}")
    if hashlib.sha256(content).hexdigest() != fixture.sha256:
        raise ValueError(f"fixture 摘要与 manifest 不一致: {fixture.fixture_id}")
    return content


def _course_media(plan: CampaignPlan, *, long_course: bool) -> CourseMedia:
    prefix = "long" if long_course else "short"
    return CourseMedia(
        teacher_video_path=_fixture(plan, f"{prefix}-teacher").path,
        student_video_path=_fixture(plan, f"{prefix}-student").path,
        slides_video_path=_fixture(plan, f"{prefix}-slides").path,
    )


def _combination(value: object) -> TaskCombination:
    try:
        return TaskCombination(str(value))
    except ValueError as error:
        raise ValueError(f"未知离线任务组合: {value}") from error


def _load_int(case: CaseSpec, key: str, *, default: int | None = None) -> int:
    value = case.load.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"用例 {case.case_id} 的 {key} 必须是整数")
    return value


def _load_float(case: CaseSpec, key: str) -> float:
    value = case.load.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"用例 {case.case_id} 的 {key} 必须是数值")
    return float(value)


def _load_string(case: CaseSpec, key: str) -> str:
    value = case.load.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"用例 {case.case_id} 的 {key} 必须是非空字符串")
    return value


def _load_string_list(case: CaseSpec, key: str) -> list[str]:
    value = case.load.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ValueError(f"用例 {case.case_id} 的 {key} 必须是非空字符串数组")
    return value


def _accepted_task_ids(
    requests: Sequence[HttpRequestSpec],
    results: Sequence[LoadResult],
) -> tuple[str, ...]:
    result_by_id = {result.request_id: result for result in results}
    accepted: list[str] = []
    for request in requests:
        result = result_by_id.get(request.request_id)
        if (
            result is None
            or result.category is not ResultCategory.SUCCESS
            or request.expected_business_rejection
            or request.json_body is None
        ):
            continue
        task_id = request.json_body.get("task_id")
        if isinstance(task_id, str):
            accepted.append(task_id)
    return tuple(dict.fromkeys(accepted))


def _combine_outcomes(*outcomes: CaseRunOutcome) -> CaseRunOutcome:
    if not outcomes:
        raise ValueError("至少需要一个用例结果")
    categories: Counter[str] = Counter()
    task_ids: list[str] = []
    terminal_counts: Counter[str] = Counter()
    for outcome in outcomes:
        categories.update(outcome.categories)
        task_ids.extend(outcome.task_ids)
        terminal_counts.update(outcome.terminal_counts or {})
    failed = [outcome for outcome in outcomes if outcome.status != "passed"]
    return CaseRunOutcome(
        status="failed" if failed else "passed",
        reason="；".join(outcome.reason for outcome in outcomes),
        request_count=sum(outcome.request_count for outcome in outcomes),
        categories=dict(sorted(categories.items())),
        latency_seconds=tuple(
            latency for outcome in outcomes for latency in outcome.latency_seconds
        ),
        task_ids=tuple(dict.fromkeys(task_ids)),
        terminal_counts=(dict(sorted(terminal_counts.items())) if terminal_counts else None),
    )


async def _poll_one_task(
    client: httpx.AsyncClient,
    plan: CampaignPlan,
    task_id: str,
    *,
    deadline: float,
    semaphore: asyncio.Semaphore,
) -> str:
    url = plan.targets.control_url(f"/api/course-jobs/{task_id}")
    async with semaphore:
        while True:
            if time.monotonic() >= deadline:
                return "timeout"
            try:
                response = await client.get(url)
                body = response.json()
            except (httpx.HTTPError, ValueError):
                await asyncio.sleep(2)
                continue
            data = body.get("data") if isinstance(body, dict) else None
            tasks = data.get("tasks") if isinstance(data, dict) else None
            if isinstance(tasks, list) and tasks:
                statuses = [item.get("status") for item in tasks if isinstance(item, dict)]
                all_terminal = len(statuses) == len(tasks) and all(
                    status in _TERMINAL_STATUSES for status in statuses
                )
                if all_terminal:
                    return (
                        "success"
                        if all(status == _SUCCESS_STATUS for status in statuses)
                        else "failed"
                    )
            await asyncio.sleep(2)


async def _poll_tasks(
    client: httpx.AsyncClient,
    plan: CampaignPlan,
    task_ids: Sequence[str],
    *,
    timeout_seconds: float,
) -> dict[str, int]:
    if not task_ids:
        return {}
    deadline = time.monotonic() + timeout_seconds
    semaphore = asyncio.Semaphore(min(64, len(task_ids)))
    states = await asyncio.gather(
        *(
            _poll_one_task(
                client,
                plan,
                task_id,
                deadline=deadline,
                semaphore=semaphore,
            )
            for task_id in task_ids
        )
    )
    return dict(sorted(Counter(states).items()))


@dataclass(frozen=True, slots=True)
class PriorityCheckpointResult:
    assessment: PriorityCheckpointAssessment
    observations: tuple[CourseQueryObservation, ...]


async def _fetch_course_query_observations(
    client: httpx.AsyncClient,
    plan: CampaignPlan,
    task_ids: Sequence[str],
) -> tuple[tuple[CourseQueryObservation, ...], tuple[str, ...]]:
    semaphore = asyncio.Semaphore(min(64, max(1, len(task_ids))))

    async def fetch(task_id: str) -> tuple[CourseQueryObservation | None, str | None]:
        url = plan.targets.control_url(f"/api/course-jobs/{task_id}")
        try:
            async with semaphore:
                response = await client.get(url)
            body = response.json()
        except (httpx.HTTPError, ValueError):
            return None, f"{task_id}:任务查询失败"
        if response.status_code != 200 or not isinstance(body, Mapping):
            return None, f"{task_id}:任务查询响应不合法"
        try:
            observation = parse_course_query_response(body)
        except ValueError as error:
            return None, f"{task_id}:{error}"
        if observation.task_id != task_id:
            return None, f"{task_id}:响应 task_id 不一致"
        return observation, None

    fetched = await asyncio.gather(*(fetch(task_id) for task_id in task_ids))
    observations = tuple(item for item, error in fetched if item is not None and error is None)
    errors = tuple(error for _item, error in fetched if error is not None)
    return observations, errors


def _case_allows_overload(case: CaseSpec) -> bool:
    kind = case.load.get("kind")
    if kind in {"online_image", "mixed_image", "s_stream", "realtime_asr"}:
        return not case.case_id.startswith("BASE-")
    return False


class CampaignCaseExecutor:
    def __init__(
        self,
        plan: CampaignPlan,
        release_root: Path,
        *,
        request_timeout_seconds: float = 300,
        sleep: SleepCallable = asyncio.sleep,
        clock: ClockCallable = time.monotonic,
        media_download_adapter: MediaDownloadAdapter | None = None,
    ) -> None:
        self.plan = plan
        self.release_root = release_root
        self.identity = ReproducibleIdentity(plan.campaign_id, plan.seed)
        self._sleep = sleep
        self._clock = clock
        self._media_download_adapter = media_download_adapter
        self.pool = HttpClientPool(
            max_connections=2048,
            max_keepalive_connections=512,
            read_timeout_seconds=request_timeout_seconds,
            write_timeout_seconds=request_timeout_seconds,
            pool_timeout_seconds=5,
        )

    async def _control_readiness(self) -> ControlReadinessEvidence:
        url = self.plan.targets.control_url("/ops/readiness")
        try:
            async with self.pool.build_client() as client:
                response = await client.get(url)
            body = response.json()
        except (httpx.HTTPError, ValueError):
            return ControlReadinessEvidence(
                False,
                None,
                None,
                (),
                (),
                "Control readiness 请求失败",
            )
        if not isinstance(body, Mapping):
            return ControlReadinessEvidence(
                False,
                response.status_code,
                None,
                (),
                (),
                "Control readiness 响应不是对象",
            )
        return validate_control_readiness_response(response.status_code, body)

    async def _wait_priority_normal_checkpoint(
        self,
        client: httpx.AsyncClient,
        normal_task_ids: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> PriorityCheckpointResult:
        deadline = self._clock() + timeout_seconds
        latest = PriorityCheckpointAssessment(
            "waiting",
            "尚未取得 NORMAL 任务领取检查点",
            0,
            0,
            0,
        )
        latest_observations: tuple[CourseQueryObservation, ...] = ()
        while self._clock() < deadline:
            observations, errors = await _fetch_course_query_observations(
                client,
                self.plan,
                normal_task_ids,
            )
            if not errors:
                latest_observations = observations
                latest = assess_priority_normal_checkpoint(observations, normal_task_ids)
                if latest.state != "waiting":
                    return PriorityCheckpointResult(latest, observations)
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            await self._sleep(min(1.0, remaining))
        blocked = PriorityCheckpointAssessment(
            "blocked",
            (
                latest.reason
                if latest.state != "waiting"
                else "超时前未同时观察到运行中与未领取的 NORMAL 节点"
            ),
            latest.observed_node_count,
            latest.running_normal_node_count,
            latest.unclaimed_normal_node_count,
        )
        return PriorityCheckpointResult(blocked, latest_observations)

    def _case(self, case_id: str) -> CaseSpec:
        case = next((item for item in self.plan.catalog.cases if item.case_id == case_id), None)
        if case is None:
            raise ValueError(f"未知 Campaign 用例: {case_id}")
        return case

    def _assert_prerequisites(self, case: CaseSpec) -> None:
        missing: list[str] = []
        failed: list[str] = []
        for prerequisite in case.prerequisites:
            path = execution_path(self.release_root, self.plan, prerequisite)
            if not path.is_file():
                missing.append(prerequisite)
                continue
            prerequisite_case = self._case(prerequisite)
            document = read_case_evidence(
                self.release_root,
                self.plan,
                prerequisite_case,
            )
            if document.get("status") != "passed":
                failed.append(prerequisite)
        if missing or failed:
            raise RuntimeError(
                "前置用例未满足: "
                + "; ".join(
                    filter(
                        None,
                        (
                            "缺失=" + ",".join(missing) if missing else "",
                            "未通过=" + ",".join(failed) if failed else "",
                        ),
                    )
                )
            )

    async def _run_http(
        self,
        case: CaseSpec,
        requests: Sequence[HttpRequestSpec],
        *,
        max_concurrency: int,
        requests_per_second: float | None = None,
        poll_terminal: bool = True,
    ) -> tuple[CaseRunOutcome, tuple[str, ...]]:
        async with self.pool.build_client() as client:
            runner = AsyncLoadRunner(
                client,
                max_concurrency=max_concurrency,
                request_timeout_seconds=min(case.timeout_seconds, 900),
            )
            results = await runner.run(requests, requests_per_second=requests_per_second)
            outcome = _http_outcome(
                requests,
                results,
                allow_overload=_case_allows_overload(case),
            )
            task_ids = _accepted_task_ids(requests, results)
            if outcome.status == "passed" and task_ids and poll_terminal:
                terminal = await _poll_tasks(
                    client,
                    self.plan,
                    task_ids,
                    timeout_seconds=case.timeout_seconds,
                )
                if terminal != {"success": len(task_ids)}:
                    outcome = CaseRunOutcome(
                        "failed",
                        "离线任务未全部成功终态",
                        outcome.request_count,
                        outcome.categories,
                        outcome.latency_seconds,
                        task_ids,
                        terminal,
                    )
                else:
                    outcome = CaseRunOutcome(
                        outcome.status,
                        outcome.reason,
                        outcome.request_count,
                        outcome.categories,
                        outcome.latency_seconds,
                        task_ids,
                        terminal,
                    )
            return outcome, task_ids

    async def _run_offline_case(self, case: CaseSpec) -> CaseRunOutcome:
        requests = tuple(await self._offline_requests(case))
        kind = _load_string(case, "kind")
        if kind == "completed_result_reuse":
            if len(requests) != 2:
                raise ValueError("已完成结果复用用例必须恰有首次提交和复用提交")
            first, _ = await self._run_http(case, requests[:1], max_concurrency=1)
            if first.status != "passed":
                return first
            reused, _ = await self._run_http(case, requests[1:], max_concurrency=1)
            return _combine_outcomes(first, reused)
        if kind == "priority":
            normal = tuple(
                request
                for request in requests
                if request.json_body is not None
                and request.json_body.get("priority") == "NORMAL"
            )
            urgent = tuple(
                request
                for request in requests
                if request.json_body is not None
                and request.json_body.get("priority") == "URGENT"
            )
            if len(normal) + len(urgent) != len(requests) or not normal or not urgent:
                raise ValueError("优先级用例必须只包含非空 NORMAL/URGENT 两组")
            normal_outcome, normal_ids = await self._run_http(
                case,
                normal,
                max_concurrency=min(2048, len(normal)),
                poll_terminal=False,
            )
            if normal_outcome.status != "passed":
                return normal_outcome
            async with self.pool.build_client() as client:
                checkpoint = await self._wait_priority_normal_checkpoint(
                    client,
                    normal_ids,
                    timeout_seconds=case.timeout_seconds,
                )
            if checkpoint.assessment.state != "ready":
                status = (
                    "failed" if checkpoint.assessment.state == "failed" else "blocked"
                )
                return CaseRunOutcome(
                    status,
                    checkpoint.assessment.reason,
                    normal_outcome.request_count,
                    normal_outcome.categories,
                    normal_outcome.latency_seconds,
                    normal_ids,
                    extra={
                        "normal_task_ids": normal_ids,
                        "urgent_task_ids": (),
                        "submission_order": ("NORMAL",),
                        "normal_checkpoint": checkpoint.assessment.to_evidence(),
                        "claim_order_evidence_required": True,
                    },
                )
            urgent_outcome, urgent_ids = await self._run_http(
                case,
                urgent,
                max_concurrency=min(2048, len(urgent)),
                poll_terminal=False,
            )
            combined = _combine_outcomes(normal_outcome, urgent_outcome)
            task_ids = (*normal_ids, *urgent_ids)
            if urgent_outcome.status != "passed":
                return CaseRunOutcome(
                    "failed",
                    urgent_outcome.reason,
                    combined.request_count,
                    combined.categories,
                    combined.latency_seconds,
                    task_ids,
                    extra={
                        "normal_task_ids": normal_ids,
                        "urgent_task_ids": urgent_ids,
                        "submission_order": ("NORMAL", "URGENT"),
                        "normal_checkpoint": checkpoint.assessment.to_evidence(),
                        "claim_order_evidence_required": True,
                    },
                )
            async with self.pool.build_client() as client:
                terminal = await _poll_tasks(
                    client,
                    self.plan,
                    task_ids,
                    timeout_seconds=case.timeout_seconds,
                )
                final_observations, evidence_errors = (
                    await _fetch_course_query_observations(client, self.plan, task_ids)
                )
            if terminal != {"success": len(task_ids)}:
                return CaseRunOutcome(
                    "failed",
                    "优先级用例未全部进入成功终态",
                    combined.request_count,
                    combined.categories,
                    combined.latency_seconds,
                    task_ids,
                    terminal,
                )
            if evidence_errors:
                return CaseRunOutcome(
                    "blocked",
                    "优先级终态查询证据不足: " + "；".join(evidence_errors[:10]),
                    combined.request_count,
                    combined.categories,
                    combined.latency_seconds,
                    task_ids,
                    terminal,
                    extra={
                        "normal_task_ids": normal_ids,
                        "urgent_task_ids": urgent_ids,
                        "submission_order": ("NORMAL", "URGENT"),
                        "normal_checkpoint": checkpoint.assessment.to_evidence(),
                        "claim_order_evidence_required": True,
                    },
                )
            claim_order = validate_priority_claim_order(
                checkpoint.observations,
                final_observations,
                normal_task_ids=normal_ids,
                urgent_task_ids=urgent_ids,
            )
            return CaseRunOutcome(
                claim_order.status,
                claim_order.reason,
                combined.request_count,
                combined.categories,
                combined.latency_seconds,
                task_ids,
                terminal,
                extra={
                    "normal_task_ids": normal_ids,
                    "urgent_task_ids": urgent_ids,
                    "submission_order": ("NORMAL", "URGENT"),
                    "normal_checkpoint": checkpoint.assessment.to_evidence(),
                    "claim_order": claim_order.to_evidence(),
                    "claim_order_evidence_required": claim_order.status != "passed",
                },
            )
        concurrency = 1 if kind == "append_task_types" else min(2048, len(requests))
        outcome, _ = await self._run_http(
            case,
            requests,
            max_concurrency=concurrency,
        )
        return outcome

    async def _offline_requests(self, case: CaseSpec) -> Sequence[HttpRequestSpec]:
        kind = _load_string(case, "kind")
        media = _course_media(self.plan, long_course=kind == "long_course")
        if kind in {"unique_submission", "long_course", "offline_baseline"}:
            count = _load_int(case, "count", default=1)
            if kind == "offline_baseline":
                task_types = _load_string_list(case, "task_types")
                mapping: dict[tuple[str, ...], TaskCombination] = {
                    ("PPT",): TaskCombination.PPT_ONLY,
                    ("ASR",): TaskCombination.ASR_ONLY,
                    ("TEACHER_BEHAVIOR",): TaskCombination.TEACHER_ONLY,
                    ("STUDENT_BEHAVIOR",): TaskCombination.STUDENT_ONLY,
                }
                combination = mapping[tuple(task_types)]
            else:
                combination = (
                    TaskCombination.ALL
                    if kind == "long_course"
                    else _combination(_load_string(case, "combination"))
                )
            return build_unique_submission_burst(
                self.plan.targets,
                self.identity,
                case.case_id,
                count,
                combination,
                media,
                student_count=38 if "STUDENT_BEHAVIOR" in combination.task_types else None,
            )
        if kind in {"idempotent_submission", "conflicting_submission"}:
            conflict = None
            if kind == "conflicting_submission":
                conflict = CourseMedia(
                    teacher_video_path=media.teacher_video_path + "?conflict=1",
                    student_video_path=media.student_video_path + "?conflict=1",
                    slides_video_path=media.slides_video_path + "?conflict=1",
                )
            return build_idempotent_burst(
                self.plan.targets,
                self.identity,
                case.case_id,
                _load_int(case, "count"),
                TaskCombination.ALL,
                media,
                student_count=38,
                conflicting_media=conflict,
            )
        if kind == "append_task_types":
            return build_append_task_type_sequence(
                self.plan.targets,
                self.identity,
                case.case_id,
                media,
                student_count=38,
            )
        if kind == "completed_result_reuse":
            task_id = self.identity.task_id(case.case_id, 0)
            first = build_unique_submission_burst(
                self.plan.targets,
                self.identity,
                case.case_id,
                1,
                TaskCombination.PPT_ONLY,
                media,
            )[0]
            reuse = build_completed_result_reuse_request(
                self.plan.targets,
                self.identity,
                case.case_id + "-REUSE",
                task_id=task_id,
                combination=TaskCombination.PPT_ONLY,
                media=media,
            )
            return (first, reuse)
        if kind == "priority":
            return build_priority_sequence(
                self.plan.targets,
                self.identity,
                case.case_id,
                media,
                normal_count=_load_int(case, "normal"),
                urgent_count=_load_int(case, "urgent"),
            )
        if kind == "negative_submission":
            normal = build_unique_submission_burst(
                self.plan.targets,
                self.identity,
                case.case_id,
                100,
                TaskCombination.ALL,
                media,
                student_count=38,
            )
            return build_negative_submission_mix(
                normal,
                ratio=_load_float(case, "ratio"),
                seed=self.plan.seed,
            )
        raise NotImplementedError(kind)

    async def _online_image_requests(self, case: CaseSpec) -> Sequence[HttpRequestSpec]:
        source, fixture = await self._online_fixture()
        kind = _load_string(case, "kind")
        if kind == "online_image":
            operator = _load_string(case, "operator").replace("-", "_")
            image_kind = ImageKind(operator)
            count = _load_int(case, "concurrency")
            return tuple(
                build_image_request(
                    self.plan.targets,
                    image_kind,
                    fixture,
                    request_index=index,
                    trace_id=self.identity.trace_id(case.case_id, index),
                )
                for index in range(count)
            )
        if kind == "mixed_image":
            return build_mixed_image_requests(
                self.plan.targets,
                self.identity,
                case.case_id,
                fixture,
                count=_load_int(case, "concurrency"),
            )
        if kind == "s_stream":
            scheduled = self._s_stream_schedule(case, fixture)
            return tuple(item.request for item in scheduled)
        if kind == "image_boundary":
            boundary = _load_string(case, "boundary")
            negative = {item.name: item for item in image_syntax_and_format_cases()}
            named = {
                "INVALID-B64": "invalid_base64",
                "BAD-FORMAT": "unsupported_format",
                "BAD-DATA-URI": "invalid_data_uri",
                "DECODE-FAIL": "decode_failure",
            }
            if boundary in named:
                item = negative[named[boundary]]
                return (
                    build_invalid_image_request(
                        self.plan.targets,
                        ImageKind.OCR,
                        request_index=0,
                        invalid_encoded=item.encoded,
                    ),
                )
            sizes = {
                "512K": 512 * 1024,
                "5M": 5 * 1024 * 1024,
                "49M": 49 * 1024 * 1024,
                "OVER-50M": 50 * 1024 * 1024 + 1,
            }
            target_size = sizes[boundary]
            if len(source) > target_size:
                raise ValueError("在线图片 fixture 大于边界构造目标")
            padded = source + b"\0" * (target_size - len(source))
            boundary_fixture = OnlineImageFixture(
                f"boundary-{boundary.lower()}",
                base64.b64encode(padded).decode(),
            )
            return (
                build_image_request(
                    self.plan.targets,
                    ImageKind.OCR,
                    boundary_fixture,
                    request_index=0,
                ),
            )
        raise NotImplementedError(kind)

    async def _online_fixture(self) -> tuple[bytes, OnlineImageFixture]:
        source = await _read_fixture_bytes(
            _fixture(self.plan, "online-image"),
            max_bytes=51 * 1024 * 1024,
        )
        return source, OnlineImageFixture(
            "campaign-image",
            base64.b64encode(source).decode(),
        )

    def _s_stream_schedule(
        self,
        case: CaseSpec,
        fixture: OnlineImageFixture,
    ) -> tuple[ScheduledImageRequest, ...]:
        interval = _load_int(case, "interval")
        return build_s_stream_requests(
            self.plan.targets,
            self.identity,
            case.case_id,
            fixture,
            stream_count=_load_int(case, "streams"),
            interval_seconds=interval,
            frame_rounds=max(1, 30 // interval),
        )

    async def _run_scheduled_http(
        self,
        case: CaseSpec,
        scheduled: Sequence[ScheduledImageRequest | ScheduledQueryRequest],
    ) -> CaseRunOutcome:
        requests, results, offsets = await self._execute_scheduled_http(case, scheduled)
        outcome = _http_outcome(
            requests,
            results,
            allow_overload=_case_allows_overload(case),
        )
        return CaseRunOutcome(
            status=outcome.status,
            reason=outcome.reason,
            request_count=outcome.request_count,
            categories=outcome.categories,
            latency_seconds=outcome.latency_seconds,
            extra={
                "scheduled_offsets_seconds": offsets,
                "scheduled_duration_seconds": max(offsets),
            },
        )

    async def _execute_scheduled_http(
        self,
        case: CaseSpec,
        scheduled: Sequence[ScheduledImageRequest | ScheduledQueryRequest],
    ) -> tuple[tuple[HttpRequestSpec, ...], list[LoadResult], tuple[float, ...]]:
        if not scheduled:
            raise ValueError("定时 HTTP 调度不能为空")
        batches: dict[float, list[HttpRequestSpec]] = {}
        for item in scheduled:
            if item.scheduled_offset_seconds < 0:
                raise ValueError("S 流 scheduled_offset 不能为负")
            batches.setdefault(item.scheduled_offset_seconds, []).append(item.request)
        results: list[LoadResult] = []
        started = self._clock()
        max_batch_size = max(len(batch) for batch in batches.values())
        async with self.pool.build_client() as client:
            runner = AsyncLoadRunner(
                client,
                max_concurrency=min(2048, max_batch_size),
                request_timeout_seconds=min(case.timeout_seconds, 900),
            )
            for offset, batch in sorted(batches.items()):
                delay = started + offset - self._clock()
                if delay > 0:
                    await self._sleep(delay)
                results.extend(await runner.run(batch))
        requests = tuple(item.request for item in scheduled)
        return requests, results, tuple(sorted(batches))

    async def _run_s_stream(self, case: CaseSpec) -> CaseRunOutcome:
        _, fixture = await self._online_fixture()
        return await self._run_scheduled_http(
            case,
            self._s_stream_schedule(case, fixture),
        )

    async def _realtime_asr(self, case: CaseSpec) -> CaseRunOutcome:
        content = await _read_fixture_bytes(
            _fixture(self.plan, "realtime-audio"),
            max_bytes=256 * 1024 * 1024,
        )
        with wave.open(io.BytesIO(content), "rb") as stream:
            fixture = AudioStreamFixture(
                pcm=stream.readframes(stream.getnframes()),
                sample_rate_hz=stream.getframerate(),
                sample_width_bytes=stream.getsampwidth(),
                channels=stream.getnchannels(),
                chunk_duration_seconds=0.2,
            )
        sessions = _load_int(case, "sessions", default=10)
        specs = build_session_specs(self.identity, case.case_id, sessions)
        runner = RealtimeAsrRunner(
            self.plan.targets,
            max_concurrency=min(150, len(specs)),
            session_timeout_seconds=min(case.timeout_seconds, 14_400),
        )
        initial = await runner.run_sessions(specs, fixture)
        if _load_string(case, "kind") == "realtime_asr_reconnect":
            reconnect_specs = build_reconnect_specs(
                self.identity,
                case.case_id,
                specs[: min(5, len(specs))],
            )
            results = (*initial, *(await runner.run_sessions(reconnect_specs, fixture)))
        else:
            results = initial
        categories = dict(sorted(Counter(item.category.value for item in results).items()))
        allowed = {ResultCategory.SUCCESS}
        if _case_allows_overload(case):
            allowed.add(ResultCategory.OVERLOAD)
        failures = [item.session_id for item in results if item.category not in allowed]
        missing_messages = [
            item.session_id
            for item in results
            if item.category is ResultCategory.SUCCESS and not item.message_digests
        ]
        return CaseRunOutcome(
            "failed" if failures or missing_messages else "passed",
            (
                "实时 ASR 存在非预期失败或未等到最终消息"
                if failures or missing_messages
                else "实时 ASR 会话结果符合合同"
            ),
            len(results),
            categories,
            (),
            extra={
                "sent_chunks": sum(item.sent_chunk_count for item in results),
                "message_digest_count": sum(len(item.message_digests) for item in results),
                "failed_session_count": len(failures),
                "missing_final_message_count": len(missing_messages),
            },
        )

    async def _persons(self, case: CaseSpec) -> CaseRunOutcome:
        content = await _read_fixture_bytes(
            _fixture(self.plan, "person-photo"),
            max_bytes=10 * 1024 * 1024,
        )
        encoded = base64.b64encode(content).decode()
        count = _load_int(case, "persons", default=500)
        persons = build_person_dataset(
            self.identity,
            case.case_id,
            count=count,
            encoded_photo=encoded,
        )
        if _load_string(case, "kind") == "face_management":
            plan = build_person_management_requests(
                self.plan.targets,
                persons,
                batch_size=50,
            )
            all_results: list[LoadResult] = []
            phase_results: dict[str, object] = {
                name: {
                    "status": "not_run",
                    "request_count": 0,
                    "categories": {},
                    "successful_person_count": 0,
                    "failed_person_count": 0,
                    "invalid_response_count": 0,
                    "observed_instance_ids": [],
                    "response_routes": [],
                }
                for name, _ in plan.phases
            }
            management_failures: list[str] = []
            observed_management_instances: set[str] = set()
            async with self.pool.build_client() as client:
                runner = AsyncLoadRunner(
                    client,
                    max_concurrency=4,
                    request_timeout_seconds=min(case.timeout_seconds, 900),
                )
                for phase_name, requests in plan.phases:
                    results = await runner.run(requests)
                    all_results.extend(results)
                    http_outcome = _http_outcome(
                        requests,
                        results,
                        allow_overload=False,
                    )
                    request_by_id = {request.request_id: request for request in requests}
                    validations = []
                    invalid_ids: list[str] = []
                    for result in results:
                        request = request_by_id.get(result.request_id)
                        response = result.evidence.get("response")
                        if (
                            request is None
                            or result.category is not ResultCategory.SUCCESS
                            or not isinstance(response, Mapping)
                        ):
                            invalid_ids.append(result.request_id)
                            continue
                        validation = validate_person_management_response(
                            request,
                            response,
                            expected_numbers=plan.expected_numbers(request),
                        )
                        validations.append(validation)
                        observed_management_instances.update(validation.instance_ids)
                        if not validation.valid:
                            invalid_ids.append(f"{result.request_id}:{validation.reason}")
                    categories, _ = _result_summary(results)
                    phase_results[phase_name] = {
                        "status": (
                            "passed"
                            if http_outcome.status == "passed" and not invalid_ids
                            else "failed"
                        ),
                        "request_count": len(results),
                        "categories": categories,
                        "successful_person_count": sum(
                            item.successful_person_count for item in validations
                        ),
                        "failed_person_count": sum(
                            item.failed_person_count for item in validations
                        ),
                        "invalid_response_count": len(invalid_ids),
                        "observed_instance_ids": sorted(
                            {
                                instance_id
                                for item in validations
                                for instance_id in item.instance_ids
                            }
                        ),
                        "response_routes": sorted(
                            {route for item in validations for route in item.response_routes}
                        ),
                    }
                    if http_outcome.status != "passed" or invalid_ids:
                        if http_outcome.status != "passed":
                            management_failures.append(http_outcome.reason)
                        management_failures.extend(invalid_ids)
                        break
            if len(observed_management_instances) > 1:
                management_failures.append("人物管理响应显示请求经过多个 FaceRec 实例")
            categories, latency = _result_summary(all_results)
            boundary = FaceManagementBoundary()
            return CaseRunOutcome(
                "failed" if management_failures else "passed",
                (
                    "；".join(management_failures[:20])
                    if management_failures
                    else "人物新增/批量新增、查询/搜索和精确删除已按阶段顺序完成"
                ),
                len(all_results),
                categories,
                latency,
                extra={
                    "dataset_id": person_dataset_id(count),
                    "dataset_person_count": len(plan.persons),
                    "retained_person_count": len(plan.retained_persons),
                    "deleted_person_count": len(plan.deleted_persons),
                    "deleted_numbers": [person.number for person in plan.deleted_persons],
                    "management_instance_count": boundary.management_instance_count,
                    "observed_management_instance_ids": sorted(
                        observed_management_instances
                    ),
                    "recognition_instance_count": boundary.recognition_instance_count,
                    "request_summary": {
                        phase_name: {
                            "request_count": len(requests),
                            "routes": dict(
                                sorted(
                                    Counter(
                                        urllib.parse.urlsplit(request.url).path
                                        for request in requests
                                    ).items()
                                )
                            ),
                        }
                        for phase_name, requests in plan.phases
                    },
                    "result_summary": phase_results,
                    "instance_consistency": {
                        "status": "pending_unproven",
                        "reason": "管理响应不能替代三个识别实例的共享 MongoDB 观察证据",
                    },
                },
            )

        boundary = FaceManagementBoundary()
        recognition_plan = build_person_recognition_plan(
            self.plan.targets,
            persons,
            recognition_instance_count=boundary.recognition_instance_count,
        )
        requests = recognition_plan.requests
        async with self.pool.build_client() as client:
            runner = AsyncLoadRunner(
                client,
                max_concurrency=min(
                    boundary.recognition_consistency_concurrency,
                    count,
                ),
                request_timeout_seconds=min(case.timeout_seconds, 900),
            )
            results = await runner.run(requests)
        http_outcome = _http_outcome(requests, results, allow_overload=False)
        request_by_id = {request.request_id: request for request in requests}
        validations = []
        expected_match_valid_count = 0
        deleted_absence_valid_count = 0
        failures: list[str] = []
        for result in results:
            request = request_by_id.get(result.request_id)
            response = result.evidence.get("response")
            if (
                request is None
                or result.category is not ResultCategory.SUCCESS
                or not isinstance(response, Mapping)
            ):
                failures.append(result.request_id)
                continue
            expected_number = recognition_expected_number(request)
            expected_present = request.work_type != "online_face_recognize_deleted"
            validation = validate_person_recognition_response(
                response,
                expected_number=expected_number,
                expected_present=expected_present,
            )
            validations.append(validation)
            if not validation.valid:
                failures.append(f"{result.request_id}:{validation.reason}")
            elif expected_present:
                expected_match_valid_count += 1
            else:
                deleted_absence_valid_count += 1
        person_fact_consistent = (
            http_outcome.status == "passed"
            and not failures
            and expected_match_valid_count == len(recognition_plan.retained_persons)
            and deleted_absence_valid_count == len(recognition_plan.deleted_persons)
        )
        categories, latency = _result_summary(results)
        return CaseRunOutcome(
            "failed" if http_outcome.status != "passed" or failures else "passed",
            (
                http_outcome.reason
                if http_outcome.status != "passed"
                else (
                    "识别响应的人物事实不完整或不唯一"
                    if failures
                    else "北向识别响应覆盖全部保留人物且删除人物未复活"
                )
            ),
            len(results),
            categories,
            latency,
            extra={
                "dataset_id": person_dataset_id(count),
                "dataset_person_count": len(persons),
                "retained_person_count": len(recognition_plan.retained_persons),
                "deleted_person_count": len(recognition_plan.deleted_persons),
                "recognition_instance_count": boundary.recognition_instance_count,
                "recognition_consistency_concurrency": (
                    boundary.recognition_consistency_concurrency
                ),
                "request_summary": {
                    "request_count": len(requests),
                    "route": "/api/online/face/recognize",
                    "unique_expected_number_count": len(
                        {
                            recognition_expected_number(request)
                            for request in recognition_plan.expected_matches
                        }
                    ),
                    "deleted_target_count": len(
                        recognition_plan.deleted_absence_checks
                    ),
                },
                "result_summary": {
                    "request_count": len(results),
                    "categories": categories,
                    "matched_expected_count": expected_match_valid_count,
                    "deleted_absence_validated_count": deleted_absence_valid_count,
                    "invalid_response_count": len(failures),
                    "observed_instance_ids": sorted(
                        {
                            instance_id
                            for item in validations
                            for instance_id in item.instance_ids
                        }
                    ),
                    "response_routes": sorted(
                        {route for item in validations for route in item.response_routes}
                    ),
                },
                "person_fact_consistency": {
                    "status": "passed" if person_fact_consistent else "failed",
                    "reason": (
                        "北向识别响应覆盖全部保留 number，删除 number 未返回，且响应事实唯一完整"
                        if person_fact_consistent
                        else "北向识别响应的人物事实不完整或不唯一"
                    ),
                    "expected_retained_number_count": len(
                        recognition_plan.retained_persons
                    ),
                    "recognized_retained_number_count": expected_match_valid_count,
                    "expected_deleted_absence_count": len(
                        recognition_plan.deleted_persons
                    ),
                    "validated_deleted_absence_count": deleted_absence_valid_count,
                    "invalid_response_count": len(failures),
                },
            },
        )

    async def _query(self, case: CaseSpec) -> CaseRunOutcome:
        asr_task_ids: list[str] = []
        other_task_ids: list[str] = []
        for item in self.plan.catalog.cases:
            if item.phase.value != "offline":
                continue
            path = execution_path(self.release_root, self.plan, item.case_id)
            if not path.is_file():
                continue
            body = read_case_evidence(self.release_root, self.plan, item)
            recorded_ids = [str(value) for value in body.get("task_ids", [])]
            combination = item.load.get("combination")
            contains_asr = combination in {"asr_only", "ppt_asr", "all"} or item.load.get(
                "kind"
            ) in {"long_course", "idempotent_submission", "conflicting_submission"}
            (asr_task_ids if contains_asr else other_task_ids).extend(recorded_ids)
        asr_task_ids = list(dict.fromkeys(asr_task_ids))[:50]
        other_task_ids = list(dict.fromkeys(other_task_ids))[:50]
        task_ids = [*asr_task_ids, *other_task_ids]
        if not task_ids:
            return CaseRunOutcome("blocked", "没有已提交任务供查询", 0, {}, ())
        readiness_before = await self._control_readiness()
        external_metrics_boundary = {
            "source": "runtime_metrics_adapter",
            "collected_by_query_executor": False,
            "reason": "PostgreSQL 主机负载由外部运行时指标采集，不由北向查询响应推断",
        }
        if not readiness_before.ready:
            return CaseRunOutcome(
                "blocked",
                "查询前 Control Service 未能证明就绪",
                0,
                {},
                (),
                extra={
                    "control_readiness": {
                        "before": readiness_before.to_evidence(),
                        "after": None,
                    },
                    "postgresql_load_evidence": external_metrics_boundary,
                },
            )
        qps = _load_int(case, "qps", default=50)
        if qps not in query_qps_tiers():
            raise ValueError("查询 QPS 只允许 50/100/300/1000")
        raw_mode_value = case.load.get("mode", "jittered")
        if not isinstance(raw_mode_value, str) or not raw_mode_value:
            raise ValueError(f"用例 {case.case_id} 的 mode 必须是非空字符串")
        raw_mode = raw_mode_value
        mode = QueryMode(raw_mode)
        interval = _load_int(case, "interval", default=2)
        scheduled = build_scheduled_query_requests(
            self.plan.targets,
            self.identity,
            case.case_id,
            task_ids,
            qps=qps,
            duration_seconds=10,
            polling_interval_seconds=interval,
            mode=mode,
            large_asr_task_ids=asr_task_ids,
        )
        if _load_string(case, "kind") == "negative_query":
            mixed = build_negative_query_mix(
                tuple(item.request for item in scheduled),
                self.plan.targets,
                ratio=_load_float(case, "ratio"),
                seed=self.plan.seed,
            )
            scheduled = tuple(
                replace(item, request=request)
                for item, request in zip(scheduled, mixed, strict=True)
            )
        requests, results, offsets = await self._execute_scheduled_http(case, scheduled)
        readiness_after = await self._control_readiness()
        outcome = _http_outcome(
            requests,
            results,
            allow_overload=False,
        )
        response_failures: list[str] = []
        request_by_id = {request.request_id: request for request in requests}
        scheduled_by_id = {item.request.request_id: item for item in scheduled}
        query_observations: list[ObservedCourseQuery] = []
        for result in results:
            request = request_by_id.get(result.request_id)
            if (
                request is None
                or request.expected_business_rejection
                or result.category is not ResultCategory.SUCCESS
            ):
                continue
            response = result.evidence.get("response")
            if not isinstance(response, Mapping):
                response_failures.append(result.request_id)
                continue
            validation = validate_course_query_response(response)
            if not validation.valid:
                response_failures.append(f"{result.request_id}:{validation.reason}")
                continue
            scheduled_request = scheduled_by_id.get(result.request_id)
            if scheduled_request is None:
                response_failures.append(f"{result.request_id}:缺少调度证据")
                continue
            try:
                observation = parse_course_query_response(response)
            except ValueError as error:
                response_failures.append(f"{result.request_id}:{error}")
                continue
            query_observations.append(
                ObservedCourseQuery(
                    scheduled_request.scheduled_offset_seconds,
                    result.request_id,
                    observation,
                )
            )
        transition_validation = validate_monotonic_query_observations(query_observations)
        status = "failed" if outcome.status != "passed" or response_failures else "passed"
        reason = outcome.reason
        if response_failures:
            status = "failed"
            reason = "查询响应结构或整数状态不合法"
        elif not transition_validation.valid:
            status = "failed"
            reason = transition_validation.reason
        elif not readiness_after.ready:
            status = "failed"
            reason = "查询突发停止后 Control Service 未恢复就绪"
        response_sizes = [
            int(size)
            for result in results
            if type(size := result.evidence.get("response_size_bytes")) is int
        ]
        return CaseRunOutcome(
            status,
            reason,
            outcome.request_count,
            outcome.categories,
            outcome.latency_seconds,
            extra={
                "mode": mode.value,
                "requested_qps": qps,
                "polling_interval_seconds": interval,
                "logical_poller_count": qps * interval,
                "queried_task_count": len(task_ids),
                "large_asr_task_count": len(asr_task_ids),
                "successful_query_observation_count": len(query_observations),
                "response_size_sample_count": len(response_sizes),
                "response_size_missing_count": len(results) - len(response_sizes),
                "response_size_bytes_total": sum(response_sizes),
                "response_size_bytes_max": max(response_sizes, default=0),
                "scheduled_offsets_seconds": offsets,
                "scheduled_duration_seconds": max(offsets),
                "invalid_response_count": len(response_failures),
                "node_state_transitions": transition_validation.to_evidence(),
                "control_readiness": {
                    "before": readiness_before.to_evidence(),
                    "after": readiness_after.to_evidence(),
                },
                "postgresql_load_evidence": external_metrics_boundary,
            },
        )

    async def _run_media_download(self, case: CaseSpec) -> CaseRunOutcome:
        adapter = self._media_download_adapter
        if adapter is None:
            return CaseRunOutcome(
                "blocked",
                "媒体下载基线需要显式远程适配器",
                0,
                {},
                (),
            )
        planned_hostname = urllib.parse.urlsplit(self.plan.control_origin).hostname
        if (
            planned_hostname is None
            or adapter.target_hostname.casefold() != planned_hostname.casefold()
        ):
            return CaseRunOutcome(
                "blocked",
                "媒体下载适配器目标主机与 Control Service 目标不一致",
                0,
                {},
                (),
            )
        result = await adapter.run(
            tuple(_fixture(self.plan, fixture_id) for fixture_id in case.fixture_ids),
            concurrency=_load_int(case, "concurrency"),
        )
        failures = result.attempts - result.successes
        latency_seconds = (
            ()
            if result.document is None
            else tuple(sample.elapsed_seconds for sample in result.document.samples)
        )
        return CaseRunOutcome(
            result.status,
            result.reason,
            result.attempts,
            {"failure": failures, "success": result.successes},
            latency_seconds,
            extra=result.to_evidence(),
        )

    async def execute(self, case_id: str) -> Path:
        case = self._case(case_id)
        self._assert_prerequisites(case)
        output = execution_path(self.release_root, self.plan, case_id)
        started_at = datetime.now(UTC).isoformat()
        started = time.perf_counter()
        try:
            kind = _load_string(case, "kind")
            if kind == "phase_gate":
                outcome = CaseRunOutcome("passed", "阶段全部必需用例已经通过", 0, {}, ())
            elif kind == "media_download":
                outcome = await self._run_media_download(case)
            elif kind in {
                "unique_submission",
                "long_course",
                "offline_baseline",
                "idempotent_submission",
                "conflicting_submission",
                "append_task_types",
                "completed_result_reuse",
                "priority",
                "negative_submission",
            }:
                outcome = await self._run_offline_case(case)
            elif kind in {"query", "negative_query"}:
                outcome = await self._query(case)
            elif kind == "s_stream":
                outcome = await self._run_s_stream(case)
            elif kind in {"online_image", "mixed_image", "image_boundary"}:
                requests = await self._online_image_requests(case)
                outcome, _ = await self._run_http(
                    case,
                    requests,
                    max_concurrency=min(2048, len(requests)),
                )
            elif kind in {"realtime_asr", "realtime_asr_reconnect"}:
                outcome = await self._realtime_asr(case)
            elif kind in {"face_management", "face_recognition"}:
                outcome = await self._persons(case)
            else:
                outcome = CaseRunOutcome(
                    "blocked",
                    f"用例需要阶段级协调器: {kind}",
                    0,
                    {},
                    (),
                )
        except Exception as error:
            outcome = CaseRunOutcome(
                "failed",
                f"用例执行异常: {type(error).__name__}: {error}",
                0,
                {},
                (),
            )
        document: JsonObject = {
            "schema_version": 1,
            "evidence_type": "extreme_load_campaign_case",
            "campaign_id": self.plan.campaign_id,
            "release_tag": self.plan.release_tag,
            "git_sha": self.plan.git_sha,
            "case_id": case.case_id,
            "phase": case.phase.value,
            "status": outcome.status,
            "reason": outcome.reason,
            "started_at": started_at,
            "completed_at": datetime.now(UTC).isoformat(),
            "elapsed_seconds": time.perf_counter() - started,
            "request_count": outcome.request_count,
            "categories": dict(outcome.categories),
            "latency_seconds": list(outcome.latency_seconds),
            "task_ids": list(outcome.task_ids),
            "terminal_counts": outcome.terminal_counts,
            "extra": dict(outcome.extra or {}),
        }
        validate_public_payload(document)
        atomic_write_report(
            output,
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        read_case_evidence(self.release_root, self.plan, case)
        return output
