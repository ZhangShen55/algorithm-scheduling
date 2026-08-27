import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event
from uuid import uuid4

import pytest
from control_service.app.api.control import create_control_app
from fastapi.testclient import TestClient
from redis import Redis

from packages.platform_common.config import PlatformSettings
from packages.platform_common.operator_registry import (
    CapacityLeaseContextConflictError,
    CapacityLeaseNotFoundError,
    CapacityUnavailableError,
    LeaseContextStatus,
    OperatorCode,
    OperatorInstance,
    OperatorInstanceNotFoundError,
    OperatorLifecycle,
    WorkContext,
)
from packages.platform_common.redis_operator_registry import RedisOperatorRegistry

pytestmark = pytest.mark.integration
TEST_REDIS_URL = "redis://127.0.0.1:6379/15"
REGISTRY_HEADERS = {"X-Operator-Registry-Token": "registry-test-token"}


def _unique_key_prefix() -> str:
    return f"algorithm-platform:test:operator-registry:{uuid4().hex}:"


def _cleanup_redis_client(client: Redis, key_prefix: str) -> None:
    try:
        keys = list(client.scan_iter(match=f"{key_prefix}*", count=100))
        if keys:
            client.delete(*keys)
    finally:
        client.close()


@pytest.fixture
def redis_registry() -> Iterator[RedisOperatorRegistry]:
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    key_prefix = _unique_key_prefix()
    try:
        client.ping()
    except Exception as exc:
        client.close()
        pytest.skip(f"Redis 集成测试环境不可用: {exc}")
    try:
        yield RedisOperatorRegistry(
            client,
            heartbeat_ttl_seconds=2,
            key_prefix=key_prefix,
        )
    finally:
        _cleanup_redis_client(client, key_prefix)


def vbas_instance(capacity: int = 1) -> OperatorInstance:
    return OperatorInstance(
        instance_id="vbas-gpu0",
        operator_code=OperatorCode.VBAS,
        capabilities=["teacher_behavior"],
        service_url="http://127.0.0.1:19001",
        declared_capacity=capacity,
        labels={"gpu": "0"},
    )


def _register_ready(
    registry: RedisOperatorRegistry,
    instance: OperatorInstance,
) -> OperatorInstance:
    registry.register(instance)
    return registry.heartbeat(
        instance.instance_id,
        inflight=instance.inflight,
        model_ready=instance.model_ready,
    )


def test_registration_requires_first_heartbeat_before_routing(
    redis_registry: RedisOperatorRegistry,
) -> None:
    registered = redis_registry.register(vbas_instance())

    assert registered.lifecycle is OperatorLifecycle.OFFLINE
    with pytest.raises(CapacityUnavailableError):
        redis_registry.lease("teacher_behavior", 30)

    redis_registry.heartbeat("vbas-gpu0", inflight=0, model_ready=True)
    assert redis_registry.lease("teacher_behavior", 30).instance_id == "vbas-gpu0"


def test_retired_text_analysis_registration_writes_no_redis_state(tmp_path) -> None:
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    key_prefix = _unique_key_prefix()
    try:
        client.ping()
    except Exception as exc:
        client.close()
        pytest.skip(f"Redis 集成测试环境不可用: {exc}")
    registry = RedisOperatorRegistry(
        client,
        heartbeat_ttl_seconds=2,
        key_prefix=key_prefix,
    )
    app = create_control_app(
        operator_registry=registry,
        settings=PlatformSettings(
            course_root=tmp_path / "course",
            result_root=tmp_path / "result",
            operator_registry_token="registry-test-token",
            trusted_operator_service_urls={
                "text-analysis-cpu0": "http://text-analysis-cpu0:8000",
            },
        ),
    )
    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/api/operator-instances/register",
                headers=REGISTRY_HEADERS,
                json={
                    "instance_id": "text-analysis-cpu0",
                    "operator_code": "text_analysis",
                    "capabilities": ["extract_keywords", "course_overviews"],
                    "service_url": "http://text-analysis-cpu0:8000",
                    "declared_capacity": 1,
                },
            )

        assert response.status_code == 422
        assert list(client.scan_iter(match=f"{key_prefix}*")) == []
    finally:
        _cleanup_redis_client(client, key_prefix)


