from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, RowMapping, text

from packages.platform_common.state_machine import validate_node_transition
from packages.platform_contracts.status import NodeStatus, Priority, TaskType

JsonObject = dict[str, Any]


class RepositoryNotFoundError(LookupError):
    """Raised when an update target no longer exists."""


class RepositoryStateConflictError(ValueError):
    """Raised when a repository write conflicts with the current state."""


@dataclass(frozen=True, slots=True)
class TaskTypeWrite:
    task_type: TaskType
    priority: Priority = Priority.NORMAL
    request_payload: JsonObject = field(default_factory=dict)
    effective_params: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class TaskTypeRecord:
    id: int
    task_id: str
    task_type: TaskType
    status: NodeStatus
    priority: Priority
    reason: str
    request_payload: JsonObject
    effective_params: JsonObject | None
    created: bool
    updated_at: datetime
    submission_id: str = ""


@dataclass(frozen=True, slots=True)
class NodeResultWrite:
    result: JsonObject | list[Any] | None = None
    artifact_path: str | None = None
    artifact_count: int | None = None
    progress: JsonObject = field(default_factory=dict)
    effective_params: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class NodeWrite:
    node_code: str
    status: NodeStatus
    priority: Priority
    reason: str
    required_capability: str | None = None
    prerequisite_node_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NodeWorkItemWrite:
    item_key: str
    ordinal: int
    status: NodeStatus = NodeStatus.PENDING
    reason: str = "子任务等待处理"
    result: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class NodeWorkItemRecord:
    id: int
    task_node_id: int
    item_key: str
    ordinal: int
    status: NodeStatus
    reason: str
    result: JsonObject | None


@dataclass(frozen=True, slots=True)
class WorkItemProgress:
    completed_count: int
    total_count: int


@dataclass(frozen=True, slots=True)
class QueueCount:
    status: NodeStatus
    priority: Priority
    capability: str | None
    count: int


@dataclass(frozen=True, slots=True)
class OperationsQueueSnapshot:
    queues: tuple[QueueCount, ...]
    outbox_pending: int


@dataclass(frozen=True, slots=True)
class NodeRecord:
    id: int
    course_task_type_id: int
    node_code: str
    status: NodeStatus
    priority: Priority
    reason: str
    required_capability: str | None
    result: JsonObject | list[Any] | None
    artifact_path: str | None
    artifact_count: int | None
    progress: JsonObject
    effective_params: JsonObject | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    event_id: UUID
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload: JsonObject
    claim_token: UUID
    claimed_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxStateRecord:
    event_id: UUID
    published_at: datetime | None
    publish_attempts: int
    last_error: str | None
    claim_token: UUID | None
    claimed_at: datetime | None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _task_type_record(row: RowMapping, *, created: bool) -> TaskTypeRecord:
    return TaskTypeRecord(
        id=row["id"],
        submission_id=str(row["submission_id"]),
        task_id=row["task_id"],
        task_type=TaskType(row["task_type"]),
        status=NodeStatus(row["status"]),
        priority=Priority(row["priority"]),
        reason=row["reason"],
        request_payload=row["request_payload"],
        effective_params=row["effective_params"],
        created=created,
        updated_at=row["updated_at"],
    )


def _node_record(row: RowMapping) -> NodeRecord:
    return NodeRecord(
        id=row["id"],
        course_task_type_id=row["course_task_type_id"],
        node_code=row["node_code"],
        status=NodeStatus(row["status"]),
        priority=Priority(row["priority"]),
        reason=row["reason"],
        required_capability=row["required_capability"],
        result=row["result"],
        artifact_path=row["artifact_path"],
        artifact_count=row["artifact_count"],
        progress=row["progress"] or {},
        effective_params=row["result_effective_params"],
        updated_at=row["updated_at"],
    )


class CourseRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_task_types(
        self,
        *,
        task_id: str,
        writes: list[TaskTypeWrite],
        input_snapshot: JsonObject | None = None,
    ) -> list[TaskTypeRecord]:
        if not writes:
            return []

        records: list[TaskTypeRecord] = []
        submission_id = str(uuid4())
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO course_jobs (task_id, input_snapshot)
                    VALUES (:task_id, CAST(:input_snapshot AS jsonb))
                    ON CONFLICT (task_id) DO UPDATE
                    SET input_snapshot = course_jobs.input_snapshot || EXCLUDED.input_snapshot,
                        updated_at = now()
                    """
                ),
                {"task_id": task_id, "input_snapshot": _json(input_snapshot or {})},
            )

            for write in writes:
                row = connection.execute(
                    text(
                        """
                        INSERT INTO course_task_types (
                            submission_id, task_id, task_type, status, priority, reason,
                            request_payload, effective_params
                        )
                        VALUES (
                            CAST(:submission_id AS uuid),
                            :task_id, :task_type, :status, :priority, :reason,
                            CAST(:request_payload AS jsonb), CAST(:effective_params AS jsonb)
                        )
                        ON CONFLICT (task_id, task_type) DO NOTHING
                        RETURNING id, submission_id, task_id, task_type, status, priority, reason,
                                  request_payload, effective_params, updated_at
                        """
                    ),
                    {
                        "task_id": task_id,
                        "submission_id": submission_id,
                        "task_type": write.task_type.value,
                        "status": NodeStatus.PENDING.value,
                        "priority": write.priority.value,
                        "reason": "任务已接收，等待处理",
                        "request_payload": _json(write.request_payload),
                        "effective_params": _json(write.effective_params),
                    },
                ).mappings().one_or_none()

                created = row is not None
                if created:
                    connection.execute(
                        text(
                            """
                            INSERT INTO outbox_events (
                                event_id, aggregate_type, aggregate_id, event_type, payload
                            )
                            VALUES (
                                :event_id, 'COURSE_TASK_TYPE', :aggregate_id,
                                'COURSE_TASK_REQUESTED', CAST(:payload AS jsonb)
                            )
                            """
                        ),
                        {
                            "event_id": uuid4(),
                            "aggregate_id": f"{task_id}:{write.task_type.value}",
                            "payload": _json(
                                {
                                    "task_id": task_id,
                                    "task_type": write.task_type.value,
                                    "priority": write.priority.value,
                                    "submission_id": submission_id,
                                }
                            ),
                        },
                    )
                if row is None:
                    row = connection.execute(
                        text(
                            """
                            SELECT id, submission_id, task_id, task_type, status, priority, reason,
                                   request_payload, effective_params, updated_at
                            FROM course_task_types
                            WHERE task_id = :task_id AND task_type = :task_type
                            """
                        ),
                        {"task_id": task_id, "task_type": write.task_type.value},
                    ).mappings().one()
                records.append(_task_type_record(row, created=created))

        return records

    def count_courses(self) -> int:
        with self._engine.connect() as connection:
            return int(connection.execute(text("SELECT count(*) FROM course_jobs")).scalar_one())

    def operations_queue_snapshot(self) -> OperationsQueueSnapshot:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT status, priority, required_capability, count(*) AS count
                    FROM task_nodes
                    WHERE status IN (10, 20, 30, 40, 50)
                    GROUP BY status, priority, required_capability
                    ORDER BY status,
                             CASE priority WHEN 'URGENT' THEN 0 ELSE 1 END,
                             required_capability NULLS FIRST
                    """
                )
            ).mappings()
            queues = tuple(
                QueueCount(
                    status=NodeStatus(row["status"]),
                    priority=Priority(row["priority"]),
                    capability=row["required_capability"],
                    count=int(row["count"]),
                )
                for row in rows
            )
            outbox_pending = int(
                connection.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM outbox_events
                        WHERE published_at IS NULL
                        """
                    )
                ).scalar_one()
            )
        return OperationsQueueSnapshot(queues=queues, outbox_pending=outbox_pending)

    def get_or_create_visual_fallback(
        self,
        course_task_type_id: int,
        metric_code: str,
        candidate_value: float,
    ) -> float:
        supported_codes = {"FRONT_OCCUPANCY_RATIO", "BACK_OCCUPANCY_RATIO"}
        if metric_code not in supported_codes:
            raise ValueError(f"不支持的视觉兜底指标: {metric_code}")
        if not 0 <= candidate_value <= 1:
            raise ValueError("视觉兜底值必须在 0 到 1 之间")
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO visual_fallback_values (
                        course_task_type_id, metric_code, value
                    )
                    VALUES (:course_task_type_id, :metric_code, :value)
                    ON CONFLICT (course_task_type_id, metric_code) DO NOTHING
                    """
                ),
                {
                    "course_task_type_id": course_task_type_id,
                    "metric_code": metric_code,
                    "value": candidate_value,
                },
            )
            value = connection.execute(
                text(
                    """
                    SELECT value
                    FROM visual_fallback_values
                    WHERE course_task_type_id = :course_task_type_id
                      AND metric_code = :metric_code
                    """
                ),
                {
                    "course_task_type_id": course_task_type_id,
                    "metric_code": metric_code,
                },
            ).scalar_one()
        return float(value)

    def list_task_types(self, task_id: str) -> list[TaskTypeRecord]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT id, submission_id, task_id, task_type, status, priority, reason,
                           request_payload, effective_params, updated_at
                    FROM course_task_types
                    WHERE task_id = :task_id
                    ORDER BY requested_at, id
                    """
                ),
                {"task_id": task_id},
            ).mappings()
            return [_task_type_record(row, created=False) for row in rows]

    def get_task_type(self, course_task_type_id: int) -> TaskTypeRecord:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT id, submission_id, task_id, task_type, status, priority, reason,
                           request_payload, effective_params, updated_at
                    FROM course_task_types
                    WHERE id = :course_task_type_id
                    """
                ),
                {"course_task_type_id": course_task_type_id},
            ).mappings().one_or_none()
        if row is None:
            raise RepositoryNotFoundError(f"任务类型不存在: {course_task_type_id}")
        return _task_type_record(row, created=False)

    def list_dispatch_capabilities(self) -> list[str]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT DISTINCT required_capability
                    FROM task_nodes
                    WHERE status IN (10, 30)
                      AND required_capability IS NOT NULL
                    ORDER BY required_capability
                    """
                )
            ).scalars()
            return [str(capability) for capability in rows]

    def aggregate_task_type_state(self, course_task_type_id: int) -> TaskTypeRecord:
        with self._engine.begin() as connection:
            task_type_row = connection.execute(
                text(
                    """
                    SELECT id, submission_id, task_id, task_type, status, priority, reason,
                           request_payload, effective_params, updated_at
                    FROM course_task_types
                    WHERE id = :course_task_type_id
                    FOR UPDATE
                    """
                ),
                {"course_task_type_id": course_task_type_id},
            ).mappings().one_or_none()
            if task_type_row is None:
                raise RepositoryNotFoundError(f"任务类型不存在: {course_task_type_id}")

            nodes = list(
                connection.execute(
                    text(
                        """
                        SELECT node_code, status, reason, required_capability
                        FROM task_nodes
                        WHERE course_task_type_id = :course_task_type_id
                        ORDER BY created_at, id
                        FOR UPDATE
                        """
                    ),
                    {"course_task_type_id": course_task_type_id},
                ).mappings()
            )
            status, reason = self._derive_task_type_state(
                TaskType(task_type_row["task_type"]),
                nodes,
            )
            row = connection.execute(
                text(
                    """
                    UPDATE course_task_types
                    SET status = :status,
                        reason = :reason,
                        started_at = CASE
                            WHEN :status = 50 THEN COALESCE(started_at, now())
                            ELSE started_at
                        END,
                        finished_at = CASE
                            WHEN :status IN (60, 70, 80) THEN COALESCE(finished_at, now())
                            ELSE finished_at
                        END,
                        updated_at = CASE
                            WHEN status <> :status OR reason <> :reason THEN now()
                            ELSE updated_at
                        END
                    WHERE id = :course_task_type_id
                    RETURNING id, submission_id, task_id, task_type, status, priority, reason,
                              request_payload, effective_params, updated_at
                    """
                ),
                {
                    "course_task_type_id": course_task_type_id,
                    "status": status.value,
                    "reason": reason,
                },
            ).mappings().one()
        return _task_type_record(row, created=False)

    def aggregate_capability_task_types(self, capability: str) -> list[TaskTypeRecord]:
        with self._engine.connect() as connection:
            task_type_ids = list(
                connection.execute(
                    text(
                        """
                        SELECT DISTINCT course_task_type_id
                        FROM task_nodes
                        WHERE required_capability = :capability
                          AND status IN (10, 30)
                        ORDER BY course_task_type_id
                        """
                    ),
                    {"capability": capability},
                ).scalars()
            )
        return [
            self.aggregate_task_type_state(int(course_task_type_id))
            for course_task_type_id in task_type_ids
        ]

    @staticmethod
    def _derive_task_type_state(
        task_type: TaskType,
        nodes: list[RowMapping],
    ) -> tuple[NodeStatus, str]:
        if not nodes:
            return NodeStatus.PENDING, "等待调度初始化"

        failed = next(
            (row for row in nodes if NodeStatus(row["status"]) is NodeStatus.FAILED),
            None,
        )
        if failed is not None:
            return NodeStatus.FAILED, f"节点处理失败: {failed['reason']}"

        if all(NodeStatus(row["status"]) is NodeStatus.COMPLETED for row in nodes):
            return NodeStatus.COMPLETED, f"{task_type.value} 所有节点处理完成"

        active = next(
            (
                row
                for row in nodes
                if NodeStatus(row["status"])
                in {NodeStatus.QUEUED, NodeStatus.RUNNING}
            ),
            None,
        )
        if active is not None:
            return NodeStatus.RUNNING, f"正在处理节点: {active['node_code']}"

        waiting_operator = next(
            (
                row
                for row in nodes
                if NodeStatus(row["status"]) is NodeStatus.WAITING_OPERATOR
            ),
            None,
        )
        if waiting_operator is not None:
            return NodeStatus.WAITING_OPERATOR, str(waiting_operator["reason"])

        next_node = next(
            (row for row in nodes if NodeStatus(row["status"]) is not NodeStatus.COMPLETED),
            nodes[0],
        )
        if any(NodeStatus(row["status"]) is NodeStatus.COMPLETED for row in nodes):
            return NodeStatus.RUNNING, f"正在处理节点: {next_node['node_code']}"
        return NodeStatus.PENDING, f"等待节点处理: {next_node['node_code']}"

    def update_task_type_state(
        self,
        course_task_type_id: int,
        status: NodeStatus,
        reason: str,
    ) -> TaskTypeRecord:
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE course_task_types
                    SET status = :status,
                        reason = :reason,
                        started_at = CASE
                            WHEN :status = 50 THEN COALESCE(started_at, now())
                            ELSE started_at
                        END,
                        finished_at = CASE
                            WHEN :status IN (60, 70, 80) THEN now()
                            ELSE finished_at
                        END,
                        updated_at = now()
                    WHERE id = :course_task_type_id
                    RETURNING id, submission_id, task_id, task_type, status, priority, reason,
                              request_payload, effective_params, updated_at
                    """
                ),
                {
                    "course_task_type_id": course_task_type_id,
                    "status": status.value,
                    "reason": reason,
                },
            ).mappings().one_or_none()
        if row is None:
            raise RepositoryNotFoundError(f"任务类型不存在: {course_task_type_id}")
        return _task_type_record(row, created=False)

    def create_node(
        self,
        *,
        course_task_type_id: int,
        node_code: str,
        status: NodeStatus,
        priority: Priority,
        reason: str,
        required_capability: str | None = None,
    ) -> NodeRecord:
        with self._engine.begin() as connection:
            node_id = connection.execute(
                text(
                    """
                    INSERT INTO task_nodes (
                        course_task_type_id, node_code, status, priority, reason,
                        required_capability, ready_at
                    )
                    VALUES (
                        :course_task_type_id, :node_code, :status, :priority, :reason,
                        :required_capability,
                        CASE WHEN :status = 10 THEN now() ELSE NULL END
                    )
                    RETURNING id
                    """
                ),
                {
                    "course_task_type_id": course_task_type_id,
                    "node_code": node_code,
                    "status": status.value,
                    "priority": priority.value,
                    "reason": reason,
                    "required_capability": required_capability,
                },
            ).scalar_one()
        return self.get_node(node_id)

    def initialize_pipeline(
        self,
        task_id: str,
        task_type: TaskType,
        nodes: list[NodeWrite],
        *,
        submission_id: str | None = None,
    ) -> list[NodeRecord]:
        with self._engine.begin() as connection:
            task_type_row = connection.execute(
                text(
                    """
                    SELECT id, submission_id
                    FROM course_task_types
                    WHERE task_id = :task_id AND task_type = :task_type
                    FOR UPDATE
                    """
                ),
                {"task_id": task_id, "task_type": task_type.value},
            ).mappings().one_or_none()
            if task_type_row is None:
                raise RepositoryNotFoundError(f"任务类型不存在: {task_id}/{task_type.value}")
            persisted_submission_id = str(task_type_row["submission_id"])
            if (
                submission_id is not None
                and persisted_submission_id != submission_id
            ):
                raise ValueError(
                    "课程任务事件 submission_id 与持久化任务事实不一致: "
                    f"{task_id}/{task_type.value}"
                )
            course_task_type_id = int(task_type_row["id"])

            for node in nodes:
                connection.execute(
                    text(
                        """
                        INSERT INTO task_nodes (
                            course_task_type_id, node_code, status, priority, reason,
                            required_capability, prerequisite_count, ready_at
                        )
                        VALUES (
                            :course_task_type_id, :node_code, :status, :priority, :reason,
                            :required_capability, :prerequisite_count,
                            CASE WHEN :status = 10 THEN now() ELSE NULL END
                        )
                        ON CONFLICT (course_task_type_id, node_code) DO NOTHING
                        """
                    ),
                    {
                        "course_task_type_id": course_task_type_id,
                        "node_code": node.node_code,
                        "status": node.status.value,
                        "priority": node.priority.value,
                        "reason": node.reason,
                        "required_capability": node.required_capability,
                        "prerequisite_count": len(node.prerequisite_node_codes),
                    },
                )
            node_rows = connection.execute(
                text(
                    """
                    SELECT id, node_code
                    FROM task_nodes
                    WHERE course_task_type_id = :course_task_type_id
                    """
                ),
                {"course_task_type_id": course_task_type_id},
            ).mappings()
            node_ids = {row["node_code"]: row["id"] for row in node_rows}
            for node in nodes:
                for prerequisite_code in node.prerequisite_node_codes:
                    try:
                        prerequisite_id = node_ids[prerequisite_code]
                        node_id = node_ids[node.node_code]
                    except KeyError as exc:
                        raise ValueError(
                            f"节点依赖不存在: {node.node_code} -> {prerequisite_code}"
                        ) from exc
                    connection.execute(
                        text(
                            """
                            INSERT INTO task_node_dependencies (node_id, prerequisite_node_id)
                            VALUES (:node_id, :prerequisite_node_id)
                            ON CONFLICT DO NOTHING
                            """
                        ),
                        {"node_id": node_id, "prerequisite_node_id": prerequisite_id},
                    )
        return self.list_nodes(int(course_task_type_id))

    def update_node_state(self, node_id: int, status: NodeStatus, reason: str) -> NodeRecord:
        return self.transition_node(node_id, status, reason)

    def transition_node(self, node_id: int, status: NodeStatus, reason: str) -> NodeRecord:
        with self._engine.begin() as connection:
            current_value = connection.execute(
                text("SELECT status FROM task_nodes WHERE id = :node_id FOR UPDATE"),
                {"node_id": node_id},
            ).scalar_one_or_none()
            if current_value is None:
                raise RepositoryNotFoundError(f"节点不存在: {node_id}")
            validate_node_transition(NodeStatus(current_value), status)
            connection.execute(
                text(
                    """
                    UPDATE task_nodes
                    SET status = :status,
                        reason = :reason,
                        started_at = CASE
                            WHEN :status = 50 THEN COALESCE(started_at, now())
                            ELSE started_at
                        END,
                        finished_at = CASE
                            WHEN :status IN (60, 70, 80) THEN now()
                            ELSE finished_at
                        END,
                        updated_at = now()
                    WHERE id = :node_id
                    """
                ),
                {"node_id": node_id, "status": status.value, "reason": reason},
            )
            if status is NodeStatus.COMPLETED:
                self._release_dependents(connection, node_id)
        return self.get_node(node_id)

    def claim_ready_node(self, capability: str, worker_id: str) -> NodeRecord | None:
        claim_token = uuid4()
        with self._engine.begin() as connection:
            node_id = connection.execute(
                text(
                    """
                    WITH candidate AS (
                        SELECT id
                        FROM task_nodes
                        WHERE status = 10
                          AND required_capability = :capability
                        ORDER BY
                            CASE priority WHEN 'URGENT' THEN 0 ELSE 1 END,
                            ready_at,
                            id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE task_nodes AS node
                    SET status = 40,
                        reason = '节点已领取，等待执行',
                        claimed_by = :worker_id,
                        claim_token = :claim_token,
                        claimed_at = now(),
                        attempt = attempt + 1,
                        updated_at = now()
                    FROM candidate
                    WHERE node.id = candidate.id
                    RETURNING node.id
                    """
                ),
                {
                    "capability": capability,
                    "worker_id": worker_id,
                    "claim_token": claim_token,
                },
            ).scalar_one_or_none()
        if node_id is None:
            return None
        return self.get_node(node_id)

    def claim_ready_visual_node(self, worker_id: str) -> NodeRecord | None:
        claim_token = uuid4()
        with self._engine.begin() as connection:
            node_id = connection.execute(
                text(
                    """
                    WITH candidate AS (
                        SELECT id
                        FROM task_nodes
                        WHERE status = 10
                          AND required_capability IS NULL
                          AND node_code IN (
                              'TEACHER_BEHAVIOR_ANALYSIS',
                              'STUDENT_BEHAVIOR_ANALYSIS'
                          )
                        ORDER BY
                            CASE priority WHEN 'URGENT' THEN 0 ELSE 1 END,
                            ready_at,
                            id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE task_nodes AS node
                    SET status = 40,
                        reason = '视觉节点已领取，正在准备本地视频',
                        claimed_by = :worker_id,
                        claim_token = :claim_token,
                        claimed_at = now(),
                        attempt = attempt + 1,
                        updated_at = now()
                    FROM candidate
                    WHERE node.id = candidate.id
                    RETURNING node.id
                    """
                ),
                {"worker_id": worker_id, "claim_token": claim_token},
            ).scalar_one_or_none()
        if node_id is None:
            return None
        return self.get_node(node_id)

    def resume_visual_nodes(self) -> int:
        with self._engine.begin() as connection:
            updated = connection.execute(
                text(
                    """
                    UPDATE task_nodes
                    SET status = 10,
                        reason = '视觉编排已恢复，等待重新发布命令',
                        ready_at = now(),
                        updated_at = now()
                    WHERE status = 30
                      AND required_capability IS NULL
                      AND node_code IN (
                          'TEACHER_BEHAVIOR_ANALYSIS',
                          'STUDENT_BEHAVIOR_ANALYSIS'
                      )
                    """
                )
            ).rowcount
        return int(updated or 0)

    def defer_capability_nodes(self, capability: str) -> int:
        with self._engine.begin() as connection:
            updated = connection.execute(
                text(
                    """
                    UPDATE task_nodes
                    SET status = 30,
                        reason = :reason,
                        updated_at = now()
                    WHERE status = 10
                      AND required_capability = :capability
                    """
                ),
                {
                    "capability": capability,
                    "reason": f"等待算子能力可用: {capability}",
                },
            ).rowcount
        return int(updated or 0)

    def resume_capability_nodes(self, capability: str) -> int:
        with self._engine.begin() as connection:
            updated = connection.execute(
                text(
                    """
                    UPDATE task_nodes
                    SET status = 10,
                        reason = '算子容量已恢复，等待调度',
                        ready_at = now(),
                        updated_at = now()
                    WHERE status = 30
                      AND required_capability = :capability
                    """
                ),
                {"capability": capability},
            ).rowcount
        return int(updated or 0)

    def claim_outbox_events(self, batch_size: int) -> list[OutboxRecord]:
        if batch_size <= 0:
            raise ValueError("Outbox 批次大小必须大于 0")

        claim_token = uuid4()
        with self._engine.begin() as connection:
            rows = connection.execute(
                text(
                    """
                    WITH candidates AS (
                        SELECT event_id
                        FROM outbox_events
                        WHERE published_at IS NULL
                          AND available_at <= now()
                          AND (
                              claimed_at IS NULL
                              OR claimed_at < now() - interval '5 minutes'
                          )
                        ORDER BY available_at, created_at, event_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT :batch_size
                    )
                    UPDATE outbox_events AS event
                    SET claim_token = :claim_token,
                        claimed_at = now()
                    FROM candidates
                    WHERE event.event_id = candidates.event_id
                    RETURNING event.event_id, event.aggregate_type, event.aggregate_id,
                              event.event_type, event.payload,
                              event.claim_token, event.claimed_at
                    """
                ),
                {"batch_size": batch_size, "claim_token": claim_token},
            ).mappings()
            return [
                OutboxRecord(
                    event_id=row["event_id"],
                    aggregate_type=row["aggregate_type"],
                    aggregate_id=row["aggregate_id"],
                    event_type=row["event_type"],
                    payload=row["payload"],
                    claim_token=row["claim_token"],
                    claimed_at=row["claimed_at"],
                )
                for row in rows
            ]

    def get_outbox_state(self, event_id: UUID) -> OutboxStateRecord:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT event_id, published_at, publish_attempts, last_error,
                           claim_token, claimed_at
                    FROM outbox_events
                    WHERE event_id = :event_id
                    """
                ),
                {"event_id": event_id},
            ).mappings().one_or_none()
        if row is None:
            raise RepositoryNotFoundError(f"Outbox 事件不存在: {event_id}")
        return OutboxStateRecord(
            event_id=row["event_id"],
            published_at=row["published_at"],
            publish_attempts=int(row["publish_attempts"]),
            last_error=row["last_error"],
            claim_token=row["claim_token"],
            claimed_at=row["claimed_at"],
        )

    @staticmethod
    def _release_dependents(connection: Connection, prerequisite_node_id: int) -> None:
        connection.execute(
            text(
                """
                UPDATE task_nodes AS dependent
                SET completed_prerequisite_count = completed_prerequisite_count + 1,
                    updated_at = now()
                FROM task_node_dependencies AS dependency
                WHERE dependency.prerequisite_node_id = :prerequisite_node_id
                  AND dependent.id = dependency.node_id
                  AND dependent.status = 20
                """
            ),
            {"prerequisite_node_id": prerequisite_node_id},
        )
        connection.execute(
            text(
                """
                UPDATE task_nodes
                SET status = 10,
                    reason = '前置节点已完成，等待执行',
                    ready_at = now(),
                    updated_at = now()
                WHERE status = 20
                  AND prerequisite_count > 0
                  AND completed_prerequisite_count >= prerequisite_count
                """
            )
        )

    def mark_outbox_published(self, event_id: UUID, claim_token: UUID) -> None:
        with self._engine.begin() as connection:
            updated = connection.execute(
                text(
                    """
                    UPDATE outbox_events
                    SET published_at = now(),
                        publish_attempts = publish_attempts + 1,
                        last_error = NULL,
                        claim_token = NULL,
                        claimed_at = NULL
                    WHERE event_id = :event_id
                      AND claim_token = :claim_token
                      AND published_at IS NULL
                    """
                ),
                {"event_id": event_id, "claim_token": claim_token},
            ).rowcount
            if updated != 1:
                raise RepositoryNotFoundError(f"Outbox 事件租约不存在: {event_id}")

    def mark_outbox_failed(self, event_id: UUID, claim_token: UUID, error: str) -> None:
        with self._engine.begin() as connection:
            updated = connection.execute(
                text(
                    """
                    UPDATE outbox_events
                    SET publish_attempts = publish_attempts + 1,
                        last_error = :error,
                        claim_token = NULL,
                        claimed_at = NULL
                    WHERE event_id = :event_id
                      AND claim_token = :claim_token
                      AND published_at IS NULL
                    """
                ),
                {"event_id": event_id, "claim_token": claim_token, "error": error[:2000]},
            ).rowcount
            if updated != 1:
                raise RepositoryNotFoundError(f"Outbox 事件租约不存在: {event_id}")

    def complete_node(
        self,
        node_id: int,
        result: NodeResultWrite,
        *,
        reason: str,
    ) -> NodeRecord:
        with self._engine.begin() as connection:
            current_value = connection.execute(
                text("SELECT status FROM task_nodes WHERE id = :node_id FOR UPDATE"),
                {"node_id": node_id},
            ).scalar_one_or_none()
            if current_value is None:
                raise RepositoryNotFoundError(f"节点不存在: {node_id}")
            validate_node_transition(NodeStatus(current_value), NodeStatus.COMPLETED)

            connection.execute(
                text(
                    """
                    INSERT INTO node_results (
                        task_node_id, result, artifact_path, artifact_count,
                        progress, effective_params
                    )
                    VALUES (
                        :node_id, CAST(:result AS jsonb), :artifact_path, :artifact_count,
                        CAST(:progress AS jsonb), CAST(:effective_params AS jsonb)
                    )
                    ON CONFLICT (task_node_id) DO UPDATE
                    SET result = EXCLUDED.result,
                        artifact_path = EXCLUDED.artifact_path,
                        artifact_count = EXCLUDED.artifact_count,
                        progress = EXCLUDED.progress,
                        effective_params = EXCLUDED.effective_params,
                        updated_at = now()
                    """
                ),
                {
                    "node_id": node_id,
                    "result": _json(result.result),
                    "artifact_path": result.artifact_path,
                    "artifact_count": result.artifact_count,
                    "progress": _json(result.progress),
                    "effective_params": _json(result.effective_params),
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE task_nodes
                    SET status = 60, reason = :reason, finished_at = now(), updated_at = now()
                    WHERE id = :node_id
                    """
                ),
                {"node_id": node_id, "reason": reason},
            )
            self._release_dependents(connection, node_id)
        return self.get_node(node_id)

    def update_node_progress(
        self,
        node_id: int,
        progress: JsonObject,
        *,
        reason: str,
    ) -> NodeRecord:
        with self._engine.begin() as connection:
            current_value = connection.execute(
                text("SELECT status FROM task_nodes WHERE id = :node_id FOR UPDATE"),
                {"node_id": node_id},
            ).scalar_one_or_none()
            if current_value is None:
                raise RepositoryNotFoundError(f"节点不存在: {node_id}")
            if NodeStatus(current_value) is not NodeStatus.RUNNING:
                raise RepositoryStateConflictError(
                    f"只有处理中节点可以更新进度: {node_id}"
                )
            connection.execute(
                text(
                    """
                    INSERT INTO node_results (task_node_id, progress)
                    VALUES (:node_id, CAST(:progress AS jsonb))
                    ON CONFLICT (task_node_id) DO UPDATE
                    SET progress = EXCLUDED.progress,
                        updated_at = now()
                    """
                ),
                {"node_id": node_id, "progress": _json(progress)},
            )
            connection.execute(
                text(
                    """
                    UPDATE task_nodes
                    SET reason = :reason,
                        updated_at = now()
                    WHERE id = :node_id
                    """
                ),
                {"node_id": node_id, "reason": reason},
            )
        return self.get_node(node_id)

    def get_node(self, node_id: int) -> NodeRecord:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT n.id, n.course_task_type_id, n.node_code, n.status, n.priority,
                           n.reason, n.required_capability, n.updated_at,
                           r.result, r.artifact_path, r.artifact_count, r.progress,
                           r.effective_params AS result_effective_params
                    FROM task_nodes AS n
                    LEFT JOIN node_results AS r ON r.task_node_id = n.id
                    WHERE n.id = :node_id
                    """
                ),
                {"node_id": node_id},
            ).mappings().one_or_none()
        if row is None:
            raise RepositoryNotFoundError(f"节点不存在: {node_id}")
        return _node_record(row)

    def list_nodes(self, course_task_type_id: int) -> list[NodeRecord]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT n.id, n.course_task_type_id, n.node_code, n.status, n.priority,
                           n.reason, n.required_capability, n.updated_at,
                           r.result, r.artifact_path, r.artifact_count, r.progress,
                           r.effective_params AS result_effective_params
                    FROM task_nodes AS n
                    LEFT JOIN node_results AS r ON r.task_node_id = n.id
                    WHERE n.course_task_type_id = :course_task_type_id
                    ORDER BY n.created_at, n.id
                    """
                ),
                {"course_task_type_id": course_task_type_id},
            ).mappings()
            return [_node_record(row) for row in rows]

    def list_running_ppt_slice_nodes(self) -> list[NodeRecord]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT n.id, n.course_task_type_id, n.node_code, n.status, n.priority,
                           n.reason, n.required_capability, n.updated_at,
                           r.result, r.artifact_path, r.artifact_count, r.progress,
                           r.effective_params AS result_effective_params
                    FROM task_nodes AS n
                    LEFT JOIN node_results AS r ON r.task_node_id = n.id
                    WHERE n.node_code = 'PPT_SLICE'
                      AND n.status = 50
                    ORDER BY n.updated_at, n.id
                    """
                )
            ).mappings()
            return [_node_record(row) for row in rows]

    def list_running_visual_nodes(self) -> list[NodeRecord]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT n.id, n.course_task_type_id, n.node_code, n.status, n.priority,
                           n.reason, n.required_capability, n.updated_at,
                           r.result, r.artifact_path, r.artifact_count, r.progress,
                           r.effective_params AS result_effective_params
                    FROM task_nodes AS n
                    LEFT JOIN node_results AS r ON r.task_node_id = n.id
                    WHERE n.status = 50
                      AND n.required_capability IS NULL
                      AND n.node_code IN (
                          'TEACHER_BEHAVIOR_ANALYSIS',
                          'STUDENT_BEHAVIOR_ANALYSIS'
                      )
                    ORDER BY n.updated_at, n.id
                    """
                )
            ).mappings()
            return [_node_record(row) for row in rows]

    def create_node_work_items(
        self,
        task_node_id: int,
        items: list[NodeWorkItemWrite],
    ) -> list[NodeWorkItemRecord]:
        with self._engine.begin() as connection:
            for item in items:
                connection.execute(
                    text(
                        """
                        INSERT INTO node_work_items (
                            task_node_id, item_key, ordinal, status, reason, result
                        )
                        VALUES (
                            :task_node_id, :item_key, :ordinal, :status, :reason,
                            CAST(:result AS jsonb)
                        )
                        ON CONFLICT (task_node_id, item_key) DO NOTHING
                        """
                    ),
                    {
                        "task_node_id": task_node_id,
                        "item_key": item.item_key,
                        "ordinal": item.ordinal,
                        "status": item.status.value,
                        "reason": item.reason,
                        "result": _json(item.result),
                    },
                )
        return self.list_node_work_items(task_node_id)

    def list_node_work_items(self, task_node_id: int) -> list[NodeWorkItemRecord]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT id, task_node_id, item_key, ordinal, status, reason, result
                    FROM node_work_items
                    WHERE task_node_id = :task_node_id
                    ORDER BY ordinal, id
                    """
                ),
                {"task_node_id": task_node_id},
            ).mappings()
            return [
                NodeWorkItemRecord(
                    id=row["id"],
                    task_node_id=row["task_node_id"],
                    item_key=row["item_key"],
                    ordinal=row["ordinal"],
                    status=NodeStatus(row["status"]),
                    reason=row["reason"],
                    result=row["result"],
                )
                for row in rows
            ]

    def complete_node_work_item(
        self,
        task_node_id: int,
        item_key: str,
        result: JsonObject,
        *,
        reason: str,
    ) -> WorkItemProgress:
        with self._engine.begin() as connection:
            work_item_id = connection.execute(
                text(
                    """
                    SELECT id
                    FROM node_work_items
                    WHERE task_node_id = :task_node_id
                      AND item_key = :item_key
                    FOR UPDATE
                    """
                ),
                {"task_node_id": task_node_id, "item_key": item_key},
            ).scalar_one_or_none()
            if work_item_id is None:
                raise RepositoryNotFoundError(
                    f"节点子任务不存在: {task_node_id}/{item_key}"
                )

            connection.execute(
                text(
                    """
                    UPDATE node_work_items
                    SET status = 60,
                        reason = :reason,
                        result = CAST(:result AS jsonb),
                        updated_at = now()
                    WHERE id = :work_item_id
                    """
                ),
                {
                    "work_item_id": work_item_id,
                    "reason": reason,
                    "result": _json(result),
                },
            )
            counts = connection.execute(
                text(
                    """
                    SELECT count(*) FILTER (WHERE status = 60) AS completed_count,
                           count(*) AS total_count
                    FROM node_work_items
                    WHERE task_node_id = :task_node_id
                    """
                ),
                {"task_node_id": task_node_id},
            ).mappings().one()
            progress = WorkItemProgress(
                completed_count=int(counts["completed_count"]),
                total_count=int(counts["total_count"]),
            )
            connection.execute(
                text(
                    """
                    INSERT INTO node_results (task_node_id, result, progress)
                    VALUES (
                        :task_node_id,
                        jsonb_build_object(CAST(:item_key AS text), CAST(:result AS jsonb)),
                        CAST(:progress AS jsonb)
                    )
                    ON CONFLICT (task_node_id) DO UPDATE
                    SET result = COALESCE(node_results.result, '{}'::jsonb)
                                 || EXCLUDED.result,
                        progress = EXCLUDED.progress,
                        updated_at = now()
                    """
                ),
                {
                    "task_node_id": task_node_id,
                    "item_key": item_key,
                    "result": _json(result),
                    "progress": _json(
                        {
                            "completed_count": progress.completed_count,
                            "total_count": progress.total_count,
                        }
                    ),
                },
            )
        return progress
