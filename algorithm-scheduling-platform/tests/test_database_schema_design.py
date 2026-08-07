from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DOCUMENT = PROJECT_ROOT / "docs/database-logical-schema.md"


def test_logical_schema_documents_required_entities_and_keys() -> None:
    document = SCHEMA_DOCUMENT.read_text(encoding="utf-8")

    for table_name in (
        "course_jobs",
        "course_task_types",
        "task_nodes",
        "node_results",
        "outbox_events",
        "operator_instances",
        "operator_instance_events",
        "visual_fallback_values",
    ):
        assert f"`{table_name}`" in document

    assert "`(task_id, task_type)`" in document
    assert "PostgreSQL" in document
    assert "Redis" in document


def test_logical_schema_preserves_contract_boundaries() -> None:
    document = SCHEMA_DOCUMENT.read_text(encoding="utf-8")

    assert "JSONB" in document
    assert "path/count" in document
    assert "result" in document
    assert "front_region_provided" in document
    assert "back_region_provided" in document
    assert "tias" not in document.lower()
