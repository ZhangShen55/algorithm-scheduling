from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from redis import Redis

from packages.platform_common.operator_registry import (
    CapacityLease,
    CapacityLeaseNotFoundError,
    CapacityUnavailableError,
    OperatorCode,
    OperatorInstance,
    OperatorInstanceNotFoundError,
    OperatorLifecycle,
)

_LEASE_SCRIPT = """
local instance_ids = redis.call('SMEMBERS', KEYS[1])
table.sort(instance_ids)
for _, instance_id in ipairs(instance_ids) do
    local instance_key = ARGV[4] .. instance_id
    local heartbeat_key = ARGV[5] .. instance_id
    if redis.call('EXISTS', heartbeat_key) == 1
        and redis.call('HGET', instance_key, 'lifecycle') == 'ONLINE'
        and redis.call('HGET', instance_key, 'model_ready') == '1' then
        local leases_key = ARGV[6] .. instance_id
        redis.call('ZREMRANGEBYSCORE', leases_key, '-inf', ARGV[1])
        local used = redis.call('ZCARD', leases_key)
        local capacity = tonumber(redis.call('HGET', instance_key, 'declared_capacity') or '0')
        if used < capacity then
            local expires_at = tonumber(ARGV[1]) + tonumber(ARGV[2])
            redis.call('ZADD', leases_key, expires_at, ARGV[3])
            local lease_key = ARGV[7] .. ARGV[3]
            redis.call('HSET', lease_key,
                'instance_id', instance_id,
                'capability', ARGV[8],
                'service_url', redis.call('HGET', instance_key, 'service_url'),
                'expires_at', expires_at)
            redis.call('PEXPIRE', lease_key, ARGV[2])
            return {instance_id, redis.call('HGET', instance_key, 'service_url'), expires_at}
        end
    end
end
return {}
"""


_RELEASE_SCRIPT = """
local instance_id = redis.call('HGET', KEYS[1], 'instance_id')
if not instance_id then
    return 0
end
redis.call('ZREM', ARGV[1] .. instance_id, ARGV[2])
redis.call('DEL', KEYS[1])
return 1
"""


_RENEW_SCRIPT = """
local instance_id = redis.call('HGET', KEYS[1], 'instance_id')
if not instance_id then
    return {}
end
local capability = redis.call('HGET', KEYS[1], 'capability')
local service_url = redis.call('HGET', KEYS[1], 'service_url')
local expires_at = tonumber(ARGV[1]) + tonumber(ARGV[2])
redis.call('ZADD', ARGV[3] .. instance_id, expires_at, ARGV[4])
redis.call('HSET', KEYS[1], 'expires_at', expires_at)
redis.call('PEXPIRE', KEYS[1], ARGV[2])
return {instance_id, capability, service_url, expires_at}
"""


