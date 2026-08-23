from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from deploy.scripts.migration_executor import (
    AppliedMigration,
    MigrationError,
    MigrationExecutor,
    discover_migrations,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakeDatabase:
    def __init__(self, applied: list[AppliedMigration] | None = None) -> None:
        self.applied = list(applied or [])
        self.calls: list[int] = []
        self.fail_version: int | None = None

    def ensure_ledger(self) -> None:
        return None

    def read_ledger(self) -> list[AppliedMigration]:
        return list(self.applied)

    def apply(self, migration: object) -> None:
        version = migration.version  # type: ignore[attr-defined]
        self.calls.append(version)
        if self.fail_version == version:
            raise MigrationError("模拟迁移中断")
        self.applied.append(
            AppliedMigration(
                version=version,
                filename=migration.filename,  # type: ignore[attr-defined]
                checksum_sha256=migration.checksum_sha256,  # type: ignore[attr-defined]
            )
        )


def test_discovers_contiguous_0001_through_0007() -> None:
    migrations = discover_migrations(PROJECT_ROOT / "migrations")

    assert [migration.version for migration in migrations] == list(range(1, 8))
    assert migrations[0].filename == "0001_initial.sql"
    assert migrations[-1].filename == "0007_retire_text_analysis_comments.sql"


def test_discovers_future_contiguous_migration_without_executor_changes(tmp_path: Path) -> None:
    for source in sorted((PROJECT_ROOT / "migrations").glob("*.sql")):
        shutil.copyfile(source, tmp_path / source.name)
    (tmp_path / "0008_future.sql").write_text(
        "BEGIN;\nSELECT 1;\nCOMMIT;\n",
        encoding="utf-8",
    )

    migrations = discover_migrations(tmp_path)

    assert [migration.version for migration in migrations] == list(range(1, 9))


def test_first_run_repeat_and_interrupted_resume() -> None:
    migrations = discover_migrations(PROJECT_ROOT / "migrations")
    database = FakeDatabase()
    executor = MigrationExecutor(database, migrations)

    assert executor.run() == list(range(1, 8))
    assert executor.run() == []

    interrupted = FakeDatabase()
    interrupted.fail_version = 4
    with pytest.raises(MigrationError, match="模拟迁移中断"):
        MigrationExecutor(interrupted, migrations).run()
    assert interrupted.calls == [1, 2, 3, 4]
    interrupted.fail_version = None
    assert MigrationExecutor(interrupted, migrations).run() == [4, 5, 6, 7]


def test_unknown_or_changed_applied_version_fails_closed() -> None:
    migrations = discover_migrations(PROJECT_ROOT / "migrations")
    unknown = FakeDatabase([AppliedMigration(99, "0099_unknown.sql", "f" * 64)])
    with pytest.raises(MigrationError, match="数据库包含未知迁移版本"):
        MigrationExecutor(unknown, migrations).run()

    changed = FakeDatabase([AppliedMigration(1, migrations[0].filename, "f" * 64)])
    with pytest.raises(MigrationError, match="迁移账本与当前文件不一致"):
        MigrationExecutor(changed, migrations).run()


def test_migration_transaction_records_checksum_after_sql() -> None:
    migration = discover_migrations(PROJECT_ROOT / "migrations")[0]

    transaction = migration.transaction_sql()

    assert transaction.startswith("BEGIN;\n")
    assert transaction.endswith("COMMIT;\n")
    assert transaction.count("BEGIN;") == 1
    assert transaction.count("COMMIT;") == 1
    assert "INSERT INTO algorithm_schema_migrations" in transaction
    assert transaction.index("CREATE TABLE course_jobs") < transaction.index(
        "INSERT INTO algorithm_schema_migrations"
    )
