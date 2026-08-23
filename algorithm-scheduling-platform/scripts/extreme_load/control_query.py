from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
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


@dataclass(frozen=True)
class ScheduledQueryRequest:
    poller_id: str
    scheduled_offset_seconds: float
    request: HttpRequestSpec

    def __post_init__(self) -> None:
        if not self.poller_id or self.scheduled_offset_seconds < 0:
            raise ValueError("查询调度的 poller_id 不能为空且偏移不能为负")


_TASK_TYPES = frozenset({"PPT", "ASR", "TEACHER_BEHAVIOR", "STUDENT_BEHAVIOR"})
_ACTIVE_NODE_STATUSES = frozenset({10, 20, 30, 40, 50})
_TERMINAL_NODE_STATUSES = frozenset({60, 70, 80})
_NODE_STATUSES = _ACTIVE_NODE_STATUSES | _TERMINAL_NODE_STATUSES
_TASK_STATUSES = frozenset({0}) | _NODE_STATUSES
_PRIORITIES = frozenset({"NORMAL", "URGENT"})
_DIRECT_NODE_TRANSITIONS: Mapping[int, frozenset[int]] = {
    10: frozenset({30, 40, 80}),
    20: frozenset({10, 80}),
    30: frozenset({10, 40, 80}),
    40: frozenset({10, 50, 80}),
    50: frozenset({30, 60, 70, 80}),
    60: frozenset(),
    70: frozenset(),
    80: frozenset(),
}


@dataclass(frozen=True)
class QueryNodeObservation:
    task_id: str
    task_type: str
    task_status: int
    node_code: str
    status: int
    priority: str
    claimed_at: str | None
    started_at: str | None
    finished_at: str | None
    updated_at: str | None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.task_id, self.task_type, self.node_code)


@dataclass(frozen=True)
class CourseQueryObservation:
    task_id: str
    task_statuses: tuple[tuple[str, int], ...]
    nodes: tuple[QueryNodeObservation, ...]


@dataclass(frozen=True)
class ObservedCourseQuery:
    scheduled_offset_seconds: float
    request_id: str
    observation: CourseQueryObservation

    def __post_init__(self) -> None:
        if self.scheduled_offset_seconds < 0 or not self.request_id:
            raise ValueError("查询观察的调度偏移不能为负且请求 ID 不能为空")


@dataclass(frozen=True)
class QueryTransitionValidation:
    valid: bool
    reason: str
    observed_course_count: int
    observed_node_count: int
    node_sample_count: int
    transition_count: int

    def to_evidence(self) -> dict[str, object]:
        return {
            "status": "proven" if self.valid else "failed",
            "reason": self.reason,
            "observed_course_count": self.observed_course_count,
            "observed_node_count": self.observed_node_count,
            "node_sample_count": self.node_sample_count,
            "transition_count": self.transition_count,
        }


@dataclass(frozen=True)
class ControlReadinessEvidence:
    ready: bool
    status_code: int | None
    reported_status: str | None
    checked_dependencies: tuple[str, ...]
    unready_dependencies: tuple[str, ...]
    reason: str

    def to_evidence(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "status_code": self.status_code,
            "reported_status": self.reported_status,
            "checked_dependencies": list(self.checked_dependencies),
            "unready_dependencies": list(self.unready_dependencies),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PriorityCheckpointAssessment:
    state: str
    reason: str
    observed_node_count: int
    running_normal_node_count: int
    unclaimed_normal_node_count: int

    def __post_init__(self) -> None:
        if self.state not in {"ready", "waiting", "blocked", "failed"}:
            raise ValueError("优先级检查点状态不合法")

    def to_evidence(self) -> dict[str, object]:
        return {
            "status": self.state,
            "reason": self.reason,
            "observed_node_count": self.observed_node_count,
            "running_normal_node_count": self.running_normal_node_count,
            "unclaimed_normal_node_count": self.unclaimed_normal_node_count,
        }


@dataclass(frozen=True)
class PriorityClaimValidation:
    status: str
    reason: str
    running_normal_node_count: int
    unclaimed_normal_node_count: int
    urgent_claimed_node_count: int
    compared_normal_node_count: int
    latest_urgent_claimed_at: str | None = None
    earliest_overtaken_normal_claimed_at: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"passed", "blocked", "failed"}:
            raise ValueError("优先级领取验证状态不合法")

    def to_evidence(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "running_normal_node_count": self.running_normal_node_count,
            "unclaimed_normal_node_count": self.unclaimed_normal_node_count,
            "urgent_claimed_node_count": self.urgent_claimed_node_count,
            "compared_normal_node_count": self.compared_normal_node_count,
            "latest_urgent_claimed_at": self.latest_urgent_claimed_at,
            "earliest_overtaken_normal_claimed_at": (
                self.earliest_overtaken_normal_claimed_at
            ),
        }


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
        # 以 100 ms 为最小框定时间槽，既覆盖整个轮询周期，也避免为
        # 上千 QPS 创建无界定时协程。
        slot_count = int(polling_interval_seconds * 10)
        offsets = tuple(
            generator.randrange(slot_count) / 10
            for _ in range(task_count)
        )
    return QuerySchedule(
        mode=mode,
        polling_interval_seconds=polling_interval_seconds,
        offsets_seconds=offsets,
    )


