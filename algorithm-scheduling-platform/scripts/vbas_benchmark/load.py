from __future__ import annotations

import asyncio
import math
import statistics
from collections import Counter, deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .fixtures import build_batch_payload, endpoint_for_stream, parse_successful_batch
from .models import (
    BenchmarkGuardrails,
    BenchmarkMode,
    FixtureManifest,
    JsonObject,
    VbasTarget,
)

SampleHook = Callable[[str, int, float], Awaitable[None]]
GuardrailHook = Callable[[], str | None]


@dataclass(frozen=True, slots=True)
class LoadRunConfig:
    mode: BenchmarkMode
    concurrency_per_instance: int
    batch_size: int = 8
    warmup_seconds: float = 60.0
    steady_seconds: float = 600.0
    min_steady_batches: int = 100
    request_timeout_seconds: float = 120.0
    drain_timeout_seconds: float = 180.0
    max_total_seconds: float = 3_600.0
    sample_interval_seconds: float = 0.2
    seed: int = 20260907
    guardrails: BenchmarkGuardrails = field(default_factory=BenchmarkGuardrails)

    def __post_init__(self) -> None:
        integer_values = (self.concurrency_per_instance, self.batch_size)
        if any(isinstance(value, bool) or value <= 0 for value in integer_values):
            raise ValueError("并发和 batch_size 必须是正整数")
        if self.batch_size > 8:
            raise ValueError("VBas 离线基准 batch_size 不能大于 8")
        durations = (
            self.warmup_seconds,
            self.steady_seconds,
            self.request_timeout_seconds,
            self.drain_timeout_seconds,
            self.max_total_seconds,
            self.sample_interval_seconds,
        )
        if any(not math.isfinite(value) or value <= 0 for value in durations):
            raise ValueError("时间参数必须是有限正数")
        if self.min_steady_batches <= 0:
            raise ValueError("最小稳态 batch 数必须大于 0")
        if self.max_total_seconds <= self.warmup_seconds:
            raise ValueError("总时间上限必须大于预热时长")


@dataclass(frozen=True, slots=True)
class RequestRecord:
    instance_id: str
    sequence: int
    stream: str
    phase: str
    started_offset_seconds: float
    elapsed_seconds: float
    replenish_delay_seconds: float
    category: str
    status_code: int | None


@dataclass(frozen=True, slots=True)
class InflightSample:
    offset_seconds: float
    actual: Mapping[str, int]
    target: Mapping[str, int]


@dataclass(slots=True)
class _MutableState:
    started_at: float
    warmup_deadline: float
    steady_deadline: float
    hard_deadline: float
    in_flight: dict[str, int]
    records: list[RequestRecord] = field(default_factory=list)
    inflight_samples: list[InflightSample] = field(default_factory=list)
    stop_reason: str | None = None
    steady_success: int = 0
    sequence: int = 0
    last_completion: dict[str, float] = field(default_factory=dict)
    latency_window: deque[float] = field(default_factory=lambda: deque(maxlen=200))
    warmup_latencies: list[float] = field(default_factory=list)
    low_inflight_started: float | None = None


@dataclass(frozen=True, slots=True)
class LoadRunResult:
    status: str
    reason: str
    started_at: str
    finished_at: str
    elapsed_seconds: float
    config: Mapping[str, Any]
    summary: Mapping[str, Any]
    records: tuple[RequestRecord, ...]
    inflight_samples: tuple[InflightSample, ...]

    def to_document(self) -> JsonObject:
        return {
            "schema_version": 1,
            "evidence_type": "vbas_sustained_load",
            "status": self.status,
            "reason": self.reason,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": self.elapsed_seconds,
            "config": dict(self.config),
            "summary": dict(self.summary),
            "records": [asdict(item) for item in self.records],
            "inflight_samples": [asdict(item) for item in self.inflight_samples],
        }


