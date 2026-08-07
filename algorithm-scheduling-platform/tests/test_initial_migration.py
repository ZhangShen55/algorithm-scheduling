from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INITIAL_MIGRATION = PROJECT_ROOT / "migrations/0001_initial.sql"


def migration_sql() -> str:
    return INITIAL_MIGRATION.read_text(encoding="utf-8").lower()


def test_initial_migration_creates_all_logical_schema_tables() -> None:
    sql = migration_sql()

    for table_name in (
        "course_jobs",
        "course_task_types",
        "task_nodes",
        "node_results",
        "node_work_items",
        "outbox_events",
        "operator_instances",
        "operator_instance_events",
        "visual_fallback_values",
    ):
        assert f"create table {table_name}" in sql


def test_initial_migration_enforces_status_and_idempotency_constraints() -> None:
    sql = migration_sql()

    assert "check (status in (10, 20, 30, 40, 50, 60, 70, 80))" in sql
    assert "unique (task_id, task_type)" in sql
    assert "unique (course_task_type_id, node_code)" in sql
    assert "check (task_type in ('ppt', 'asr', 'teacher_behavior', 'student_behavior'))" in sql
    assert "check (priority in ('urgent', 'normal'))" in sql
