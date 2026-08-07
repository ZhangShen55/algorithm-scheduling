from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psycopg
import pytest
from control_service.app.application import factory
from control_service.app.application.factory import create_app
from control_service.app.core.config import ControlSettings
from fastapi.testclient import TestClient
from psycopg import sql
from redis import Redis
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEST_REDIS_URL = os.getenv(
    "PLATFORM_MILESTONE1_TEST_REDIS_URL",
    "redis://127.0.0.1:6379/14",
)
REDIS_PREFIX = f"milestone1-control-test:{uuid4().hex[:8]}:"
MISSING_TABLE_SCHEMA = "milestone1_readiness_missing_table_test"
MISSING_COMMENT_SCHEMA = "milestone1_readiness_missing_comment_test"
MISSING_COLUMN_SCHEMA = "milestone1_readiness_missing_column_test"
STALE_FOUNDATION_SCHEMA = "milestone1_readiness_stale_foundation_test"
READINESS_SCHEMA_NAMES = (
    MISSING_TABLE_SCHEMA,
    MISSING_COMMENT_SCHEMA,
    MISSING_COLUMN_SCHEMA,
    STALE_FOUNDATION_SCHEMA,
)

if TYPE_CHECKING:
    from conftest import Milestone1Postgres


def _delete_redis_test_prefix(redis_client: Redis) -> None:
    keys = list(redis_client.scan_iter(match=f"{REDIS_PREFIX}*", count=100))
    if keys:
        redis_client.delete(*keys)


def _schema_dsn(postgres_dsn: str, schema_name: str) -> str:
    url = make_url(postgres_dsn).update_query_dict(
        {"options": f"-csearch_path={schema_name}"}
    )
    return url.render_as_string(hide_password=False)


def _assert_test_database(postgres: Milestone1Postgres) -> None:
    database_name = make_url(postgres.dsn).database or ""
    assert database_name.endswith("_test"), (
        "Control 真实集成测试拒绝操作非 _test 数据库: " f"{database_name!r}"
    )


def _control_settings(
    *,
    postgres_dsn: str,
    redis_url: str = TEST_REDIS_URL,
    redis_prefix: str = REDIS_PREFIX,
) -> ControlSettings:
    return ControlSettings(
        postgres={
            "dsn": postgres_dsn,
            "pool_size": 4,
            "max_overflow": 4,
            "pool_timeout_seconds": 0.2,
            "pool_pre_ping": True,
        },
        redis={
            "url": redis_url,
            "key_prefix": redis_prefix,
            "heartbeat_ttl_seconds": 5,
            "max_connections": 16,
            "socket_connect_timeout_seconds": 0.1,
            "socket_timeout_seconds": 0.1,
        },
        operator_registry={
            "default_lease_ttl_seconds": 30,
            "max_lease_ttl_seconds": 120,
            "heartbeat_audit_interval_seconds": 60,
        },
        readiness={"dependency_timeout_seconds": 2.0},
    )


def _isolate_workspace_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original_adapter = factory.to_platform_settings

    def isolated_adapter(settings: ControlSettings) -> Any:
        return original_adapter(settings).model_copy(
            update={
                "course_root": tmp_path / "course",
                "result_root": tmp_path / "result",
            }
        )

    monkeypatch.setattr(factory, "to_platform_settings", isolated_adapter)


