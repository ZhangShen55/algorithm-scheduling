from __future__ import annotations

import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from deploy.scripts.migration_executor import (
    DockerComposePostgres,
    MigrationError,
    MigrationExecutor,
    discover_migrations,
)

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class IsolatedDatabase:
    name: str
    dsn: str


class LedgerDriftPostgres(DockerComposePostgres):
    def __init__(
        self,
        *,
        platform_root: Path,
        compose_path: Path,
        git_sha: str,
        database_name: str,
        drift_dsn: str,
    ) -> None:
        super().__init__(
            platform_root=platform_root,
            compose_path=compose_path,
            git_sha=git_sha,
            database_name=database_name,
        )
        self._drift_dsn = drift_dsn
        self._drifted = False

    def _schema_signature_digest(
        self,
        schema_name: str,
        *,
        only_table: str | None = None,
    ) -> str:
        digest = super()._schema_signature_digest(
            schema_name,
            only_table=only_table,
        )
        if (
            schema_name == "public"
            and only_table == "algorithm_schema_migrations"
            and not self._drifted
        ):
            with psycopg.connect(self._drift_dsn, autocommit=True) as connection:
                connection.execute(
                    "ALTER TABLE public.algorithm_schema_migrations DROP CONSTRAINT "
                    "algorithm_schema_migrations_filename_key"
                )
            self._drifted = True
        return digest


class ConcurrentCreatePostgres(DockerComposePostgres):
    def __init__(
        self,
        *,
        platform_root: Path,
        compose_path: Path,
        git_sha: str,
        database_name: str,
        contender_dsn: str,
    ) -> None:
        super().__init__(
            platform_root=platform_root,
            compose_path=compose_path,
            git_sha=git_sha,
            database_name=database_name,
        )
        self._contender_dsn = contender_dsn
        self._instrumented = False
        self.concurrent_create_blocked = False
        self.competitor_error: BaseException | None = None

    def _try_concurrent_create(self) -> None:
        try:
            time.sleep(0.2)
            with psycopg.connect(self._contender_dsn, autocommit=True) as connection:
                connection.execute("SET lock_timeout TO '100ms'")
                try:
                    connection.execute("CREATE TABLE public.concurrent_drift (id integer)")
                except psycopg.errors.LockNotAvailable:
                    self.concurrent_create_blocked = True
        except BaseException as error:
            self.competitor_error = error

    def _psql(self, sql_text: str, *, tuples_only: bool = False) -> str:
        guard_marker = "$maintenance_guard$;"
        if guard_marker in sql_text and not self._instrumented:
            self._instrumented = True
            competitor = Thread(target=self._try_concurrent_create, daemon=True)
            competitor.start()
            instrumented_sql = sql_text.replace(
                guard_marker,
                guard_marker + "\nSELECT pg_sleep(1);",
                1,
            )
            try:
                return super()._psql(instrumented_sql, tuples_only=tuples_only)
            finally:
                competitor.join(timeout=5)
                if competitor.is_alive():
                    raise AssertionError("并发 DDL 验证线程未结束")
        return super()._psql(sql_text, tuples_only=tuples_only)


class ConcurrentSetvalPostgres(DockerComposePostgres):
    def __init__(
        self,
        *,
        platform_root: Path,
        compose_path: Path,
        git_sha: str,
        database_name: str,
        contender_dsn: str,
    ) -> None:
        super().__init__(
            platform_root=platform_root,
            compose_path=compose_path,
            git_sha=git_sha,
            database_name=database_name,
        )
        self._contender_dsn = contender_dsn
        self._instrumented = False
        self.concurrent_setval_blocked = False
        self.competitor_error: BaseException | None = None

    def _try_concurrent_setval(self) -> None:
        try:
            time.sleep(0.2)
            with psycopg.connect(self._contender_dsn, autocommit=True) as connection:
                connection.execute("SET lock_timeout TO '100ms'")
                try:
                    connection.execute(
                        "SELECT setval('public.course_jobs_id_seq'::regclass, 1, true)"
                    )
                except psycopg.errors.LockNotAvailable:
                    self.concurrent_setval_blocked = True
        except BaseException as error:
            self.competitor_error = error

    def _psql(self, sql_text: str, *, tuples_only: bool = False) -> str:
        lock_marker = "$sequence_relation_lock$;"
        if lock_marker in sql_text and not self._instrumented:
            self._instrumented = True
            competitor = Thread(target=self._try_concurrent_setval, daemon=True)
            competitor.start()
            instrumented_sql = sql_text.replace(
                lock_marker,
                lock_marker + "\nSELECT pg_sleep(1);",
                1,
            )
            try:
                return super()._psql(instrumented_sql, tuples_only=tuples_only)
            finally:
                competitor.join(timeout=5)
                if competitor.is_alive():
                    raise AssertionError("并发 setval 验证线程未结束")
        return super()._psql(sql_text, tuples_only=tuples_only)


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

    assert executor.adopt_existing() == []
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


