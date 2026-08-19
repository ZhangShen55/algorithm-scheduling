from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import psycopg
from redis import Redis
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

from packages.platform_common.operator_audit_repository import OperatorAuditRepository
from packages.platform_common.redis_operator_registry import RedisOperatorRegistry
from packages.platform_common.repository import CourseRepository

from ..core.config import ControlSettings
from .audited_operator_registry import (
    AuditedOperatorRegistry,
    HttpOperatorHealthChecker,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from fastapi import FastAPI

    from packages.platform_common.operator_registry import OperatorRegistry


CONTROL_SCHEMA_COLUMNS = {
    "course_jobs": (
        "id",
        "task_id",
        "input_snapshot",
        "created_at",
        "updated_at",
    ),
    "course_task_types": (
        "id",
        "submission_id",
        "task_id",
        "task_type",
        "status",
        "priority",
        "reason",
        "request_payload",
        "effective_params",
        "requested_at",
        "started_at",
        "finished_at",
        "updated_at",
    ),
    "task_nodes": (
        "id",
        "course_task_type_id",
        "node_code",
        "status",
        "priority",
        "reason",
        "required_capability",
        "prerequisite_count",
        "completed_prerequisite_count",
        "attempt",
        "ready_at",
        "claimed_by",
        "claim_token",
        "claimed_at",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    ),
    "task_node_dependencies": ("node_id", "prerequisite_node_id"),
    "node_work_items": (
        "id",
        "task_node_id",
        "item_key",
        "ordinal",
        "status",
        "reason",
        "result",
        "attempt",
        "created_at",
        "updated_at",
    ),
    "node_results": (
        "task_node_id",
        "result",
        "artifact_path",
        "artifact_count",
        "progress",
        "effective_params",
        "result_version",
        "created_at",
        "updated_at",
    ),
    "visual_fallback_values": (
        "id",
        "course_task_type_id",
        "metric_code",
        "value",
        "created_at",
    ),
    "outbox_events": (
        "event_id",
        "aggregate_type",
        "aggregate_id",
        "event_type",
        "payload",
        "available_at",
        "published_at",
        "publish_attempts",
        "last_error",
        "created_at",
        "claim_token",
        "claimed_at",
    ),
    "operator_instances": (
        "instance_id",
        "operator_code",
        "capabilities",
        "service_url",
        "model_version",
        "api_version",
        "declared_capacity",
        "labels",
        "desired_state",
        "last_registered_at",
        "last_heartbeat_at",
        "unregistered_at",
        "created_at",
        "updated_at",
    ),
    "operator_instance_events": (
        "id",
        "instance_id",
        "event_type",
        "event_payload",
        "occurred_at",
    ),
}
CONTROL_SCHEMA_TABLES = tuple(CONTROL_SCHEMA_COLUMNS)
REQUIRED_SCHEMA_INDEXES = ("idx_operator_instance_events_instance_time",)
REQUIRED_STATUS_COMMENTS = {
    "course_task_types.status": (
        "任务类型整数状态：10 等待、20 前置等待、30 等待算子、40 已排队、"
        "50 处理中、60 完成、70 失败、80 取消"
    ),
    "task_nodes.status": (
        "节点整数状态：10 就绪、20 前置等待、30 等待算子、40 已排队、"
        "50 处理中、60 完成、70 失败、80 取消"
    ),
    "node_work_items.status": (
        "子项整数状态：10 等待、20 前置等待、30 等待算子、40 已排队、"
        "50 处理中、60 完成、70 失败、80 取消"
    ),
}
_CHINESE_TEXT = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    ready: bool
    detail: str

    def as_dict(self) -> dict[str, bool | str]:
        return {"ready": self.ready, "detail": self.detail}


class ControlReadinessChecker:
    def __init__(
        self,
        engine: Engine | None,
        redis_client: Redis | None,
        audit_error_getter: Callable[[], str | None] | None = None,
        *,
        postgres_dsn: str | None = None,
        redis_url: str | None = None,
        dependency_timeout_seconds: float = 3.0,
    ) -> None:
        if dependency_timeout_seconds < 2:
            raise ValueError("readiness 依赖超时必须大于或等于 2 秒")
        self._engine = engine
        self._redis_client = redis_client
        self._audit_error_getter = audit_error_getter
        self._postgres_dsn = postgres_dsn
        self._redis_url = redis_url
        self._dependency_timeout_seconds = dependency_timeout_seconds

    def check(self) -> dict[str, Any]:
        check_functions = {
            "postgresql": self._check_postgresql,
            "redis": self._check_redis,
            "schema": self._check_schema,
        }
        check_labels = {
            "postgresql": "PostgreSQL",
            "redis": "Redis",
            "schema": "schema",
        }
        executor = ThreadPoolExecutor(
            max_workers=len(check_functions),
            thread_name_prefix="control-readiness",
        )
        try:
            futures = {
                name: executor.submit(check_function)
                for name, check_function in check_functions.items()
            }
            _, pending = wait(
                futures.values(),
                timeout=self._dependency_timeout_seconds,
            )
            checks: dict[str, ReadinessCheck] = {}
            for name, future in futures.items():
                if future in pending:
                    future.cancel()
                    timeout_text = f"{self._dependency_timeout_seconds:g}"
                    checks[name] = ReadinessCheck(
                        False,
                        f"{check_labels[name]} 检查超过 {timeout_text} 秒总预算",
                    )
                    continue
                try:
                    checks[name] = future.result()
                except Exception as exc:
                    checks[name] = ReadinessCheck(
                        False,
                        f"{check_labels[name]} 检查异常: {exc}",
                    )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        ready = all(check.ready for check in checks.values())
        return {
            "status": "ready" if ready else "not_ready",
            "checks": {name: check.as_dict() for name, check in checks.items()},
        }

    def _check_postgresql(self) -> ReadinessCheck:
        if self._engine is None:
            return ReadinessCheck(False, "PostgreSQL 检查资源不可用")
        try:
            with self._postgres_connection(statement_count=1) as connection:
                connection.execute("SELECT 1").fetchone()
        except Exception as exc:
            return ReadinessCheck(False, f"PostgreSQL 不可用: {exc}")
        if self._audit_error_getter is not None:
            audit_error = self._audit_error_getter()
            if audit_error:
                return ReadinessCheck(
                    False,
                    f"PostgreSQL 算子心跳审计待补写: {audit_error}",
                )
        return ReadinessCheck(True, "PostgreSQL 连接正常")

    def _check_redis(self) -> ReadinessCheck:
        if self._redis_client is None and self._redis_url is None:
            return ReadinessCheck(False, "Redis 检查资源不可用")
        probe = self._redis_client
        owns_probe = False
        if self._redis_url is not None:
            connect_timeout = self._dependency_timeout_seconds / 2
            command_timeout = self._dependency_timeout_seconds - connect_timeout
            probe = Redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=connect_timeout,
                socket_timeout=command_timeout,
            )
            owns_probe = True
        try:
            assert probe is not None
            if not probe.ping():
                raise RuntimeError("PING 未返回成功")
        except Exception as exc:
            return ReadinessCheck(False, f"Redis 不可用: {exc}")
        finally:
            if owns_probe and probe is not None:
                probe.close()
        return ReadinessCheck(True, "Redis 连接正常")

    def _check_schema(self) -> ReadinessCheck:
        if self._engine is None:
            return ReadinessCheck(False, "无法检查 schema，PostgreSQL 检查资源不可用")
        try:
            with self._postgres_connection(statement_count=2) as connection:
                rows = connection.execute(
                    """
                        SELECT
                            tables.relname AS table_name,
                            obj_description(tables.oid, 'pg_class') AS table_comment,
                            columns.attname AS column_name,
                            col_description(tables.oid, columns.attnum) AS column_comment
                        FROM pg_catalog.pg_class AS tables
                        JOIN pg_catalog.pg_namespace AS schemas
                          ON schemas.oid = tables.relnamespace
                        JOIN pg_catalog.pg_attribute AS columns
                          ON columns.attrelid = tables.oid
                        WHERE schemas.nspname = current_schema()
                          AND tables.relkind IN ('r', 'p')
                          AND tables.relname = ANY(%s)
                          AND columns.attnum > 0
                          AND NOT columns.attisdropped
                        ORDER BY tables.relname, columns.attnum
                    """,
                    (list(CONTROL_SCHEMA_TABLES),),
                ).fetchall()
                index_rows = connection.execute(
                    """
                        SELECT indexname
                        FROM pg_catalog.pg_indexes
                        WHERE schemaname = current_schema()
                          AND indexname = ANY(%s)
                    """,
                    (list(REQUIRED_SCHEMA_INDEXES),),
                ).fetchall()
        except Exception as exc:
            return ReadinessCheck(False, f"无法检查 schema，PostgreSQL 不可用: {exc}")

        observed_tables = {str(row[0]) for row in rows}
        observed_columns = {f"{row[0]}.{row[2]}" for row in rows}
        expected_columns = {
            f"{table_name}.{column_name}"
            for table_name, columns in CONTROL_SCHEMA_COLUMNS.items()
            for column_name in columns
        }
        column_comments = {f"{row[0]}.{row[2]}": row[3] for row in rows}
        missing_tables = sorted(set(CONTROL_SCHEMA_TABLES) - observed_tables)
        missing_columns = sorted(expected_columns - observed_columns)
        missing_table_comments = sorted(
            {
                str(row[0])
                for row in rows
                if not self._has_chinese_comment(row[1])
            }
        )
        missing_column_comments = sorted(
            {
                f"{row[0]}.{row[2]}"
                for row in rows
                if not self._has_chinese_comment(row[3])
            }
        )
        observed_indexes = {str(row[0]) for row in index_rows}
        missing_indexes = sorted(set(REQUIRED_SCHEMA_INDEXES) - observed_indexes)
        stale_status_comments = sorted(
            field_name
            for field_name, expected_comment in REQUIRED_STATUS_COMMENTS.items()
            if column_comments.get(field_name) != expected_comment
        )
        issues: list[str] = []
        if missing_tables:
            issues.append(f"缺少表: {', '.join(missing_tables)}")
        if missing_columns:
            issues.append(f"缺少字段: {', '.join(missing_columns)}")
        if missing_table_comments:
            issues.append(f"缺少表中文说明: {', '.join(missing_table_comments)}")
        if missing_column_comments:
            issues.append(f"缺少字段中文说明: {', '.join(missing_column_comments)}")
        if missing_indexes:
            issues.append(f"缺少索引: {', '.join(missing_indexes)}")
        if stale_status_comments:
            issues.append(
                "状态字段说明未更新: " + ", ".join(stale_status_comments)
            )
        if issues:
            return ReadinessCheck(False, "；".join(issues))
        return ReadinessCheck(
            True,
            "当前 schema 符合里程碑 1 的正式表、字段、索引和中文说明契约",
        )

    @staticmethod
    def _has_chinese_comment(value: Any) -> bool:
        return isinstance(value, str) and bool(_CHINESE_TEXT.search(value.strip()))

    def _postgres_connection(
        self,
        *,
        statement_count: int,
    ) -> psycopg.Connection[Any]:
        if statement_count <= 0:
            raise ValueError("PostgreSQL 检查语句数量必须大于 0")
        if self._postgres_dsn is not None:
            url = make_url(self._postgres_dsn)
        elif self._engine is not None:
            url = self._engine.url
        else:
            raise RuntimeError("PostgreSQL 检查资源不可用")
        existing_options = url.query.get("options")
        option_parts: list[str]
        if isinstance(existing_options, tuple):
            option_parts = list(existing_options)
        elif existing_options is None:
            option_parts = []
        else:
            option_parts = [existing_options]
        url = url.difference_update_query(("options",))
        conninfo = url.set(drivername=url.get_backend_name()).render_as_string(
            hide_password=False
        )
        total_timeout_ms = max(
            1,
            math.ceil(self._dependency_timeout_seconds * 1000),
        )
        connect_timeout_seconds = max(
            1,
            math.floor(self._dependency_timeout_seconds / 2),
        )
        statement_timeout_ms = max(
            1,
            (total_timeout_ms - connect_timeout_seconds * 1000) // statement_count,
        )
        option_parts.append(f"-c statement_timeout={statement_timeout_ms}")
        return psycopg.connect(
            conninfo,
            connect_timeout=connect_timeout_seconds,
            options=" ".join(option_parts),
        )


