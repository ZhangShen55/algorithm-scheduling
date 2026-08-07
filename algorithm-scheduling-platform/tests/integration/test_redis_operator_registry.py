import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from redis import Redis

from packages.platform_common.operator_registry import (
    CapacityUnavailableError,
    OperatorCode,
    OperatorInstance,
    OperatorLifecycle,
)
from packages.platform_common.redis_operator_registry import RedisOperatorRegistry

pytestmark = pytest.mark.integration
TEST_REDIS_URL = "redis://127.0.0.1:6379/15"


@pytest.fixture
def redis_registry() -> RedisOperatorRegistry:
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        client.ping()
    except Exception as exc:
        pytest.skip(f"Redis 集成测试环境不可用: {exc}")
    client.flushdb()
    return RedisOperatorRegistry(client, heartbeat_ttl_seconds=2)


def vbas_instance(capacity: int = 1) -> OperatorInstance:
    return OperatorInstance(
        instance_id="vbas-gpu0",
        operator_code=OperatorCode.VBAS,
        capabilities=["teacher_behavior"],
        service_url="http://127.0.0.1:19001",
        declared_capacity=capacity,
        labels={"gpu": "0"},
    )


def test_atomic_capacity_lease_allows_only_one_final_slot(
    redis_registry: RedisOperatorRegistry,
) -> None:
    redis_registry.register(vbas_instance(capacity=1))

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
    redis_registry.register(vbas_instance())
    first = redis_registry.lease("teacher_behavior", 30)
    redis_registry.release(first.lease_id)
    second = redis_registry.lease("teacher_behavior", 1)

    time.sleep(1.1)
    recovered = redis_registry.lease("teacher_behavior", 30)

    assert second.instance_id == recovered.instance_id


def test_renew_keeps_async_capacity_reserved(
    redis_registry: RedisOperatorRegistry,
) -> None:
    redis_registry.register(vbas_instance())
    lease = redis_registry.lease("teacher_behavior", 1)

    renewed = redis_registry.renew(lease.lease_id, 3)
    time.sleep(1.1)

    assert renewed.lease_id == lease.lease_id
    assert renewed.expires_at > lease.expires_at
    with pytest.raises(CapacityUnavailableError):
        redis_registry.lease("teacher_behavior", 30)


def test_expired_heartbeat_excludes_instance_from_routing() -> None:
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        client.ping()
    except Exception as exc:
        pytest.skip(f"Redis 集成测试环境不可用: {exc}")
    client.flushdb()
    registry = RedisOperatorRegistry(client, heartbeat_ttl_seconds=1)
    registry.register(vbas_instance())

    time.sleep(1.1)

    with pytest.raises(CapacityUnavailableError):
        registry.lease("teacher_behavior", 30)
    assert registry.list_instances()[0].lifecycle is OperatorLifecycle.OFFLINE


def test_draining_instance_keeps_visibility_but_rejects_new_leases(
    redis_registry: RedisOperatorRegistry,
) -> None:
    redis_registry.register(vbas_instance())

    draining = redis_registry.set_lifecycle("vbas-gpu0", OperatorLifecycle.DRAINING)

    assert draining.lifecycle is OperatorLifecycle.DRAINING
    with pytest.raises(CapacityUnavailableError):
        redis_registry.lease("teacher_behavior", 30)

    redis_registry.set_lifecycle("vbas-gpu0", OperatorLifecycle.ONLINE)
    assert redis_registry.lease("teacher_behavior", 30).instance_id == "vbas-gpu0"
