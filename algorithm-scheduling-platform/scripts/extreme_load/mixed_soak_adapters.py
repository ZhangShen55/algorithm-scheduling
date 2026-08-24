from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import time
import wave
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

import httpx

from .catalog import CampaignPhase, CaseSpec
from .control_query import validate_control_readiness_response
from .core import (
    AsyncLoadRunner,
    HttpClientPool,
    HttpRequestSpec,
    LoadResult,
    ReproducibleIdentity,
    ResultCategory,
)
from .execution import _course_media, _fixture, _read_fixture_bytes
from .offline import TaskCombination, build_unique_submission_burst
from .online_images import (
    ImageKind,
    OnlineImageFixture,
    build_image_request,
    build_mixed_image_requests,
)
from .plan import CampaignPlan, read_case_evidence
from .realtime_asr import AudioStreamFixture, RealtimeAsrRunner, build_session_specs
from .stage_runtime import StageCaseAdapter, StageCaseOutcome

StageKind = Literal["mixed", "soak"]
ClockCallable = Callable[[], float]
WaitUntilCallable = Callable[[float], Awaitable[None]]

_LONG_FIXTURE_IDS = ("long-teacher", "long-student", "long-slides")
_STAGE_FIXTURE_IDS = (*_LONG_FIXTURE_IDS, "online-image", "realtime-audio")
_MAX_HTTP_CONCURRENCY = 2048
_QUERY_DURATION_SECONDS = 10
_TERMINAL_STATUSES = frozenset({60, 70, 80})
_SUCCESS_STATUS = 60


