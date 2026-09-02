from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic, sleep
from typing import Any

import pytest
from fastapi.testclient import TestClient


@dataclass
class _OwnedEngine:
    disposed: bool = False

    def dispose(self) -> None:
        self.disposed = True


@dataclass
class _ConnectionPool:
    disconnected: bool = False

    def disconnect(self) -> None:
        self.disconnected = True


@dataclass
class _OwnedRedis:
    closed: bool = False
    connection_pool: _ConnectionPool = field(default_factory=_ConnectionPool)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def _isolated_workspace_roots(tmp_path: Any, monkeypatch: Any) -> None:
    from control_service.app.application import factory

    original_adapter = factory.to_platform_settings

    def isolated_adapter(settings: Any) -> Any:
        return original_adapter(settings).model_copy(
            update={
                "course_root": tmp_path / "course",
                "result_root": tmp_path / "result",
            }
        )

    monkeypatch.setattr(factory, "to_platform_settings", isolated_adapter)


def _settings() -> Any:
    from control_service.app.core.config import ControlSettings

    return ControlSettings(
        postgres={
            "dsn": "postgresql+psycopg://test:test@127.0.0.1:6543/runtime_test",
            "pool_size": 3,
            "max_overflow": 4,
            "pool_timeout_seconds": 1.25,
            "pool_pre_ping": False,
        },
        redis={
            "url": "redis://127.0.0.1:6388/14",
            "key_prefix": "runtime-test:",
            "heartbeat_ttl_seconds": 23,
            "max_connections": 7,
            "socket_connect_timeout_seconds": 1.5,
            "socket_timeout_seconds": 2.5,
        },
        readiness={"dependency_timeout_seconds": 2.0},
    )


def test_readiness_dependency_timeout_rejects_unenforceable_subsecond_budget() -> None:
    from control_service.app.core.config import ControlSettings

    with pytest.raises(ValueError, match="greater than or equal to 2"):
        ControlSettings(readiness={"dependency_timeout_seconds": 1.9})


def _patch_resource_factories(monkeypatch: Any) -> tuple[
    list[tuple[str, str, dict[str, Any]]],
    _OwnedEngine,
    _OwnedRedis,
]:
    from control_service.app.api import control

    calls: list[tuple[str, str, dict[str, Any]]] = []
    engine = _OwnedEngine()
    redis_client = _OwnedRedis()

    def create_engine_spy(dsn: str, **kwargs: Any) -> _OwnedEngine:
        calls.append(("postgresql", dsn, kwargs))
        return engine

    def redis_from_url_spy(url: str, **kwargs: Any) -> _OwnedRedis:
        calls.append(("redis", url, kwargs))
        return redis_client

    if hasattr(control, "create_engine"):
        monkeypatch.setattr(control, "create_engine", create_engine_spy, raising=False)
    if hasattr(control, "Redis"):
        monkeypatch.setattr(
            control.Redis,
            "from_url",
            staticmethod(redis_from_url_spy),
            raising=False,
        )

    # The target module is introduced by the implementation task. Patch it when present
    # without making its absence a collection error for this RED suite.
    try:
        from control_service.app.infrastructure import runtime
    except ImportError:
        runtime = None
    if runtime is not None:
        monkeypatch.setattr(runtime, "create_engine", create_engine_spy)
        monkeypatch.setattr(runtime.Redis, "from_url", staticmethod(redis_from_url_spy))

    return calls, engine, redis_client


def test_app_construction_does_not_create_external_resources(
    monkeypatch: Any,
    _isolated_workspace_roots: None,
) -> None:
    from control_service.app.application.factory import create_app

    calls, _, _ = _patch_resource_factories(monkeypatch)

    create_app(_settings())

    assert calls == []


def test_lifespan_creates_resources_with_complete_configuration_and_closes_them(
    monkeypatch: Any,
    _isolated_workspace_roots: None,
) -> None:
    from control_service.app.application.factory import create_app

    calls, engine, redis_client = _patch_resource_factories(monkeypatch)
    app = create_app(_settings())

    with TestClient(app):
        assert len(calls) == 2
        assert engine.disposed is False
        assert redis_client.closed is False
        assert redis_client.connection_pool.disconnected is False

        postgres_call = next(call for call in calls if call[0] == "postgresql")
        redis_call = next(call for call in calls if call[0] == "redis")
        assert postgres_call == (
            "postgresql",
            "postgresql+psycopg://test:test@127.0.0.1:6543/runtime_test",
            {
                "pool_size": 3,
                "max_overflow": 4,
                "pool_timeout": 1.25,
                "pool_pre_ping": False,
            },
        )
        assert redis_call == (
            "redis",
            "redis://127.0.0.1:6388/14",
            {
                "decode_responses": True,
                "max_connections": 7,
                "socket_connect_timeout": 1.5,
                "socket_timeout": 2.5,
            },
        )
    assert engine.disposed is True
    assert redis_client.closed or redis_client.connection_pool.disconnected