@pytest.fixture(scope="session")
def readiness_schema_dsns(
    milestone1_postgres: Milestone1Postgres,
) -> Iterator[dict[str, str]]:
    _assert_test_database(milestone1_postgres)
    assert all(schema_name.endswith("_test") for schema_name in READINESS_SCHEMA_NAMES)
    raw_dsn = milestone1_postgres.raw_dsn

    with psycopg.connect(raw_dsn, autocommit=True) as connection:
        for schema_name in READINESS_SCHEMA_NAMES:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )
            connection.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name))
            )
            connection.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name))
            )
            migration_paths = sorted((PROJECT_ROOT / "migrations").glob("*.sql"))
            if schema_name == STALE_FOUNDATION_SCHEMA:
                migration_paths = [
                    path
                    for path in migration_paths
                    if not path.name.startswith("0005_")
                ]
            for migration_path in migration_paths:
                connection.execute(migration_path.read_text(encoding="utf-8"))

        connection.execute(
            sql.SQL("DROP TABLE {}.{} CASCADE").format(
                sql.Identifier(MISSING_TABLE_SCHEMA),
                sql.Identifier("task_nodes"),
            )
        )
        connection.execute(
            sql.SQL("COMMENT ON COLUMN {}.{}.{} IS NULL").format(
                sql.Identifier(MISSING_COMMENT_SCHEMA),
                sql.Identifier("task_nodes"),
                sql.Identifier("reason"),
            )
        )
        connection.execute(
            sql.SQL("ALTER TABLE {}.{} DROP COLUMN {}").format(
                sql.Identifier(MISSING_COLUMN_SCHEMA),
                sql.Identifier("operator_instances"),
                sql.Identifier("api_version"),
            )
        )

    dsns = {
        schema_name: _schema_dsn(milestone1_postgres.dsn, schema_name)
        for schema_name in READINESS_SCHEMA_NAMES
    }
    for schema_name, dsn in dsns.items():
        validation_engine = create_engine(dsn)
        try:
            with validation_engine.connect() as connection:
                current_schema = connection.execute(
                    text("SELECT current_schema()")
                ).scalar_one()
                assert current_schema == schema_name
        finally:
            validation_engine.dispose()

    try:
        yield dsns
    finally:
        _assert_test_database(milestone1_postgres)
        with psycopg.connect(raw_dsn, autocommit=True) as connection:
            for schema_name in READINESS_SCHEMA_NAMES:
                connection.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(schema_name)
                    )
                )


@pytest.fixture
def foundation_environment(
    milestone1_postgres: Milestone1Postgres,
) -> Iterator[tuple[Engine, Redis]]:
    _assert_test_database(milestone1_postgres)
    with milestone1_postgres.engine.begin() as connection:
        connection.execute(
            text(
                """
                TRUNCATE TABLE
                    operator_instance_events,
                    operator_instances,
                    outbox_events,
                    visual_fallback_values,
                    node_results,
                    node_work_items,
                    task_node_dependencies,
                    task_nodes,
                    course_task_types,
                    course_jobs
                RESTART IDENTITY CASCADE
                """
            )
        )

    redis_client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    redis_database = int(redis_client.connection_pool.connection_kwargs.get("db", 0))
    assert redis_database != 0, "Control 真实集成测试拒绝使用 Redis DB 0"
    try:
        redis_client.ping()
    except Exception as exc:
        redis_client.close()
        pytest.skip(f"Redis 集成测试环境不可用: {exc}")
    _delete_redis_test_prefix(redis_client)
    try:
        yield milestone1_postgres.engine, redis_client
    finally:
        _delete_redis_test_prefix(redis_client)
        redis_client.close()


@pytest.fixture
def control_client(
    foundation_environment: tuple[Engine, Redis],
    milestone1_postgres: Milestone1Postgres,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, Engine, Redis]]:
    engine, redis_client = foundation_environment
    _isolate_workspace_roots(monkeypatch, tmp_path)
    settings = _control_settings(postgres_dsn=milestone1_postgres.dsn)

    with TestClient(create_app(settings)) as client:
        yield client, engine, redis_client


def _request_readiness(
    settings: ControlSettings,
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):  # type: ignore[no-untyped-def]
    _isolate_workspace_roots(monkeypatch, tmp_path)
    with TestClient(create_app(settings)) as client:
        health = client.get("/health")
        readiness = client.get("/ops/readiness")
    assert health.status_code == 200
    return readiness