def build_scheduled_query_requests(
    targets: NorthboundTargets,
    identity: ReproducibleIdentity,
    case_id: str,
    task_ids: Sequence[str],
    *,
    qps: int,
    duration_seconds: int,
    polling_interval_seconds: int,
    mode: QueryMode,
    large_asr_task_ids: Sequence[str] = (),
) -> tuple[ScheduledQueryRequest, ...]:
    """Build virtual pollers whose aggregate rate is the requested QPS tier."""

    if qps not in query_qps_tiers() or duration_seconds <= 0:
        raise ValueError("qps 只允许 50/100/300/1000 且 duration_seconds 必须为正数")
    if polling_interval_seconds not in {2, 5}:
        raise ValueError("轮询周期只允许 2 秒或 5 秒")
    if duration_seconds % polling_interval_seconds:
        raise ValueError("查询持续时间必须是轮询周期的整数倍")
    if not task_ids:
        raise ValueError("查询至少需要一个 task_id")
    large_asr_ids = set(large_asr_task_ids)
    if not large_asr_ids.issubset(set(task_ids)):
        raise ValueError("大 ASR 结果 task_id 必须属于查询集合")

    poller_count = qps * polling_interval_seconds
    schedule = build_query_schedule(
        identity,
        case_id,
        task_count=poller_count,
        polling_interval_seconds=polling_interval_seconds,
        mode=mode,
    )
    result: list[ScheduledQueryRequest] = []
    cycles = duration_seconds // polling_interval_seconds
    for cycle in range(cycles):
        cycle_offset = cycle * polling_interval_seconds
        for poller_index, initial_offset in enumerate(schedule.offsets_seconds):
            task_id = task_ids[poller_index % len(task_ids)]
            request_index = cycle * poller_count + poller_index
            result.append(
                ScheduledQueryRequest(
                    poller_id=f"poller-{poller_index}",
                    scheduled_offset_seconds=cycle_offset + initial_offset,
                    request=HttpRequestSpec(
                        request_id=f"query-{request_index}",
                        method="GET",
                        url=targets.control_url(f"/api/course-jobs/{task_id}"),
                        work_type=(
                            "large_asr_result_query"
                            if task_id in large_asr_ids
                            else "course_query"
                        ),
                    ),
                )
            )
    return tuple(result)


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


def _optional_timestamp(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} 必须是带时区时间字符串或 null")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field_name} 不是合法 ISO-8601 时间") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} 必须包含时区")
    return value


def _parsed_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _timestamp_order_valid(node: QueryNodeObservation) -> bool:
    ordered = tuple(
        _parsed_timestamp(value)
        for value in (node.claimed_at, node.started_at, node.finished_at)
        if value is not None
    )
    return all(
        earlier <= later for earlier, later in zip(ordered, ordered[1:], strict=False)
    )