def test_retired_operator_left_in_redis_is_not_listed_or_routable() -> None:
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    key_prefix = _unique_key_prefix()
    try:
        client.ping()
    except Exception as exc:
        client.close()
        pytest.skip(f"Redis 集成测试环境不可用: {exc}")
    registry = RedisOperatorRegistry(
        client,
        heartbeat_ttl_seconds=30,
        key_prefix=key_prefix,
    )
    instance_id = "text-analysis-cpu0"
    instance_key = f"{key_prefix}instance:{instance_id}"
    try:
        client.hset(
            instance_key,
            mapping={
                "operator_code": "text_analysis",
                "capabilities": '["extract_keywords","course_overviews"]',
                "service_url": "http://text-analysis-cpu0:8000",
                "model_version": "historical",
                "api_version": "v1",
                "declared_capacity": "256",
                "labels": "{}",
                "lifecycle": "ONLINE",
                "inflight": "0",
                "model_ready": "1",
                "last_heartbeat_at": "2026-08-21T00:00:00+00:00",
            },
        )
        client.sadd(f"{key_prefix}instances", instance_id)
        client.sadd(f"{key_prefix}capability:extract_keywords", instance_id)
        client.set(f"{key_prefix}heartbeat:{instance_id}", "1", ex=30)

        assert registry.list_instances() == []
        with pytest.raises(CapacityUnavailableError):
            registry.lease("extract_keywords", 30)
        assert list(client.scan_iter(match=f"{key_prefix}lease:*")) == []
    finally:
        _cleanup_redis_client(client, key_prefix)


def test_atomic_capacity_lease_allows_only_one_final_slot(
    redis_registry: RedisOperatorRegistry,
) -> None:
    _register_ready(redis_registry, vbas_instance(capacity=1))

    def try_lease() -> str:
        try:
            return redis_registry.lease("teacher_behavior", 30).lease_id
        except CapacityUnavailableError:
            return "NO_CAPACITY"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: try_lease(), range(2)))

    assert results.count("NO_CAPACITY") == 1
    assert len([result for result in results if result != "NO_CAPACITY"]) == 1


def test_release_and_expiry_recover_capacity(redis_registry: RedisOperatorRegistry) -> None:
    _register_ready(redis_registry, vbas_instance())
    first = redis_registry.lease("teacher_behavior", 30)
    redis_registry.release(first.lease_id)
    second = redis_registry.lease("teacher_behavior", 1)

    time.sleep(1.1)
    recovered = redis_registry.lease("teacher_behavior", 30)

    assert second.instance_id == recovered.instance_id


def test_renew_keeps_async_capacity_reserved(
    redis_registry: RedisOperatorRegistry,
) -> None:
    _register_ready(redis_registry, vbas_instance())
    lease = redis_registry.lease("teacher_behavior", 1)

    renewed = redis_registry.renew(lease.lease_id, 3)
    time.sleep(1.1)

    assert renewed.lease_id == lease.lease_id
    assert renewed.expires_at > lease.expires_at
    with pytest.raises(CapacityUnavailableError):
        redis_registry.lease("teacher_behavior", 30)


def test_redis_restart_invalidates_persisted_capacity_lease(
    redis_registry: RedisOperatorRegistry,
) -> None:
    _register_ready(redis_registry, vbas_instance(capacity=1))
    stale_lease = redis_registry.lease("teacher_behavior", 30)
    lease_key = f"{redis_registry._prefix}lease:{stale_lease.lease_id}"

    # AOF keeps the lease data, while a restarted Redis process has a new run_id.
    redis_registry._client.hset(lease_key, "redis_run_id", "previous-redis-process")

    with pytest.raises(CapacityLeaseNotFoundError):
        redis_registry.release(stale_lease.lease_id)
    assert redis_registry.active_lease_count(stale_lease.instance_id) == 0
    assert (
        redis_registry.lease("teacher_behavior", 30).instance_id
        == stale_lease.instance_id
    )


