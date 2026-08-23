from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from .core import HttpRequestSpec, NorthboundTargets, ReproducibleIdentity


class QueryMode(StrEnum):
    JITTERED = "jittered"
    HERD = "herd"


@dataclass(frozen=True)
class QuerySchedule:
    mode: QueryMode
    polling_interval_seconds: float
    offsets_seconds: tuple[float, ...]


def query_qps_tiers() -> tuple[int, ...]:
    return (50, 100, 300, 1000)


def build_query_schedule(
    identity: ReproducibleIdentity,
    case_id: str,
    *,
    task_count: int,
    polling_interval_seconds: float,
    mode: QueryMode,
) -> QuerySchedule:
    if task_count <= 0:
        raise ValueError("查询任务数必须为正数")
    if polling_interval_seconds not in {2, 5}:
        raise ValueError("轮询周期只允许 2 秒或 5 秒")
    if mode is QueryMode.HERD:
        offsets = (0.0,) * task_count
    else:
        generator = identity.random(f"{case_id}:query-jitter")
        offsets = tuple(
            round(generator.uniform(0, polling_interval_seconds * 0.2), 6)
            for _ in range(task_count)
        )
    return QuerySchedule(
        mode=mode,
        polling_interval_seconds=polling_interval_seconds,
        offsets_seconds=offsets,
    )


def build_query_requests(
    targets: NorthboundTargets,
    task_ids: Sequence[str],
    *,
    qps: int,
    duration_seconds: int,
    large_asr_task_ids: Sequence[str] = (),
) -> tuple[HttpRequestSpec, ...]:
    if qps not in query_qps_tiers() or duration_seconds <= 0:
        raise ValueError("qps 只允许 50/100/300/1000 且 duration_seconds 必须为正数")
    if not task_ids:
        raise ValueError("查询至少需要一个 task_id")
    large_asr_ids = set(large_asr_task_ids)
    if not large_asr_ids.issubset(set(task_ids)):
        raise ValueError("大 ASR 结果 task_id 必须属于查询集合")
    total = qps * duration_seconds
    return tuple(
        HttpRequestSpec(
            request_id=f"query-{index}",
            method="GET",
            url=targets.control_url(f"/api/course-jobs/{task_ids[index % len(task_ids)]}"),
            work_type=(
                "large_asr_result_query"
                if task_ids[index % len(task_ids)] in large_asr_ids
                else "course_query"
            ),
        )
        for index in range(total)
    )


def build_negative_query_mix(
    requests: Sequence[HttpRequestSpec],
    targets: NorthboundTargets,
    *,
    ratio: float,
    seed: int,
) -> tuple[HttpRequestSpec, ...]:
    if ratio not in {0.01, 0.05, 0.20} or not requests:
        raise ValueError("查询负向比例只允许 1%/5%/20% 且请求不能为空")
    count = max(1, round(len(requests) * ratio))
    selected = set(random.Random(seed).sample(range(len(requests)), count))
    return tuple(
        replace(
            request,
            url=targets.control_url(f"/api/course-jobs/load-missing-{index}"),
            work_type="negative_query:not_found",
            expected_business_rejection=True,
        )
        if index in selected
        else request
        for index, request in enumerate(requests)
    )


@dataclass(frozen=True)
class QueryValidation:
    valid: bool
    reason: str = ""


def _integer_status(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_course_query_response(body: Mapping[str, Any]) -> QueryValidation:
    if body.get("code") != 0:
        return QueryValidation(False, "业务码不是成功")
    data = body.get("data")
    if not isinstance(data, Mapping) or not isinstance(data.get("task_id"), str):
        return QueryValidation(False, "缺少课程任务数据")
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return QueryValidation(False, "tasks 不是数组")
    for task in tasks:
        if not isinstance(task, Mapping) or not _integer_status(task.get("status")):
            return QueryValidation(False, "任务状态必须是整数")
        if task.get("task_type") not in {
            "PPT",
            "ASR",
            "TEACHER_BEHAVIOR",
            "STUDENT_BEHAVIOR",
        }:
            return QueryValidation(False, "任务类型不在四类权威集合中")
        nodes = task.get("nodes", [])
        if not isinstance(nodes, list):
            return QueryValidation(False, "nodes 不是数组")
        if any(
            not isinstance(node, Mapping) or not _integer_status(node.get("status"))
            for node in nodes
        ):
            return QueryValidation(False, "节点状态必须是整数")
        for node in nodes:
            assert isinstance(node, Mapping)
            if "path" in node and not isinstance(node["path"], str):
                return QueryValidation(False, "节点 path 必须是字符串")
            if "count" in node and not _integer_status(node["count"]):
                return QueryValidation(False, "节点 count 必须是整数")
            if "result" in node and not isinstance(node["result"], (Mapping, list)):
                return QueryValidation(False, "节点 result 必须是对象或数组")
    return QueryValidation(True)