def parse_course_query_response(body: Mapping[str, Any]) -> CourseQueryObservation:
    if body.get("code") != 0:
        raise ValueError("业务码不是成功")
    data = body.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("缺少课程任务数据")
    task_id = data.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("缺少课程任务数据")
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("tasks 不是数组")
    task_statuses: list[tuple[str, int]] = []
    nodes: list[QueryNodeObservation] = []
    seen_task_types: set[str] = set()
    for task in tasks:
        if not isinstance(task, Mapping) or not _integer_status(task.get("status")):
            raise ValueError("任务状态必须是整数")
        task_status = int(task["status"])
        if task_status not in _TASK_STATUSES:
            raise ValueError("任务状态不在整数状态权威集合中")
        task_type = task.get("task_type")
        if not isinstance(task_type, str) or task_type not in _TASK_TYPES:
            raise ValueError("任务类型不在四类权威集合中")
        if task_type in seen_task_types:
            raise ValueError("查询响应包含重复任务类型")
        seen_task_types.add(task_type)
        task_statuses.append((task_type, task_status))
        raw_nodes = task.get("nodes", [])
        if not isinstance(raw_nodes, list):
            raise ValueError("nodes 不是数组")
        seen_node_codes: set[str] = set()
        for node in raw_nodes:
            if not isinstance(node, Mapping) or not _integer_status(node.get("status")):
                raise ValueError("节点状态必须是整数")
            node_status = int(node["status"])
            if node_status not in _NODE_STATUSES:
                raise ValueError("节点状态不在整数状态权威集合中")
            node_code = node.get("node_code")
            if not isinstance(node_code, str) or not node_code:
                raise ValueError("节点缺少 node_code")
            if node_code in seen_node_codes:
                raise ValueError("查询响应包含重复节点")
            seen_node_codes.add(node_code)
            priority = node.get("priority")
            if not isinstance(priority, str) or priority not in _PRIORITIES:
                raise ValueError("节点优先级不合法")
            if "path" in node and not isinstance(node["path"], str):
                raise ValueError("节点 path 必须是字符串")
            if "count" in node and not _integer_status(node["count"]):
                raise ValueError("节点 count 必须是整数")
            if "result" in node and not isinstance(node["result"], (Mapping, list)):
                raise ValueError("节点 result 必须是对象或数组")
            observation = QueryNodeObservation(
                task_id=task_id,
                task_type=task_type,
                task_status=task_status,
                node_code=node_code,
                status=node_status,
                priority=priority,
                claimed_at=_optional_timestamp(node.get("claimed_at"), "claimed_at"),
                started_at=_optional_timestamp(node.get("started_at"), "started_at"),
                finished_at=_optional_timestamp(node.get("finished_at"), "finished_at"),
                updated_at=_optional_timestamp(node.get("updated_at"), "updated_at"),
            )
            if not _timestamp_order_valid(observation):
                raise ValueError("节点领取、开始和结束时间顺序不合法")
            nodes.append(observation)
    return CourseQueryObservation(
        task_id=task_id,
        task_statuses=tuple(task_statuses),
        nodes=tuple(nodes),
    )


def validate_course_query_response(body: Mapping[str, Any]) -> QueryValidation:
    try:
        parse_course_query_response(body)
    except ValueError as error:
        return QueryValidation(False, str(error))
    return QueryValidation(True)


def validate_control_readiness_response(
    status_code: int,
    body: Mapping[str, Any],
) -> ControlReadinessEvidence:
    reported_status = body.get("status")
    checks = body.get("checks")
    if not isinstance(reported_status, str) or not isinstance(checks, Mapping) or not checks:
        return ControlReadinessEvidence(
            False,
            status_code,
            reported_status if isinstance(reported_status, str) else None,
            (),
            (),
            "Control readiness 响应结构不合法",
        )
    checked: list[str] = []
    unready: list[str] = []
    for raw_name, raw_check in checks.items():
        name = str(raw_name)
        if not isinstance(raw_check, Mapping) or not isinstance(raw_check.get("ready"), bool):
            return ControlReadinessEvidence(
                False,
                status_code,
                reported_status,
                tuple(sorted(checked)),
                tuple(sorted(unready)),
                f"Control readiness 依赖检查结构不合法: {name}",
            )
        checked.append(name)
        if raw_check["ready"] is not True:
            unready.append(name)
    ready = status_code == 200 and reported_status == "ready" and not unready
    return ControlReadinessEvidence(
        ready,
        status_code,
        reported_status,
        tuple(sorted(checked)),
        tuple(sorted(unready)),
        "Control Service 已就绪" if ready else "Control Service 未就绪",
    )