@dataclass(frozen=True, slots=True)
class MixedLoadProfile:
    level: str
    long_courses: int
    s_streams: int
    s_stream_interval_seconds: int
    online_images: int
    asr_sessions: int
    query_qps: int

    def __post_init__(self) -> None:
        if not self.level:
            raise ValueError("混合负载档位不能为空")
        values = (
            self.long_courses,
            self.s_streams,
            self.s_stream_interval_seconds,
            self.online_images,
            self.asr_sessions,
            self.query_qps,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("混合负载档位必须使用正整数入口参数")
        if self.online_images > 1000 or self.asr_sessions > 150 or self.query_qps > 1000:
            raise ValueError("混合负载档位超过已批准上限")

    def scaled(self, ratio: float, *, level: str) -> MixedLoadProfile:
        if not math.isfinite(ratio) or not 0 < ratio <= 1:
            raise ValueError("稳定容量比例必须位于 0 与 1 之间")

        def amount(value: int) -> int:
            return max(1, math.floor(value * ratio))

        return MixedLoadProfile(
            level=level,
            long_courses=amount(self.long_courses),
            s_streams=amount(self.s_streams),
            s_stream_interval_seconds=self.s_stream_interval_seconds,
            online_images=amount(self.online_images),
            asr_sessions=amount(self.asr_sessions),
            query_qps=amount(self.query_qps),
        )

    def to_evidence(self) -> dict[str, object]:
        return {
            "level": self.level,
            "long_courses": self.long_courses,
            "s_streams": self.s_streams,
            "s_stream_interval_seconds": self.s_stream_interval_seconds,
            "online_images": self.online_images,
            "asr_sessions": self.asr_sessions,
            "query_qps": self.query_qps,
        }


MIXED_PROFILES: Mapping[str, MixedLoadProfile] = {
    "daily": MixedLoadProfile("daily", 3, 30, 10, 10, 10, 20),
    "peak": MixedLoadProfile("peak", 12, 100, 5, 100, 30, 100),
    "extreme": MixedLoadProfile("extreme", 36, 300, 5, 1000, 150, 1000),
}


@dataclass(frozen=True, slots=True)
class ProfileHttpRequests:
    offline: tuple[HttpRequestSpec, ...]
    online_images: tuple[HttpRequestSpec, ...]
    s_stream_round: tuple[HttpRequestSpec, ...]
    queries: tuple[HttpRequestSpec, ...]
    maximum_concurrency: int

    @property
    def all_requests(self) -> tuple[HttpRequestSpec, ...]:
        return (*self.offline, *self.online_images, *self.s_stream_round, *self.queries)


@dataclass(frozen=True, slots=True)
class HttpConcurrencyBudget:
    online_images: int
    s_streams: int
    queries: int

    @property
    def total(self) -> int:
        return self.online_images + self.s_streams + self.queries


def profile_http_concurrency(profile: MixedLoadProfile) -> HttpConcurrencyBudget:
    images = min(profile.online_images, _MAX_HTTP_CONCURRENCY)
    remaining = _MAX_HTTP_CONCURRENCY - images
    streams = min(profile.s_streams, remaining)
    remaining -= streams
    queries = max(1, min(profile.query_qps, remaining))
    budget = HttpConcurrencyBudget(images, streams, queries)
    if budget.total > _MAX_HTTP_CONCURRENCY:
        raise ValueError("混合 HTTP 并发预算超过负载机上限")
    return budget


def build_profile_http_requests(
    plan: CampaignPlan,
    case: CaseSpec,
    profile: MixedLoadProfile,
    online_fixture: OnlineImageFixture,
    *,
    query_task_ids: Sequence[str],
    query_duration_seconds: int = _QUERY_DURATION_SECONDS,
) -> ProfileHttpRequests:
    if query_duration_seconds <= 0 or not query_task_ids:
        raise ValueError("混合查询需要正持续时间和至少一个 task_id")
    identity = ReproducibleIdentity(plan.campaign_id, plan.seed)
    namespace = case.case_id
    offline = build_unique_submission_burst(
        plan.targets,
        identity,
        f"{namespace}-LONG",
        profile.long_courses,
        TaskCombination.ALL,
        _course_media(plan, long_course=True),
        student_count=38,
    )
    images = build_mixed_image_requests(
        plan.targets,
        identity,
        f"{namespace}-IMAGES",
        online_fixture,
        count=profile.online_images,
    )
    streams = tuple(
        build_image_request(
            plan.targets,
            ImageKind.VBAS,
            online_fixture,
            request_index=index,
            trace_id=identity.trace_id(f"{namespace}-S-STREAM", index),
        )
        for index in range(profile.s_streams)
    )
    query_count = profile.query_qps * query_duration_seconds
    queries = tuple(
        HttpRequestSpec(
            request_id=identity.request_id(f"{namespace}-QUERY", index),
            method="GET",
            url=plan.targets.control_url(
                f"/api/course-jobs/{query_task_ids[index % len(query_task_ids)]}"
            ),
            headers={"X-Trace-ID": identity.trace_id(f"{namespace}-QUERY", index)},
            work_type="course_query",
        )
        for index in range(query_count)
    )
    return ProfileHttpRequests(
        offline=offline,
        online_images=images,
        s_stream_round=streams,
        queries=queries,
        maximum_concurrency=max(
            profile.long_courses,
            profile.online_images,
            profile.s_streams,
            min(profile.query_qps, _MAX_HTTP_CONCURRENCY),
        ),
    )


@dataclass(frozen=True, slots=True)
class TrafficSnapshot:
    round_count: int
    categories: Mapping[str, int]
    latency_seconds: tuple[float, ...]
    accepted_task_ids: tuple[str, ...]
    correctness_failures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecoveryEvidence:
    recovered: bool
    queue_drained: bool
    attempts: int
    control_ready: bool
    task_queue_depth: int | None
    outbox_pending: int | None
    active_leases: int | None
    inflight: int | None
    terminal_counts: Mapping[str, int]

    def to_evidence(self) -> dict[str, object]:
        return {
            "recovered": self.recovered,
            "queue_drained": self.queue_drained,
            "attempts": self.attempts,
            "control_ready": self.control_ready,
            "task_queue_depth": self.task_queue_depth,
            "outbox_pending": self.outbox_pending,
            "active_leases": self.active_leases,
            "inflight": self.inflight,
            "terminal_counts": dict(self.terminal_counts),
        }


class MixedTrafficRunner(Protocol):
    async def run_round(
        self,
        case: CaseSpec,
        profile: MixedLoadProfile,
        abort_event: asyncio.Event,
    ) -> None: ...

    def snapshot(self) -> TrafficSnapshot: ...

    async def recover(
        self,
        case: CaseSpec,
        *,
        timeout_seconds: float,
    ) -> RecoveryEvidence: ...


class StopMonitor(Protocol):
    async def wait(self, case: CaseSpec, finished: asyncio.Event) -> str | None: ...


class RuntimeEvidenceStopMonitor:
    def __init__(
        self,
        release_root: Path,
        *,
        campaign_id: str,
        clock: ClockCallable = time.monotonic,
        poll_seconds: float = 0.25,
    ) -> None:
        if not campaign_id:
            raise ValueError("Campaign ID 不能为空")
        if poll_seconds <= 0:
            raise ValueError("护栏证据轮询周期必须是正数")
        self._release_root = release_root.resolve()
        self._campaign_id = campaign_id
        self._clock = clock
        self._poll_seconds = poll_seconds

    def _latest_document(self, case: CaseSpec) -> tuple[int, Mapping[str, object]] | None:
        directory = self._release_root / "campaign" / "runtime-metrics" / case.case_id
        paths = sorted(directory.glob("[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].json"))
        if not paths:
            return None
        path = paths[-1]
        if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("运行时指标证据文件类型或大小不合法")
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("运行时指标证据不是对象")
        document = cast(Mapping[str, object], raw)
        if (
            document.get("campaign_id") != self._campaign_id
            or document.get("case_id") != case.case_id
        ):
            raise ValueError("运行时指标证据不属于当前 Campaign 用例")
        return int(path.stem), document

    async def wait(self, case: CaseSpec, finished: asyncio.Event) -> str | None:
        stale_seconds = 2.0 if case.load.get("kind") == "mixed" else 15.0
        last_sequence = 0
        last_observed = self._clock()
        while not finished.is_set():
            try:
                latest = await asyncio.to_thread(self._latest_document, case)
                if latest is not None and latest[0] > last_sequence:
                    last_sequence, document = latest
                    guardrail = document.get("guardrail")
                    if not isinstance(guardrail, Mapping):
                        raise ValueError("运行时指标证据缺少护栏结果")
                    level = guardrail.get("level")
                    reasons = guardrail.get("reasons")
                    if level == "STOP":
                        safe_reasons = (
                            [str(item) for item in reasons]
                            if isinstance(reasons, list)
                            else ["运行时护栏触发 STOP"]
                        )
                        return "；".join(safe_reasons[:20])
                    if level not in {"CLEAR", "WARNING"}:
                        raise ValueError("运行时指标证据护栏等级不合法")
                    last_observed = self._clock()
                if self._clock() - last_observed > stale_seconds:
                    return "运行时指标证据流超时，停止产生新负载"
            except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
                return f"运行时指标证据不可验证: {type(error).__name__}"
            try:
                await asyncio.wait_for(finished.wait(), timeout=self._poll_seconds)
            except TimeoutError:
                pass
        return None


@dataclass(frozen=True, slots=True)
class _RoundAssets:
    image: OnlineImageFixture
    audio: AudioStreamFixture


class _NorthboundTrafficRunner:
    def __init__(
        self,
        plan: CampaignPlan,
        *,
        clock: ClockCallable = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._plan = plan
        self._clock = clock
        self._sleep = sleep
        self._identity = ReproducibleIdentity(plan.campaign_id, plan.seed)
        self._pool = HttpClientPool(
            max_connections=_MAX_HTTP_CONCURRENCY,
            max_keepalive_connections=512,
            read_timeout_seconds=900,
            write_timeout_seconds=900,
            pool_timeout_seconds=5,
        )
        self._recovery_pool = HttpClientPool(
            max_connections=128,
            max_keepalive_connections=64,
            connect_timeout_seconds=5,
            read_timeout_seconds=5,
            write_timeout_seconds=5,
            pool_timeout_seconds=5,
        )
        self._categories: Counter[str] = Counter()
        self._latencies: list[float] = []
        self._accepted_task_ids: dict[str, None] = {}
        self._active_task_ids: dict[str, None] = {}
        self._correctness_failures: list[str] = []
        self._round_count = 0
        self._assets: _RoundAssets | None = None
        self._assets_lock = asyncio.Lock()

    async def _load_assets(self) -> _RoundAssets:
        if self._assets is not None:
            return self._assets
        async with self._assets_lock:
            if self._assets is not None:
                return self._assets
            image_bytes = await _read_fixture_bytes(
                _fixture(self._plan, "online-image"),
                max_bytes=51 * 1024 * 1024,
            )
            audio_bytes = await _read_fixture_bytes(
                _fixture(self._plan, "realtime-audio"),
                max_bytes=256 * 1024 * 1024,
            )
            with wave.open(io.BytesIO(audio_bytes), "rb") as stream:
                audio = AudioStreamFixture(
                    pcm=stream.readframes(stream.getnframes()),
                    sample_rate_hz=stream.getframerate(),
                    sample_width_bytes=stream.getsampwidth(),
                    channels=stream.getnchannels(),
                    chunk_duration_seconds=0.2,
                )
            self._assets = _RoundAssets(
                OnlineImageFixture("campaign-mixed-image", base64.b64encode(image_bytes).decode()),
                audio,
            )
            return self._assets

    async def _run_http(
        self,
        requests: Sequence[HttpRequestSpec],
        *,
        concurrency: int,
        abort_event: asyncio.Event,
        requests_per_second: float | None = None,
    ) -> list[LoadResult]:
        if not requests:
            return []
        async with self._pool.build_client() as client:
            runner = AsyncLoadRunner(
                client,
                max_concurrency=min(_MAX_HTTP_CONCURRENCY, max(1, concurrency)),
                request_timeout_seconds=900,
            )
            return await runner.run(
                requests,
                requests_per_second=requests_per_second,
                abort_event=abort_event,
            )

    def _record_http(
        self,
        requests: Sequence[HttpRequestSpec],
        results: Sequence[LoadResult],
    ) -> None:
        self._categories.update(result.category.value for result in results)
        self._latencies.extend(result.elapsed_seconds for result in results)
        request_by_id = {request.request_id: request for request in requests}
        for result in results:
            request = request_by_id.get(result.request_id)
            if (
                request is None
                or result.category is not ResultCategory.SUCCESS
                or request.json_body is None
            ):
                continue
            task_id = request.json_body.get("task_id")
            if isinstance(task_id, str) and task_id:
                self._accepted_task_ids[task_id] = None
                self._active_task_ids[task_id] = None

    async def _refresh_active_tasks(self) -> None:
        if not self._active_task_ids:
            return
        semaphore = asyncio.Semaphore(64)

        async def state(client: httpx.AsyncClient, task_id: str) -> tuple[str, str]:
            try:
                async with semaphore:
                    response = await client.get(
                        self._plan.targets.control_url(f"/api/course-jobs/{task_id}")
                    )
                body = response.json()
                data = body.get("data") if isinstance(body, Mapping) else None
                tasks = data.get("tasks") if isinstance(data, Mapping) else None
                if response.status_code != 200 or not isinstance(tasks, list) or not tasks:
                    return task_id, "running"
                statuses = [item.get("status") for item in tasks if isinstance(item, Mapping)]
                if len(statuses) != len(tasks) or not all(
                    type(status) is int and status in _TERMINAL_STATUSES for status in statuses
                ):
                    return task_id, "running"
                if all(status == _SUCCESS_STATUS for status in statuses):
                    return task_id, "success"
                return task_id, "failed"
            except (httpx.HTTPError, ValueError):
                return task_id, "running"

        async with self._recovery_pool.build_client() as client:
            states = await asyncio.gather(
                *(state(client, task_id) for task_id in self._active_task_ids)
            )
        for task_id, task_state in states:
            if task_state == "running":
                continue
            self._active_task_ids.pop(task_id, None)
            if task_state == "failed":
                self._correctness_failures.append(f"长稳期间已接受长课进入失败终态: {task_id}")

    async def _run_s_stream(
        self,
        requests: Sequence[HttpRequestSpec],
        profile: MixedLoadProfile,
        abort_event: asyncio.Event,
        *,
        concurrency: int,
    ) -> None:
        rounds = max(2, _QUERY_DURATION_SECONDS // profile.s_stream_interval_seconds + 1)
        for round_index in range(rounds):
            if abort_event.is_set():
                return
            round_requests = tuple(
                HttpRequestSpec(
                    request_id=f"{request.request_id}-round-{round_index}",
                    method=request.method,
                    url=request.url,
                    json_body=request.json_body,
                    headers=request.headers,
                    work_type=request.work_type,
                    expected_business_rejection=request.expected_business_rejection,
                    expected_lease_acquisition=request.expected_lease_acquisition,
                )
                for request in requests
            )
            results = await self._run_http(
                round_requests,
                concurrency=concurrency,
                abort_event=abort_event,
            )
            self._record_http(round_requests, results)
            if round_index == rounds - 1:
                return
            try:
                await asyncio.wait_for(
                    abort_event.wait(),
                    timeout=profile.s_stream_interval_seconds,
                )
                return
            except TimeoutError:
                pass

    async def _run_asr(
        self,
        case: CaseSpec,
        profile: MixedLoadProfile,
        assets: _RoundAssets,
    ) -> None:
        namespace = f"{case.case_id}-ASR"
        specs = build_session_specs(
            self._identity,
            namespace,
            profile.asr_sessions,
        )
        runner = RealtimeAsrRunner(
            self._plan.targets,
            max_concurrency=min(150, profile.asr_sessions),
            session_timeout_seconds=min(case.timeout_seconds, 14_400),
            sleep=self._sleep,
        )
        results = await runner.run_sessions(specs, assets.audio)
        self._categories.update(result.category.value for result in results)
        missing = sum(
            result.category is ResultCategory.SUCCESS and not result.message_digests
            for result in results
        )
        if missing:
            self._correctness_failures.append(f"{missing} 个实时 ASR 成功会话缺少隔离后的响应摘要")

    async def _lane(
        self,
        name: str,
        operation: Awaitable[None],
    ) -> None:
        try:
            await operation
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._categories[ResultCategory.LOAD_GENERATOR_FAILURE.value] += 1
            self._correctness_failures.append(f"{name} 负载机异常: {type(error).__name__}")

    async def run_round(
        self,
        case: CaseSpec,
        profile: MixedLoadProfile,
        abort_event: asyncio.Event,
    ) -> None:
        self._round_count += 1
        round_case = case.model_copy(
            update={"case_id": f"{case.case_id}-ROUND-{self._round_count}"}
        )
        assets = await self._load_assets()
        await self._refresh_active_tasks()
        placeholder_task_ids = tuple(self._accepted_task_ids) or (
            self._identity.task_id(f"{round_case.case_id}-LONG", 0),
        )
        requests = build_profile_http_requests(
            self._plan,
            round_case,
            profile,
            assets.image,
            query_task_ids=placeholder_task_ids,
        )
        if not self._active_task_ids:
            offline_results = await self._run_http(
                requests.offline,
                concurrency=profile.long_courses,
                abort_event=abort_event,
            )
            self._record_http(requests.offline, offline_results)
        if abort_event.is_set():
            return
        query_ids = tuple(self._accepted_task_ids)
        if not query_ids:
            self._correctness_failures.append("离线长课没有已接受 task_id，不能启动真实查询负载")
            return
        requests = build_profile_http_requests(
            self._plan,
            round_case,
            profile,
            assets.image,
            query_task_ids=query_ids,
        )
        concurrency = profile_http_concurrency(profile)

        async def run_images() -> None:
            results = await self._run_http(
                requests.online_images,
                concurrency=concurrency.online_images,
                abort_event=abort_event,
            )
            self._record_http(requests.online_images, results)

        async def run_queries() -> None:
            results = await self._run_http(
                requests.queries,
                concurrency=concurrency.queries,
                abort_event=abort_event,
                requests_per_second=float(profile.query_qps),
            )
            self._record_http(requests.queries, results)

        async with asyncio.TaskGroup() as group:
            group.create_task(self._lane("在线图片", run_images()))
            group.create_task(
                self._lane(
                    "S 流",
                    self._run_s_stream(
                        requests.s_stream_round,
                        profile,
                        abort_event,
                        concurrency=concurrency.s_streams,
                    ),
                )
            )
            group.create_task(self._lane("任务查询", run_queries()))
            group.create_task(self._lane("实时 ASR", self._run_asr(round_case, profile, assets)))

    def snapshot(self) -> TrafficSnapshot:
        return TrafficSnapshot(
            round_count=self._round_count,
            categories=dict(sorted(self._categories.items())),
            latency_seconds=tuple(self._latencies),
            accepted_task_ids=tuple(self._accepted_task_ids),
            correctness_failures=tuple(self._correctness_failures),
        )

    @staticmethod
    def _non_negative(value: object, field_name: str) -> int:
        if type(value) is not int or value < 0:
            raise ValueError(f"{field_name} 不是非负整数")
        return value

    async def _recovery_snapshot(
        self,
        client: httpx.AsyncClient,
    ) -> tuple[bool, int, int, int, int, Counter[str]]:
        origin = self._plan.control_origin
        readiness_response, queues_response, instances_response = await asyncio.gather(
            client.get(f"{origin}/ops/readiness"),
            client.get(f"{origin}/ops/queues"),
            client.get(f"{origin}/ops/operator-instances/snapshot"),
        )
        readiness_body = readiness_response.json()
        queues_body = queues_response.json()
        instances_body = instances_response.json()
        if not isinstance(readiness_body, Mapping):
            raise ValueError("Control readiness 响应不是对象")
        readiness = validate_control_readiness_response(
            readiness_response.status_code,
            readiness_body,
        )
        if not isinstance(queues_body, Mapping) or not isinstance(queues_body.get("queues"), list):
            raise ValueError("Control 队列响应不合法")
        queue_depth = sum(
            self._non_negative(item.get("count"), "queue.count")
            for item in queues_body["queues"]
            if isinstance(item, Mapping)
        )
        outbox_pending = self._non_negative(queues_body.get("outbox_pending"), "outbox")
        if not isinstance(instances_body, list):
            raise ValueError("Control 实例快照响应不合法")
        active_leases = sum(
            self._non_negative(item.get("active_lease_count"), "active_lease_count")
            for item in instances_body
            if isinstance(item, Mapping)
        )
        inflight = sum(
            self._non_negative(item.get("reported_inflight"), "reported_inflight")
            for item in instances_body
            if isinstance(item, Mapping)
        )

        terminal: Counter[str] = Counter()
        semaphore = asyncio.Semaphore(64)

        async def task_state(task_id: str) -> str:
            async with semaphore:
                response = await client.get(
                    self._plan.targets.control_url(f"/api/course-jobs/{task_id}")
                )
            body = response.json()
            data = body.get("data") if isinstance(body, Mapping) else None
            tasks = data.get("tasks") if isinstance(data, Mapping) else None
            if response.status_code != 200 or not isinstance(tasks, list) or not tasks:
                return "running"
            statuses = [item.get("status") for item in tasks if isinstance(item, Mapping)]
            if len(statuses) != len(tasks) or not all(
                type(status) is int and status in _TERMINAL_STATUSES for status in statuses
            ):
                return "running"
            return "success" if all(status == _SUCCESS_STATUS for status in statuses) else "failed"

        states = await asyncio.gather(*(task_state(task_id) for task_id in self._accepted_task_ids))
        terminal.update(states)
        return readiness.ready, queue_depth, outbox_pending, active_leases, inflight, terminal

    async def recover(
        self,
        case: CaseSpec,
        *,
        timeout_seconds: float,
    ) -> RecoveryEvidence:
        del case
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("恢复超时必须是有限正数")
        deadline = self._clock() + timeout_seconds
        attempts = 0
        latest = RecoveryEvidence(False, False, 0, False, None, None, None, None, {})
        async with self._recovery_pool.build_client() as client:
            while self._clock() < deadline:
                attempts += 1
                try:
                    (
                        ready,
                        queue_depth,
                        outbox_pending,
                        active_leases,
                        inflight,
                        terminal,
                    ) = await self._recovery_snapshot(client)
                    tasks_finished = sum(terminal.values()) == len(self._accepted_task_ids)
                    tasks_succeeded = terminal == Counter({"success": len(self._accepted_task_ids)})
                    drained = (
                        tasks_finished
                        and queue_depth == 0
                        and outbox_pending == 0
                        and active_leases == 0
                        and inflight == 0
                    )
                    recovered = ready and drained and tasks_succeeded
                    latest = RecoveryEvidence(
                        recovered,
                        drained,
                        attempts,
                        ready,
                        queue_depth,
                        outbox_pending,
                        active_leases,
                        inflight,
                        dict(sorted(terminal.items())),
                    )
                    if recovered:
                        return latest
                except (httpx.HTTPError, ValueError):
                    latest = RecoveryEvidence(
                        False,
                        False,
                        attempts,
                        False,
                        None,
                        None,
                        None,
                        None,
                        {},
                    )
                remaining = deadline - self._clock()
                if remaining <= 0:
                    break
                await self._sleep(min(2.0, remaining))
        return latest


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def _guardrail_clear(document: Mapping[str, object], key: str) -> bool:
    value = document.get(key)
    return isinstance(value, Mapping) and value.get("level") == "CLEAR"


def load_stable_profile(plan: CampaignPlan, release_root: Path) -> MixedLoadProfile | None:
    selected: MixedLoadProfile | None = None
    for level in ("daily", "peak", "extreme"):
        case = next(
            (
                item
                for item in plan.catalog.cases
                if item.phase is CampaignPhase.MIXED and item.case_id == f"MIXED-{level.upper()}"
            ),
            None,
        )
        if case is None:
            continue
        try:
            document = read_case_evidence(release_root, plan, case)
        except (OSError, ValueError):
            continue
        adapter_evidence = document.get("adapter_evidence")
        if (
            document.get("status") != "passed"
            or document.get("recovery_succeeded") is not True
            or not _guardrail_clear(document, "guardrail_before")
            or not _guardrail_clear(document, "guardrail_after")
            or not isinstance(adapter_evidence, Mapping)
            or adapter_evidence.get("stable_capacity") is not True
            or adapter_evidence.get("profile") != MIXED_PROFILES[level].to_evidence()
        ):
            continue
        selected = MIXED_PROFILES[level]
    return selected


class MixedSoakStageAdapter(StageCaseAdapter):
    def __init__(
        self,
        plan: CampaignPlan,
        release_root: Path,
        *,
        stage_kind: StageKind,
        traffic_runner: MixedTrafficRunner | None = None,
        stop_monitor: StopMonitor | None = None,
        clock: ClockCallable = time.monotonic,
        wait_until: WaitUntilCallable | None = None,
        stable_profile_loader: Callable[
            [CampaignPlan, Path], MixedLoadProfile | None
        ] = load_stable_profile,
    ) -> None:
        self._plan = plan
        self._release_root = release_root.resolve()
        self._stage_kind = stage_kind
        self._clock = clock
        self._wait_until = wait_until or self._real_wait_until
        self._stable_profile_loader = stable_profile_loader
        self._runner = traffic_runner or _NorthboundTrafficRunner(plan, clock=clock)
        self._monitor = stop_monitor or RuntimeEvidenceStopMonitor(
            self._release_root,
            campaign_id=plan.campaign_id,
            clock=clock,
        )

    async def _real_wait_until(self, deadline: float) -> None:
        await asyncio.sleep(max(0.0, deadline - self._clock()))

    def _validated_profile(
        self,
        case: CaseSpec,
    ) -> tuple[MixedLoadProfile | None, float | None, StageCaseOutcome | None]:
        if case.fixture_ids != _STAGE_FIXTURE_IDS:
            return (
                None,
                None,
                StageCaseOutcome(
                    "failed",
                    "混合/长稳用例必须绑定规范长课、在线图片和实时音频 fixture",
                    {"validation_state": "fixture_invalid"},
                ),
            )
        if self._stage_kind == "mixed":
            if (
                case.phase is not CampaignPhase.MIXED
                or set(case.load) != {"kind", "level"}
                or case.load.get("kind") != "mixed"
                or case.load.get("level") not in MIXED_PROFILES
            ):
                return (
                    None,
                    None,
                    StageCaseOutcome(
                        "failed",
                        "阶段用例不是规范 mixed 用例",
                        {"validation_state": "case_invalid"},
                    ),
                )
            return MIXED_PROFILES[cast(str, case.load["level"])], None, None

        hours = case.load.get("hours")
        ratio = case.load.get("stable_capacity_ratio")
        if (
            case.phase is not CampaignPhase.SOAK
            or set(case.load) != {"kind", "hours", "stable_capacity_ratio"}
            or case.load.get("kind") != "soak"
            or type(hours) is not int
            or hours not in {4, 8}
            or type(ratio) is not float
            or ratio != 0.75
        ):
            return (
                None,
                None,
                StageCaseOutcome(
                    "failed",
                    "阶段用例不是规范 4h/8h 75% soak 用例",
                    {"validation_state": "case_invalid"},
                ),
            )
        stable = self._stable_profile_loader(self._plan, self._release_root)
        if stable is None:
            return (
                None,
                None,
                StageCaseOutcome(
                    "blocked",
                    "缺少同 Campaign 已通过且护栏清晰的实测稳定容量证据",
                    {"configuration_state": "stable_capacity_unproven"},
                ),
            )
        return stable.scaled(ratio, level="soak-75pct"), float(hours * 3600), None

    @staticmethod
    async def _cancel(task: asyncio.Task[object] | None) -> None:
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _run_load(
        self,
        case: CaseSpec,
        profile: MixedLoadProfile,
        duration_seconds: float | None,
        abort_event: asyncio.Event,
        finished: asyncio.Event,
    ) -> tuple[str | None, bool]:
        monitor = asyncio.create_task(
            self._monitor.wait(case, finished),
            name=f"mixed-guardrail-{case.case_id}",
        )
        deadline = None if duration_seconds is None else self._clock() + duration_seconds
        deadline_task: asyncio.Task[None] | None = None
        if deadline is not None:

            async def wait_for_deadline() -> None:
                await self._wait_until(deadline)

            deadline_task = asyncio.create_task(
                wait_for_deadline(),
                name=f"soak-deadline-{case.case_id}",
            )
        round_task: asyncio.Task[None] | None = None
        stop_reason: str | None = None
        planned_completion = False
        try:
            while True:
                round_task = asyncio.create_task(
                    self._runner.run_round(case, profile, abort_event),
                    name=f"mixed-round-{case.case_id}",
                )
                watched: set[asyncio.Task[object]] = {
                    cast(asyncio.Task[object], round_task),
                    cast(asyncio.Task[object], monitor),
                }
                if deadline_task is not None:
                    watched.add(cast(asyncio.Task[object], deadline_task))
                done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
                if monitor in done:
                    stop_reason = monitor.result()
                    if stop_reason is not None:
                        abort_event.set()
                        await self._cancel(cast(asyncio.Task[object], round_task))
                        break
                if deadline_task is not None and deadline_task in done:
                    planned_completion = True
                    abort_event.set()
                    await self._cancel(cast(asyncio.Task[object], round_task))
                    break
                if round_task in done:
                    round_task.result()
                    if deadline is None:
                        planned_completion = True
                        break
                    if self._clock() >= deadline:
                        planned_completion = True
                        abort_event.set()
                        break
        except asyncio.CancelledError:
            stop_reason = "阶段执行收到取消请求"
            abort_event.set()
            await self._cancel(cast(asyncio.Task[object] | None, round_task))
        finally:
            finished.set()
            await self._cancel(cast(asyncio.Task[object], monitor))
            await self._cancel(cast(asyncio.Task[object] | None, deadline_task))
        return stop_reason, planned_completion

    async def execute(self, case: CaseSpec) -> StageCaseOutcome:
        profile, duration_seconds, rejected = self._validated_profile(case)
        if rejected is not None:
            return rejected
        assert profile is not None
        started = self._clock()
        abort_event = asyncio.Event()
        finished = asyncio.Event()
        load_error_type: str | None = None
        recovery_error_type: str | None = None
        stop_reason: str | None = None
        planned_completion = False
        try:
            stop_reason, planned_completion = await self._run_load(
                case,
                profile,
                duration_seconds,
                abort_event,
                finished,
            )
        except Exception as error:
            abort_event.set()
            load_error_type = type(error).__name__
        load_finished = self._clock()
        remaining = max(1.0, case.timeout_seconds - (load_finished - started))
        try:
            recovery = await self._runner.recover(case, timeout_seconds=remaining)
        except Exception as error:
            recovery_error_type = type(error).__name__
            recovery = RecoveryEvidence(
                recovered=False,
                queue_drained=False,
                attempts=0,
                control_ready=False,
                task_queue_depth=None,
                outbox_pending=None,
                active_leases=None,
                inflight=None,
                terminal_counts={},
            )

        snapshot = self._runner.snapshot()
        categories = Counter(snapshot.categories)
        if load_error_type is not None:
            categories[ResultCategory.LOAD_GENERATOR_FAILURE.value] += 1
        if stop_reason is not None and categories[ResultCategory.GUARDRAIL_ABORT.value] == 0:
            categories[ResultCategory.GUARDRAIL_ABORT.value] = 1
        forbidden = sum(
            categories[name]
            for name in (
                ResultCategory.BUSINESS_REJECTED.value,
                ResultCategory.TIMEOUT.value,
                ResultCategory.CONNECTION_FAILURE.value,
                ResultCategory.UNDEFINED_5XX.value,
                ResultCategory.LOAD_GENERATOR_FAILURE.value,
            )
        )
        recovered = recovery.recovered and recovery.queue_drained
        observed_duration = load_finished - started
        duration_valid = duration_seconds is None or observed_duration >= duration_seconds
        stable_capacity = (
            stop_reason is None
            and planned_completion
            and forbidden == 0
            and not snapshot.correctness_failures
            and categories[ResultCategory.OVERLOAD.value] == 0
            and recovered
            and duration_valid
        )
        if stop_reason is not None:
            status = "blocked"
            classification = "guardrail_stop"
            reason = f"停止产生新负载并完成恢复检查: {stop_reason}"
        elif not recovered or forbidden or snapshot.correctness_failures or not duration_valid:
            status = "failed"
            classification = "nonconforming"
            reason = "混合/长稳负载出现非预期结果、证据缺口或未完成恢复排空"
        elif self._stage_kind == "soak" and categories[ResultCategory.OVERLOAD.value]:
            status = "failed"
            classification = "nonconforming"
            reason = "75% 稳定容量长稳期间出现过载拒绝"
            stable_capacity = False
        else:
            status = "passed"
            classification = (
                "expected_overload"
                if categories[ResultCategory.OVERLOAD.value]
                else "stable_capacity"
            )
            reason = "混合/长稳入口负载完成，结果分类明确且平台恢复排空"

        evidence: dict[str, object] = {
            "profile": profile.to_evidence(),
            "source_stable_capacity_ratio": (
                case.load.get("stable_capacity_ratio") if self._stage_kind == "soak" else None
            ),
            "configured_duration_seconds": duration_seconds,
            "observed_duration_seconds": observed_duration,
            "round_count": snapshot.round_count,
            "request_count": sum(categories.values()),
            "categories": dict(sorted(categories.items())),
            "latency_p95_seconds": _percentile(snapshot.latency_seconds, 0.95),
            "latency_p99_seconds": _percentile(snapshot.latency_seconds, 0.99),
            "accepted_task_count": len(snapshot.accepted_task_ids),
            "correctness_failures": list(snapshot.correctness_failures),
            "maximum_http_concurrency": max(
                profile.long_courses,
                profile_http_concurrency(profile).total,
            ),
            "bounded_http_concurrency_limit": _MAX_HTTP_CONCURRENCY,
            "stop_reason": stop_reason,
            "load_error_type": load_error_type,
            "recovery_error_type": recovery_error_type,
            "classification": classification,
            "stable_capacity": stable_capacity,
            "recovery": recovery.to_evidence(),
        }
        return StageCaseOutcome(
            cast(Literal["passed", "failed", "blocked", "not_run"], status),
            reason,
            evidence,
            recovery_succeeded=recovered,
        )


def mixed_factory(plan: CampaignPlan, release_root: Path) -> StageCaseAdapter:
    return MixedSoakStageAdapter(plan, release_root, stage_kind="mixed")


def soak_factory(plan: CampaignPlan, release_root: Path) -> StageCaseAdapter:
    return MixedSoakStageAdapter(plan, release_root, stage_kind="soak")
