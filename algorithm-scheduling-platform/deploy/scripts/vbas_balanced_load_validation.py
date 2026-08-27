#!/usr/bin/env python3
"""里程碑 2B VBas 三实例真实均衡验证器。

图片字节只存在于内存和北向请求中。证据仅保留标识、大小、摘要、状态、租约归属与
资源计数，避免把 Base64、媒体内容或完整算法结果写入报告。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import resource
import secrets
import subprocess
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

JsonObject = dict[str, Any]
VBAS_INSTANCE_IDS = ("vbas-gpu0", "vbas-gpu1", "vbas-gpu2")
TERMINAL_STATUSES = frozenset({60, 70, 80})
SUCCESS_STATUS = 60
VISION_SOURCE = "vision-orchestrator-service"
ONLINE_SOURCE = "online-gateway-service"
STUDENT_VIDEO_URL = (
    "http://192.168.29.12:5555/course/"
    "%E4%B8%9C%E5%8D%97%E5%A4%A7%E5%AD%A6-%E6%9D%8E%E9%AA%8F%E6%89%AC/"
    "%E8%AE%A1%E7%AE%97%E6%80%9D%E7%BB%B4%E4%B8%8E%E7%A8%8B%E5%BA%8F%E5%AE%9E%E8%B7%B5II_"
    "202520263B61G060201_%E6%9D%8E%E9%AA%8F%E6%89%AC_"
    "2026%E5%B9%B45%E6%9C%8821%E5%8F%B714%E6%97%B60%E5%88%86/"
    "%E5%AD%A6%E7%94%9F1.mp4"
)
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}")


class ValidationError(RuntimeError):
    """表示验证门槛未满足；错误文本可直接写入中文证据。"""


@dataclass(frozen=True, slots=True)
class HttpObservation:
    status_code: int
    body: object
    elapsed_seconds: float


class AsyncHttp(Protocol):
    async def get(self, url: str) -> HttpObservation: ...

    async def post(
        self,
        url: str,
        body: Mapping[str, object],
        headers: Mapping[str, str],
    ) -> HttpObservation: ...


class HttpxTransport:
    def __init__(self, *, timeout_seconds: float) -> None:
        timeout = httpx.Timeout(timeout_seconds)
        self._client = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_connections=2048, max_keepalive_connections=512),
        )

    async def __aenter__(self) -> HttpxTransport:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, **kwargs: object) -> HttpObservation:
        started = time.perf_counter()
        try:
            response = await self._client.request(method, url, **kwargs)
            try:
                body: object = response.json()
            except ValueError:
                body = response.text
            return HttpObservation(response.status_code, body, time.perf_counter() - started)
        except httpx.TimeoutException as exc:
            raise ValidationError("北向请求超时") from exc
        except httpx.TransportError as exc:
            raise ValidationError(f"北向连接失败: {type(exc).__name__}") from exc

    async def get(self, url: str) -> HttpObservation:
        return await self._request("GET", url)

    async def post(
        self,
        url: str,
        body: Mapping[str, object],
        headers: Mapping[str, str],
    ) -> HttpObservation:
        return await self._request("POST", url, json=body, headers=headers)


def _origin(value: str, port: int) -> str:
    parsed = urlsplit(value.rstrip("/"))
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.port != port
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"服务地址必须是无凭据、无路径且端口为 {port} 的 HTTP origin")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _safe_run_id(value: str) -> str:
    if SAFE_ID.fullmatch(value) is None:
        raise ValueError("run-id 必须是 1-200 位安全标识")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _business_code(observation: HttpObservation) -> int | None:
    if not isinstance(observation.body, Mapping):
        return None
    value = observation.body.get("code")
    return value if type(value) is int else None


def _response_class(observation: HttpObservation) -> str:
    code = _business_code(observation)
    if code == 0 and 200 <= observation.status_code < 300:
        return "成功"
    if code == 50301 or observation.status_code in {429, 503}:
        return "容量不足"
    if code == 50000 or observation.status_code >= 500:
        return "服务错误"
    return "业务拒绝"


_PROMETHEUS_SAMPLE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)\{(?P<labels>[^}]*)\} (?P<value>[0-9.eE+-]+)$"
)
_PROMETHEUS_LABEL = re.compile(r'(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<value>[^"]*)"')


def parse_gateway_metrics(text: object) -> dict[str, dict[str, int]]:
    if not isinstance(text, str):
        raise ValidationError("Online Gateway 指标响应不是文本")
    counters: dict[str, Counter[str]] = {
        "lease_acquired": Counter(),
        "lease_released": Counter(),
        "operator_requests": Counter(),
        "operator_errors": Counter(),
    }
    for line in text.splitlines():
        match = _PROMETHEUS_SAMPLE.fullmatch(line.strip())
        if match is None:
            continue
        labels = {
            item.group("name"): item.group("value")
            for item in _PROMETHEUS_LABEL.finditer(match.group("labels"))
        }
        try:
            value = float(match.group("value"))
        except ValueError:
            continue
        if not value.is_integer() or value < 0:
            continue
        name = match.group("name")
        instance_id = labels.get("instance_id")
        if instance_id not in VBAS_INSTANCE_IDS:
            continue
        if (
            name == "algorithm_capacity_lease_events_total"
            and labels.get("capability") == "student_behavior"
        ):
            outcome = labels.get("outcome")
            if outcome == "acquired":
                counters["lease_acquired"][instance_id] = int(value)
            elif outcome == "released":
                counters["lease_released"][instance_id] = int(value)
        elif (
            name == "algorithm_operator_request_latency_seconds_count"
            and labels.get("operator_code") == "vbas"
            and labels.get("capability") == "student_behavior"
        ):
            counters["operator_requests"][instance_id] = int(value)
        elif (
            name == "algorithm_operator_request_errors_total"
            and labels.get("operator_code") == "vbas"
            and labels.get("capability") == "student_behavior"
        ):
            counters["operator_errors"][instance_id] = int(value)
    return {name: dict(values) for name, values in counters.items()}


def gateway_metric_delta(
    before: Mapping[str, Mapping[str, int]],
    after: Mapping[str, Mapping[str, int]],
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for metric_name in ("lease_acquired", "lease_released", "operator_requests", "operator_errors"):
        prior = before.get(metric_name, {})
        current = after.get(metric_name, {})
        values: dict[str, int] = {}
        for instance_id in VBAS_INSTANCE_IDS:
            delta = current.get(instance_id, 0) - prior.get(instance_id, 0)
            if delta < 0:
                raise ValidationError("Online Gateway 指标计数器在验证期间回退")
            values[instance_id] = delta
        result[metric_name] = values
    return result


def validate_gateway_metric_delta(delta: Mapping[str, Mapping[str, int]], expected: int) -> None:
    for metric_name in ("lease_acquired", "lease_released", "operator_requests"):
        values = delta.get(metric_name)
        if not isinstance(values, Mapping) or sum(values.values()) != expected:
            raise ValidationError(f"Online Gateway 的 {metric_name} 增量与成功请求数不一致")
        if any(values.get(instance_id, 0) <= 0 for instance_id in VBAS_INSTANCE_IDS):
            raise ValidationError(f"Online Gateway 的 {metric_name} 没有覆盖三个实例")
    errors = delta.get("operator_errors")
    if not isinstance(errors, Mapping) or sum(errors.values()) != 0:
        raise ValidationError("Online Gateway 在验证期间记录了 VBas 调用错误")


async def _gateway_metrics(http: AsyncHttp, gateway_origin: str) -> dict[str, dict[str, int]]:
    observation = await http.get(f"{gateway_origin}/metrics")
    if observation.status_code != 200:
        raise ValidationError("Online Gateway 指标接口没有返回 HTTP 200")
    return parse_gateway_metrics(observation.body)


def build_student_submission(task_id: str) -> JsonObject:
    """冻结 A 服务既有字段，除 task_id 外不得按验证场景改写。"""

    return {
        "task_id": task_id,
        "task_types": ["STUDENT_BEHAVIOR"],
        "priority": "NORMAL",
        "teacher_video_path": "",
        "student_video_path": STUDENT_VIDEO_URL,
        "slides_video_path": "",
        "front_points": [
            {"X": 0, "Y": 0},
            {"X": 1920, "Y": 0},
            {"X": 1920, "Y": 540},
            {"X": 0, "Y": 540},
        ],
        "back_point": [
            {"X": 0, "Y": 540},
            {"X": 1920, "Y": 540},
            {"X": 1920, "Y": 1080},
            {"X": 0, "Y": 1080},
        ],
        "student_count": 70,
        "asr_options": None,
    }


def build_online_request(image_id: str, encoded: str) -> JsonObject:
    return {
        "stream_type": "student",
        "ImageList": [{"ImageId": image_id, "StoragePath": encoded}],
    }


def _task_ids(run_id: str, scenario: str, count: int = 20) -> tuple[str, ...]:
    return tuple(f"{run_id}-{scenario.lower()}-{index:03d}" for index in range(1, count + 1))


def _course_summary(body: object, task_id: str) -> JsonObject:
    if not isinstance(body, Mapping) or body.get("code") != 0:
        raise ValidationError(f"课程 {task_id} 查询没有返回业务成功")
    data = body.get("data")
    if not isinstance(data, Mapping) or data.get("task_id") != task_id:
        raise ValidationError(f"课程 {task_id} 查询响应身份不匹配")
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        raise ValidationError(f"课程 {task_id} 查询响应缺少任务字典")
    selected = next(
        (
            item
            for item in tasks
            if isinstance(item, Mapping) and item.get("task_type") == "STUDENT_BEHAVIOR"
        ),
        None,
    )
    if not isinstance(selected, Mapping) or type(selected.get("status")) is not int:
        raise ValidationError(f"课程 {task_id} 缺少学生行为整数状态")
    nodes = selected.get("nodes")
    node_summary = []
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, Mapping):
                continue
            node_summary.append(
                {
                    "node_code": node.get("node_code"),
                    "status": node.get("status"),
                    "reason": node.get("reason"),
                    "progress": node.get("progress"),
                }
            )
    return {
        "task_id": task_id,
        "task_type": "STUDENT_BEHAVIOR",
        "status": selected["status"],
        "reason": selected.get("reason"),
        "nodes": node_summary,
    }


@dataclass(slots=True)
class LeaseFact:
    lease_id: str
    instance_id: str
    capability: str
    source_service: str | None
    work_type: str | None
    work_id: str | None
    task_id: str | None
    batch_id: str | None
    trace_id: str | None
    first_seen_at: str
    last_seen_at: str
    disappeared_at: str | None = None
    renewals_observed: int = 0
    _expires_at: str | None = field(default=None, repr=False)

    def public(self) -> JsonObject:
        return {
            "lease_id": self.lease_id,
            "instance_id": self.instance_id,
            "capability": self.capability,
            "source_service": self.source_service,
            "work_type": self.work_type,
            "work_id": self.work_id,
            "task_id": self.task_id,
            "batch_id": self.batch_id,
            "trace_id": self.trace_id,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "disappeared_at": self.disappeared_at,
            "renewals_observed": self.renewals_observed,
        }


@dataclass(slots=True)
class EvidenceTracker:
    instance_ids: tuple[str, ...]
    leases: dict[str, LeaseFact] = field(default_factory=dict)
    instance_peak_active: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    instance_peak_reported: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    declared_capacity: dict[str, int] = field(default_factory=dict)
    change_samples: list[JsonObject] = field(default_factory=list)
    probe_failures: list[str] = field(default_factory=list)
    _last_signature: object = field(default=None, repr=False)

    def observe(self, snapshots: object, lease_documents: Sequence[object], queues: object) -> None:
        observed_at = _now()
        if not isinstance(snapshots, list):
            raise ValidationError("Control 容量快照不是数组")
        by_instance: dict[str, Mapping[str, object]] = {}
        for item in snapshots:
            if not isinstance(item, Mapping) or item.get("instance_id") not in self.instance_ids:
                continue
            instance_id = str(item["instance_id"])
            if item.get("operator_code") != "vbas":
                raise ValidationError(f"实例 {instance_id} 的 operator_code 不是 vbas")
            for key in ("declared_capacity", "reported_inflight", "active_lease_count"):
                if type(item.get(key)) is not int or int(item[key]) < 0:
                    raise ValidationError(f"实例 {instance_id} 的 {key} 不合法")
            capacity = int(item["declared_capacity"])
            if capacity <= 0:
                raise ValidationError(f"实例 {instance_id} 的声明容量不是正数")
            by_instance[instance_id] = item
            self.declared_capacity[instance_id] = capacity
            self.instance_peak_active[instance_id] = max(
                self.instance_peak_active[instance_id], int(item["active_lease_count"])
            )
            self.instance_peak_reported[instance_id] = max(
                self.instance_peak_reported[instance_id], int(item["reported_inflight"])
            )
            if int(item["active_lease_count"]) > capacity:
                raise ValidationError(f"实例 {instance_id} 出现租约超卖")
        if set(by_instance) != set(self.instance_ids):
            raise ValidationError("Control 未返回完整的三个 VBas 实例")

        active_ids: set[str] = set()
        for raw in lease_documents:
            if not isinstance(raw, Mapping):
                raise ValidationError("Control 活跃租约响应不是对象")
            instance_id = raw.get("instance_id")
            leases = raw.get("leases")
            if instance_id not in self.instance_ids or not isinstance(leases, list):
                raise ValidationError("Control 活跃租约响应结构错误")
            for lease in leases:
                if not isinstance(lease, Mapping):
                    raise ValidationError("Control 活跃租约项不是对象")
                lease_id = lease.get("lease_id")
                if not isinstance(lease_id, str) or not lease_id:
                    raise ValidationError("Control 活跃租约缺少 lease_id")
                context = lease.get("work_context")
                context = context if isinstance(context, Mapping) else {}
                active_ids.add(lease_id)
                expires_at = (
                    lease.get("expires_at") if isinstance(lease.get("expires_at"), str) else None
                )
                existing = self.leases.get(lease_id)
                if existing is None:
                    self.leases[lease_id] = LeaseFact(
                        lease_id=lease_id,
                        instance_id=str(instance_id),
                        capability=str(lease.get("capability", "")),
                        source_service=_optional_string(context.get("source_service")),
                        work_type=_optional_string(context.get("work_type")),
                        work_id=_optional_string(context.get("work_id")),
                        task_id=_optional_string(context.get("task_id")),
                        batch_id=_optional_string(context.get("item_id")),
                        trace_id=_optional_string(context.get("trace_id")),
                        first_seen_at=observed_at,
                        last_seen_at=observed_at,
                        _expires_at=expires_at,
                    )
                else:
                    existing.last_seen_at = observed_at
                    existing.disappeared_at = None
                    if expires_at is not None and existing._expires_at not in {None, expires_at}:
                        existing.renewals_observed += 1
                    existing._expires_at = expires_at
        for lease_id, lease in self.leases.items():
            if lease_id not in active_ids and lease.disappeared_at is None:
                lease.disappeared_at = observed_at

        queue_summary = _queue_summary(queues)
        signature = (
            tuple(
                (
                    instance_id,
                    int(by_instance[instance_id]["active_lease_count"]),
                    int(by_instance[instance_id]["reported_inflight"]),
                )
                for instance_id in self.instance_ids
            ),
            tuple(sorted(active_ids)),
            tuple(sorted(queue_summary.items())),
        )
        if signature != self._last_signature:
            self.change_samples.append(
                {
                    "recorded_at": observed_at,
                    "instances": {
                        instance_id: {
                            "active_leases": int(by_instance[instance_id]["active_lease_count"]),
                            "reported_inflight": int(by_instance[instance_id]["reported_inflight"]),
                            "declared_capacity": int(by_instance[instance_id]["declared_capacity"]),
                        }
                        for instance_id in self.instance_ids
                    },
                    "active_lease_ids": sorted(active_ids),
                    "queues": queue_summary,
                }
            )
            self._last_signature = signature

    def instances_for_source(self, source: str) -> set[str]:
        return {fact.instance_id for fact in self.leases.values() if fact.source_service == source}

    def current_source_count(self, source: str) -> int:
        return sum(
            1
            for fact in self.leases.values()
            if fact.source_service == source and fact.disappeared_at is None
        )

    def all_converged(self) -> bool:
        if not self.change_samples:
            return False
        latest = self.change_samples[-1]
        instances = latest["instances"]
        assert isinstance(instances, Mapping)
        return all(
            isinstance(item, Mapping)
            and item.get("active_leases") == 0
            and item.get("reported_inflight") == 0
            for item in instances.values()
        )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _queue_summary(raw: object) -> dict[str, int]:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("queues"), list):
        raise ValidationError("Control 队列响应结构错误")
    summary: Counter[str] = Counter()
    for item in raw["queues"]:
        if not isinstance(item, Mapping) or type(item.get("count")) is not int:
            raise ValidationError("Control 队列项结构错误")
        key = f"{item.get('capability') or 'none'}:{item.get('status')}"
        summary[key] += int(item["count"])
    outbox = raw.get("outbox_pending")
    if type(outbox) is not int:
        raise ValidationError("Control outbox_pending 结构错误")
    summary["outbox_pending"] = outbox
    return dict(sorted(summary.items()))


class LeaseSampler:
    def __init__(
        self,
        http: AsyncHttp,
        control_origin: str,
        tracker: EvidenceTracker,
        *,
        interval_seconds: float,
    ) -> None:
        self.http = http
        self.control_origin = control_origin
        self.tracker = tracker
        self.interval_seconds = interval_seconds
        self.stop_event = asyncio.Event()
        self.ready_event = asyncio.Event()

    async def sample_once(self) -> None:
        urls = [
            f"{self.control_origin}/ops/operator-instances/snapshot",
            f"{self.control_origin}/ops/queues",
            *(
                f"{self.control_origin}/ops/operator-instances/{instance_id}/active-leases"
                for instance_id in self.tracker.instance_ids
            ),
        ]
        observations = await asyncio.gather(*(self.http.get(url) for url in urls))
        if any(item.status_code != 200 for item in observations):
            raise ValidationError("Control 观测接口没有全部返回 HTTP 200")
        self.tracker.observe(
            observations[0].body,
            [item.body for item in observations[2:]],
            observations[1].body,
        )

    async def run(self) -> None:
        try:
            await self.sample_once()
            self.ready_event.set()
            while not self.stop_event.is_set():
                await asyncio.sleep(self.interval_seconds)
                await self.sample_once()
        except BaseException as exc:
            self.tracker.probe_failures.append(f"租约采集失败: {type(exc).__name__}")
            self.ready_event.set()
            raise


@dataclass(slots=True)
class BurstResult:
    records: list[JsonObject]
    peak_client_inflight: int
    released_count: int


async def _run_synchronized_posts(
    http: AsyncHttp,
    url: str,
    requests: Sequence[tuple[str, Mapping[str, object], Mapping[str, str]]],
) -> BurstResult:
    ready = 0
    active = 0
    peak = 0
    released_count = 0
    lock = asyncio.Lock()
    release = asyncio.Event()
    all_ready = asyncio.Event()

    async def execute(
        request_id: str, body: Mapping[str, object], headers: Mapping[str, str]
    ) -> JsonObject:
        nonlocal ready, active, peak, released_count
        async with lock:
            ready += 1
            if ready == len(requests):
                all_ready.set()
        await release.wait()
        async with lock:
            active += 1
            peak = max(peak, active)
        try:
            observation = await http.post(url, body, headers)
            return {
                "request_id": request_id,
                "http_status": observation.status_code,
                "business_code": _business_code(observation),
                "classification": _response_class(observation),
                "elapsed_seconds": round(observation.elapsed_seconds, 6),
            }
        except ValidationError as exc:
            return {
                "request_id": request_id,
                "http_status": None,
                "business_code": None,
                "classification": "负载机或网络错误",
                "reason": str(exc),
            }
        finally:
            async with lock:
                active -= 1

    tasks = [asyncio.create_task(execute(*request)) for request in requests]
    await asyncio.wait_for(all_ready.wait(), timeout=30)
    released_count = ready
    release.set()
    records = await asyncio.gather(*tasks)
    return BurstResult(records, peak, released_count)


def _accepted_submission(record: Mapping[str, object]) -> bool:
    return record.get("classification") == "成功" and record.get("business_code") == 0


async def _submit_offline(
    http: AsyncHttp,
    control_origin: str,
    run_id: str,
    scenario: str,
) -> tuple[tuple[str, ...], BurstResult]:
    task_ids = _task_ids(run_id, scenario)
    requests = [
        (
            task_id,
            build_student_submission(task_id),
            {"X-Trace-ID": f"{run_id}-{scenario.lower()}-submit-{index:03d}"},
        )
        for index, task_id in enumerate(task_ids, start=1)
    ]
    result = await _run_synchronized_posts(
        http,
        f"{control_origin}/api/course-jobs",
        requests,
    )
    if len({item["request_id"] for item in result.records}) != 20:
        raise ValidationError("20 个离线任务的 task_id 不唯一")
    if not all(_accepted_submission(item) for item in result.records):
        raise ValidationError("20 个离线任务没有全部受理或幂等复用")
    return task_ids, result


async def _query_courses(
    http: AsyncHttp,
    control_origin: str,
    task_ids: Sequence[str],
) -> list[JsonObject]:
    observations = await asyncio.gather(
        *(http.get(f"{control_origin}/api/course-jobs/{task_id}") for task_id in task_ids)
    )
    return [
        _course_summary(item.body, task_id)
        for item, task_id in zip(observations, task_ids, strict=True)
    ]


async def _wait_courses(
    http: AsyncHttp,
    control_origin: str,
    task_ids: Sequence[str],
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> list[JsonObject]:
    deadline = time.monotonic() + timeout_seconds
    last: list[JsonObject] = []
    while time.monotonic() < deadline:
        last = await _query_courses(http, control_origin, task_ids)
        if all(int(item["status"]) in TERMINAL_STATUSES for item in last):
            if any(int(item["status"]) != SUCCESS_STATUS for item in last):
                raise ValidationError("至少一个学生行为任务进入失败或取消终态")
            return last
        await asyncio.sleep(poll_seconds)
    raise ValidationError("等待 20 个学生行为任务完成超时")


async def _wait_mixed_gate(
    tracker: EvidenceTracker,
    sampler_task: asyncio.Task[None],
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if sampler_task.done():
            await sampler_task
        if tracker.current_source_count(VISION_SOURCE) > 0 and tracker.instances_for_source(
            VISION_SOURCE
        ) == set(tracker.instance_ids):
            return
        await asyncio.sleep(0.05)
    raise ValidationError("混合场景未在期限内形成三实例离线真实租约门槛")


async def _run_online(
    http: AsyncHttp,
    gateway_origin: str,
    run_id: str,
    scenario: str,
    encoded: str,
    count: int,
) -> BurstResult:
    requests = []
    for index in range(1, count + 1):
        image_id = f"{run_id}-{scenario.lower()}-image-{index:04d}"
        trace_id = f"{run_id}-{scenario.lower()}-trace-{index:04d}"
        requests.append(
            (
                image_id,
                build_online_request(image_id, encoded),
                {"X-Trace-ID": trace_id},
            )
        )
    return await _run_synchronized_posts(
        http,
        f"{gateway_origin}/api/online/vbas/analyze",
        requests,
    )


async def _wait_convergence(
    tracker: EvidenceTracker,
    sampler_task: asyncio.Task[None],
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if sampler_task.done():
            await sampler_task
        if tracker.all_converged():
            latest = tracker.change_samples[-1]
            queues = latest.get("queues")
            if isinstance(queues, Mapping) and queues.get("outbox_pending") == 0:
                return
        await asyncio.sleep(0.1)
    raise ValidationError("VBas 活跃租约、上报在途或 Outbox 未在期限内归零")


def _validate_preflight(tracker: EvidenceTracker, *, require_idle: bool) -> None:
    latest = tracker.change_samples[-1] if tracker.change_samples else None
    if not isinstance(latest, Mapping):
        raise ValidationError("没有取得 Control 预检快照")
    instances = latest.get("instances")
    if not isinstance(instances, Mapping):
        raise ValidationError("Control 预检缺少实例数据")
    for instance_id in tracker.instance_ids:
        item = instances.get(instance_id)
        if not isinstance(item, Mapping) or item.get("declared_capacity") != 1024:
            raise ValidationError(f"实例 {instance_id} 的声明容量不是 1024")
        if require_idle and (item.get("active_leases") != 0 or item.get("reported_inflight") != 0):
            raise ValidationError(f"实例 {instance_id} 在场景开始前不空闲")


def _validate_distribution(
    tracker: EvidenceTracker,
    source: str,
    *,
    expected_minimum: int,
) -> dict[str, int]:
    facts = [fact for fact in tracker.leases.values() if fact.source_service == source]
    counts = Counter(fact.instance_id for fact in facts)
    if set(counts) != set(tracker.instance_ids):
        raise ValidationError(f"{source} 的真实租约没有覆盖三个 VBas 实例")
    if sum(counts.values()) < expected_minimum:
        raise ValidationError(f"{source} 的可关联租约数量不足")
    first_seen_at = min(item.first_seen_at for item in facts)
    first_cohort = [item for item in facts if item.first_seen_at == first_seen_at]
    if len(first_cohort) < 3 or {item.instance_id for item in first_cohort} != set(
        tracker.instance_ids
    ):
        raise ValidationError(f"{source} 首次观测租约集合没有覆盖三个实例")
    return dict(sorted(counts.items()))


def _validate_online(result: BurstResult, expected: int) -> Counter[str]:
    categories = Counter(str(item["classification"]) for item in result.records)
    if result.released_count != expected or result.peak_client_inflight != expected:
        raise ValidationError("负载机没有形成目标数量的同时在途在线请求")
    if len(result.records) != expected or categories != {"成功": expected}:
        raise ValidationError("在线请求存在容量不足、服务错误、业务拒绝或负载机错误")
    return categories


def _redacted_request_contract() -> JsonObject:
    payload = build_student_submission("<唯一-task-id>")
    return {
        "path": "/api/course-jobs",
        "method": "POST",
        "field_names": list(payload),
        "task_types": payload["task_types"],
        "student_count": payload["student_count"],
        "media_url_sha256": hashlib.sha256(STUDENT_VIDEO_URL.encode()).hexdigest(),
        "兼容结论": "A 服务请求字段、路径、整数状态和异步语义未改变",
    }


def _load_image(path: Path) -> tuple[str, JsonObject]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("在线图片 fixture 必须是普通文件")
    content = path.read_bytes()
    if not content or len(content) > 5 * 1024 * 1024:
        raise ValueError("在线图片 fixture 必须大于 0 且不超过 5 MiB")
    return base64.b64encode(content).decode(), {
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _atomic_write(path: Path, document: Mapping[str, object]) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"证据文件已存在，禁止覆盖: {path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError("证据文件写入未取得进展")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(temporary, path, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)
    os.chmod(path, 0o600)


def load_host_preflight(online_count: int) -> JsonObject:
    soft_limit, hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
    required = online_count + 256 if online_count else 256
    if soft_limit < required:
        raise ValidationError(f"负载机文件句柄软上限 {soft_limit} 小于所需值 {required}")
    return {
        "file_descriptor_soft_limit": soft_limit,
        "file_descriptor_hard_limit": hard_limit,
        "required_file_descriptors": required,
        "max_connections": 2048,
        "max_keepalive_connections": 512,
    }


def collect_local_system_evidence() -> JsonObject:
    """GPU 只作补充证据；探针失败会保留类型并由最终门禁失败关闭。"""

    commands = {
        "gpu": (
            "nvidia-smi",
            "--query-gpu=index,uuid,utilization.gpu,memory.used",
            "--format=csv,noheader,nounits",
        ),
        "processes": (
            "nvidia-smi",
            "--query-compute-apps=gpu_uuid,pid,used_memory,process_name",
            "--format=csv,noheader,nounits",
        ),
    }
    result: JsonObject = {}
    for name, command in commands.items():
        try:
            completed = subprocess.run(
                command, check=True, capture_output=True, text=True, timeout=10
            )
            result[name] = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        except (OSError, subprocess.SubprocessError) as exc:
            result[name] = {"reason": f"GPU 补充探针失败: {type(exc).__name__}"}
    return result


class SystemSampler:
    def __init__(self, *, interval_seconds: float) -> None:
        self.interval_seconds = interval_seconds
        self.stop_event = asyncio.Event()
        self.samples: list[JsonObject] = []
        self.failures: list[str] = []

    async def run(self) -> None:
        while not self.stop_event.is_set():
            sample = await asyncio.to_thread(collect_local_system_evidence)
            if any(isinstance(value, Mapping) and "reason" in value for value in sample.values()):
                self.failures.append("GPU 时序探针存在失败采样")
            sample["recorded_at"] = _now()
            self.samples.append(sample)
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass


def _docker_service_container(service: str) -> str:
    completed = subprocess.run(
        (
            "docker",
            "ps",
            "--no-trunc",
            "--filter",
            "label=com.docker.compose.project=algorithm-operators",
            "--filter",
            f"label=com.docker.compose.service={service}",
            "--format",
            "{{.ID}}",
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    ids = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(ids) != 1 or re.fullmatch(r"[0-9a-f]{64}", ids[0]) is None:
        raise ValidationError(f"无法定位唯一运行中的算子容器: {service}")
    return ids[0]


def collect_vbas_log_summary(started_at: str, task_ids: Sequence[str]) -> JsonObject:
    summary: JsonObject = {}
    for instance_id in VBAS_INSTANCE_IDS:
        try:
            container_id = _docker_service_container(instance_id)
            completed = subprocess.run(
                ("docker", "logs", "--since", started_at, container_id),
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            text = completed.stdout + completed.stderr
            matched_tasks = sorted(task_id for task_id in task_ids if task_id in text)
            summary[instance_id] = {
                "container_id": container_id,
                "student_batches_accepted": text.count("收到学生推理批次"),
                "student_batches_rejected": text.count("拒绝学生推理批次"),
                "student_batches_failed": text.count("学生推理批次失败"),
                "matched_task_ids": matched_tasks,
            }
        except (OSError, subprocess.SubprocessError, ValidationError) as exc:
            summary[instance_id] = {"reason": f"VBas 容器日志摘要采集失败: {type(exc).__name__}"}
    return summary


def validate_vbas_log_summary(
    summary: Mapping[str, object],
    *,
    scenario: str,
    task_ids: Sequence[str],
    online_count: int,
) -> None:
    if set(summary) != set(VBAS_INSTANCE_IDS):
        raise ValidationError("VBas 日志摘要没有覆盖三个实例")
    accepted_total = 0
    matched_tasks: set[str] = set()
    for instance_id in VBAS_INSTANCE_IDS:
        item = summary.get(instance_id)
        if not isinstance(item, Mapping) or "reason" in item:
            raise ValidationError(f"实例 {instance_id} 的日志摘要不可用")
        accepted = item.get("student_batches_accepted")
        rejected = item.get("student_batches_rejected")
        failed = item.get("student_batches_failed")
        if any(type(value) is not int or value < 0 for value in (accepted, rejected, failed)):
            raise ValidationError(f"实例 {instance_id} 的日志计数不合法")
        assert isinstance(accepted, int) and isinstance(rejected, int) and isinstance(failed, int)
        if accepted <= 0:
            raise ValidationError(f"实例 {instance_id} 没有接受真实学生检测批次")
        if rejected or failed:
            raise ValidationError(f"实例 {instance_id} 出现拒绝或失败批次")
        accepted_total += accepted
        raw_tasks = item.get("matched_task_ids")
        if not isinstance(raw_tasks, list) or not all(
            isinstance(value, str) for value in raw_tasks
        ):
            raise ValidationError(f"实例 {instance_id} 的任务关联摘要不合法")
        matched_tasks.update(raw_tasks)
    if scenario == "online1000" and accepted_total != online_count:
        raise ValidationError("三个 VBas 容器的在线接受批次数与成功请求数不一致")
    if scenario == "mixed" and accepted_total < online_count:
        raise ValidationError("混合场景的 VBas 接受批次数少于在线成功请求数")
    if scenario in {"offline20", "mixed"} and matched_tasks != set(task_ids):
        raise ValidationError("VBas 容器日志没有关联全部 20 个离线 task_id")


def collect_kafka_lag() -> int:
    completed = subprocess.run(
        (
            "docker",
            "ps",
            "--no-trunc",
            "--filter",
            "label=com.docker.compose.project=algorithm-scheduling-platform",
            "--filter",
            "label=com.docker.compose.service=kafka",
            "--format",
            "{{.ID}}",
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    ids = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(ids) != 1 or re.fullmatch(r"[0-9a-f]{64}", ids[0]) is None:
        raise ValidationError("无法定位唯一运行中的 Kafka 容器")
    result = subprocess.run(
        (
            "docker",
            "exec",
            ids[0],
            "/opt/kafka/bin/kafka-consumer-groups.sh",
            "--bootstrap-server",
            "kafka:29092",
            "--describe",
            "--all-groups",
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    total = 0
    observed = 0
    lag_index: int | None = None
    for line in result.stdout.splitlines():
        fields = line.split()
        if "LAG" in fields:
            lag_index = fields.index("LAG")
            continue
        if lag_index is None or len(fields) <= lag_index or not fields[lag_index].isdigit():
            continue
        total += int(fields[lag_index])
        observed += 1
    if observed == 0:
        raise ValidationError("Kafka lag 探针没有取得任何消费组分区")
    return total


@dataclass(frozen=True, slots=True)
class RunConfig:
    scenario: str
    run_id: str
    control_origin: str
    gateway_origin: str
    image_path: Path | None
    online_count: int
    task_timeout_seconds: float
    mixed_gate_timeout_seconds: float
    convergence_timeout_seconds: float
    course_poll_seconds: float
    lease_poll_seconds: float
    system_poll_seconds: float


async def run_validation(config: RunConfig, http: AsyncHttp) -> JsonObject:
    started_at = _now()
    tracker = EvidenceTracker(VBAS_INSTANCE_IDS)
    sampler = LeaseSampler(
        http,
        config.control_origin,
        tracker,
        interval_seconds=config.lease_poll_seconds,
    )
    sampler_task = asyncio.create_task(sampler.run())
    system_sampler = SystemSampler(interval_seconds=config.system_poll_seconds)
    system_task = asyncio.create_task(system_sampler.run())
    offline_result: BurstResult | None = None
    online_result: BurstResult | None = None
    course_results: list[JsonObject] = []
    image_evidence: JsonObject | None = None
    gateway_delta: dict[str, dict[str, int]] | None = None
    kafka_lag_before: int | None = None
    kafka_lag_after: int | None = None
    task_ids: tuple[str, ...] = ()
    scenario_reason = "全部验证门槛通过"
    passed = False
    error: str | None = None
    try:
        await asyncio.wait_for(sampler.ready_event.wait(), timeout=10)
        if sampler_task.done():
            await sampler_task
        _validate_preflight(tracker, require_idle=True)
        preflight = load_host_preflight(
            config.online_count if config.scenario != "offline20" else 0
        )
        kafka_lag_before = await asyncio.to_thread(collect_kafka_lag)

        encoded = None
        if config.scenario in {"online1000", "mixed"}:
            if config.image_path is None:
                raise ValidationError("在线或混合场景必须提供 --image-file")
            encoded, image_evidence = _load_image(config.image_path)

        if config.scenario in {"offline20", "mixed"}:
            task_ids, offline_result = await _submit_offline(
                http, config.control_origin, config.run_id, config.scenario
            )
            course_results = await _query_courses(http, config.control_origin, task_ids)

        if config.scenario == "mixed":
            await _wait_mixed_gate(
                tracker,
                sampler_task,
                timeout_seconds=config.mixed_gate_timeout_seconds,
            )

        if config.scenario in {"online1000", "mixed"}:
            assert encoded is not None
            gateway_before = await _gateway_metrics(http, config.gateway_origin)
            online_result = await _run_online(
                http,
                config.gateway_origin,
                config.run_id,
                config.scenario,
                encoded,
                config.online_count,
            )
            _validate_online(online_result, config.online_count)
            gateway_after = await _gateway_metrics(http, config.gateway_origin)
            gateway_delta = gateway_metric_delta(gateway_before, gateway_after)
            validate_gateway_metric_delta(gateway_delta, config.online_count)

        if task_ids:
            course_results = await _wait_courses(
                http,
                config.control_origin,
                task_ids,
                timeout_seconds=config.task_timeout_seconds,
                poll_seconds=config.course_poll_seconds,
            )

        await _wait_convergence(
            tracker,
            sampler_task,
            timeout_seconds=config.convergence_timeout_seconds,
        )
        kafka_lag_after = await asyncio.to_thread(collect_kafka_lag)
        if kafka_lag_after != 0:
            raise ValidationError("Kafka lag 在验证结束后没有收敛为 0")
        if config.scenario in {"offline20", "mixed"}:
            _validate_distribution(tracker, VISION_SOURCE, expected_minimum=3)
        if config.scenario in {"online1000", "mixed"}:
            # 每个成功在线响应都证明该请求取得过租约；高频短租约不要求轮询端点逐个捕获。
            _validate_distribution(tracker, ONLINE_SOURCE, expected_minimum=3)
        passed = True
    except Exception as exc:
        error = (
            str(exc) if isinstance(exc, ValidationError) else f"验证执行异常: {type(exc).__name__}"
        )
        scenario_reason = error
    finally:
        sampler.stop_event.set()
        system_sampler.stop_event.set()
        try:
            await asyncio.wait_for(sampler_task, timeout=max(2.0, config.lease_poll_seconds * 3))
        except (TimeoutError, asyncio.CancelledError):
            sampler_task.cancel()
            await asyncio.gather(sampler_task, return_exceptions=True)
        except Exception as exc:
            if error is None:
                error = f"租约采集异常: {type(exc).__name__}"
                scenario_reason = error
                passed = False
        try:
            await asyncio.wait_for(system_task, timeout=max(2.0, config.system_poll_seconds * 2))
        except (TimeoutError, asyncio.CancelledError):
            system_task.cancel()
            await asyncio.gather(system_task, return_exceptions=True)
        except Exception as exc:
            if error is None:
                error = f"GPU 时序采集异常: {type(exc).__name__}"
                scenario_reason = error
                passed = False

    vbas_logs = await asyncio.to_thread(collect_vbas_log_summary, started_at, task_ids)
    try:
        validate_vbas_log_summary(
            vbas_logs,
            scenario=config.scenario,
            task_ids=task_ids,
            online_count=config.online_count,
        )
    except ValidationError as exc:
        passed = False
        error = error or str(exc)
        scenario_reason = error
    if system_sampler.failures:
        passed = False
        error = error or "GPU 时序探针存在失败采样"
        scenario_reason = error

    facts = sorted(tracker.leases.values(), key=lambda item: (item.first_seen_at, item.lease_id))
    by_source_instance: dict[str, Counter[str]] = defaultdict(Counter)
    for fact in facts:
        by_source_instance[fact.source_service or "未绑定"][fact.instance_id] += 1
    online_categories = Counter(
        str(item["classification"]) for item in (online_result.records if online_result else [])
    )
    offline_categories = Counter(
        str(item["classification"]) for item in (offline_result.records if offline_result else [])
    )
    return {
        "schema_version": 1,
        "evidence_type": "vbas_balanced_load_validation",
        "scenario": config.scenario,
        "run_id": config.run_id,
        "started_at": started_at,
        "finished_at": _now(),
        "status": "通过" if passed else "失败",
        "reason": scenario_reason,
        "request_contract": _redacted_request_contract(),
        "load_host_preflight": preflight if "preflight" in locals() else None,
        "image_fixture": image_evidence,
        "offline": {
            "request_count": len(offline_result.records) if offline_result else 0,
            "response_categories": dict(sorted(offline_categories.items())),
            "peak_client_inflight": offline_result.peak_client_inflight if offline_result else 0,
            "task_ids": [item["request_id"] for item in offline_result.records]
            if offline_result
            else [],
            "course_states": course_results,
        },
        "online": {
            "request_count": len(online_result.records) if online_result else 0,
            "response_categories": dict(sorted(online_categories.items())),
            "peak_client_inflight": online_result.peak_client_inflight if online_result else 0,
            "released_count": online_result.released_count if online_result else 0,
            "responses": online_result.records if online_result else [],
            "gateway_metric_delta": gateway_delta,
        },
        "routing": {
            "lease_count": len(facts),
            "by_source_and_instance": {
                source: dict(sorted(counts.items()))
                for source, counts in sorted(by_source_instance.items())
            },
            "instance_peak_active": dict(sorted(tracker.instance_peak_active.items())),
            "instance_peak_reported": dict(sorted(tracker.instance_peak_reported.items())),
            "declared_capacity": dict(sorted(tracker.declared_capacity.items())),
            "leases": [item.public() for item in facts],
            "change_samples": tracker.change_samples,
            "probe_failures": tracker.probe_failures,
        },
        "kafka_lag": {"before": kafka_lag_before, "after": kafka_lag_after},
        "vbas_log_summary": vbas_logs,
        "gpu_supplement": {
            "samples": system_sampler.samples,
            "failures": system_sampler.failures,
        },
        "failure": error,
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="执行 VBas 三实例真实均衡验证")
    parser.add_argument("--scenario", choices=("offline20", "online1000", "mixed"), required=True)
    parser.add_argument("--run-id", required=True, type=_safe_run_id)
    parser.add_argument("--control-origin", default="http://127.0.0.1:18100")
    parser.add_argument("--gateway-origin", default="http://127.0.0.1:18103")
    parser.add_argument("--image-file", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--online-count", type=int, default=1000)
    parser.add_argument("--request-timeout-seconds", type=float, default=900)
    parser.add_argument("--task-timeout-seconds", type=float, default=14400)
    parser.add_argument("--mixed-gate-timeout-seconds", type=float, default=1800)
    parser.add_argument("--convergence-timeout-seconds", type=float, default=600)
    parser.add_argument("--course-poll-seconds", type=float, default=5)
    parser.add_argument("--lease-poll-seconds", type=float, default=0.05)
    parser.add_argument("--system-poll-seconds", type=float, default=1)
    return parser.parse_args()


async def _main(arguments: argparse.Namespace) -> int:
    if arguments.online_count <= 0 or arguments.online_count > 1000:
        raise ValueError("online-count 必须位于 1-1000")
    for name in (
        "request_timeout_seconds",
        "task_timeout_seconds",
        "mixed_gate_timeout_seconds",
        "convergence_timeout_seconds",
        "course_poll_seconds",
        "lease_poll_seconds",
        "system_poll_seconds",
    ):
        if getattr(arguments, name) <= 0:
            raise ValueError(f"{name} 必须为正数")
    config = RunConfig(
        scenario=arguments.scenario,
        run_id=arguments.run_id,
        control_origin=_origin(arguments.control_origin, 18100),
        gateway_origin=_origin(arguments.gateway_origin, 18103),
        image_path=arguments.image_file,
        online_count=arguments.online_count,
        task_timeout_seconds=arguments.task_timeout_seconds,
        mixed_gate_timeout_seconds=arguments.mixed_gate_timeout_seconds,
        convergence_timeout_seconds=arguments.convergence_timeout_seconds,
        course_poll_seconds=arguments.course_poll_seconds,
        lease_poll_seconds=arguments.lease_poll_seconds,
        system_poll_seconds=arguments.system_poll_seconds,
    )
    async with HttpxTransport(timeout_seconds=arguments.request_timeout_seconds) as http:
        document = await run_validation(config, http)
    _atomic_write(arguments.output.absolute(), document)
    print(
        json.dumps(
            {
                "status": document["status"],
                "reason": document["reason"],
                "output": str(arguments.output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if document["status"] == "通过" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main(_arguments())))
    except (ValueError, FileExistsError) as exc:
        print(json.dumps({"status": "失败", "reason": str(exc)}, ensure_ascii=False))
        raise SystemExit(2) from exc
