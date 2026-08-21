from __future__ import annotations

import importlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import Engine, text

from packages.platform_common.operator_registry import (
    CapacityLease,
    CapacityUnavailableError,
    OperatorCode,
    OperatorInstance,
    OperatorInstanceNotFoundError,
    OperatorLifecycle,
)
from packages.platform_common.redis_operator_registry import RedisOperatorRegistry

pytestmark = pytest.mark.integration

if TYPE_CHECKING:
    from conftest import Milestone1Postgres


class AlwaysHealthy:
    def check(self, instance: OperatorInstance) -> bool:
        del instance
        return True


def _load_audit_types() -> tuple[type[Any], type[Any]]:
    try:
        repository_module = importlib.import_module(
            "packages.platform_common.operator_audit_repository"
        )
        registry_module = importlib.import_module(
            "control_service.app.infrastructure.audited_operator_registry"
        )
    except ModuleNotFoundError as exc:
        pytest.fail(f"里程碑 1 算子审计生产模块尚未实现: {exc.name}")
    return (
        repository_module.OperatorAuditRepository,
        registry_module.AuditedOperatorRegistry,
    )


def _assert_test_database(engine: Engine) -> None:
    database_name = engine.url.database or ""
    assert database_name.endswith("_test"), (
        "PostgreSQL 集成测试拒绝操作非 _test 数据库: " f"{database_name!r}"
    )


def _cleanup_redis_prefix(client: Redis, prefix: str) -> None:
    keys = list(client.scan_iter(match=f"{prefix}*", count=100))
    if keys:
        client.delete(*keys)


def test_draining_desired_state_survives_unregister_and_real_redis_loss(
    milestone1_postgres: Milestone1Postgres,
) -> None:
    repository_type, registry_type = _load_audit_types()
    engine = milestone1_postgres.engine
    _assert_test_database(engine)
    suffix = uuid4().hex
    prefix = f"m2b:integration:reg-014:{suffix}:"
    instance = replace(_instance(), instance_id=f"vbas-reg-014-{suffix}")
    first_client = Redis.from_url(
        "redis://127.0.0.1:6379/15", decode_responses=True
    )
    try:
        first_client.ping()
    except RedisError as exc:
        first_client.close()
        pytest.skip(f"Redis 集成测试环境不可用: {exc}")
    _cleanup_redis_prefix(first_client, prefix)
    try:
        first_repository = repository_type(engine)
        first = registry_type(
            RedisOperatorRegistry(first_client, key_prefix=prefix),
            first_repository,
            heartbeat_audit_interval_seconds=60,
            health_checker=AlwaysHealthy(),
        )
        first.register(instance)
        first.heartbeat(instance.instance_id, inflight=0, model_ready=True)
        first.set_lifecycle(instance.instance_id, OperatorLifecycle.DRAINING)
        first.unregister(instance.instance_id)
        _cleanup_redis_prefix(first_client, prefix)
    finally:
        first_client.close()

    second_client = Redis.from_url(
        "redis://127.0.0.1:6379/15", decode_responses=True
    )
    try:
        second_client.ping()
        second_repository = repository_type(engine)
        restarted = registry_type(
            RedisOperatorRegistry(second_client, key_prefix=prefix),
            second_repository,
            heartbeat_audit_interval_seconds=60,
            health_checker=AlwaysHealthy(),
        )
        restarted.register(instance)
        heartbeat = restarted.heartbeat(
            instance.instance_id,
            inflight=0,
            model_ready=True,
        )
        persisted_repository = repository_type(engine)
        assert first_repository is not second_repository
        assert second_repository is not persisted_repository
        assert (
            persisted_repository.get_desired_lifecycle(instance.instance_id)
            is OperatorLifecycle.DRAINING
        )
        assert heartbeat.lifecycle is OperatorLifecycle.DRAINING
        with pytest.raises(CapacityUnavailableError):
            restarted.lease("teacher_behavior", 30)

        restarted.set_lifecycle(instance.instance_id, OperatorLifecycle.ONLINE)
        assert (
            restarted.lease("teacher_behavior", 30).instance_id
            == instance.instance_id
        )
    finally:
        _cleanup_redis_prefix(second_client, prefix)
        second_client.close()


