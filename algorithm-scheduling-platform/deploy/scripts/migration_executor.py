#!/usr/bin/env python3
"""Ordered PostgreSQL migration executor with an append-only checksum ledger."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

PLATFORM_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_ROOT = PLATFORM_ROOT / "migrations"
COMPOSE_PATH = PLATFORM_ROOT / "deploy/docker-compose.platform.yml"
MIGRATION_PATTERN = re.compile(r"(?P<version>[0-9]{4})_[a-z0-9_]+\.sql")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")


class MigrationError(RuntimeError):
    """Raised when migration history is incomplete, changed or cannot advance."""


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    version: int
    filename: str
    checksum_sha256: str


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    filename: str
    checksum_sha256: str
    sql: str

    def transaction_sql(self) -> str:
        lines = self.sql.strip().splitlines()
        if not lines or lines[0].strip().upper() != "BEGIN;":
            raise MigrationError(f"迁移缺少外层 BEGIN: {self.filename}")
        if lines[-1].strip().upper() != "COMMIT;":
            raise MigrationError(f"迁移缺少外层 COMMIT: {self.filename}")
        body = "\n".join(lines[1:-1]).strip()
        if re.search(r"(?im)^\s*(BEGIN|COMMIT)\s*;", body):
            raise MigrationError(f"迁移包含嵌套事务边界: {self.filename}")
        return (
            "BEGIN;\n"
            + body
            + "\n\nINSERT INTO algorithm_schema_migrations "
            + "(version, filename, checksum_sha256, applied_git_sha) VALUES "
            + f"({self.version}, '{self.filename}', '{self.checksum_sha256}', "
            + ":'migration_git_sha');\nCOMMIT;\n"
        )


def discover_migrations(root: Path) -> list[Migration]:
    if root.is_symlink() or not root.is_dir():
        raise MigrationError(f"迁移目录无效: {root}")
    migrations: list[Migration] = []
    for path in sorted(root.iterdir()):
        if path.name.startswith("."):
            continue
        match = MIGRATION_PATTERN.fullmatch(path.name)
        if match is None or path.is_symlink() or not path.is_file():
            raise MigrationError(f"迁移文件命名或类型无效: {path.name}")
        content = path.read_text(encoding="utf-8")
        migration = Migration(
            version=int(match.group("version")),
            filename=path.name,
            checksum_sha256=hashlib.sha256(content.encode()).hexdigest(),
            sql=content,
        )
        migration.transaction_sql()
        migrations.append(migration)
    expected = list(range(1, len(migrations) + 1))
    versions = [migration.version for migration in migrations]
    if not migrations or versions != expected:
        raise MigrationError(
            f"迁移版本必须从 0001 连续递增: expected={expected}, actual={versions}"
        )
    return migrations


class MigrationDatabase(Protocol):
    def ensure_ledger(self) -> None: ...

    def read_ledger(self) -> list[AppliedMigration]: ...

    def apply(self, migration: Migration) -> None: ...


class MigrationExecutor:
    def __init__(
        self,
        database: MigrationDatabase,
        migrations: Sequence[Migration],
    ) -> None:
        self._database = database
        self._migrations = list(migrations)

    def run(self) -> list[int]:
        self._database.ensure_ledger()
        applied = self._database.read_ledger()
        by_version = {migration.version: migration for migration in self._migrations}
        if len(applied) != len({row.version for row in applied}):
            raise MigrationError("迁移账本包含重复版本")
        for row in applied:
            if row.version not in by_version:
                raise MigrationError(f"数据库包含未知迁移版本: {row.version:04d}")
        expected_applied = list(range(1, len(applied) + 1))
        if sorted(row.version for row in applied) != expected_applied:
            raise MigrationError("迁移账本版本不连续")
        for row in applied:
            migration = by_version[row.version]
            if (
                row.filename != migration.filename
                or row.checksum_sha256 != migration.checksum_sha256
            ):
                raise MigrationError(f"迁移账本与当前文件不一致: {migration.filename}")
        executed: list[int] = []
        for migration in self._migrations[len(applied) :]:
            self._database.apply(migration)
            executed.append(migration.version)
        return executed


class DockerComposePostgres:
    """Run psql inside the authoritative PostgreSQL Compose service."""

    def __init__(
        self,
        *,
        platform_root: Path,
        compose_path: Path,
        git_sha: str,
    ) -> None:
        if SHA_PATTERN.fullmatch(git_sha) is None:
            raise MigrationError("git_sha 必须是完整小写 Git SHA")
        self._platform_root = platform_root
        self._compose_path = compose_path
        self._git_sha = git_sha

    def _psql(self, sql: str, *, tuples_only: bool = False) -> str:
        command = [
            "docker",
            "compose",
            "--project-directory",
            str(self._platform_root / "deploy"),
            "-f",
            str(self._compose_path),
            "exec",
            "-T",
            "postgres",
            "psql",
            "--username",
            "algorithm",
            "--dbname",
            "algorithm",
            "--no-psqlrc",
            "--set=ON_ERROR_STOP=1",
        ]
        if tuples_only:
            command.extend(("--tuples-only", "--no-align", "--field-separator=\t"))
        completed = subprocess.run(
            command,
            input=sql,
            text=True,
            capture_output=True,
            check=False,
            timeout=900,
        )
        if completed.returncode != 0:
            raise MigrationError("PostgreSQL 迁移命令失败")
        return completed.stdout

    def ensure_ledger(self) -> None:
        self._psql(
            """
CREATE TABLE IF NOT EXISTS algorithm_schema_migrations (
    version integer PRIMARY KEY CHECK (version > 0),
    filename text NOT NULL UNIQUE,
    checksum_sha256 char(64) NOT NULL,
    applied_git_sha char(40) NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now(),
    CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (applied_git_sha ~ '^[0-9a-f]{40}$')
);
COMMENT ON TABLE algorithm_schema_migrations IS
    '算法调度平台前向数据库迁移账本，迁移文件一旦执行不得改写';
"""
        )

    def read_ledger(self) -> list[AppliedMigration]:
        output = self._psql(
            "SELECT version, filename, checksum_sha256 "
            "FROM algorithm_schema_migrations ORDER BY version;\n",
            tuples_only=True,
        )
        rows: list[AppliedMigration] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) != 3 or not parts[0].isdigit():
                raise MigrationError("迁移账本查询结果无效")
            rows.append(AppliedMigration(int(parts[0]), parts[1], parts[2]))
        return rows

    def apply(self, migration: Migration) -> None:
        sql = "\\set migration_git_sha '" + self._git_sha + "'\n" + migration.transaction_sql()
        self._psql(sql)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按顺序执行算法调度平台 PostgreSQL 迁移",
        allow_abbrev=False,
    )
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--platform-root", type=Path, default=PLATFORM_ROOT)
    parser.add_argument("--migrations-root", type=Path, default=MIGRATIONS_ROOT)
    parser.add_argument("--compose-path", type=Path, default=COMPOSE_PATH)
    parser.add_argument("--plan", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    migrations = discover_migrations(args.migrations_root)
    if args.plan:
        for migration in migrations:
            print(f"{migration.version:04d} {migration.checksum_sha256} {migration.filename}")
        return 0
    database = DockerComposePostgres(
        platform_root=args.platform_root,
        compose_path=args.compose_path,
        git_sha=args.git_sha,
    )
    executed = MigrationExecutor(database, migrations).run()
    if executed:
        print("database-migrations: applied " + ",".join(f"{item:04d}" for item in executed))
    else:
        print("database-migrations: already current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