def test_redis_restart_stale_lease_does_not_block_new_capacity(
    redis_registry: RedisOperatorRegistry,
) -> None:
    _register_ready(redis_registry, vbas_instance(capacity=1))
    stale_lease = redis_registry.lease("teacher_behavior", 30)
    lease_key = f"{redis_registry._prefix}lease:{stale_lease.lease_id}"
    redis_registry._client.hset(lease_key, "redis_run_id", "previous-redis-process")

    replacement = redis_registry.lease("teacher_behavior", 30)

    assert replacement.instance_id == stale_lease.instance_id
    assert replacement.lease_id != stale_lease.lease_id
    with pytest.raises(CapacityLeaseNotFoundError):
        redis_registry.release(stale_lease.lease_id)


def test_expired_heartbeat_excludes_instance_from_routing() -> None:
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    key_prefix = _unique_key_prefix()
    try:
        client.ping()
    except Exception as exc:
        client.close()
        pytest.skip(f"Redis 集成测试环境不可用: {exc}")
    try:
        registry = RedisOperatorRegistry(
            client,
            heartbeat_ttl_seconds=1,
            key_prefix=key_prefix,
        )
        _register_ready(registry, vbas_instance())

        time.sleep(1.1)

        with pytest.raises(CapacityUnavailableError):
            registry.lease("teacher_behavior", 30)
        assert registry.list_instances()[0].lifecycle is OperatorLifecycle.OFFLINE
    finally:
        _cleanup_redis_client(client, key_prefix)


def test_existing_lease_can_renew_during_transient_heartbeat_expiry() -> None:
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    key_prefix = _unique_key_prefix()
    try:
        client.ping()
    except Exception as exc:
        client.close()
        pytest.skip(f"Redis 集成测试环境不可用: {exc}")
    try:
        registry = RedisOperatorRegistry(
            client,
            heartbeat_ttl_seconds=1,
            key_prefix=key_prefix,
        )
        _register_ready(registry, vbas_instance(capacity=1))
        lease = registry.lease("teacher_behavior", 3)

        time.sleep(1.1)

        renewed = registry.renew(lease.lease_id, 3)
        assert renewed.lease_id == lease.lease_id
        assert registry.list_instances()[0].lifecycle is OperatorLifecycle.OFFLINE
        with pytest.raises(CapacityUnavailableError):
            registry.lease("teacher_behavior", 30)

        registry.heartbeat("vbas-gpu0", inflight=1, model_ready=True)
        with pytest.raises(CapacityUnavailableError):
            registry.lease("teacher_behavior", 30)
    finally:
        _cleanup_redis_client(client, key_prefix)


def test_draining_instance_keeps_visibility_but_rejects_new_leases(
    redis_registry: RedisOperatorRegistry,
) -> None:
    _register_ready(redis_registry, vbas_instance())

    draining = redis_registry.set_lifecycle("vbas-gpu0", OperatorLifecycle.DRAINING)

    assert draining.lifecycle is OperatorLifecycle.DRAINING
    with pytest.raises(CapacityUnavailableError):
        redis_registry.lease("teacher_behavior", 30)

    redis_registry.set_lifecycle("vbas-gpu0", OperatorLifecycle.ONLINE)
    assert redis_registry.lease("teacher_behavior", 30).instance_id == "vbas-gpu0"


def test_reregistering_draining_instance_does_not_restore_routing(
    redis_registry: RedisOperatorRegistry,
) -> None:
    _register_ready(redis_registry, vbas_instance())
    redis_registry.set_lifecycle("vbas-gpu0", OperatorLifecycle.DRAINING)

    redis_registry.register(vbas_instance())
    heartbeat = redis_registry.heartbeat(
        "vbas-gpu0",
        inflight=0,
        model_ready=True,
    )

    assert heartbeat.lifecycle is OperatorLifecycle.DRAINING
    with pytest.raises(CapacityUnavailableError):
        redis_registry.lease("teacher_behavior", 30)