@pytest.fixture
def clean_audit_database(milestone1_postgres: Milestone1Postgres) -> Engine:
    engine = milestone1_postgres.engine
    _assert_test_database(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE operator_instance_events, operator_instances "
                "RESTART IDENTITY"
            )
        )
    return engine


def _instance(**changes: Any) -> OperatorInstance:
    instance = OperatorInstance(
        instance_id="vbas-audit-gpu0",
        operator_code=OperatorCode.VBAS,
        capabilities=["teacher_behavior", "student_behavior"],
        service_url="http://127.0.0.1:19001",
        model_version="model-v1",
        api_version="v1",
        declared_capacity=2,
        labels={"gpu": "0"},
    )
    return replace(instance, **changes)


def test_operator_audit_repository_and_registry_decorator_are_available() -> None:
    repository_type, registry_type = _load_audit_types()

    assert repository_type.__name__ == "OperatorAuditRepository"
    assert registry_type.__name__ == "AuditedOperatorRegistry"


def test_registration_and_reregistration_update_snapshot_and_append_history(
    clean_audit_database: Engine,
) -> None:
    repository_type, _ = _load_audit_types()
    repository = repository_type(clean_audit_database)

    repository.record_registration(_instance())
    repository.record_registration(
        _instance(
            capabilities=["teacher_behavior"],
            model_version="model-v2",
            declared_capacity=3,
        )
    )

    with clean_audit_database.connect() as connection:
        snapshot = connection.execute(
            text(
                "SELECT capabilities, model_version, declared_capacity, desired_state "
                "FROM operator_instances WHERE instance_id = :instance_id"
            ),
            {"instance_id": "vbas-audit-gpu0"},
        ).mappings().one()
        event_types = connection.execute(
            text(
                "SELECT event_type FROM operator_instance_events "
                "WHERE instance_id = :instance_id ORDER BY id"
            ),
            {"instance_id": "vbas-audit-gpu0"},
        ).scalars().all()

    assert snapshot == {
        "capabilities": ["teacher_behavior"],
        "model_version": "model-v2",
        "declared_capacity": 3,
        "desired_state": "ONLINE",
    }
    assert event_types == ["REGISTERED", "REREGISTERED"]


