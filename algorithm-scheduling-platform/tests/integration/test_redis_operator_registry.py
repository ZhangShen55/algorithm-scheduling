import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

import pytest
from redis import Redis

from packages.platform_common.operator_registry import (
    CapacityLeaseNotFoundError,
    CapacityUnavailableError,
    OperatorCode,
    OperatorInstance,
    OperatorInstanceNotFoundError,
    OperatorLifecycle,
)
from packages.platform_common.redis_operator_registry import RedisOperatorRegistry

pytestmark = pytest.mark.integration
TEST_REDIS_URL = "redis://127.0.0.1:6379/15"


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


def test_reported_inflight_consumes_capacity_without_double_counting_leases(
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