def test_logically_expired_lease_cannot_be_renewed(
    redis_registry: RedisOperatorRegistry,
) -> None:
    registry = redis_registry
    _register_ready(registry, vbas_instance())
    lease = registry.lease("teacher_behavior", 30)
    client = registry._client
    redis_seconds, redis_microseconds = client.time()
    redis_now_ms = redis_seconds * 1000 + redis_microseconds // 1000
    client.zadd(
        f"{registry._prefix}leases:{lease.instance_id}",
        {lease.lease_id: redis_now_ms - 1000},
    )

    with pytest.raises(CapacityLeaseNotFoundError):
        registry.renew(lease.lease_id, 30)


def test_unregister_invalidates_existing_leases(
    redis_registry: RedisOperatorRegistry,
) -> None:
    _register_ready(redis_registry, vbas_instance())
    lease = redis_registry.lease("teacher_behavior", 30)

    redis_registry.unregister("vbas-gpu0")

    with pytest.raises(CapacityLeaseNotFoundError):
        redis_registry.renew(lease.lease_id, 30)


def test_reregistering_same_instance_does_not_inherit_old_leases(
    redis_registry: RedisOperatorRegistry,
) -> None:
    _register_ready(redis_registry, vbas_instance(capacity=1))
    old_lease = redis_registry.lease("teacher_behavior", 30)

    redis_registry.register(vbas_instance(capacity=1))

    with pytest.raises(CapacityLeaseNotFoundError):
        redis_registry.renew(old_lease.lease_id, 30)
    with pytest.raises(CapacityUnavailableError):
        redis_registry.lease("teacher_behavior", 30)
    redis_registry.heartbeat("vbas-gpu0", inflight=0, model_ready=True)
    assert redis_registry.lease("teacher_behavior", 30).instance_id == "vbas-gpu0"


def test_effective_inflight_uses_the_greater_of_leases_and_reported_load(
    redis_registry: RedisOperatorRegistry,
) -> None:
    redis_registry.register(vbas_instance(capacity=2))
    redis_registry.heartbeat("vbas-gpu0", inflight=1, model_ready=True)

    first = redis_registry.lease("teacher_behavior", 30)
    second = redis_registry.lease("teacher_behavior", 30)
    assert first.instance_id == second.instance_id == "vbas-gpu0"
    with pytest.raises(CapacityUnavailableError):
        redis_registry.lease("teacher_behavior", 30)

    redis_registry.release(first.lease_id)
    redis_registry.release(second.lease_id)
    redis_registry.heartbeat("vbas-gpu0", inflight=2, model_ready=True)
    with pytest.raises(CapacityUnavailableError):
        redis_registry.lease("teacher_behavior", 30)
    snapshot = redis_registry.list_active_leases("vbas-gpu0")
    assert snapshot.active_lease_count == 0
    assert snapshot.reported_inflight == 2
    assert snapshot.attribution_difference == 2


def test_expired_lease_is_cleaned_but_reported_inflight_still_holds_capacity(
    redis_registry: RedisOperatorRegistry,
) -> None:
    _register_ready(redis_registry, vbas_instance(capacity=1))
    expired = redis_registry.lease("teacher_behavior", 1)

    time.sleep(1.1)
    redis_registry.heartbeat("vbas-gpu0", inflight=1, model_ready=True)
    expired_snapshot = redis_registry.list_active_leases("vbas-gpu0")

    assert expired_snapshot.active_lease_count == 0
    assert expired_snapshot.reported_inflight == 1
    assert expired_snapshot.attribution_difference == 1
    with pytest.raises(CapacityLeaseNotFoundError):
        redis_registry.renew(expired.lease_id, 30)

    with pytest.raises(CapacityUnavailableError):
        redis_registry.lease("teacher_behavior", 30)

    redis_registry.heartbeat("vbas-gpu0", inflight=0, model_ready=True)
    replacement = redis_registry.lease("teacher_behavior", 30)
    assert replacement.instance_id == "vbas-gpu0"


