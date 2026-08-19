from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMENT_MIGRATION = PROJECT_ROOT / "migrations/0004_schema_comments.sql"
FOUNDATION_FORWARD_MIGRATION = (
    PROJECT_ROOT / "migrations/0005_operator_audit_and_status_comments.sql"
)
SUBMISSION_FORWARD_MIGRATION = (
    PROJECT_ROOT / "migrations/0006_course_task_type_submission.sql"
)

EXPECTED_COLUMNS = {
    "course_jobs": (
        "id",
        "task_id",
        "input_snapshot",
        "created_at",
        "updated_at",
    ),
    "course_task_types": (
        "id",
        "task_id",
        "task_type",
        "status",
        "priority",
        "reason",
        "request_payload",
        "effective_params",
        "requested_at",
        "started_at",
        "finished_at",
        "updated_at",
    ),
    "task_nodes": (
        "id",
        "course_task_type_id",
        "node_code",
        "status",
        "priority",
        "reason",
        "required_capability",
        "prerequisite_count",
        "completed_prerequisite_count",
        "attempt",
        "ready_at",
        "claimed_by",
        "claim_token",
        "claimed_at",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    ),
    "node_results": (
        "task_node_id",
        "result",
        "artifact_path",
        "artifact_count",
        "progress",
        "effective_params",
        "result_version",
        "created_at",
        "updated_at",
    ),
    "node_work_items": (
        "id",
        "task_node_id",
        "item_key",
        "ordinal",
        "status",
        "reason",
        "result",
        "attempt",
        "created_at",
        "updated_at",
    ),
    "outbox_events": (
        "event_id",
        "aggregate_type",
        "aggregate_id",
        "event_type",
        "payload",
        "available_at",
        "published_at",
        "publish_attempts",
        "last_error",
        "created_at",
        "claim_token",
        "claimed_at",
    ),
    "operator_instances": (
        "instance_id",
        "operator_code",
        "capabilities",
        "service_url",
        "model_version",
        "api_version",
        "declared_capacity",
        "labels",
        "desired_state",
        "last_registered_at",
        "last_heartbeat_at",
        "unregistered_at",
        "created_at",
        "updated_at",
    ),
    "operator_instance_events": (
        "id",
        "instance_id",
        "event_type",
        "event_payload",
        "occurred_at",
    ),
    "visual_fallback_values": (
        "id",
        "course_task_type_id",
        "metric_code",
        "value",
        "created_at",
    ),
    "task_node_dependencies": (
        "node_id",
        "prerequisite_node_id",
    ),
}


def test_comment_migration_documents_every_scheduling_table_and_column() -> None:
    sql = COMMENT_MIGRATION.read_text(encoding="utf-8").lower()

    for table_name, columns in EXPECTED_COLUMNS.items():
        assert f"comment on table {table_name} is '" in sql
        for column_name in columns:
            assert f"comment on column {table_name}.{column_name} is '" in sql


def test_comment_migration_is_forward_only_and_non_destructive() -> None:
    sql = COMMENT_MIGRATION.read_text(encoding="utf-8").lower()

    assert "begin;" in sql
    assert "commit;" in sql
    for forbidden in ("drop table", "drop column", "delete from", "truncate"):
        assert forbidden not in sql


def test_0005_forward_migration_corrects_queued_and_processing_status_comments() -> None:
    assert FOUNDATION_FORWARD_MIGRATION.is_file(), (
        "里程碑 1 必须使用新的 0005 前向迁移修正已发布的状态注释"
    )
    sql = FOUNDATION_FORWARD_MIGRATION.read_text(encoding="utf-8").lower()

    assert (
        "comment on column course_task_types.status is "
        "'任务类型整数状态：10 等待、20 前置等待、30 等待算子、"
        "40 已排队、50 处理中、60 完成、70 失败、80 取消';"
    ) in sql
    assert (
        "comment on column task_nodes.status is "
        "'节点整数状态：10 就绪、20 前置等待、30 等待算子、"
        "40 已排队、50 处理中、60 完成、70 失败、80 取消';"
    ) in sql
    assert (
        "comment on column node_work_items.status is "
        "'子项整数状态：10 等待、20 前置等待、30 等待算子、"
        "40 已排队、50 处理中、60 完成、70 失败、80 取消';"
    ) in sql


def test_0005_forward_migration_adds_operator_event_history_index() -> None:
    assert FOUNDATION_FORWARD_MIGRATION.is_file(), (
        "里程碑 1 必须提供算子事件历史查询索引的 0005 前向迁移"
    )
    sql = " ".join(
        FOUNDATION_FORWARD_MIGRATION.read_text(encoding="utf-8").lower().split()
    )

    assert (
        "create index idx_operator_instance_events_instance_time "
        "on operator_instance_events (instance_id, occurred_at desc, id desc);"
    ) in sql
    for forbidden in ("drop table", "drop column", "delete from", "truncate"):
        assert forbidden not in sql


def test_0006_forward_migration_persists_and_documents_submission_id() -> None:
    sql = " ".join(
        SUBMISSION_FORWARD_MIGRATION.read_text(encoding="utf-8").lower().split()
    )

    assert "add column submission_id uuid" in sql
    assert "set submission_id = gen_random_uuid()" in sql
    assert "alter column submission_id set not null" in sql
    assert "comment on column course_task_types.submission_id is '" in sql
    for forbidden in ("drop table", "drop column", "delete from", "truncate"):
        assert forbidden not in sql