def test_historical_text_analysis_snapshot_remains_readable_as_string(
    clean_audit_database: Engine,
) -> None:
    repository_type, _ = _load_audit_types()
    repository = repository_type(clean_audit_database)
    with clean_audit_database.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO operator_instances (
                    instance_id,
                    operator_code,
                    capabilities,
                    service_url,
                    model_version,
                    api_version,
                    declared_capacity,
                    labels,
                    desired_state,
                    unregistered_at
                )
                VALUES (
                    'text-analysis-cpu0',
                    'text_analysis',
                    CAST('["extract_keywords","course_overviews"]' AS jsonb),
                    'http://text-analysis-cpu0:8000',
                    'historical-v1',
                    'v1',
                    256,
                    CAST('{"deployment":"retired"}' AS jsonb),
                    'OFFLINE',
                    now()
                )
                """
            )
        )

    snapshot = repository.get_instance_snapshot("text-analysis-cpu0")

    assert snapshot.instance_id == "text-analysis-cpu0"
    assert snapshot.operator_code == "text_analysis"
    assert isinstance(snapshot.operator_code, str)
    assert snapshot.capabilities == ("extract_keywords", "course_overviews")
    assert snapshot.desired_state == "OFFLINE"
    assert snapshot.unregistered_at is not None


def test_heartbeat_summaries_are_throttled_atomically_across_concurrent_calls(
    clean_audit_database: Engine,
) -> None:
    repository_type, _ = _load_audit_types()
    repository = repository_type(clean_audit_database)
    repository.record_registration(_instance())

    def heartbeat(_: int) -> bool:
        return bool(
            repository.record_heartbeat_summary(
                "vbas-audit-gpu0",
                inflight=1,
                model_ready=True,
                min_interval_seconds=60,
            )
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        recorded = list(executor.map(heartbeat, range(16)))

    assert recorded.count(True) == 1
    with clean_audit_database.connect() as connection:
        first_count = connection.execute(
            text(
                "SELECT count(*) FROM operator_instance_events "
                "WHERE instance_id = :instance_id "
                "AND event_type = 'HEARTBEAT_SUMMARY'"
            ),
            {"instance_id": "vbas-audit-gpu0"},
        ).scalar_one()
    assert first_count == 1

    with clean_audit_database.begin() as connection:
        connection.execute(
            text(
                "UPDATE operator_instances "
                "SET last_heartbeat_at = now() - interval '61 seconds' "
                "WHERE instance_id = :instance_id"
            ),
            {"instance_id": "vbas-audit-gpu0"},
        )
    assert repository.record_heartbeat_summary(
        "vbas-audit-gpu0",
        inflight=0,
        model_ready=True,
        min_interval_seconds=60,
    )


def test_lifecycle_unregistration_are_idempotent_and_history_is_newest_first(
    clean_audit_database: Engine,
) -> None:
    repository_type, _ = _load_audit_types()
    repository = repository_type(clean_audit_database)
    repository.record_registration(_instance())

    repository.record_lifecycle(
        "vbas-audit-gpu0",
        OperatorLifecycle.DRAINING,
        source="ops",
        reason="运维排空",
    )
    repository.record_lifecycle(
        "vbas-audit-gpu0",
        OperatorLifecycle.DRAINING,
        source="ops",
        reason="重复排空",
    )
    repository.record_lifecycle(
        "vbas-audit-gpu0",
        OperatorLifecycle.OFFLINE,
        source="ops",
        reason="停止路由",
    )
    repository.record_unregistration("vbas-audit-gpu0", source="operator")
    repository.record_unregistration("vbas-audit-gpu0", source="operator")

    events = repository.list_events("vbas-audit-gpu0", limit=20)
    assert [event.event_type for event in events] == [
        "UNREGISTERED",
        "LIFECYCLE_CHANGED",
        "LIFECYCLE_CHANGED",
        "REGISTERED",
    ]
    with clean_audit_database.connect() as connection:
        snapshot = connection.execute(
            text(
                "SELECT desired_state, unregistered_at FROM operator_instances "
                "WHERE instance_id = :instance_id"
            ),
            {"instance_id": "vbas-audit-gpu0"},
        ).one()
    assert snapshot.desired_state == "OFFLINE"
    assert snapshot.unregistered_at is not None


def test_draining_desired_state_survives_unregistration_and_reregistration(
    clean_audit_database: Engine,
) -> None:
    repository_type, _ = _load_audit_types()
    repository = repository_type(clean_audit_database)
    instance = _instance()

    repository.record_registration(instance)
    repository.record_lifecycle(
        instance.instance_id,
        OperatorLifecycle.DRAINING,
        source="ops",
        reason="运维排空",
    )
    repository.record_unregistration(instance.instance_id, source="operator")

    assert (
        repository.get_desired_lifecycle(instance.instance_id)
        is OperatorLifecycle.DRAINING
    )

    repository.record_registration(instance)

    assert (
        repository.get_desired_lifecycle(instance.instance_id)
        is OperatorLifecycle.DRAINING
    )
    with clean_audit_database.connect() as connection:
        snapshot = connection.execute(
            text(
                "SELECT desired_state, unregistered_at FROM operator_instances "
                "WHERE instance_id = :instance_id"
            ),
            {"instance_id": instance.instance_id},
        ).one()
    assert snapshot.desired_state == "DRAINING"
    assert snapshot.unregistered_at is None


def test_event_insert_failure_rolls_back_snapshot_in_same_transaction(
    clean_audit_database: Engine,
) -> None:
    repository_type, _ = _load_audit_types()
    repository = repository_type(clean_audit_database)
    with clean_audit_database.begin() as connection:
        connection.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION milestone1_reject_audit_event()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.instance_id = 'vbas-audit-gpu0' THEN
                        RAISE EXCEPTION 'forced operator event failure';
                    END IF;
                    RETURN NEW;
                END;
                $$;
                CREATE TRIGGER milestone1_reject_audit_event
                BEFORE INSERT ON operator_instance_events
                FOR EACH ROW EXECUTE FUNCTION milestone1_reject_audit_event();
                """
            )
        )

    try:
        with pytest.raises(Exception, match="forced operator event failure"):
            repository.record_registration(_instance())
        with clean_audit_database.connect() as connection:
            snapshot_count = connection.execute(
                text(
                    "SELECT count(*) FROM operator_instances "
                    "WHERE instance_id = :instance_id"
                ),
                {"instance_id": "vbas-audit-gpu0"},
            ).scalar_one()
        assert snapshot_count == 0
    finally:
        with clean_audit_database.begin() as connection:
            connection.execute(
                text(
                    "DROP TRIGGER IF EXISTS milestone1_reject_audit_event "
                    "ON operator_instance_events"
                )
            )
            connection.execute(
                text("DROP FUNCTION IF EXISTS milestone1_reject_audit_event()")
            )