def test_lease_prefers_the_lowest_effective_load(
    redis_registry: RedisOperatorRegistry,
) -> None:
    for index, inflight in enumerate((1, 0, 0)):
        instance = replace(
            vbas_instance(capacity=1024),
            instance_id=f"vbas-gpu{index}",
            service_url=f"http://127.0.0.1:{19001 + index}",
            labels={"gpu": str(index)},
            inflight=inflight,
        )
        _register_ready(redis_registry, instance)

    lease = redis_registry.lease("teacher_behavior", 30)

    assert lease.instance_id in {"vbas-gpu1", "vbas-gpu2"}


def test_lease_compares_normalized_load_instead_of_absolute_work_count(
    redis_registry: RedisOperatorRegistry,
) -> None:
    for instance_id, capacity, inflight in (
        ("vbas-small", 4, 2),
        ("vbas-large", 100, 10),
    ):
        instance = replace(
            vbas_instance(capacity=capacity),
            instance_id=instance_id,
            service_url=f"http://{instance_id}.test",
            inflight=inflight,
        )
        _register_ready(redis_registry, instance)

    lease = redis_registry.lease("teacher_behavior", 30)

    assert lease.instance_id == "vbas-large"


def test_equal_load_instances_use_a_persistent_round_robin_cursor(
    redis_registry: RedisOperatorRegistry,
) -> None:
    for index in range(3):
        _register_ready(
            redis_registry,
            replace(
                vbas_instance(capacity=1024),
                instance_id=f"vbas-gpu{index}",
                service_url=f"http://127.0.0.1:{19001 + index}",
                labels={"gpu": str(index)},
            ),
        )

    selected: list[str] = []
    for _ in range(6):
        lease = redis_registry.lease("teacher_behavior", 30)
        selected.append(lease.instance_id)
        redis_registry.release(lease.lease_id)

    assert selected == [
        "vbas-gpu0",
        "vbas-gpu1",
        "vbas-gpu2",
        "vbas-gpu0",
        "vbas-gpu1",
        "vbas-gpu2",
    ]


def test_first_three_concurrent_leases_cover_three_equal_instances(
    redis_registry: RedisOperatorRegistry,
) -> None:
    for index in range(3):
        _register_ready(
            redis_registry,
            replace(
                vbas_instance(capacity=1024),
                instance_id=f"vbas-gpu{index}",
                service_url=f"http://127.0.0.1:{19001 + index}",
                labels={"gpu": str(index)},
            ),
        )
    barrier = Barrier(3)

    def acquire() -> str:
        barrier.wait()
        return redis_registry.lease("teacher_behavior", 30).instance_id

    with ThreadPoolExecutor(max_workers=3) as executor:
        selected = list(executor.map(lambda _: acquire(), range(3)))

    assert set(selected) == {"vbas-gpu0", "vbas-gpu1", "vbas-gpu2"}


def test_concurrent_leases_never_oversell_total_instance_capacity(
    redis_registry: RedisOperatorRegistry,
) -> None:
    for index in range(3):
        _register_ready(
            redis_registry,
            replace(
                vbas_instance(capacity=2),
                instance_id=f"vbas-gpu{index}",
                service_url=f"http://127.0.0.1:{19001 + index}",
                labels={"gpu": str(index)},
            ),
        )
    barrier = Barrier(18)

    def acquire() -> str:
        barrier.wait()
        try:
            return redis_registry.lease("teacher_behavior", 30).instance_id
        except CapacityUnavailableError:
            return "NO_CAPACITY"

    with ThreadPoolExecutor(max_workers=18) as executor:
        selected = list(executor.map(lambda _: acquire(), range(18)))

    assert selected.count("NO_CAPACITY") == 12
    assert {
        instance_id: selected.count(instance_id)
        for instance_id in {"vbas-gpu0", "vbas-gpu1", "vbas-gpu2"}
    } == {"vbas-gpu0": 2, "vbas-gpu1": 2, "vbas-gpu2": 2}


