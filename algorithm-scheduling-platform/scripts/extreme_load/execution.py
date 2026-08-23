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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .catalog import CaseSpec, FixtureDescriptor
from .control_query import build_negative_query_mix, build_query_requests
from .core import (
    AsyncLoadRunner,
    HttpClientPool,
    HttpRequestSpec,
    LoadResult,
    ReproducibleIdentity,
    ResultCategory,
)
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
    build_person_dataset,
    build_person_management_requests,
    build_recognition_requests,
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
    ) -> None:
        self.plan = plan
        self.release_root = release_root
        self.identity = ReproducibleIdentity(plan.campaign_id, plan.seed)
        self._sleep = sleep
        self._clock = clock
        self.pool = HttpClientPool(
            max_connections=2048,
            max_keepalive_connections=512,
            read_timeout_seconds=request_timeout_seconds,
            write_timeout_seconds=request_timeout_seconds,
            pool_timeout_seconds=5,
        )

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
            if outcome.status == "passed" and task_ids:
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
        scheduled: Sequence[ScheduledImageRequest],
    ) -> CaseRunOutcome:
        if not scheduled:
            raise ValueError("S 流调度不能为空")
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
                "scheduled_offsets_seconds": tuple(sorted(batches)),
                "scheduled_duration_seconds": max(batches),
            },
        )

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
            requests = build_person_management_requests(
                self.plan.targets,
                persons,
                batch_size=50,
            )
            concurrency = 4
        else:
            requests = build_recognition_requests(
                self.plan.targets,
                persons,
                repeats=count,
            )
            concurrency = min(1000, count)
        outcome, _ = await self._run_http(
            case,
            requests,
            max_concurrency=concurrency,
        )
        return outcome

    async def _query(self, case: CaseSpec) -> CaseRunOutcome:
        task_ids: list[str] = []
        for item in self.plan.catalog.cases:
            if item.phase.value != "offline":
                continue
            path = execution_path(self.release_root, self.plan, item.case_id)
            if not path.is_file():
                continue
            body = read_case_evidence(self.release_root, self.plan, item)
            task_ids.extend(str(value) for value in body.get("task_ids", []))
            if len(task_ids) >= 100:
                break
        task_ids = list(dict.fromkeys(task_ids))[:100]
        if not task_ids:
            return CaseRunOutcome("blocked", "没有已提交任务供查询", 0, {}, ())
        qps = _load_int(case, "qps", default=50)
        nearest = min((50, 100, 300, 1000), key=lambda value: abs(value - qps))
        requests = build_query_requests(
            self.plan.targets,
            task_ids,
            qps=nearest,
            duration_seconds=10,
        )
        if _load_string(case, "kind") == "negative_query":
            requests = build_negative_query_mix(
                requests,
                self.plan.targets,
                ratio=_load_float(case, "ratio"),
                seed=self.plan.seed,
            )
        outcome, _ = await self._run_http(
            case,
            requests,
            max_concurrency=min(2048, qps),
            requests_per_second=qps,
        )
        return outcome

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
