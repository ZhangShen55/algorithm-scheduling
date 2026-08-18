from __future__ import annotations

import json
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

_REGISTER_SCRIPT = """
local previous_capabilities = redis.call('HGET', KEYS[1], 'capabilities')
local existing_lifecycle = redis.call('HGET', KEYS[1], 'lifecycle')
local registration_lifecycle = ARGV[11]
if existing_lifecycle == 'DRAINING' then
    registration_lifecycle = existing_lifecycle
end
if previous_capabilities then
    for _, capability in ipairs(cjson.decode(previous_capabilities)) do
        redis.call('SREM', ARGV[1] .. capability, ARGV[2])
    end
end

local existing_leases = redis.call('ZRANGE', KEYS[4], 0, -1)
for _, lease_id in ipairs(existing_leases) do
    redis.call('DEL', ARGV[3] .. lease_id)
end
redis.call('DEL', KEYS[4])

redis.call('HSET', KEYS[1],
    'operator_code', ARGV[4],
    'capabilities', ARGV[5],
    'service_url', ARGV[6],
    'model_version', ARGV[7],
    'api_version', ARGV[8],
    'declared_capacity', ARGV[9],
    'labels', ARGV[10],
    'lifecycle', registration_lifecycle,
    'inflight', ARGV[12],
    'model_ready', ARGV[13],
    'last_heartbeat_at', ARGV[14])
redis.call('SADD', KEYS[3], ARGV[2])
for _, capability in ipairs(cjson.decode(ARGV[5])) do
    redis.call('SADD', ARGV[1] .. capability, ARGV[2])
end
redis.call('DEL', KEYS[2])
return 1
"""


_HEARTBEAT_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 0 then
    return 0
end
redis.call('HSET', KEYS[1],
    'inflight', ARGV[1],
    'model_ready', ARGV[2],
    'last_heartbeat_at', ARGV[3])
redis.call('SET', KEYS[2], '1', 'EX', ARGV[4])
return 1
"""


_UNREGISTER_SCRIPT = """
local capabilities = redis.call('HGET', KEYS[1], 'capabilities')
if not capabilities then
    return 0
end
for _, capability in ipairs(cjson.decode(capabilities)) do
    redis.call('SREM', ARGV[1] .. capability, ARGV[2])
end
local leases = redis.call('ZRANGE', KEYS[4], 0, -1)
for _, lease_id in ipairs(leases) do
    redis.call('DEL', ARGV[3] .. lease_id)
end
redis.call('DEL', KEYS[4])
redis.call('SREM', KEYS[3], ARGV[2])
redis.call('DEL', KEYS[1], KEYS[2])
return 1
"""


_SET_LIFECYCLE_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 0 then
    return 0
end
redis.call('HSET', KEYS[1], 'lifecycle', ARGV[1])
return 1
"""


_LEASE_SCRIPT = """
local redis_time = redis.call('TIME')
local now_ms = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
local instance_ids = redis.call('SMEMBERS', KEYS[1])
table.sort(instance_ids)
for _, instance_id in ipairs(instance_ids) do
    local instance_key = ARGV[3] .. instance_id
    local heartbeat_key = ARGV[4] .. instance_id
    if redis.call('EXISTS', heartbeat_key) == 1
        and redis.call('HGET', instance_key, 'lifecycle') == 'ONLINE'
        and redis.call('HGET', instance_key, 'model_ready') == '1' then
        local leases_key = ARGV[5] .. instance_id
        redis.call('ZREMRANGEBYSCORE', leases_key, '-inf', now_ms)
        local active_leases = redis.call('ZCARD', leases_key)
        local reported_inflight = tonumber(redis.call('HGET', instance_key, 'inflight') or '0')
        local used = math.max(active_leases, reported_inflight)
        local capacity = tonumber(redis.call('HGET', instance_key, 'declared_capacity') or '0')
        if used < capacity then
            local expires_at = now_ms + tonumber(ARGV[1])
            redis.call('ZADD', leases_key, expires_at, ARGV[2])
            local lease_key = ARGV[6] .. ARGV[2]
            redis.call('HSET', lease_key,
                'instance_id', instance_id,
                'capability', ARGV[7],
                'service_url', redis.call('HGET', instance_key, 'service_url'),
                'expires_at', expires_at)
            redis.call('PEXPIRE', lease_key, ARGV[1])
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
local redis_time = redis.call('TIME')
local now_ms = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
local leases_key = ARGV[2] .. instance_id
local current_expiry = redis.call('ZSCORE', leases_key, ARGV[3])
local instance_key = ARGV[4] .. instance_id
local heartbeat_key = ARGV[5] .. instance_id
if not current_expiry
    or tonumber(current_expiry) <= now_ms
    or redis.call('EXISTS', instance_key) == 0
    or redis.call('EXISTS', heartbeat_key) == 0
    or redis.call('HGET', instance_key, 'lifecycle') == 'OFFLINE' then
    redis.call('ZREM', leases_key, ARGV[3])
    redis.call('DEL', KEYS[1])
    return {}
end
local capability = redis.call('HGET', KEYS[1], 'capability')
local service_url = redis.call('HGET', KEYS[1], 'service_url')
local expires_at = now_ms + tonumber(ARGV[1])
redis.call('ZADD', leases_key, expires_at, ARGV[3])
redis.call('HSET', KEYS[1], 'expires_at', expires_at)
redis.call('PEXPIRE', KEYS[1], ARGV[1])
return {instance_id, capability, service_url, expires_at}
"""