def test_multi_capability_instance_uses_one_shared_capacity_pool(
    redis_registry: RedisOperatorRegistry,
) -> None:
    instance = replace(
        vbas_instance(capacity=1),
        capabilities=["teacher_behavior", "student_behavior"],
    )
    _register_ready(redis_registry, instance)

    redis_registry.lease("teacher_behavior", 30)

    with pytest.raises(CapacityUnavailableError):
        redis_registry.lease("student_behavior", 30)


def test_lease_context_can_be_created_bound_idempotently_and_queried(
    redis_registry: RedisOperatorRegistry,
) -> None:
    _register_ready(redis_registry, vbas_instance())
    initial = WorkContext(
        source_service="online-gateway-service",
        work_type="online_vbas",
        work_id="request-001",
        trace_id="trace-001",
    )
    lease = redis_registry.lease("teacher_behavior", 30, initial)

    assert lease.work_context == initial
    assert lease.acquired_at < lease.expires_at
    renewed = redis_registry.renew(lease.lease_id, 60)
    assert renewed.acquired_at == lease.acquired_at
    assert renewed.work_context == initial

    snapshot = redis_registry.list_active_leases("vbas-gpu0")
    assert snapshot.active_lease_count == 1
    assert snapshot.leases[0].context_status is LeaseContextStatus.BOUND
    assert snapshot.leases[0].work_context == initial


def test_lease_context_post_binding_is_idempotent_and_rejects_conflict(
    redis_registry: RedisOperatorRegistry,
) -> None:
    _register_ready(redis_registry, vbas_instance())
    lease = redis_registry.lease("teacher_behavior", 30)
    context = WorkContext(
        source_service="orchestrator-service",
        work_type="node",
        work_id="node-7",
        task_id="course-001",
        node_id="7",
    )

    first = redis_registry.bind_lease_context(lease.lease_id, context)
    repeated = redis_registry.bind_lease_context(lease.lease_id, context)

    assert first.work_context == repeated.work_context == context
    with pytest.raises(CapacityLeaseContextConflictError):
        redis_registry.bind_lease_context(
            lease.lease_id,
            replace(context, work_id="node-8", node_id="8"),
        )


def test_released_or_expired_lease_cannot_be_bound(
    redis_registry: RedisOperatorRegistry,
) -> None:
    _register_ready(redis_registry, vbas_instance())
    context = WorkContext(
        source_service="orchestrator-service",
        work_type="node",
        work_id="node-7",
    )
    released = redis_registry.lease("teacher_behavior", 30)
    redis_registry.release(released.lease_id)

    with pytest.raises(CapacityLeaseNotFoundError):
        redis_registry.bind_lease_context(released.lease_id, context)


def test_invalid_capacity_record_is_never_schedulable(
    redis_registry: RedisOperatorRegistry,
) -> None:
    _register_ready(redis_registry, vbas_instance())
    redis_registry._client.hset(
        redis_registry._instance_key("vbas-gpu0"),
        "declared_capacity",
        "1.5",
    )

    with pytest.raises(CapacityUnavailableError):
        redis_registry.lease("teacher_behavior", 30)