def _status_reachable(current: int, target: int) -> bool:
    if current == target:
        return True
    pending = [current]
    visited = {current}
    while pending:
        state = pending.pop()
        for candidate in _DIRECT_NODE_TRANSITIONS.get(state, frozenset()):
            if candidate == target:
                return True
            if candidate not in visited:
                visited.add(candidate)
                pending.append(candidate)
    return False


def _validate_status_groups(
    groups: Sequence[tuple[float, frozenset[int]]],
    *,
    label: str,
) -> str | None:
    previous: frozenset[int] | None = None
    terminal_seen: int | None = None
    for _offset, statuses in groups:
        terminal = statuses.intersection(_TERMINAL_NODE_STATUSES)
        if len(terminal) > 1:
            return f"{label} 同一调度时点观察到多个冲突终态"
        if terminal_seen is not None:
            if statuses != frozenset({terminal_seen}):
                return f"{label} 进入终态后发生回退或终态改变"
            previous = statuses
            continue
        if previous is not None and not all(
            any(_status_reachable(old, current) for old in previous)
            for current in statuses
        ):
            return f"{label} 出现不合法状态迁移"
        if terminal:
            terminal_seen = next(iter(terminal))
        previous = statuses
    return None


def validate_monotonic_query_observations(
    observations: Sequence[ObservedCourseQuery],
) -> QueryTransitionValidation:
    if not observations:
        return QueryTransitionValidation(False, "没有成功查询观察可验证", 0, 0, 0, 0)
    by_node: dict[tuple[str, str, str], list[tuple[float, QueryNodeObservation]]] = {}
    course_ids: set[str] = set()
    for observed in observations:
        course_ids.add(observed.observation.task_id)
        for node in observed.observation.nodes:
            by_node.setdefault(node.key, []).append((observed.scheduled_offset_seconds, node))
    if not by_node:
        return QueryTransitionValidation(
            False,
            "成功查询响应没有任何节点状态",
            len(course_ids),
            0,
            0,
            0,
        )

    transition_count = 0
    for key, samples in sorted(by_node.items()):
        grouped: dict[float, set[int]] = {}
        for offset, node in samples:
            grouped.setdefault(offset, set()).add(node.status)
        groups = tuple(
            (offset, frozenset(statuses)) for offset, statuses in sorted(grouped.items())
        )
        transition_count += max(0, len(groups) - 1)
        label = "/".join(key)
        failure = _validate_status_groups(groups, label=label)
        if failure is not None:
            return QueryTransitionValidation(
                False,
                failure,
                len(course_ids),
                len(by_node),
                sum(len(item) for item in by_node.values()),
                transition_count,
            )

        previous_timestamps: dict[str, tuple[float, str]] = {}
        for offset, node in sorted(samples, key=lambda item: item[0]):
            for field_name in ("claimed_at", "started_at", "finished_at", "updated_at"):
                value = getattr(node, field_name)
                previous = previous_timestamps.get(field_name)
                if previous is not None and offset > previous[0]:
                    if value is None:
                        return QueryTransitionValidation(
                            False,
                            f"{label} 的 {field_name} 在后续查询中消失",
                            len(course_ids),
                            len(by_node),
                            sum(len(item) for item in by_node.values()),
                            transition_count,
                        )
                    if field_name != "updated_at" and value != previous[1]:
                        return QueryTransitionValidation(
                            False,
                            f"{label} 的 {field_name} 在后续查询中被改写",
                            len(course_ids),
                            len(by_node),
                            sum(len(item) for item in by_node.values()),
                            transition_count,
                        )
                    if _parsed_timestamp(value) < _parsed_timestamp(previous[1]):
                        return QueryTransitionValidation(
                            False,
                            f"{label} 的 {field_name} 时间发生倒退",
                            len(course_ids),
                            len(by_node),
                            sum(len(item) for item in by_node.values()),
                            transition_count,
                        )
                if value is not None and (previous is None or offset >= previous[0]):
                    previous_timestamps[field_name] = (offset, value)

    return QueryTransitionValidation(
        True,
        "整数节点状态及时间事实保持合法单调迁移",
        len(course_ids),
        len(by_node),
        sum(len(item) for item in by_node.values()),
        transition_count,
    )


