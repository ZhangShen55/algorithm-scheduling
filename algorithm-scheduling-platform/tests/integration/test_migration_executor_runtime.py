from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from deploy.scripts.migration_executor import (
    DockerComposePostgres,
    MigrationExecutor,
    discover_migrations,
)

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class IsolatedDatabase:
    name: str
    dsn: str


@pytest.fixture
def isolated_migration_database() -> Iterator[IsolatedDatabase]:
    name = f"algorithm_migration_{uuid4().hex[:12]}_test"
    admin_dsn = os.getenv(
        "PLATFORM_TEST_POSTGRES_ADMIN_DSN",
        "postgresql://algorithm:algorithm@127.0.0.1:5432/postgres",
    )
    database_created = False
    try:
        try:
            with psycopg.connect(admin_dsn, autocommit=True) as connection:
                connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            database_created = True
        except psycopg.OperationalError as error:
            pytest.skip(f"PostgreSQL 迁移集成环境不可用: {error}")
        yield IsolatedDatabase(
            name=name,
            dsn=f"postgresql://algorithm:algorithm@127.0.0.1:5432/{name}",
        )
    finally:
        if database_created:
            assert name.endswith("_test")
            with psycopg.connect(admin_dsn, autocommit=True) as connection:
                connection.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (name,),
                )
                connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_real_postgres_migration_ledger_first_run_and_repeat(
    isolated_migration_database: IsolatedDatabase,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    migrations = discover_migrations(project_root / "migrations")
    database = DockerComposePostgres(
        platform_root=project_root,
        compose_path=project_root / "deploy/docker-compose.infrastructure.yml",
        git_sha="a" * 40,
        database_name=isolated_migration_database.name,
    )
    executor = MigrationExecutor(database, migrations)

    assert executor.run() == list(range(1, 8))
    assert executor.run() == []

    with psycopg.connect(isolated_migration_database.dsn) as connection:
        rows = connection.execute(
            "SELECT version, filename, checksum_sha256, applied_git_sha "
            "FROM algorithm_schema_migrations ORDER BY version"
        ).fetchall()
    assert [row[0] for row in rows] == list(range(1, 8))
    assert [row[1] for row in rows] == [migration.filename for migration in migrations]
    assert [row[2] for row in rows] == [migration.checksum_sha256 for migration in migrations]
    assert {row[3] for row in rows} == {"a" * 40}