def test_concurrent_reregistration_does_not_leave_stale_capability_membership(
    redis_registry: RedisOperatorRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = redis_registry
    _register_ready(registry, vbas_instance())
    barrier = Barrier(2)
    original_hget = registry._client.hget
    instance_key = registry._instance_key("vbas-gpu0")

    def synchronized_hget(name: str, key: str):  # type: ignore[no-untyped-def]
        value = original_hget(name, key)
        if name == instance_key and key == "capabilities":
            barrier.wait(timeout=5)
        return value

    monkeypatch.setattr(registry._client, "hget", synchronized_hget)

    def reregister(capability: str) -> None:
        registry.register(
            OperatorInstance(
                instance_id="vbas-gpu0",
                operator_code=OperatorCode.VBAS,
                capabilities=[capability],
                service_url="http://127.0.0.1:19001",
                declared_capacity=1,
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(reregister, "teacher_behavior_v2"),
            executor.submit(reregister, "student_behavior_v2"),
        ]
        for future in futures:
            future.result(timeout=5)

    final_capability = registry.list_instances()[0].capabilities[0]
    for capability in ("teacher_behavior", "teacher_behavior_v2", "student_behavior_v2"):
        members = registry._client.smembers(registry._capability_key(capability))
        assert ("vbas-gpu0" in members) is (capability == final_capability)


def test_concurrent_heartbeat_and_unregister_never_leave_partial_instance_state(
    redis_registry: RedisOperatorRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = redis_registry
    client = registry._client
    instance_key = registry._instance_key("vbas-gpu0")
    heartbeat_key = registry._heartbeat_key("vbas-gpu0")
    capability_key = registry._capability_key("teacher_behavior")
    required_hash_fields = {
        "operator_code",
        "capabilities",
        "service_url",
        "model_version",
        "api_version",
        "declared_capacity",
        "labels",
        "lifecycle",
        "inflight",
        "model_ready",
        "last_heartbeat_at",
    }
    inconsistent_states: list[str] = []
    unexpected_errors: list[str] = []

    for round_number in range(10):
        registry.register(vbas_instance())
        precheck_seen = Event()
        unregister_done = Event()
        original_exists = client.exists

        def pause_legacy_precheck(
            *names: str,
            _original_exists: Callable[..., int] = original_exists,
            _precheck_seen: Event = precheck_seen,
            _unregister_done: Event = unregister_done,
        ) -> int:
            result = _original_exists(*names)
            if names == (instance_key,) and result:
                _precheck_seen.set()
                _unregister_done.wait(timeout=5)
            return result

        def unregister_after_precheck(
            _precheck_seen: Event = precheck_seen,
            _unregister_done: Event = unregister_done,
        ) -> None:
            _precheck_seen.wait(timeout=0.05)
            try:
                registry.unregister("vbas-gpu0")
            finally:
                _unregister_done.set()

        with monkeypatch.context() as patch:
            patch.setattr(client, "exists", pause_legacy_precheck)
            with ThreadPoolExecutor(max_workers=2) as executor:
                heartbeat_future = executor.submit(
                    registry.heartbeat,
                    "vbas-gpu0",
                    inflight=round_number,
                    model_ready=True,
                )
                unregister_future = executor.submit(unregister_after_precheck)
                heartbeat_error = heartbeat_future.exception(timeout=5)
                unregister_error = unregister_future.exception(timeout=5)

        raw_hash = client.hgetall(instance_key)
        heartbeat_present = bool(client.exists(heartbeat_key))
        instance_member = "vbas-gpu0" in client.smembers(registry._instances_key)
        capability_member = "vbas-gpu0" in client.smembers(capability_key)
        complete = (
            required_hash_fields <= raw_hash.keys()
            and heartbeat_present
            and instance_member
            and capability_member
        )
        absent = (
            not raw_hash
            and not heartbeat_present
            and not instance_member
            and not capability_member
        )
        if not (complete or absent):
            inconsistent_states.append(
                f"round={round_number}, hash_fields={sorted(raw_hash)}, "
                f"heartbeat={heartbeat_present}, instance_member={instance_member}, "
                f"capability_member={capability_member}"
            )
        if heartbeat_error is not None and not isinstance(
            heartbeat_error,
            OperatorInstanceNotFoundError,
        ):
            unexpected_errors.append(f"round={round_number}, heartbeat={heartbeat_error!r}")
        if unregister_error is not None:
            unexpected_errors.append(f"round={round_number}, unregister={unregister_error!r}")

    assert not inconsistent_states, "检测到部分注册状态:\n" + "\n".join(inconsistent_states)
    assert not unexpected_errors, "并发操作出现异常:\n" + "\n".join(unexpected_errors)


def test_list_instances_tolerates_member_removed_during_snapshot(
    redis_registry: RedisOperatorRegistry,
) -> None:
    registry = redis_registry
    registry.register(vbas_instance())
    registry._client.delete(registry._instance_key("vbas-gpu0"))

    assert registry.list_instances() == []