def assess_priority_normal_checkpoint(
    observations: Sequence[CourseQueryObservation],
    normal_task_ids: Sequence[str],
) -> PriorityCheckpointAssessment:
    expected_ids = set(normal_task_ids)
    observed_ids = {observation.task_id for observation in observations}
    nodes = tuple(node for observation in observations for node in observation.nodes)
    if observed_ids != expected_ids:
        return PriorityCheckpointAssessment(
            "waiting",
            "NORMAL 任务查询事实尚未齐全",
            len(nodes),
            0,
            0,
        )
    if not nodes:
        return PriorityCheckpointAssessment(
            "waiting",
            "NORMAL 任务 DAG 节点尚未初始化",
            0,
            0,
            0,
        )
    if any(node.priority != "NORMAL" for node in nodes):
        return PriorityCheckpointAssessment(
            "failed",
            "NORMAL 任务出现非 NORMAL 节点优先级",
            len(nodes),
            0,
            0,
        )
    missing_claim = [
        node for node in nodes if node.status >= 40 and node.claimed_at is None
    ]
    missing_start = [
        node for node in nodes if node.status >= 50 and node.started_at is None
    ]
    if missing_claim or missing_start:
        return PriorityCheckpointAssessment(
            "blocked",
            "任务查询未提供 claimed_at/started_at，不能证明真实领取顺序",
            len(nodes),
            0,
            0,
        )
    running = tuple(
        node
        for node in nodes
        if node.status == 50 and node.claimed_at is not None and node.started_at is not None
    )
    unclaimed = tuple(
        node
        for node in nodes
        if node.status in {10, 20, 30}
        and node.claimed_at is None
        and node.started_at is None
    )
    if running and unclaimed:
        return PriorityCheckpointAssessment(
            "ready",
            "已观察到运行中与尚未领取的 NORMAL 节点",
            len(nodes),
            len(running),
            len(unclaimed),
        )
    if all(node.status in _TERMINAL_NODE_STATUSES for node in nodes):
        return PriorityCheckpointAssessment(
            "blocked",
            "NORMAL 任务已终态，未形成可验证的运行中/未领取堆积",
            len(nodes),
            len(running),
            len(unclaimed),
        )
    return PriorityCheckpointAssessment(
        "waiting",
        "尚未同时观察到运行中与未领取的 NORMAL 节点",
        len(nodes),
        len(running),
        len(unclaimed),
    )


def _indexed_nodes(
    observations: Sequence[CourseQueryObservation],
) -> tuple[dict[tuple[str, str, str], QueryNodeObservation], str | None]:
    indexed: dict[tuple[str, str, str], QueryNodeObservation] = {}
    for observation in observations:
        for node in observation.nodes:
            if node.key in indexed:
                return {}, "领取证据包含重复节点"
            indexed[node.key] = node
    return indexed, None


