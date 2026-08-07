from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_MIGRATION = PROJECT_ROOT / "migrations/0002_scheduling_indexes.sql"


def test_scheduling_index_migration_covers_hot_queries() -> None:
    sql = INDEX_MIGRATION.read_text(encoding="utf-8").lower()

    assert "idx_task_nodes_ready_claim" in sql
    assert "idx_course_task_types_task_query" in sql
    assert "idx_task_nodes_task_query" in sql
    assert "idx_outbox_events_pending_scan" in sql
    assert "where published_at is null" in sql