_ACTIVE_LEASE_COUNT_SCRIPT = """
local redis_time = redis.call('TIME')
local now_ms = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms)
return redis.call('ZCARD', KEYS[1])
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
        heartbeat_at = datetime.now(UTC)
        self._client.eval(
            _REGISTER_SCRIPT,
            4,
            instance_key,
            self._heartbeat_key(instance.instance_id),
            self._instances_key,
            f"{self._prefix}leases:{instance.instance_id}",
            f"{self._prefix}capability:",
            instance.instance_id,
            f"{self._prefix}lease:",
            instance.operator_code.value,
            json.dumps(instance.capabilities, separators=(",", ":")),
            instance.service_url,
            instance.model_version or "",
            instance.api_version or "",
            str(instance.declared_capacity),
            json.dumps(instance.labels, separators=(",", ":")),
            instance.lifecycle.value,
            str(instance.inflight),
            "1" if instance.model_ready else "0",
            heartbeat_at.isoformat(),
        )
        return self._get_instance(instance.instance_id)

    def heartbeat(
        self,
        instance_id: str,
        *,
        inflight: int,
        model_ready: bool,
    ) -> OperatorInstance:
        instance_key = self._instance_key(instance_id)
        heartbeat_at = datetime.now(UTC)
        updated = self._client.eval(
            _HEARTBEAT_SCRIPT,
            2,
            instance_key,
            self._heartbeat_key(instance_id),
            str(inflight),
            "1" if model_ready else "0",
            heartbeat_at.isoformat(),
            str(self._heartbeat_ttl_seconds),
        )
        if not updated:
            raise OperatorInstanceNotFoundError(instance_id)
        return self._get_instance(instance_id)

    def unregister(self, instance_id: str) -> None:
        instance_key = self._instance_key(instance_id)
        removed = self._client.eval(
            _UNREGISTER_SCRIPT,
            4,
            instance_key,
            self._heartbeat_key(instance_id),
            self._instances_key,
            f"{self._prefix}leases:{instance_id}",
            f"{self._prefix}capability:",
            instance_id,
            f"{self._prefix}lease:",
        )
        if not removed:
            raise OperatorInstanceNotFoundError(instance_id)

    def list_instances(self) -> list[OperatorInstance]:
        instance_ids = sorted(cast(set[str], self._client.smembers(self._instances_key)))
        instances: list[OperatorInstance] = []
        for instance_id in instance_ids:
            try:
                instances.append(self._get_instance(instance_id))
            except OperatorInstanceNotFoundError:
                # Unregistration may remove the hash after the membership snapshot.
                continue
        return instances

    def set_lifecycle(
        self,
        instance_id: str,
        lifecycle: OperatorLifecycle,
    ) -> OperatorInstance:
        instance_key = self._instance_key(instance_id)
        updated = self._client.eval(
            _SET_LIFECYCLE_SCRIPT,
            1,
            instance_key,
            lifecycle.value,
        )
        if not updated:
            raise OperatorInstanceNotFoundError(instance_id)
        return self._get_instance(instance_id)

    def lease(self, capability: str, ttl_seconds: int) -> CapacityLease:
        if ttl_seconds <= 0:
            raise ValueError("租约 TTL 必须大于 0")
        lease_id = str(uuid4())
        ttl_ms = ttl_seconds * 1000
        result = cast(
            list[Any],
            self._client.eval(
                _LEASE_SCRIPT,
                1,
                self._capability_key(capability),
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
        released = self._client.eval(
            _RELEASE_SCRIPT,
            1,
            f"{self._prefix}lease:{lease_id}",
            f"{self._prefix}leases:",
            lease_id,
        )
        if not released:
            raise CapacityLeaseNotFoundError(lease_id)

    def active_lease_count(self, instance_id: str) -> int:
        return cast(
            int,
            self._client.eval(
                _ACTIVE_LEASE_COUNT_SCRIPT,
                1,
                f"{self._prefix}leases:{instance_id}",
            ),
        )

    def renew(self, lease_id: str, ttl_seconds: int) -> CapacityLease:
        if ttl_seconds <= 0:
            raise ValueError("租约 TTL 必须大于 0")
        ttl_ms = ttl_seconds * 1000
        result = cast(
            list[Any],
            self._client.eval(
                _RENEW_SCRIPT,
                1,
                f"{self._prefix}lease:{lease_id}",
                str(ttl_ms),
                f"{self._prefix}leases:",
                lease_id,
                f"{self._prefix}instance:",
                f"{self._prefix}heartbeat:",
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