def test_real_readiness_reports_only_postgresql_and_schema_unavailable(
    foundation_environment: tuple[Engine, Redis],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del foundation_environment
    readiness = _request_readiness(
        _control_settings(
            postgres_dsn=(
                "postgresql+psycopg://algorithm:algorithm@127.0.0.1:1/"
                "milestone1_unavailable_test"
            ),
            redis_prefix=f"{REDIS_PREFIX}readiness-pg:",
        ),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert readiness.status_code == 503
    checks = readiness.json()["checks"]
    assert checks["postgresql"]["ready"] is False
    assert "PostgreSQL" in checks["postgresql"]["detail"]
    assert checks["schema"]["ready"] is False
    assert "PostgreSQL" in checks["schema"]["detail"]
    assert checks["redis"]["ready"] is True
    assert checks["redis"]["detail"]


def test_real_readiness_reports_only_redis_unavailable(
    foundation_environment: tuple[Engine, Redis],
    milestone1_postgres: Milestone1Postgres,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del foundation_environment
    readiness = _request_readiness(
        _control_settings(
            postgres_dsn=milestone1_postgres.dsn,
            redis_url="redis://127.0.0.1:1/14",
            redis_prefix=f"{REDIS_PREFIX}readiness-redis:",
        ),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert readiness.status_code == 503
    checks = readiness.json()["checks"]
    assert checks["postgresql"]["ready"] is True
    assert checks["postgresql"]["detail"]
    assert checks["schema"]["ready"] is True
    assert checks["schema"]["detail"]
    assert checks["redis"]["ready"] is False
    assert "Redis" in checks["redis"]["detail"]


def test_real_readiness_reports_the_missing_table_from_search_path_schema(
    foundation_environment: tuple[Engine, Redis],
    readiness_schema_dsns: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del foundation_environment
    readiness = _request_readiness(
        _control_settings(
            postgres_dsn=readiness_schema_dsns[MISSING_TABLE_SCHEMA],
            redis_prefix=f"{REDIS_PREFIX}readiness-table:",
        ),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert readiness.status_code == 503
    checks = readiness.json()["checks"]
    assert checks["postgresql"]["ready"] is True
    assert checks["redis"]["ready"] is True
    assert checks["schema"]["ready"] is False
    assert "task_nodes" in checks["schema"]["detail"]


def test_real_readiness_reports_the_missing_column_comment_from_search_path_schema(
    foundation_environment: tuple[Engine, Redis],
    readiness_schema_dsns: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del foundation_environment
    readiness = _request_readiness(
        _control_settings(
            postgres_dsn=readiness_schema_dsns[MISSING_COMMENT_SCHEMA],
            redis_prefix=f"{REDIS_PREFIX}readiness-comment:",
        ),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert readiness.status_code == 503
    checks = readiness.json()["checks"]
    assert checks["postgresql"]["ready"] is True
    assert checks["redis"]["ready"] is True
    assert checks["schema"]["ready"] is False
    assert "task_nodes.reason" in checks["schema"]["detail"]


def test_real_readiness_reports_a_missing_expected_column(
    foundation_environment: tuple[Engine, Redis],
    readiness_schema_dsns: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del foundation_environment
    readiness = _request_readiness(
        _control_settings(
            postgres_dsn=readiness_schema_dsns[MISSING_COLUMN_SCHEMA],
            redis_prefix=f"{REDIS_PREFIX}readiness-column:",
        ),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert readiness.status_code == 503
    checks = readiness.json()["checks"]
    assert checks["postgresql"]["ready"] is True
    assert checks["redis"]["ready"] is True
    assert checks["schema"]["ready"] is False
    assert "operator_instances.api_version" in checks["schema"]["detail"]


def test_real_readiness_rejects_schema_without_foundation_forward_migration(
    foundation_environment: tuple[Engine, Redis],
    readiness_schema_dsns: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del foundation_environment
    readiness = _request_readiness(
        _control_settings(
            postgres_dsn=readiness_schema_dsns[STALE_FOUNDATION_SCHEMA],
            redis_prefix=f"{REDIS_PREFIX}readiness-stale:",
        ),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert readiness.status_code == 503
    checks = readiness.json()["checks"]
    assert checks["postgresql"]["ready"] is True
    assert checks["redis"]["ready"] is True
    assert checks["schema"]["ready"] is False
    assert "idx_operator_instance_events_instance_time" in checks["schema"]["detail"]
    assert "course_task_types.status" in checks["schema"]["detail"]


def test_real_http_submission_is_idempotent_appendable_and_queryable(
    control_client: tuple[TestClient, Engine, Redis],
) -> None:
    client, engine, _ = control_client
    combined_payload = {
        "task_id": "course-http-foundation",
        "task_types": ["ASR", "TEACHER_BEHAVIOR"],
        "priority": "URGENT",
        "teacher_video_path": "http://media/teacher.mp4",
    }

    first = client.post("/api/course-jobs", json=combined_payload)
    with ThreadPoolExecutor(max_workers=6) as executor:
        duplicates = list(
            executor.map(
                lambda _: client.post("/api/course-jobs", json=combined_payload),
                range(6),
            )
        )
    appended = client.post(
        "/api/course-jobs",
        json={
            "task_id": "course-http-foundation",
            "task_types": ["PPT"],
            "priority": "NORMAL",
            "slides_video_path": "http://media/slides.mp4",
        },
    )
    queried = client.get("/api/course-jobs/course-http-foundation")

    assert first.status_code == appended.status_code == queried.status_code == 200
    assert first.json()["code"] == appended.json()["code"] == queried.json()["code"] == 0
    assert all(
        response.status_code == 200 and response.json()["code"] == 0
        for response in duplicates
    )

    tasks = {item["task_type"]: item for item in queried.json()["data"]["tasks"]}
    assert tasks["PPT"]["status"] == 10
    assert tasks["PPT"]["priority"] == "NORMAL"
    assert tasks["ASR"]["status"] == 10
    assert tasks["ASR"]["priority"] == "URGENT"
    assert tasks["TEACHER_BEHAVIOR"]["status"] == 10
    assert tasks["TEACHER_BEHAVIOR"]["reason"] == "任务已接收，等待处理"
    assert tasks["STUDENT_BEHAVIOR"]["status"] == 0
    assert tasks["STUDENT_BEHAVIOR"]["reason"] == "未请求该任务"

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT task_type, priority FROM course_task_types "
                "WHERE task_id = 'course-http-foundation' ORDER BY task_type"
            )
        ).all()
        payloads = connection.execute(
            text(
                "SELECT payload FROM outbox_events "
                "WHERE payload->>'task_id' = 'course-http-foundation' "
                "ORDER BY created_at, aggregate_id"
            )
        ).scalars().all()

    assert rows == [("ASR", "URGENT"), ("PPT", "NORMAL"), ("TEACHER_BEHAVIOR", "URGENT")]
    assert len(payloads) == 3
    combined_submission_ids = {
        payload["submission_id"]
        for payload in payloads
        if payload["task_type"] in {"ASR", "TEACHER_BEHAVIOR"}
    }
    assert len(combined_submission_ids) == 1


def test_first_concurrent_http_submissions_create_one_task_type_and_outbox_event(
    control_client: tuple[TestClient, Engine, Redis],
) -> None:
    client, engine, _ = control_client
    contender_count = 8
    start = Barrier(contender_count)
    payload = {
        "task_id": "course-http-first-race",
        "task_types": ["PPT"],
        "slides_video_path": "http://media/first-race-slides.mp4",
    }

    def submit(_: int):  # type: ignore[no-untyped-def]
        start.wait(timeout=5)
        return client.post("/api/course-jobs", json=payload)

    with ThreadPoolExecutor(max_workers=contender_count) as executor:
        responses = list(executor.map(submit, range(contender_count)))

    assert all(
        response.status_code == 200 and response.json()["code"] == 0
        for response in responses
    )
    with engine.connect() as connection:
        task_type_count = connection.execute(
            text(
                "SELECT count(*) FROM course_task_types "
                "WHERE task_id = 'course-http-first-race' AND task_type = 'PPT'"
            )
        ).scalar_one()
        outbox_count = connection.execute(
            text(
                "SELECT count(*) FROM outbox_events "
                "WHERE aggregate_id = 'course-http-first-race:PPT'"
            )
        ).scalar_one()

    assert task_type_count == 1
    assert outbox_count == 1


def test_outbox_failure_rolls_back_course_and_task_type_facts(
    control_client: tuple[TestClient, Engine, Redis],
) -> None:
    client, engine, _ = control_client
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION milestone1_reject_outbox_event()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.aggregate_id LIKE 'course-http-rollback:%' THEN
                        RAISE EXCEPTION 'forced outbox failure';
                    END IF;
                    RETURN NEW;
                END;
                $$;
                CREATE TRIGGER milestone1_reject_outbox_event
                BEFORE INSERT ON outbox_events
                FOR EACH ROW EXECUTE FUNCTION milestone1_reject_outbox_event();
                """
            )
        )

    try:
        response = client.post(
            "/api/course-jobs",
            json={
                "task_id": "course-http-rollback",
                "task_types": ["PPT"],
                "slides_video_path": "http://media/slides.mp4",
            },
        )
        assert response.status_code == 200
        assert response.json()["code"] == 50000
        assert response.json()["message"] == "任务数据库暂不可用"
        with engine.connect() as connection:
            counts = connection.execute(
                text(
                    """
                    SELECT
                        (SELECT count(*) FROM course_jobs
                         WHERE task_id = 'course-http-rollback'),
                        (SELECT count(*) FROM course_task_types
                         WHERE task_id = 'course-http-rollback'),
                        (SELECT count(*) FROM outbox_events
                         WHERE aggregate_id LIKE 'course-http-rollback:%')
                    """
                )
            ).one()
        assert tuple(counts) == (0, 0, 0)
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DROP TRIGGER IF EXISTS milestone1_reject_outbox_event "
                    "ON outbox_events"
                )
            )
            connection.execute(
                text("DROP FUNCTION IF EXISTS milestone1_reject_outbox_event()")
            )


def test_real_readiness_registry_drain_capacity_and_audit_are_consistent(
    control_client: tuple[TestClient, Engine, Redis],
) -> None:
    client, engine, redis_client = control_client
    health = client.get("/health")
    readiness = client.get("/ops/readiness")
    registration = client.post(
        "/api/operator-instances/register",
        json={
            "instance_id": "vbas-http-gpu0",
            "operator_code": "vbas",
            "capabilities": ["teacher_behavior"],
            "service_url": "http://127.0.0.1:19001",
            "model_version": "model-v1",
            "api_version": "v1",
            "declared_capacity": 1,
            "labels": {"gpu": "0"},
        },
    )
    heartbeat = client.post(
        "/api/operator-instances/heartbeat",
        json={"instance_id": "vbas-http-gpu0", "inflight": 0, "model_ready": True},
    )
    lease = client.post(
        "/internal/operator-instances/lease",
        json={"capability": "teacher_behavior", "ttl_seconds": 30},
    )
    drain = client.post("/ops/operator-instances/vbas-http-gpu0/drain")
    released = client.post(
        "/internal/operator-instances/release",
        json={"lease_id": lease.json().get("lease_id", "missing")},
    )
    lease_after_drain = client.post(
        "/internal/operator-instances/lease",
        json={"capability": "teacher_behavior", "ttl_seconds": 30},
    )
    history = client.get("/ops/operator-instances/vbas-http-gpu0/events?limit=10")

    with engine.connect() as connection:
        audit_snapshot = connection.execute(
            text(
                "SELECT desired_state FROM operator_instances "
                "WHERE instance_id = 'vbas-http-gpu0'"
            )
        ).scalar_one_or_none()
        audit_events = connection.execute(
            text(
                "SELECT event_type FROM operator_instance_events "
                "WHERE instance_id = 'vbas-http-gpu0' ORDER BY id"
            )
        ).scalars().all()

    assert health.status_code == 200
    assert readiness.status_code == 200
    assert all(
        check["ready"] for check in readiness.json()["checks"].values()
    )
    assert registration.status_code == 201
    assert registration.json()["lifecycle"] == "OFFLINE"
    assert heartbeat.status_code == lease.status_code == drain.status_code == 200
    assert heartbeat.json()["lifecycle"] == "ONLINE"
    assert released.status_code == 200
    assert lease_after_drain.status_code == 503
    assert history.status_code == 200
    assert [event["event_type"] for event in history.json()] == list(
        reversed(audit_events)
    )
    assert drain.json()["lifecycle"] == "DRAINING"
    assert redis_client.exists(f"{REDIS_PREFIX}instance:vbas-http-gpu0") == 1
    heartbeat_ttl = redis_client.ttl(f"{REDIS_PREFIX}heartbeat:vbas-http-gpu0")
    assert 0 < heartbeat_ttl <= 5
    assert audit_snapshot == "DRAINING"
    assert audit_events[0] == "REGISTERED"
    assert "HEARTBEAT_SUMMARY" in audit_events
    assert audit_events[-1] == "LIFECYCLE_CHANGED"


def test_heartbeat_audit_failure_is_visible_until_same_instance_recovers(
    control_client: tuple[TestClient, Engine, Redis],
) -> None:
    client, engine, _ = control_client
    instance_id = "vbas-audit-readiness-gpu0"
    registration = client.post(
        "/api/operator-instances/register",
        json={
            "instance_id": instance_id,
            "operator_code": "vbas",
            "capabilities": ["teacher_behavior"],
            "service_url": "http://127.0.0.1:19002",
            "declared_capacity": 1,
        },
    )
    assert registration.status_code == 201

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION milestone1_reject_heartbeat_audit()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.instance_id = 'vbas-audit-readiness-gpu0'
                       AND NEW.event_type = 'HEARTBEAT_SUMMARY' THEN
                        RAISE EXCEPTION 'forced heartbeat audit failure';
                    END IF;
                    RETURN NEW;
                END;
                $$;
                CREATE TRIGGER milestone1_reject_heartbeat_audit
                BEFORE INSERT ON operator_instance_events
                FOR EACH ROW EXECUTE FUNCTION milestone1_reject_heartbeat_audit();
                """
            )
        )

    try:
        heartbeat = client.post(
            "/api/operator-instances/heartbeat",
            json={"instance_id": instance_id, "inflight": 0, "model_ready": True},
        )
        degraded = client.get("/ops/readiness")

        assert heartbeat.status_code == 200
        assert degraded.status_code == 503
        assert degraded.json()["checks"]["postgresql"]["ready"] is False
        assert instance_id in degraded.json()["checks"]["postgresql"]["detail"]
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DROP TRIGGER IF EXISTS milestone1_reject_heartbeat_audit "
                    "ON operator_instance_events"
                )
            )
            connection.execute(
                text("DROP FUNCTION IF EXISTS milestone1_reject_heartbeat_audit()")
            )

    recovered_heartbeat = client.post(
        "/api/operator-instances/heartbeat",
        json={"instance_id": instance_id, "inflight": 0, "model_ready": True},
    )
    recovered = client.get("/ops/readiness")

    assert recovered_heartbeat.status_code == 200
    assert recovered.status_code == 200
