from pathlib import Path

import pytest

from scripts.check_migrations import validate_migration_names

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_makefile_exposes_required_quality_commands() -> None:
    makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")

    for target in (
        "lint:",
        "type-check:",
        "test:",
        "contract-test:",
        "migration-check:",
        "compose-check:",
        "verify:",
    ):
        assert target in makefile


def test_ci_runs_the_single_verify_entrypoint() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "pip install -e '.[dev]'" in workflow
    assert "make verify" in workflow


def test_migration_validator_checks_order_and_names(tmp_path: Path) -> None:
    (tmp_path / "0001_initial.sql").write_text("select 1;", encoding="utf-8")
    (tmp_path / "0002_add_outbox.sql").write_text("select 1;", encoding="utf-8")
    validate_migration_names(tmp_path)

    (tmp_path / "latest.sql").write_text("select 1;", encoding="utf-8")
    with pytest.raises(ValueError, match="迁移文件名"):
        validate_migration_names(tmp_path)