def validate_priority_claim_order(
    before_normal: Sequence[CourseQueryObservation],
    after_all: Sequence[CourseQueryObservation],
    *,
    normal_task_ids: Sequence[str],
    urgent_task_ids: Sequence[str],
) -> PriorityClaimValidation:
    checkpoint = assess_priority_normal_checkpoint(before_normal, normal_task_ids)
    if checkpoint.state != "ready":
        status = "failed" if checkpoint.state == "failed" else "blocked"
        return PriorityClaimValidation(
            status,
            checkpoint.reason,
            checkpoint.running_normal_node_count,
            checkpoint.unclaimed_normal_node_count,
            0,
            0,
        )
    before, before_error = _indexed_nodes(before_normal)
    after, after_error = _indexed_nodes(after_all)
    if before_error or after_error:
        return PriorityClaimValidation(
            "failed",
            before_error or after_error or "领取证据重复",
            checkpoint.running_normal_node_count,
            checkpoint.unclaimed_normal_node_count,
            0,
            0,
        )
    expected_after_ids = set(normal_task_ids) | set(urgent_task_ids)
    observed_after_ids = {observation.task_id for observation in after_all}
    if observed_after_ids != expected_after_ids:
        return PriorityClaimValidation(
            "blocked",
            "终态领取证据未覆盖全部 NORMAL/URGENT 任务",
            checkpoint.running_normal_node_count,
            checkpoint.unclaimed_normal_node_count,
            0,
            0,
        )

    running_before = tuple(
        node for node in before.values() if node.status == 50 and node.started_at is not None
    )
    unclaimed_before = tuple(
        node
        for node in before.values()
        if node.status in {10, 20, 30}
        and node.claimed_at is None
        and node.started_at is None
    )
    for original in running_before:
        current = after.get(original.key)
        if current is None:
            return PriorityClaimValidation(
                "blocked",
                "终态证据缺少注入前已运行 NORMAL 节点",
                len(running_before),
                len(unclaimed_before),
                0,
                0,
            )
        if current.claimed_at != original.claimed_at or current.started_at != original.started_at:
            return PriorityClaimValidation(
                "failed",
                "注入前已运行 NORMAL 节点的领取/开始事实被改写",
                len(running_before),
                len(unclaimed_before),
                0,
                0,
            )
        if current.status in {70, 80}:
            return PriorityClaimValidation(
                "failed",
                "注入前已运行 NORMAL 节点被失败或取消",
                len(running_before),
                len(unclaimed_before),
                0,
                0,
            )

    overtaken_normal: list[QueryNodeObservation] = []
    for original in unclaimed_before:
        current = after.get(original.key)
        if current is None or current.claimed_at is None:
            return PriorityClaimValidation(
                "blocked",
                "终态证据缺少原未领取 NORMAL 节点的 claimed_at",
                len(running_before),
                len(unclaimed_before),
                0,
                len(overtaken_normal),
            )
        overtaken_normal.append(current)

    urgent_nodes = tuple(
        node
        for node in after.values()
        if node.task_id in set(urgent_task_ids)
    )
    if not urgent_nodes or any(node.priority != "URGENT" for node in urgent_nodes):
        return PriorityClaimValidation(
            "failed",
            "URGENT 任务缺少 URGENT 节点领取事实",
            len(running_before),
            len(unclaimed_before),
            0,
            len(overtaken_normal),
        )
    if any(node.claimed_at is None for node in urgent_nodes):
        return PriorityClaimValidation(
            "blocked",
            "URGENT 节点缺少 claimed_at，不能证明插队顺序",
            len(running_before),
            len(unclaimed_before),
            0,
            len(overtaken_normal),
        )
    urgent_claims = [
        (node.claimed_at, _parsed_timestamp(node.claimed_at))
        for node in urgent_nodes
        if node.claimed_at is not None
    ]
    normal_claims = [
        (node.claimed_at, _parsed_timestamp(node.claimed_at))
        for node in overtaken_normal
        if node.claimed_at is not None
    ]
    latest_urgent_raw, latest_urgent = max(urgent_claims, key=lambda item: item[1])
    earliest_normal_raw, earliest_normal = min(normal_claims, key=lambda item: item[1])
    if latest_urgent > earliest_normal:
        return PriorityClaimValidation(
            "failed",
            "至少一个原未领取 NORMAL 节点早于全部 URGENT 节点被领取",
            len(running_before),
            len(unclaimed_before),
            len(urgent_nodes),
            len(overtaken_normal),
            latest_urgent_raw,
            earliest_normal_raw,
        )
    if latest_urgent == earliest_normal:
        return PriorityClaimValidation(
            "blocked",
            "URGENT 与原未领取 NORMAL 的领取时间相同，证据精度不足",
            len(running_before),
            len(unclaimed_before),
            len(urgent_nodes),
            len(overtaken_normal),
            latest_urgent_raw,
            earliest_normal_raw,
        )
    return PriorityClaimValidation(
        "passed",
        "全部 URGENT 节点先于注入时未领取的 NORMAL 节点领取，已运行节点未被改写",
        len(running_before),
        len(unclaimed_before),
        len(urgent_nodes),
        len(overtaken_normal),
        latest_urgent_raw,
        earliest_normal_raw,
    )