@pytest.mark.parametrize("legacy_version", (6, 7))
def test_real_postgres_adopts_exact_legacy_schema_prefix(
    isolated_migration_database: IsolatedDatabase,
    legacy_version: int,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    migrations = discover_migrations(project_root / "migrations")
    with psycopg.connect(isolated_migration_database.dsn, autocommit=True) as connection:
        for migration in migrations[:legacy_version]:
            connection.execute(migration.sql)

    database = DockerComposePostgres(
        platform_root=project_root,
        compose_path=project_root / "deploy/docker-compose.infrastructure.yml",
        git_sha="b" * 40,
        database_name=isolated_migration_database.name,
    )
    executor = MigrationExecutor(database, migrations)
    assert executor.adopt_existing() == list(range(1, legacy_version + 1))
    assert executor.run() == list(range(legacy_version + 1, 8))

    with psycopg.connect(isolated_migration_database.dsn) as connection:
        rows = connection.execute(
            "SELECT version, applied_git_sha FROM algorithm_schema_migrations ORDER BY version"
        ).fetchall()
    assert rows == [(version, "b" * 40) for version in range(1, 8)]


@pytest.mark.parametrize(
    "drift_sql",
    (
        "DROP INDEX idx_operator_instance_events_instance_time",
        "UPDATE pg_index SET indisvalid = false "
        "WHERE indexrelid = 'idx_operator_instance_events_instance_time'::regclass",
        'ALTER TABLE operator_instances ALTER COLUMN model_version TYPE text COLLATE "C"',
        "DELETE FROM pg_depend WHERE classid = 'pg_class'::regclass "
        "AND objid = 'course_jobs_id_seq'::regclass AND deptype = 'i'",
        "COMMENT ON TABLE course_jobs IS E'"
        "课程主任务表，一行对应 A 服务提供的一个全局唯一课程 task_id"
        "\\n额外漂移内容'",
        "UPDATE pg_class SET relam = 0 WHERE oid = 'course_jobs'::regclass",
        "ALTER SEQUENCE course_jobs_id_seq SET UNLOGGED",
    ),
)
def test_real_postgres_adoption_fails_closed_on_legacy_schema_drift(
    isolated_migration_database: IsolatedDatabase,
    drift_sql: str,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    migrations = discover_migrations(project_root / "migrations")
    with psycopg.connect(isolated_migration_database.dsn, autocommit=True) as connection:
        for migration in migrations:
            connection.execute(migration.sql)
        connection.execute(drift_sql)

    database = DockerComposePostgres(
        platform_root=project_root,
        compose_path=project_root / "deploy/docker-compose.infrastructure.yml",
        git_sha="c" * 40,
        database_name=isolated_migration_database.name,
    )
    expected_error = (
        "PostgreSQL 迁移命令失败"
        if "SET relam" in drift_sql
        else "schema 与连续迁移前缀不一致"
    )
    with pytest.raises(MigrationError, match=expected_error):
        MigrationExecutor(database, migrations).adopt_existing()

    with psycopg.connect(isolated_migration_database.dsn) as connection:
        count = connection.execute(
            "SELECT count(*) FROM algorithm_schema_migrations"
        ).fetchone()
    assert count == (0,)


def test_real_postgres_adoption_ignores_ambient_search_path(
    isolated_migration_database: IsolatedDatabase,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    migrations = discover_migrations(project_root / "migrations")
    with psycopg.connect(isolated_migration_database.dsn, autocommit=True) as connection:
        for migration in migrations[:6]:
            connection.execute(migration.sql)
        connection.execute("CREATE SCHEMA shadow")
        connection.execute(
            sql.SQL("ALTER DATABASE {} SET search_path TO shadow, public").format(
                sql.Identifier(isolated_migration_database.name)
            )
        )

    database = DockerComposePostgres(
        platform_root=project_root,
        compose_path=project_root / "deploy/docker-compose.infrastructure.yml",
        git_sha="d" * 40,
        database_name=isolated_migration_database.name,
    )
    executor = MigrationExecutor(database, migrations)
    assert executor.adopt_existing() == list(range(1, 7))
    assert executor.run() == [7]

    with psycopg.connect(isolated_migration_database.dsn) as connection:
        public_count = connection.execute(
            "SELECT count(*) FROM public.algorithm_schema_migrations"
        ).fetchone()
        shadow_table = connection.execute(
            "SELECT to_regclass('shadow.algorithm_schema_migrations')"
        ).fetchone()
    assert public_count == (7,)
    assert shadow_table == (None,)


def test_real_postgres_adoption_rejects_non_public_ledger(
    isolated_migration_database: IsolatedDatabase,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    migrations = discover_migrations(project_root / "migrations")
    with psycopg.connect(isolated_migration_database.dsn, autocommit=True) as connection:
        for migration in migrations[:6]:
            connection.execute(migration.sql)
        connection.execute("CREATE SCHEMA shadow")
        connection.execute(
            "CREATE TABLE shadow.algorithm_schema_migrations (version integer)"
        )

    database = DockerComposePostgres(
        platform_root=project_root,
        compose_path=project_root / "deploy/docker-compose.infrastructure.yml",
        git_sha="e" * 40,
        database_name=isolated_migration_database.name,
    )
    with pytest.raises(MigrationError, match="非 public 迁移账本"):
        MigrationExecutor(database, migrations).adopt_existing()

    with psycopg.connect(isolated_migration_database.dsn) as connection:
        public_ledger = connection.execute(
            "SELECT to_regclass('public.algorithm_schema_migrations')"
        ).fetchone()
    assert public_ledger == (None,)


def test_real_postgres_adoption_rejects_malformed_empty_public_ledger(
    isolated_migration_database: IsolatedDatabase,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    migrations = discover_migrations(project_root / "migrations")
    with psycopg.connect(isolated_migration_database.dsn, autocommit=True) as connection:
        for migration in migrations[:6]:
            connection.execute(migration.sql)
        connection.execute(
            """
            CREATE TABLE public.algorithm_schema_migrations (
                version integer NOT NULL,
                filename text NOT NULL,
                checksum_sha256 char(64) NOT NULL,
                applied_git_sha char(40) NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )

    database = DockerComposePostgres(
        platform_root=project_root,
        compose_path=project_root / "deploy/docker-compose.infrastructure.yml",
        git_sha="1" * 40,
        database_name=isolated_migration_database.name,
    )
    with pytest.raises(MigrationError, match="迁移账本结构"):
        MigrationExecutor(database, migrations).adopt_existing()

    with psycopg.connect(isolated_migration_database.dsn) as connection:
        constraints = connection.execute(
            "SELECT count(*) FROM pg_constraint "
            "WHERE conrelid = 'public.algorithm_schema_migrations'::regclass"
        ).fetchone()
    assert constraints == (0,)


def test_postgres_sequence_catalog_row_lock_blocks_concurrent_alter(
    isolated_migration_database: IsolatedDatabase,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    migrations = discover_migrations(project_root / "migrations")
    with psycopg.connect(isolated_migration_database.dsn, autocommit=True) as connection:
        connection.execute(migrations[0].sql)

    with (
        psycopg.connect(isolated_migration_database.dsn) as locker,
        psycopg.connect(isolated_migration_database.dsn) as contender,
    ):
        locker.execute(
            "SELECT seqrelid FROM pg_catalog.pg_sequence "
            "WHERE seqrelid = 'public.course_jobs_id_seq'::regclass FOR UPDATE"
        )
        contender.execute("SET lock_timeout TO '100ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            contender.execute("ALTER SEQUENCE public.course_jobs_id_seq CACHE 20")
        contender.rollback()
        locker.rollback()

    with psycopg.connect(isolated_migration_database.dsn) as connection:
        cache_size = connection.execute(
            "SELECT seqcache FROM pg_catalog.pg_sequence "
            "WHERE seqrelid = 'public.course_jobs_id_seq'::regclass"
        ).fetchone()
    assert cache_size == (1,)


def test_real_postgres_adoption_rechecks_ledger_under_final_lock(
    isolated_migration_database: IsolatedDatabase,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    migrations = discover_migrations(project_root / "migrations")
    with psycopg.connect(isolated_migration_database.dsn, autocommit=True) as connection:
        for migration in migrations[:6]:
            connection.execute(migration.sql)

    database = LedgerDriftPostgres(
        platform_root=project_root,
        compose_path=project_root / "deploy/docker-compose.infrastructure.yml",
        git_sha="2" * 40,
        database_name=isolated_migration_database.name,
        drift_dsn=isolated_migration_database.dsn,
    )
    with pytest.raises(MigrationError, match="PostgreSQL 迁移命令失败"):
        MigrationExecutor(database, migrations).adopt_existing()

    with psycopg.connect(isolated_migration_database.dsn) as connection:
        ledger_count = connection.execute(
            "SELECT count(*) FROM public.algorithm_schema_migrations"
        ).fetchone()
        filename_unique = connection.execute(
            "SELECT count(*) FROM pg_constraint "
            "WHERE conrelid = 'public.algorithm_schema_migrations'::regclass "
            "AND contype = 'u'"
        ).fetchone()
    assert ledger_count == (0,)
    assert filename_unique == (0,)


def test_real_postgres_adoption_blocks_concurrent_relation_creation(
    isolated_migration_database: IsolatedDatabase,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    migrations = discover_migrations(project_root / "migrations")
    with psycopg.connect(isolated_migration_database.dsn, autocommit=True) as connection:
        for migration in migrations[:6]:
            connection.execute(migration.sql)

    database = ConcurrentCreatePostgres(
        platform_root=project_root,
        compose_path=project_root / "deploy/docker-compose.infrastructure.yml",
        git_sha="3" * 40,
        database_name=isolated_migration_database.name,
        contender_dsn=isolated_migration_database.dsn,
    )
    executor = MigrationExecutor(database, migrations)
    assert executor.adopt_existing() == list(range(1, 7))
    assert database.competitor_error is None
    assert database.concurrent_create_blocked is True

    with psycopg.connect(isolated_migration_database.dsn) as connection:
        drift_table = connection.execute(
            "SELECT to_regclass('public.concurrent_drift')"
        ).fetchone()
        ledger_count = connection.execute(
            "SELECT count(*) FROM public.algorithm_schema_migrations"
        ).fetchone()
    assert drift_table == (None,)
    assert ledger_count == (6,)


def test_real_postgres_adoption_rejects_preexisting_open_transaction(
    isolated_migration_database: IsolatedDatabase,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    migrations = discover_migrations(project_root / "migrations")
    with psycopg.connect(isolated_migration_database.dsn, autocommit=True) as connection:
        for migration in migrations[:6]:
            connection.execute(migration.sql)

    database = DockerComposePostgres(
        platform_root=project_root,
        compose_path=project_root / "deploy/docker-compose.infrastructure.yml",
        git_sha="5" * 40,
        database_name=isolated_migration_database.name,
    )
    database.ensure_ledger()
    with psycopg.connect(isolated_migration_database.dsn) as contender:
        contender.execute("CREATE TABLE public.inflight_drift (id integer)")
        with pytest.raises(MigrationError, match="PostgreSQL 迁移命令失败"):
            MigrationExecutor(database, migrations).adopt_existing()
        contender.commit()

    with psycopg.connect(isolated_migration_database.dsn) as connection:
        drift_table = connection.execute(
            "SELECT to_regclass('public.inflight_drift')"
        ).fetchone()
        ledger_count = connection.execute(
            "SELECT count(*) FROM public.algorithm_schema_migrations"
        ).fetchone()
    assert drift_table == ("inflight_drift",)
    assert ledger_count == (0,)


def test_real_postgres_adoption_blocks_concurrent_setval(
    isolated_migration_database: IsolatedDatabase,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    migrations = discover_migrations(project_root / "migrations")
    with psycopg.connect(isolated_migration_database.dsn, autocommit=True) as connection:
        for migration in migrations[:6]:
            connection.execute(migration.sql)

    database = ConcurrentSetvalPostgres(
        platform_root=project_root,
        compose_path=project_root / "deploy/docker-compose.infrastructure.yml",
        git_sha="6" * 40,
        database_name=isolated_migration_database.name,
        contender_dsn=isolated_migration_database.dsn,
    )
    assert MigrationExecutor(database, migrations).adopt_existing() == list(range(1, 7))
    assert database.competitor_error is None
    assert database.concurrent_setval_blocked is True

    with psycopg.connect(isolated_migration_database.dsn) as connection:
        ledger_count = connection.execute(
            "SELECT count(*) FROM public.algorithm_schema_migrations"
        ).fetchone()
        sequence_state = connection.execute(
            "SELECT last_value, is_called FROM public.course_jobs_id_seq"
        ).fetchone()
    assert ledger_count == (6,)
    assert sequence_state == (1, False)


def test_real_postgres_adoption_rejects_identity_sequence_behind_table_data(
    isolated_migration_database: IsolatedDatabase,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    migrations = discover_migrations(project_root / "migrations")
    with psycopg.connect(isolated_migration_database.dsn, autocommit=True) as connection:
        for migration in migrations[:6]:
            connection.execute(migration.sql)
        connection.execute(
            "INSERT INTO public.course_jobs (task_id) VALUES "
            "('sequence-a'), ('sequence-b')"
        )
        connection.execute(
            "SELECT setval('public.course_jobs_id_seq'::regclass, 1, true)"
        )

    database = DockerComposePostgres(
        platform_root=project_root,
        compose_path=project_root / "deploy/docker-compose.infrastructure.yml",
        git_sha="4" * 40,
        database_name=isolated_migration_database.name,
    )
    with pytest.raises(MigrationError, match="PostgreSQL 迁移命令失败"):
        MigrationExecutor(database, migrations).adopt_existing()

    with psycopg.connect(isolated_migration_database.dsn) as connection:
        ledger_count = connection.execute(
            "SELECT count(*) FROM public.algorithm_schema_migrations"
        ).fetchone()
    assert ledger_count == (0,)


def test_real_postgres_adoption_rejects_exhausted_identity_sequence(
    isolated_migration_database: IsolatedDatabase,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    migrations = discover_migrations(project_root / "migrations")
    with psycopg.connect(isolated_migration_database.dsn, autocommit=True) as connection:
        for migration in migrations[:6]:
            connection.execute(migration.sql)
        connection.execute("INSERT INTO public.course_jobs (task_id) VALUES ('sequence-max')")
        connection.execute(
            "SELECT setval('public.course_jobs_id_seq'::regclass, "
            "9223372036854775807, true)"
        )

    database = DockerComposePostgres(
        platform_root=project_root,
        compose_path=project_root / "deploy/docker-compose.infrastructure.yml",
        git_sha="7" * 40,
        database_name=isolated_migration_database.name,
    )
    with pytest.raises(MigrationError, match="PostgreSQL 迁移命令失败"):
        MigrationExecutor(database, migrations).adopt_existing()

    with psycopg.connect(isolated_migration_database.dsn) as connection:
        ledger_count = connection.execute(
            "SELECT count(*) FROM public.algorithm_schema_migrations"
        ).fetchone()
    assert ledger_count == (0,)


@pytest.mark.parametrize(
    "invalid_rows_sql",
    (
        "INSERT INTO course_jobs (task_id) VALUES ('legacy-invalid-submission'); "
        "INSERT INTO course_task_types (task_id, task_type, submission_id) VALUES "
        "('legacy-invalid-submission', 'ASR', "
        "'00000000-0000-0000-0000-000000000000')",
        "INSERT INTO course_jobs (task_id) VALUES ('legacy-course-a'), ('legacy-course-b'); "
        "INSERT INTO course_task_types (task_id, task_type, submission_id) VALUES "
        "('legacy-course-a', 'ASR', '11111111-1111-1111-1111-111111111111'), "
        "('legacy-course-b', 'ASR', '11111111-1111-1111-1111-111111111111')",
    ),
)
def test_real_postgres_adoption_rejects_invalid_submission_backfill(
    isolated_migration_database: IsolatedDatabase,
    invalid_rows_sql: str,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    migrations = discover_migrations(project_root / "migrations")
    with psycopg.connect(isolated_migration_database.dsn, autocommit=True) as connection:
        for migration in migrations[:6]:
            connection.execute(migration.sql)
        connection.execute(invalid_rows_sql)

    database = DockerComposePostgres(
        platform_root=project_root,
        compose_path=project_root / "deploy/docker-compose.infrastructure.yml",
        git_sha="f" * 40,
        database_name=isolated_migration_database.name,
    )
    with pytest.raises(MigrationError, match="PostgreSQL 迁移命令失败"):
        MigrationExecutor(database, migrations).adopt_existing()

    with psycopg.connect(isolated_migration_database.dsn) as connection:
        count = connection.execute(
            "SELECT count(*) FROM public.algorithm_schema_migrations"
        ).fetchone()
    assert count == (0,)