def test_injected_dependencies_remain_caller_owned(
    monkeypatch: Any,
    _isolated_workspace_roots: None,
) -> None:
    from control_service.app.application.factory import create_app

    class InjectedDependency:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    repository = InjectedDependency()
    registry = InjectedDependency()
    calls, _, _ = _patch_resource_factories(monkeypatch)

    app = create_app(
        _settings(),
        repository=repository,
        operator_registry=registry,
    )
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert calls == []
    assert repository.close_calls == 0
    assert registry.close_calls == 0


def test_partial_lifespan_start_failure_disposes_created_engine(
    monkeypatch: Any,
    _isolated_workspace_roots: None,
) -> None:
    from control_service.app.application.factory import create_app
    from control_service.app.infrastructure import runtime

    calls, engine, _ = _patch_resource_factories(monkeypatch)

    def fail_redis_start(url: str, **kwargs: Any) -> None:
        del url, kwargs
        raise RuntimeError("forced redis startup failure")

    monkeypatch.setattr(runtime.Redis, "from_url", staticmethod(fail_redis_start))
    app = create_app(_settings())

    with pytest.raises(RuntimeError, match="forced redis startup failure"), TestClient(app):
        pass

    assert [call[0] for call in calls] == ["postgresql"]
    assert engine.disposed is True
    assert app.state.control_runtime.engine is None
    assert app.state.control_runtime.repository is None


def test_blocking_repository_and_redis_routes_are_sync_endpoints(
    _isolated_workspace_roots: None,
) -> None:
    from control_service.app.application.factory import create_app

    app = create_app(
        _settings(),
        repository=object(),
        operator_registry=object(),
    )
    routes = {
        route.path: route.endpoint
        for route in app.routes
        if hasattr(route, "endpoint")
    }

    blocking_paths = (
        "/api/course-jobs",
        "/api/course-jobs/{task_id}",
        "/api/operator-instances/register",
        "/api/operator-instances/heartbeat",
        "/api/operator-instances/unregister",
        "/api/operator-instances",
        "/api/operator-instances/lifecycle",
        "/internal/operator-instances/lease",
        "/internal/operator-instances/release",
        "/internal/operator-instances/lease/renew",
        "/ops/course-jobs/{task_id}",
        "/ops/course-jobs",
        "/ops/operator-instances",
        "/ops/operator-instances/{instance_id}/drain",
        "/ops/operator-instances/{instance_id}/events",
        "/ops/queues",
        "/ops/kafka",
        "/ops/storage",
        "/ops/readiness",
    )
    route_failures = []
    for path in blocking_paths:
        endpoint = routes.get(path)
        if endpoint is None:
            route_failures.append(f"{path}: missing")
        elif inspect.iscoroutinefunction(endpoint):
            route_failures.append(f"{path}: async")

    assert route_failures == []


def test_readiness_starts_dependency_checks_concurrently(
    monkeypatch: Any,
) -> None:
    from control_service.app.infrastructure.runtime import (
        ControlReadinessChecker,
        ReadinessCheck,
    )

    checker = ControlReadinessChecker(None, None)
    all_started = Event()
    release = Event()
    counter_lock = Lock()
    started = 0

    def blocked_check(detail: str) -> ReadinessCheck:
        nonlocal started
        with counter_lock:
            started += 1
            if started == 3:
                all_started.set()
        release.wait(timeout=2)
        return ReadinessCheck(True, detail)

    monkeypatch.setattr(checker, "_check_postgresql", lambda: blocked_check("postgresql"))
    monkeypatch.setattr(checker, "_check_redis", lambda: blocked_check("redis"))
    monkeypatch.setattr(checker, "_check_schema", lambda: blocked_check("schema"))
    result: dict[str, Any] = {}
    check_thread = Thread(target=lambda: result.setdefault("value", checker.check()))
    check_thread.start()
    try:
        assert all_started.wait(timeout=0.5), "readiness 依赖检查被串行执行"
    finally:
        release.set()
        check_thread.join(timeout=2)

    assert check_thread.is_alive() is False
    assert result["value"]["status"] == "ready"