def test_lease_hot_path_never_calls_postgresql_audit() -> None:
    _, registry_type = _load_audit_types()

    class RealtimeRegistry:
        def lease(self, capability: str, ttl_seconds: int) -> CapacityLease:
            return CapacityLease(
                lease_id="lease-audit-1",
                instance_id="vbas-audit-gpu0",
                capability=capability,
                service_url="http://127.0.0.1:19001",
                expires_at=_instance().last_heartbeat_at,
            )

        def renew(self, lease_id: str, ttl_seconds: int) -> CapacityLease:
            return self.lease("teacher_behavior", ttl_seconds)

        def release(self, lease_id: str) -> None:
            return None

    class AuditMustNotBeCalled:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"lease 热路径访问了 PostgreSQL 审计: {name}")

    registry = registry_type(
        RealtimeRegistry(),
        AuditMustNotBeCalled(),
        heartbeat_audit_interval_seconds=60,
        health_checker=AlwaysHealthy(),
    )

    lease = registry.lease("teacher_behavior", 30)
    renewed = registry.renew(lease.lease_id, 30)
    registry.release(lease.lease_id)

    assert lease.instance_id == renewed.instance_id == "vbas-audit-gpu0"


def test_cross_store_failures_keep_routing_in_the_conservative_state() -> None:
    _, registry_type = _load_audit_types()

    class RealtimeRegistry:
        def __init__(self) -> None:
            self.register_calls = 0
            self.heartbeat_calls = 0
            self.lifecycle = OperatorLifecycle.ONLINE

        def register(self, instance: OperatorInstance) -> OperatorInstance:
            self.register_calls += 1
            return instance

        def set_lifecycle(
            self,
            instance_id: str,
            lifecycle: OperatorLifecycle,
        ) -> OperatorInstance:
            self.lifecycle = lifecycle
            return _instance(lifecycle=lifecycle)

        def heartbeat(
            self,
            instance_id: str,
            *,
            inflight: int,
            model_ready: bool,
        ) -> OperatorInstance:
            self.heartbeat_calls += 1
            return _instance(inflight=inflight, model_ready=model_ready)

    class FailingAudit:
        def record_registration(self, instance: OperatorInstance) -> None:
            raise RuntimeError("postgres unavailable")

        def record_lifecycle(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("postgres unavailable")

        def record_heartbeat_summary(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("postgres unavailable")

    realtime = RealtimeRegistry()
    registry = registry_type(
        realtime,
        FailingAudit(),
        heartbeat_audit_interval_seconds=60,
        health_checker=AlwaysHealthy(),
    )

    with pytest.raises(RuntimeError, match="postgres unavailable"):
        registry.register(_instance())
    assert realtime.register_calls == 0

    with pytest.raises(RuntimeError, match="postgres unavailable"):
        registry.set_lifecycle("vbas-audit-gpu0", OperatorLifecycle.DRAINING)
    assert realtime.lifecycle is OperatorLifecycle.DRAINING

    heartbeat = registry.heartbeat(
        "vbas-audit-gpu0",
        inflight=1,
        model_ready=True,
    )
    assert heartbeat.inflight == 1
    assert realtime.heartbeat_calls == 2

    class RedisFailingRegistry(RealtimeRegistry):
        def register(self, instance: OperatorInstance) -> OperatorInstance:
            raise RuntimeError("redis unavailable")

    class RecordingAudit:
        def __init__(self) -> None:
            self.registrations = 0

        def record_registration(self, instance: OperatorInstance) -> None:
            self.registrations += 1

        def get_desired_lifecycle(
            self,
            instance_id: str,
        ) -> OperatorLifecycle:
            del instance_id
            return OperatorLifecycle.ONLINE

    recording_audit = RecordingAudit()
    redis_failing_registry = registry_type(
        RedisFailingRegistry(),
        recording_audit,
        heartbeat_audit_interval_seconds=60,
        health_checker=AlwaysHealthy(),
    )
    with pytest.raises(RuntimeError, match="redis unavailable"):
        redis_failing_registry.register(_instance())
    assert recording_audit.registrations == 1


def test_unregister_retry_is_idempotent_after_redis_state_was_removed() -> None:
    _, registry_type = _load_audit_types()

    class RealtimeRegistry:
        def __init__(self) -> None:
            self.present = True

        def set_lifecycle(
            self,
            instance_id: str,
            lifecycle: OperatorLifecycle,
        ) -> OperatorInstance:
            if not self.present:
                raise OperatorInstanceNotFoundError(instance_id)
            return _instance(lifecycle=lifecycle)

        def unregister(self, instance_id: str) -> None:
            if not self.present:
                raise AssertionError("不应对已缺失的 Redis 实例重复注销")
            self.present = False

    class IdempotentAudit:
        def __init__(self) -> None:
            self.calls = 0

        def record_unregistration(self, instance_id: str, *, source: str) -> bool:
            self.calls += 1
            return self.calls == 1

    realtime = RealtimeRegistry()
    audit = IdempotentAudit()
    registry = registry_type(
        realtime,
        audit,
        heartbeat_audit_interval_seconds=60,
        health_checker=AlwaysHealthy(),
    )

    registry.unregister("vbas-audit-gpu0")
    registry.unregister("vbas-audit-gpu0")

    assert realtime.present is False
    assert audit.calls == 2


def test_successful_heartbeat_only_clears_its_own_pending_audit_error() -> None:
    _, registry_type = _load_audit_types()

    class RealtimeRegistry:
        def heartbeat(
            self,
            instance_id: str,
            *,
            inflight: int,
            model_ready: bool,
        ) -> OperatorInstance:
            return replace(
                _instance(inflight=inflight, model_ready=model_ready),
                instance_id=instance_id,
            )

    class SelectiveAudit:
        def record_heartbeat_summary(
            self,
            instance_id: str,
            **kwargs: Any,
        ) -> bool:
            if instance_id == "operator-a":
                raise RuntimeError("operator-a audit unavailable")
            return True

    registry = registry_type(
        RealtimeRegistry(),
        SelectiveAudit(),
        heartbeat_audit_interval_seconds=60,
        health_checker=AlwaysHealthy(),
    )

    registry.heartbeat("operator-a", inflight=1, model_ready=True)
    registry.heartbeat("operator-b", inflight=0, model_ready=True)

    assert registry.last_audit_error is not None
    assert "operator-a" in registry.last_audit_error
    assert "operator-b" not in registry.last_audit_error