async def run_sustained_load(
    *,
    targets: Sequence[VbasTarget],
    manifest: FixtureManifest,
    fixture_root_in_container: str,
    config: LoadRunConfig,
    guardrail_probe: GuardrailHook | None = None,
    sample_hook: SampleHook | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LoadRunResult:
    if not targets:
        raise ValueError("至少需要一个 VBas 目标")
    if len({target.instance_id for target in targets}) != len(targets):
        raise ValueError("VBas 目标 instance_id 不能重复")
    available = manifest.entries_for(config.mode)
    if not available:
        raise ValueError(f"fixture manifest 不支持 {config.mode.value} 模式")

    loop = asyncio.get_running_loop()
    started = loop.time()
    state = _MutableState(
        started_at=started,
        warmup_deadline=started + config.warmup_seconds,
        steady_deadline=started + config.warmup_seconds + config.steady_seconds,
        hard_deadline=started + config.max_total_seconds,
        in_flight={target.instance_id: 0 for target in targets},
    )
    state_lock = asyncio.Lock()
    stop_event = asyncio.Event()
    wall_started = _utc_now()
    limits = httpx.Limits(
        max_connections=sum(config.concurrency_per_instance for _ in targets) + 8,
        max_keepalive_connections=sum(config.concurrency_per_instance for _ in targets),
    )
    timeout = httpx.Timeout(config.request_timeout_seconds)

    async with httpx.AsyncClient(
        transport=transport,
        timeout=timeout,
        limits=limits,
    ) as client:
        workers = [
            asyncio.create_task(
                _worker(
                    client=client,
                    target=target,
                    manifest=manifest,
                    fixture_root_in_container=fixture_root_in_container,
                    config=config,
                    state=state,
                    state_lock=state_lock,
                    stop_event=stop_event,
                    worker_index=worker_index,
                ),
                name=f"vbas-benchmark-{target.instance_id}-{worker_index}",
            )
            for target in targets
            for worker_index in range(config.concurrency_per_instance)
        ]
        monitor = asyncio.create_task(
            _monitor(
                config=config,
                state=state,
                state_lock=state_lock,
                stop_event=stop_event,
                guardrail_probe=guardrail_probe,
                sample_hook=sample_hook,
            ),
            name="vbas-benchmark-monitor",
        )
        await monitor
        try:
            await asyncio.wait_for(
                asyncio.gather(*workers, return_exceptions=True),
                timeout=config.drain_timeout_seconds,
            )
        except TimeoutError:
            state.stop_reason = state.stop_reason or "优雅排空超时"
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    elapsed = loop.time() - started
    summary = summarize_records(
        state.records,
        state.inflight_samples,
        targets=targets,
        config=config,
    )
    passed = state.stop_reason == "完成稳态时长和最小 batch 数"
    return LoadRunResult(
        status="passed" if passed else "failed",
        reason=state.stop_reason or "未知停止原因",
        started_at=wall_started,
        finished_at=_utc_now(),
        elapsed_seconds=elapsed,
        config={
            "mode": config.mode.value,
            "concurrency_per_instance": config.concurrency_per_instance,
            "batch_size": config.batch_size,
            "warmup_seconds": config.warmup_seconds,
            "steady_seconds": config.steady_seconds,
            "min_steady_batches": config.min_steady_batches,
            "targets": [target.instance_id for target in targets],
            "seed": config.seed,
        },
        summary=summary,
        records=tuple(state.records),
        inflight_samples=tuple(state.inflight_samples),
    )


async def _worker(
    *,
    client: httpx.AsyncClient,
    target: VbasTarget,
    manifest: FixtureManifest,
    fixture_root_in_container: str,
    config: LoadRunConfig,
    state: _MutableState,
    state_lock: asyncio.Lock,
    stop_event: asyncio.Event,
    worker_index: int,
) -> None:
    del worker_index
    loop = asyncio.get_running_loop()
    while not stop_event.is_set():
        async with state_lock:
            sequence = state.sequence
            state.sequence += 1
            previous_completion = state.last_completion.get(target.instance_id, loop.time())
            state.in_flight[target.instance_id] += 1
        started = loop.time()
        replenish_delay = max(0.0, started - previous_completion)
        stream, payload = build_batch_payload(
            manifest=manifest,
            fixture_root_in_container=Path(fixture_root_in_container),
            mode=config.mode,
            batch_size=config.batch_size,
            sequence=sequence,
            seed=config.seed,
        )
        status_code: int | None = None
        category = "connection_error"
        try:
            response = await client.post(
                f"{target.base_url.rstrip('/')}{endpoint_for_stream(stream)}",
                json=payload,
                headers={"X-Algorithm-Work-Type": "offline"},
            )
            status_code = response.status_code
            if response.status_code == 200:
                try:
                    category = (
                        "success"
                        if parse_successful_batch(response.json(), config.batch_size)
                        else "business_error"
                    )
                except ValueError:
                    category = "business_error"
            elif response.status_code in {429, 503}:
                category = "overload"
            else:
                category = "http_error"
        except httpx.TimeoutException:
            category = "timeout"
        except httpx.TransportError:
            category = "connection_error"
        finished = loop.time()
        phase = "warmup" if started < state.warmup_deadline else "steady"
        record = RequestRecord(
            instance_id=target.instance_id,
            sequence=sequence,
            stream=stream,
            phase=phase,
            started_offset_seconds=started - state.started_at,
            elapsed_seconds=finished - started,
            replenish_delay_seconds=replenish_delay,
            category=category,
            status_code=status_code,
        )
        async with state_lock:
            state.in_flight[target.instance_id] -= 1
            state.last_completion[target.instance_id] = finished
            state.records.append(record)
            state.latency_window.append(record.elapsed_seconds)
            if phase == "warmup" and category == "success":
                state.warmup_latencies.append(record.elapsed_seconds)
            if phase == "steady" and category == "success":
                state.steady_success += 1
        # Mock 或极低延迟端点可能同步完成，显式让出以保证护栏采样。
        await asyncio.sleep(0)


async def _monitor(
    *,
    config: LoadRunConfig,
    state: _MutableState,
    state_lock: asyncio.Lock,
    stop_event: asyncio.Event,
    guardrail_probe: GuardrailHook | None,
    sample_hook: SampleHook | None,
) -> None:
    loop = asyncio.get_running_loop()
    target_value = config.concurrency_per_instance
    while not stop_event.is_set():
        now = loop.time()
        async with state_lock:
            actual = dict(state.in_flight)
            records = tuple(state.records)
            steady_success = state.steady_success
            state.inflight_samples.append(
                InflightSample(
                    offset_seconds=now - state.started_at,
                    actual=actual,
                    target={key: target_value for key in actual},
                )
            )
        if sample_hook is not None:
            await sample_hook(
                "steady" if now >= state.warmup_deadline else "warmup",
                len(records),
                now,
            )
        reason = guardrail_probe() if guardrail_probe is not None else None
        if reason:
            state.stop_reason = reason
            stop_event.set()
            return
        observed = len(records)
        errors = sum(record.category != "success" for record in records)
        if _error_rate_exceeds_guardrail(
            observed,
            errors,
            config.guardrails.max_error_rate,
        ):
            state.stop_reason = f"错误率超过护栏: {errors}/{observed}"
            stop_event.set()
            return
        total_target = target_value * len(actual)
        total_actual = sum(actual.values())
        if (
            now >= state.warmup_deadline
            and total_actual < total_target * config.guardrails.min_inflight_ratio
        ):
            state.low_inflight_started = state.low_inflight_started or now
            if now - state.low_inflight_started >= 5.0:
                state.stop_reason = "负载器无法持续维持目标在途量"
                stop_event.set()
                return
        else:
            state.low_inflight_started = None
        replenish = [record.replenish_delay_seconds for record in records[-500:]]
        if (
            len(replenish) >= 20
            and _quantile(replenish, 0.95)
            > config.guardrails.max_replenish_p95_seconds
        ):
            state.stop_reason = "负载器补位延迟 P95 超过护栏"
            stop_event.set()
            return
        if len(state.warmup_latencies) >= 20 and len(state.latency_window) >= 50:
            baseline = _quantile(state.warmup_latencies, 0.95)
            current = _quantile(tuple(state.latency_window), 0.95)
            if (
                baseline > 0
                and current / baseline
                > config.guardrails.max_latency_degradation_ratio
            ):
                state.stop_reason = "尾延迟相对预热基线持续恶化"
                stop_event.set()
                return
        if now >= state.steady_deadline and steady_success >= config.min_steady_batches:
            state.stop_reason = "完成稳态时长和最小 batch 数"
            stop_event.set()
            return
        if now >= state.hard_deadline:
            state.stop_reason = "达到测试总时间硬上限"
            stop_event.set()
            return
        await asyncio.sleep(config.sample_interval_seconds)


def _error_rate_exceeds_guardrail(
    observed: int,
    errors: int,
    max_error_rate: float,
) -> bool:
    minimum_samples = (
        1 if max_error_rate == 0 else max(20, math.ceil(1 / max_error_rate))
    )
    return observed >= minimum_samples and errors / observed > max_error_rate


def summarize_records(
    records: Sequence[RequestRecord],
    inflight_samples: Sequence[InflightSample],
    *,
    targets: Sequence[VbasTarget],
    config: LoadRunConfig,
) -> JsonObject:
    steady = [record for record in records if record.phase == "steady"]
    success = [record for record in steady if record.category == "success"]
    latencies = [record.elapsed_seconds for record in success]
    replenish = [record.replenish_delay_seconds for record in steady]
    categories = Counter(record.category for record in records)
    steady_categories = Counter(record.category for record in steady)
    if steady:
        started = min(record.started_offset_seconds for record in steady)
        finished = max(record.started_offset_seconds + record.elapsed_seconds for record in steady)
        steady_elapsed = max(finished - started, 1e-9)
    else:
        steady_elapsed = 0.0
    by_instance = {
        target.instance_id: sum(
            record.category == "success" and record.instance_id == target.instance_id
            for record in steady
        )
        for target in targets
    }
    ratios = [
        sum(sample.actual.values()) / max(1, sum(sample.target.values()))
        for sample in inflight_samples
        if sample.offset_seconds >= config.warmup_seconds
    ]
    return {
        "steady_batch_count": len(steady),
        "steady_success_count": len(success),
        "batch_per_second": len(success) / steady_elapsed if steady_elapsed else 0.0,
        "frame_per_second": (
            len(success) * config.batch_size / steady_elapsed if steady_elapsed else 0.0
        ),
        "latency_p50_seconds": _quantile(latencies, 0.50),
        "latency_p95_seconds": _quantile(latencies, 0.95),
        "latency_p99_seconds": _quantile(latencies, 0.99),
        "replenish_p95_seconds": _quantile(replenish, 0.95),
        "categories": dict(sorted(categories.items())),
        "steady_categories": dict(sorted(steady_categories.items())),
        "success_by_instance": by_instance,
        "mean_inflight_ratio": statistics.fmean(ratios) if ratios else 0.0,
        "steady_elapsed_seconds": steady_elapsed,
    }


def _quantile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
