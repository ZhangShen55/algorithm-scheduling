from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from deploy.scripts import migration_executor
from deploy.scripts.migration_executor import (
    AppliedMigration,
    Migration,
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
        self.existing_version = 0
        self.adopt_error: MigrationError | None = None

    def ensure_ledger(self) -> None:
        return None

    def read_ledger(self) -> list[AppliedMigration]:
        return list(self.applied)

    def apply(self, migration: Migration) -> None:
        version = migration.version
        self.calls.append(version)
        if self.fail_version == version:
            raise MigrationError("模拟迁移中断")
        self.applied.append(
            AppliedMigration(
                version=version,
                filename=migration.filename,
                checksum_sha256=migration.checksum_sha256,
            )
        )

    def adopt_existing(self, migrations: Sequence[Migration]) -> int:
        if self.adopt_error is not None:
            raise self.adopt_error
        if self.existing_version == 0:
            return 0
        for migration in migrations[: self.existing_version]:
            self.applied.append(
                AppliedMigration(
                    version=migration.version,
                    filename=migration.filename,
                    checksum_sha256=migration.checksum_sha256,
                )
            )
        return self.existing_version


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


def test_adopts_exact_existing_prefix_once_and_empty_schema_still_migrates() -> None:
    migrations = discover_migrations(PROJECT_ROOT / "migrations")
    existing = FakeDatabase()
    existing.existing_version = 6
    executor = MigrationExecutor(existing, migrations)

    assert executor.adopt_existing() == list(range(1, 7))
    assert executor.adopt_existing() == []
    assert executor.run() == [7]
    assert existing.calls == [7]

    empty = FakeDatabase()
    empty_executor = MigrationExecutor(empty, migrations)
    assert empty_executor.adopt_existing() == []
    assert empty_executor.run() == list(range(1, 8))


def test_adoption_rejects_partial_ledger_and_schema_mismatch() -> None:
    migrations = discover_migrations(PROJECT_ROOT / "migrations")
    valid_prefix = FakeDatabase(
        [AppliedMigration(1, migrations[0].filename, migrations[0].checksum_sha256)]
    )
    assert MigrationExecutor(valid_prefix, migrations).adopt_existing() == []

    mismatch = FakeDatabase()
    mismatch.existing_version = 6
    mismatch.adopt_error = MigrationError("既有 PostgreSQL schema 与连续迁移前缀不一致")
    with pytest.raises(MigrationError, match="schema 与连续迁移前缀不一致"):
        MigrationExecutor(mismatch, migrations).adopt_existing()
    assert mismatch.applied == []


def test_cli_adopts_existing_prefix_before_applying_remaining_migrations(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = FakeDatabase()
    database.existing_version = 6
    monkeypatch.setattr(
        migration_executor,
        "DockerComposePostgres",
        lambda **_kwargs: database,
    )

    assert migration_executor.main(["--git-sha", "a" * 40, "--adopt-existing"]) == 0

    assert database.calls == [7]
    assert capsys.readouterr().out.splitlines() == [
        "database-migrations: adopted 0001,0002,0003,0004,0005,0006",
        "database-migrations: applied 0007",
    ]


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