def test_readiness_returns_when_the_shared_dependency_budget_expires(
    monkeypatch: Any,
) -> None:
    from control_service.app.infrastructure.runtime import (
        ControlReadinessChecker,
        ReadinessCheck,
    )

    checker = ControlReadinessChecker(
        None,
        None,
        dependency_timeout_seconds=2.0,
    )

    def slow_postgresql_check() -> ReadinessCheck:
        sleep(2.5)
        return ReadinessCheck(True, "迟到的 PostgreSQL 结果")

    monkeypatch.setattr(checker, "_check_postgresql", slow_postgresql_check)
    monkeypatch.setattr(
        checker,
        "_check_redis",
        lambda: ReadinessCheck(True, "Redis 正常"),
    )
    monkeypatch.setattr(
        checker,
        "_check_schema",
        lambda: ReadinessCheck(True, "schema 正常"),
    )

    started_at = monotonic()
    result = checker.check()
    elapsed_seconds = monotonic() - started_at

    assert elapsed_seconds < 2.3
    assert result["status"] == "not_ready"
    assert result["checks"]["postgresql"] == {
        "ready": False,
        "detail": "PostgreSQL 检查超过 2 秒总预算",
    }


def test_redis_readiness_splits_connect_and_command_timeout_budget(
    monkeypatch: Any,
) -> None:
    from control_service.app.infrastructure import runtime
    from control_service.app.infrastructure.runtime import ControlReadinessChecker

    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeRedis:
        def ping(self) -> bool:
            return True

        def close(self) -> None:
            return None

    def redis_from_url_spy(url: str, **kwargs: Any) -> FakeRedis:
        calls.append((url, kwargs))
        return FakeRedis()

    monkeypatch.setattr(runtime.Redis, "from_url", staticmethod(redis_from_url_spy))
    checker = ControlReadinessChecker(
        None,
        None,
        redis_url="redis://127.0.0.1:6379/14",
        dependency_timeout_seconds=3.0,
    )

    result = checker._check_redis()

    assert result.ready is True
    _, options = calls[0]
    assert options["socket_connect_timeout"] == 1.5
    assert options["socket_timeout"] == 1.5
    assert options["socket_connect_timeout"] + options["socket_timeout"] <= 3.0


def test_postgresql_readiness_splits_the_dependency_timeout_budget(
    monkeypatch: Any,
) -> None:
    from control_service.app.infrastructure import runtime
    from control_service.app.infrastructure.runtime import ControlReadinessChecker

    calls: list[tuple[Any, ...]] = []

    class FakeConnection:
        pass

    def connect_spy(
        conninfo: str,
        *,
        connect_timeout: int,
        options: str,
    ) -> FakeConnection:
        calls.append((conninfo, connect_timeout, options))
        return FakeConnection()

    monkeypatch.setattr(runtime.psycopg, "connect", connect_spy)
    checker = ControlReadinessChecker(
        None,
        None,
        postgres_dsn="postgresql+psycopg://test:test@127.0.0.1:5432/readiness",
        dependency_timeout_seconds=3.0,
    )

    checker._postgres_connection(statement_count=1)
    checker._postgres_connection(statement_count=2)
    checker_with_search_path = ControlReadinessChecker(
        None,
        None,
        postgres_dsn=(
            "postgresql+psycopg://test:test@127.0.0.1:5432/readiness"
            "?options=-csearch_path%3Dcustom_schema"
        ),
        dependency_timeout_seconds=3.0,
    )
    checker_with_search_path._postgres_connection(statement_count=1)

    assert calls[0][1] == 1
    assert calls[0][2] == "-c statement_timeout=2000"
    assert calls[1][1] == 1
    assert calls[1][2] == "-c statement_timeout=1000"
    assert "options=" not in calls[2][0]
    assert calls[2][2] == (
        "-csearch_path=custom_schema -c statement_timeout=2000"
    )


def test_control_service_does_not_wire_a_kafka_client_or_producer() -> None:
    service_root = Path(__file__).resolve().parents[1]
    forbidden_modules = {"aiokafka", "confluent_kafka", "kafka"}
    forbidden_symbols = {
        "AIOKafkaProducer",
        "KafkaProducer",
        "create_kafka_producer",
    }
    violations: list[str] = []
    for source_path in sorted((service_root / "app").rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".", 1)[0] in forbidden_modules:
                        violations.append(f"{source_path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module_root = (node.module or "").split(".", 1)[0]
                if module_root in forbidden_modules or "kafka" in (node.module or "").lower():
                    violations.append(f"{source_path}: from {node.module}")
                for alias in node.names:
                    if alias.name in forbidden_symbols:
                        violations.append(f"{source_path}: import {alias.name}")

    config = (service_root / "config.toml").read_text(encoding="utf-8").lower()
    requirements = (service_root / "requirements.txt").read_text(encoding="utf-8").lower()
    assert violations == []
    assert "[kafka]" not in config
    assert "bootstrap_servers" not in config
    assert not any(
        package in requirements
        for package in ("aiokafka", "confluent-kafka", "kafka-python")
    )