class RedisOperatorRegistry:
    def __init__(
        self,
        client: Redis,
        *,
        heartbeat_ttl_seconds: int = 15,
        key_prefix: str = "algorithm-platform:",
    ) -> None:
        if heartbeat_ttl_seconds <= 0:
            raise ValueError("心跳 TTL 必须大于 0")
        self._client = client
        self._heartbeat_ttl_seconds = heartbeat_ttl_seconds
        self._prefix = key_prefix

    def _instance_key(self, instance_id: str) -> str:
        return f"{self._prefix}instance:{instance_id}"

    def _heartbeat_key(self, instance_id: str) -> str:
        return f"{self._prefix}heartbeat:{instance_id}"

    def _capability_key(self, capability: str) -> str:
        return f"{self._prefix}capability:{capability}"

    @property
    def _instances_key(self) -> str:
        return f"{self._prefix}instances"

    def register(self, instance: OperatorInstance) -> OperatorInstance:
        instance_key = self._instance_key(instance.instance_id)
        previous_capabilities = self._client.hget(instance_key, "capabilities")
        pipeline = self._client.pipeline(transaction=True)
        if previous_capabilities:
            for capability in json.loads(str(previous_capabilities)):
                pipeline.srem(self._capability_key(capability), instance.instance_id)
        heartbeat_at = datetime.now(UTC)
        pipeline.hset(
            instance_key,
            mapping={
                "operator_code": instance.operator_code.value,
                "capabilities": json.dumps(instance.capabilities, separators=(",", ":")),
                "service_url": instance.service_url,
                "model_version": instance.model_version or "",
                "api_version": instance.api_version or "",
                "declared_capacity": instance.declared_capacity,
                "labels": json.dumps(instance.labels, separators=(",", ":")),
                "lifecycle": instance.lifecycle.value,
                "inflight": instance.inflight,
                "model_ready": "1" if instance.model_ready else "0",
                "last_heartbeat_at": heartbeat_at.isoformat(),
            },
        )
        pipeline.sadd(self._instances_key, instance.instance_id)
        for capability in instance.capabilities:
            pipeline.sadd(self._capability_key(capability), instance.instance_id)
        pipeline.set(
            self._heartbeat_key(instance.instance_id),
            "1",
            ex=self._heartbeat_ttl_seconds,
        )
        pipeline.execute()
        return self._get_instance(instance.instance_id)

    def heartbeat(
        self,
        instance_id: str,
        *,
        inflight: int,
        model_ready: bool,
    ) -> OperatorInstance:
        instance_key = self._instance_key(instance_id)
        if not self._client.exists(instance_key):
            raise OperatorInstanceNotFoundError(instance_id)
        heartbeat_at = datetime.now(UTC)
        pipeline = self._client.pipeline(transaction=True)
        pipeline.hset(
            instance_key,
            mapping={
                "inflight": inflight,
                "model_ready": "1" if model_ready else "0",
                "last_heartbeat_at": heartbeat_at.isoformat(),
            },
        )
        pipeline.set(
            self._heartbeat_key(instance_id),
            "1",
            ex=self._heartbeat_ttl_seconds,
        )
        pipeline.execute()
        return self._get_instance(instance_id)

    def unregister(self, instance_id: str) -> None:
        instance_key = self._instance_key(instance_id)
        capabilities = self._client.hget(instance_key, "capabilities")
        if capabilities is None:
            raise OperatorInstanceNotFoundError(instance_id)
        pipeline = self._client.pipeline(transaction=True)
        for capability in json.loads(str(capabilities)):
            pipeline.srem(self._capability_key(capability), instance_id)
        pipeline.srem(self._instances_key, instance_id)
        pipeline.delete(instance_key, self._heartbeat_key(instance_id))
        pipeline.execute()

    def list_instances(self) -> list[OperatorInstance]:
        instance_ids = sorted(cast(set[str], self._client.smembers(self._instances_key)))
        return [self._get_instance(instance_id) for instance_id in instance_ids]

    def set_lifecycle(
        self,
        instance_id: str,
        lifecycle: OperatorLifecycle,
    ) -> OperatorInstance:
        instance_key = self._instance_key(instance_id)
        if not self._client.exists(instance_key):
            raise OperatorInstanceNotFoundError(instance_id)
        self._client.hset(instance_key, "lifecycle", lifecycle.value)
        return self._get_instance(instance_id)

    def lease(self, capability: str, ttl_seconds: int) -> CapacityLease:
        if ttl_seconds <= 0:
            raise ValueError("租约 TTL 必须大于 0")
        lease_id = str(uuid4())
        now_ms = int(time.time() * 1000)
        ttl_ms = ttl_seconds * 1000
        result = cast(
            list[Any],
            self._client.eval(
                _LEASE_SCRIPT,
                1,
                self._capability_key(capability),
                str(now_ms),
                str(ttl_ms),
                lease_id,
                f"{self._prefix}instance:",
                f"{self._prefix}heartbeat:",
                f"{self._prefix}leases:",
                f"{self._prefix}lease:",
                capability,
            ),
        )
        if not result:
            raise CapacityUnavailableError(capability)
        return CapacityLease(
            lease_id=lease_id,
            instance_id=str(result[0]),
            capability=capability,
            service_url=str(result[1]),
            expires_at=datetime.fromtimestamp(int(result[2]) / 1000, tz=UTC),
        )

    def release(self, lease_id: str) -> None:
        self._client.eval(
            _RELEASE_SCRIPT,
            1,
            f"{self._prefix}lease:{lease_id}",
            f"{self._prefix}leases:",
            lease_id,
        )

    def renew(self, lease_id: str, ttl_seconds: int) -> CapacityLease:
        if ttl_seconds <= 0:
            raise ValueError("租约 TTL 必须大于 0")
        now_ms = int(time.time() * 1000)
        ttl_ms = ttl_seconds * 1000
        result = cast(
            list[Any],
            self._client.eval(
                _RENEW_SCRIPT,
                1,
                f"{self._prefix}lease:{lease_id}",
                str(now_ms),
                str(ttl_ms),
                f"{self._prefix}leases:",
                lease_id,
            ),
        )
        if not result:
            raise CapacityLeaseNotFoundError(lease_id)
        return CapacityLease(
            lease_id=lease_id,
            instance_id=str(result[0]),
            capability=str(result[1]),
            service_url=str(result[2]),
            expires_at=datetime.fromtimestamp(int(result[3]) / 1000, tz=UTC),
        )

    def _get_instance(self, instance_id: str) -> OperatorInstance:
        raw = cast(dict[str, str], self._client.hgetall(self._instance_key(instance_id)))
        if not raw:
            raise OperatorInstanceNotFoundError(instance_id)
        lifecycle = OperatorLifecycle(raw["lifecycle"])
        if not self._client.exists(self._heartbeat_key(instance_id)):
            lifecycle = OperatorLifecycle.OFFLINE
        return OperatorInstance(
            instance_id=instance_id,
            operator_code=OperatorCode(raw["operator_code"]),
            capabilities=json.loads(raw["capabilities"]),
            service_url=raw["service_url"],
            model_version=raw["model_version"] or None,
            api_version=raw["api_version"] or None,
            declared_capacity=int(raw["declared_capacity"]),
            labels=json.loads(raw["labels"]),
            lifecycle=lifecycle,
            inflight=int(raw["inflight"]),
            model_ready=raw["model_ready"] == "1",
            last_heartbeat_at=datetime.fromisoformat(raw["last_heartbeat_at"]),
        )