class ControlRuntime:
    """Own control-service infrastructure created during the application lifespan."""

    def __init__(
        self,
        settings: ControlSettings,
        *,
        repository: Any | None = None,
        operator_registry: OperatorRegistry | Any | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.operator_registry = operator_registry
        self.engine: Engine | None = None
        self.redis_client: Redis | None = None
        self.audit_repository: OperatorAuditRepository | None = None
        self.realtime_operator_registry: RedisOperatorRegistry | None = None
        self.readiness_checker: ControlReadinessChecker | None = None
        self._injected_repository = repository
        self._injected_operator_registry = operator_registry
        self._started = False

    @classmethod
    def from_platform_settings(
        cls,
        settings: Any,
        *,
        repository: Any | None = None,
        operator_registry: Any | None = None,
    ) -> ControlRuntime:
        return cls(
            ControlSettings.model_validate(
                {
                    "service": {
                        "name": settings.service_name,
                        "environment": settings.environment,
                        "log_level": settings.log_level,
                        "trace_header": settings.trace_header,
                    },
                    "postgres": {"dsn": settings.postgres_dsn},
                    "redis": {"url": settings.redis_url},
                }
            ),
            repository=repository,
            operator_registry=operator_registry,
        )

    def attach(self, app: FastAPI) -> None:
        app.state.control_runtime = self
        app.state.database_engine = self.engine
        app.state.redis_client = self.redis_client
        app.state.course_repository = self.repository
        app.state.operator_registry = self.operator_registry
        app.state.readiness_checker = self.readiness_checker

    def start(self, app: FastAPI) -> None:
        if self._started:
            return
        try:
            needs_owned_engine = self.repository is None or self.operator_registry is None
            if needs_owned_engine:
                postgres = self.settings.postgres
                self.engine = create_engine(
                    postgres.dsn,
                    pool_size=postgres.pool_size,
                    max_overflow=postgres.max_overflow,
                    pool_timeout=postgres.pool_timeout_seconds,
                    pool_pre_ping=postgres.pool_pre_ping,
                )
            if self.repository is None:
                assert self.engine is not None
                self.repository = CourseRepository(self.engine)
            if self.operator_registry is None:
                assert self.engine is not None
                redis = self.settings.redis
                self.redis_client = Redis.from_url(
                    redis.url,
                    decode_responses=True,
                    max_connections=redis.max_connections,
                    socket_connect_timeout=redis.socket_connect_timeout_seconds,
                    socket_timeout=redis.socket_timeout_seconds,
                )
                self.audit_repository = OperatorAuditRepository(self.engine)
                self.realtime_operator_registry = RedisOperatorRegistry(
                    self.redis_client,
                    key_prefix=redis.key_prefix,
                    heartbeat_ttl_seconds=redis.heartbeat_ttl_seconds,
                )
                self.operator_registry = AuditedOperatorRegistry(
                    self.realtime_operator_registry,
                    self.audit_repository,
                    heartbeat_audit_interval_seconds=(
                        self.settings.operator_registry.heartbeat_audit_interval_seconds
                    ),
                    health_checker=HttpOperatorHealthChecker(
                        trusted_service_urls=(
                            self.settings.operator_registry.trusted_service_urls
                        )
                    ),
                )
            audit_error_getter: Callable[[], str | None] | None = None
            if isinstance(self.operator_registry, AuditedOperatorRegistry):
                audited_registry = self.operator_registry

                def get_audit_error() -> str | None:
                    return audited_registry.last_audit_error

                audit_error_getter = get_audit_error
            self.readiness_checker = ControlReadinessChecker(
                self.engine,
                self.redis_client,
                audit_error_getter,
                postgres_dsn=self.settings.postgres.dsn,
                redis_url=self.settings.redis.url,
                dependency_timeout_seconds=(
                    self.settings.readiness.dependency_timeout_seconds
                ),
            )
            self._started = True
            self.attach(app)
        except BaseException:
            self.stop(app)
            raise

    def stop(self, app: FastAPI) -> None:
        try:
            if self.redis_client is not None:
                self.redis_client.close()
        finally:
            try:
                if self.engine is not None:
                    self.engine.dispose()
            finally:
                self.engine = None
                self.redis_client = None
                self.audit_repository = None
                self.realtime_operator_registry = None
                self.readiness_checker = None
                self.repository = self._injected_repository
                self.operator_registry = self._injected_operator_registry
                self._started = False
                self.attach(app)

    @asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncIterator[None]:
        self.start(app)
        try:
            yield
        finally:
            self.stop(app)
