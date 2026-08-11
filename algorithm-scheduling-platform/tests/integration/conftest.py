from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POSTGRES_TEMPLATE_DSN = (
    "postgresql+psycopg://algorithm:algorithm@127.0.0.1:5432/"
    "algorithm_control_milestone1_test"
)


@dataclass(frozen=True)
class Milestone1Postgres:
    engine: Engine
    dsn: str
    raw_dsn: str


def _raw_psycopg_dsn(dsn: str) -> str:
    url = make_url(dsn)
    drivername = url.drivername.split("+", 1)[0]
    return url.set(drivername=drivername).render_as_string(hide_password=False)


def _unique_database_name(template_dsn: str) -> str:
    template_name = make_url(template_dsn).database or "algorithm_control_milestone1"
    base = re.sub(r"[^A-Za-z0-9_]+", "_", template_name.removesuffix("_test"))
    worker = re.sub(r"[^A-Za-z0-9_]+", "_", os.getenv("PYTEST_XDIST_WORKER", "main"))
    suffix = f"_{worker[:12]}_{uuid4().hex[:8]}_test"
    base = base[: 63 - len(suffix)].rstrip("_") or "milestone1"
    database_name = f"{base}{suffix}"
    assert database_name.endswith("_test")
    assert len(database_name.encode("ascii")) <= 63
    return database_name


@pytest.fixture(scope="session")
def milestone1_postgres() -> Iterator[Milestone1Postgres]:
    template_dsn = os.getenv(
        "PLATFORM_MILESTONE1_TEST_POSTGRES_DSN",
        DEFAULT_POSTGRES_TEMPLATE_DSN,
    )
    database_name = _unique_database_name(template_dsn)
    dsn = make_url(template_dsn).set(database=database_name).render_as_string(
        hide_password=False
    )
    raw_dsn = _raw_psycopg_dsn(dsn)
    admin_dsn = os.getenv(
        "PLATFORM_TEST_POSTGRES_ADMIN_DSN",
        make_url(raw_dsn).set(database="postgres").render_as_string(hide_password=False),
    )
    admin_dsn = _raw_psycopg_dsn(admin_dsn)
    engine: Engine | None = None
    database_created = False

    try:
        try:
            with psycopg.connect(admin_dsn, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
                )
            database_created = True
        except psycopg.OperationalError as exc:
            pytest.skip(f"PostgreSQL 集成测试环境不可用: {exc}")

        engine = create_engine(dsn)
        migration_paths = sorted((PROJECT_ROOT / "migrations").glob("*.sql"))
        assert migration_paths, "PostgreSQL 集成测试未找到迁移文件"
        with psycopg.connect(raw_dsn, autocommit=True) as connection:
            for migration_path in migration_paths:
                connection.execute(migration_path.read_text(encoding="utf-8"))

        yield Milestone1Postgres(engine=engine, dsn=dsn, raw_dsn=raw_dsn)
    finally:
        if engine is not None:
            engine.dispose()
        if database_created:
            assert database_name.endswith("_test"), (
                "PostgreSQL 集成测试拒绝删除非 _test 数据库: "
                f"{database_name!r}"
            )
            with psycopg.connect(admin_dsn, autocommit=True) as admin:
                admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (database_name,),
                )
                admin.execute(
                    sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name))
                )
