from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from redis import Redis

from packages.platform_common.operator_registry import (
    ActiveCapacityLease,
    CapacityLease,
    CapacityLeaseContextConflictError,
    CapacityLeaseNotFoundError,
    CapacityUnavailableError,
    LeaseContextStatus,
    OperatorActiveLeases,
    OperatorCode,
    OperatorInstance,
    OperatorInstanceNotFoundError,
    OperatorLifecycle,
    WorkContext,
    validate_positive_int,
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
    'last_heartbeat_at', ARGV[14],
    'capacity_pools', ARGV[15],
    'inflight_by_pool', ARGV[16])
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
    'last_heartbeat_at', ARGV[3],
    'inflight_by_pool', ARGV[4])
redis.call('SET', KEYS[2], '1', 'EX', ARGV[5])
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
local server_info = redis.call('INFO', 'server')
local redis_run_id = string.match(server_info, 'run_id:([%w]+)')
if not redis_run_id then
    return redis.error_reply('Redis run_id unavailable')
end
local redis_time = redis.call('TIME')
local now_ms = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
local instance_ids = redis.call('SMEMBERS', KEYS[1])
local allowed_operator_codes = {}
for _, operator_code in ipairs(cjson.decode(ARGV[9])) do
    allowed_operator_codes[operator_code] = true
end
local requested_pool = ARGV[10]
table.sort(instance_ids)
local lowest_load_candidates = {}
local lowest_effective_inflight = nil
local lowest_declared_capacity = nil
for _, instance_id in ipairs(instance_ids) do
    local instance_key = ARGV[3] .. instance_id
    local heartbeat_key = ARGV[4] .. instance_id
    if allowed_operator_codes[redis.call('HGET', instance_key, 'operator_code')]
        and redis.call('EXISTS', heartbeat_key) == 1
        and redis.call('HGET', instance_key, 'lifecycle') == 'ONLINE'
        and redis.call('HGET', instance_key, 'model_ready') == '1' then
        local leases_key = ARGV[5] .. instance_id
        local expired_lease_ids = redis.call('ZRANGEBYSCORE', leases_key, '-inf', now_ms)
        for _, expired_lease_id in ipairs(expired_lease_ids) do
            redis.call('DEL', ARGV[6] .. expired_lease_id)
        end
        redis.call('ZREMRANGEBYSCORE', leases_key, '-inf', now_ms)
        local lease_ids = redis.call('ZRANGE', leases_key, 0, -1)
        for _, existing_lease_id in ipairs(lease_ids) do
            local existing_lease_key = ARGV[6] .. existing_lease_id
            if redis.call('HGET', existing_lease_key, 'redis_run_id') ~= redis_run_id then
                redis.call('ZREM', leases_key, existing_lease_id)
                redis.call('DEL', existing_lease_key)
            end
        end
        local declared_capacity = tonumber(redis.call('HGET', instance_key, 'declared_capacity') or '0')
        local declared_capacity_valid = declared_capacity
            and declared_capacity > 0
            and declared_capacity == math.floor(declared_capacity)
        local capacities = cjson.decode(redis.call('HGET', instance_key, 'capacity_pools') or '{}')
        local capacity = tonumber(capacities[requested_pool] or capacities['default'] or declared_capacity or '0')
        local active_leases = 0
        for _, existing_lease_id in ipairs(redis.call('ZRANGE', leases_key, 0, -1)) do
            local existing_pool = redis.call('HGET', ARGV[6] .. existing_lease_id, 'capacity_pool') or 'default'
            if existing_pool == requested_pool then
                active_leases = active_leases + 1
            end
        end
        local inflight_by_pool = cjson.decode(redis.call('HGET', instance_key, 'inflight_by_pool') or '{}')
        local reported_inflight = tonumber(inflight_by_pool[requested_pool] or (requested_pool == 'default' and redis.call('HGET', instance_key, 'inflight') or '0'))
        if not reported_inflight or reported_inflight < 0
            or reported_inflight ~= math.floor(reported_inflight) then
            reported_inflight = 0
        end
        local effective_inflight = math.max(active_leases, reported_inflight)
        if declared_capacity_valid
            and capacity and capacity > 0 and capacity == math.floor(capacity)
            and effective_inflight < capacity then
            local candidate = {
                instance_id = instance_id,
                service_url = redis.call('HGET', instance_key, 'service_url'),
                effective_inflight = effective_inflight,
                declared_capacity = capacity
            }
            if not lowest_effective_inflight then
                lowest_load_candidates = {candidate}
                lowest_effective_inflight = effective_inflight
                lowest_declared_capacity = capacity
            else
                -- 交叉相乘比较负载率，避免 Lua 浮点精度影响路由。
                local candidate_ratio = effective_inflight * lowest_declared_capacity
                local lowest_ratio = lowest_effective_inflight * capacity
                if candidate_ratio < lowest_ratio then
                    lowest_load_candidates = {candidate}
                    lowest_effective_inflight = effective_inflight
                    lowest_declared_capacity = capacity
                elseif candidate_ratio == lowest_ratio then
                    table.insert(lowest_load_candidates, candidate)
                end
            end
        end
    end
end
if #lowest_load_candidates == 0 then
    return {}
end

table.sort(lowest_load_candidates, function(left, right)
    return left.instance_id < right.instance_id
end)
local cursor = redis.call('INCR', KEYS[2])
local selected_index = ((cursor - 1) % #lowest_load_candidates) + 1
local selected = lowest_load_candidates[selected_index]
local expires_at = now_ms + tonumber(ARGV[1])
redis.call('ZADD', ARGV[5] .. selected.instance_id, expires_at, ARGV[2])
local lease_key = ARGV[6] .. ARGV[2]
redis.call('HSET', lease_key,
    'instance_id', selected.instance_id,
    'capability', ARGV[7],
    'service_url', selected.service_url,
    'acquired_at', now_ms,
    'expires_at', expires_at,
    'work_context', ARGV[8],
    'capacity_pool', ARGV[10],
    'redis_run_id', redis_run_id)
redis.call('PEXPIRE', lease_key, ARGV[1])
return {
    selected.instance_id,
    selected.service_url,
    now_ms,
    expires_at,
    ARGV[8],
    ARGV[10]
}
"""


_RELEASE_SCRIPT = """
local instance_id = redis.call('HGET', KEYS[1], 'instance_id')
if not instance_id then
    return 0
end
local capability = redis.call('HGET', KEYS[1], 'capability') or ''
local capacity_pool = redis.call('HGET', KEYS[1], 'capacity_pool') or 'default'
local server_info = redis.call('INFO', 'server')
local redis_run_id = string.match(server_info, 'run_id:([%w]+)')
if not redis_run_id then
    return redis.error_reply('Redis run_id unavailable')
end
if redis.call('HGET', KEYS[1], 'redis_run_id') ~= redis_run_id then
    redis.call('ZREM', ARGV[1] .. instance_id, ARGV[2])
    redis.call('DEL', KEYS[1])
    return 0
end
redis.call('ZREM', ARGV[1] .. instance_id, ARGV[2])
redis.call('DEL', KEYS[1])
redis.call('PUBLISH', ARGV[3], cjson.encode({
    instance_id = instance_id,
    capability = capability,
    capacity_pool = capacity_pool
}))
return 1
"""


_RENEW_SCRIPT = """
local instance_id = redis.call('HGET', KEYS[1], 'instance_id')
if not instance_id then
    return {}
end
local server_info = redis.call('INFO', 'server')
local redis_run_id = string.match(server_info, 'run_id:([%w]+)')
if not redis_run_id then
    return redis.error_reply('Redis run_id unavailable')
end
local redis_time = redis.call('TIME')
local now_ms = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
local leases_key = ARGV[2] .. instance_id
local current_expiry = redis.call('ZSCORE', leases_key, ARGV[3])
local instance_key = ARGV[4] .. instance_id
if not current_expiry
    or tonumber(current_expiry) <= now_ms
    or redis.call('EXISTS', instance_key) == 0
    or redis.call('HGET', instance_key, 'lifecycle') == 'OFFLINE' then
    redis.call('ZREM', leases_key, ARGV[3])
    redis.call('DEL', KEYS[1])
    return {}
end
if redis.call('HGET', KEYS[1], 'redis_run_id') ~= redis_run_id then
    redis.call('ZREM', leases_key, ARGV[3])
    redis.call('DEL', KEYS[1])
    return {}
end
local capability = redis.call('HGET', KEYS[1], 'capability')
local capacity_pool = redis.call('HGET', KEYS[1], 'capacity_pool') or 'default'
local service_url = redis.call('HGET', KEYS[1], 'service_url')
local acquired_at = redis.call('HGET', KEYS[1], 'acquired_at')
local work_context = redis.call('HGET', KEYS[1], 'work_context') or ''
local expires_at = now_ms + tonumber(ARGV[1])
redis.call('ZADD', leases_key, expires_at, ARGV[3])
redis.call('HSET', KEYS[1], 'expires_at', expires_at)
redis.call('PEXPIRE', KEYS[1], ARGV[1])
return {instance_id, capability, service_url, acquired_at, expires_at, work_context, capacity_pool}
"""


_BIND_LEASE_CONTEXT_SCRIPT = """
local instance_id = redis.call('HGET', KEYS[1], 'instance_id')
if not instance_id then
    return {0}
end
local server_info = redis.call('INFO', 'server')
local redis_run_id = string.match(server_info, 'run_id:([%w]+)')
if not redis_run_id then
    return redis.error_reply('Redis run_id unavailable')
end
local redis_time = redis.call('TIME')
local now_ms = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
local leases_key = ARGV[1] .. instance_id
local current_expiry = redis.call('ZSCORE', leases_key, ARGV[2])
local instance_key = ARGV[3] .. instance_id
local heartbeat_key = ARGV[4] .. instance_id
if not current_expiry
    or tonumber(current_expiry) <= now_ms
    or redis.call('EXISTS', instance_key) == 0
    or redis.call('EXISTS', heartbeat_key) == 0
    or redis.call('HGET', KEYS[1], 'redis_run_id') ~= redis_run_id then
    redis.call('ZREM', leases_key, ARGV[2])
    redis.call('DEL', KEYS[1])
    return {0}
end
local existing_context = redis.call('HGET', KEYS[1], 'work_context') or ''
if existing_context ~= '' and existing_context ~= ARGV[5] then
    return {-1}
end
if existing_context == '' then
    redis.call('HSET', KEYS[1], 'work_context', ARGV[5])
end
return {
    1,
    instance_id,
    redis.call('HGET', KEYS[1], 'capability'),
    redis.call('HGET', KEYS[1], 'service_url'),
    redis.call('HGET', KEYS[1], 'acquired_at'),
    redis.call('HGET', KEYS[1], 'expires_at'),
    ARGV[5]
}
"""


_ACTIVE_LEASE_COUNT_SCRIPT = """
local server_info = redis.call('INFO', 'server')
local redis_run_id = string.match(server_info, 'run_id:([%w]+)')
if not redis_run_id then
    return redis.error_reply('Redis run_id unavailable')
end
local redis_time = redis.call('TIME')
local now_ms = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
local expired_lease_ids = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', now_ms)
for _, expired_lease_id in ipairs(expired_lease_ids) do
    redis.call('DEL', ARGV[1] .. expired_lease_id)
end
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms)
local lease_ids = redis.call('ZRANGE', KEYS[1], 0, -1)
for _, lease_id in ipairs(lease_ids) do
    local lease_key = ARGV[1] .. lease_id
    if redis.call('HGET', lease_key, 'redis_run_id') ~= redis_run_id then
        redis.call('ZREM', KEYS[1], lease_id)
        redis.call('DEL', lease_key)
    end
end
return redis.call('ZCARD', KEYS[1])
"""


_LIST_ACTIVE_LEASES_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 0 then
    return {}
end
local server_info = redis.call('INFO', 'server')
local redis_run_id = string.match(server_info, 'run_id:([%w]+)')
if not redis_run_id then
    return redis.error_reply('Redis run_id unavailable')
end
local redis_time = redis.call('TIME')
local now_ms = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
local expired_lease_ids = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now_ms)
for _, expired_lease_id in ipairs(expired_lease_ids) do
    redis.call('DEL', ARGV[1] .. expired_lease_id)
end
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now_ms)
local result = {redis.call('HGET', KEYS[1], 'inflight') or '0'}
local lease_ids = redis.call('ZRANGE', KEYS[2], 0, -1)
for _, lease_id in ipairs(lease_ids) do
    local lease_key = ARGV[1] .. lease_id
    if redis.call('HGET', lease_key, 'redis_run_id') ~= redis_run_id then
        redis.call('ZREM', KEYS[2], lease_id)
        redis.call('DEL', lease_key)
    else
        table.insert(result, cjson.encode({
            lease_id = lease_id,
            instance_id = redis.call('HGET', lease_key, 'instance_id'),
            capability = redis.call('HGET', lease_key, 'capability'),
            service_url = redis.call('HGET', lease_key, 'service_url'),
            acquired_at = tonumber(redis.call('HGET', lease_key, 'acquired_at')),
            expires_at = tonumber(redis.call('HGET', lease_key, 'expires_at')),
            work_context = redis.call('HGET', lease_key, 'work_context') or '',
            capacity_pool = redis.call('HGET', lease_key, 'capacity_pool') or 'default'
        }))
    end
end
return result
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
        validate_positive_int(
            instance.declared_capacity,
            field_name="算子声明容量",
        )
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
            json.dumps(instance.capacity_pools, separators=(",", ":")),
            json.dumps(instance.inflight_by_pool, separators=(",", ":")),
        )
        return self._get_instance(instance.instance_id)

    def heartbeat(
        self,
        instance_id: str,
        *,
        inflight: int,
        model_ready: bool,
        inflight_by_pool: dict[str, int] | None = None,
    ) -> OperatorInstance:
        if type(inflight) is not int or inflight < 0:
            raise ValueError("算子在途数必须是非负整数")
        for pool, value in (inflight_by_pool or {}).items():
            if not isinstance(pool, str) or not pool.strip() or type(value) is not int or value < 0:
                raise ValueError("容量池在途数必须是非负整数")
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
            json.dumps(inflight_by_pool or {}, separators=(",", ":")),
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

    def lease(
        self,
        capability: str,
        ttl_seconds: int,
        work_context: WorkContext | None = None,
        capacity_pool: str = "default",
    ) -> CapacityLease:
        if ttl_seconds <= 0:
            raise ValueError("租约 TTL 必须大于 0")
        lease_id = str(uuid4())
        ttl_ms = ttl_seconds * 1000
        result = cast(
            list[Any],
            self._client.eval(
                _LEASE_SCRIPT,
                2,
                self._capability_key(capability),
                f"{self._prefix}routing-cursor:{capability}",
                str(ttl_ms),
                lease_id,
                f"{self._prefix}instance:",
                f"{self._prefix}heartbeat:",
                f"{self._prefix}leases:",
                f"{self._prefix}lease:",
                capability,
                (
                    json.dumps(work_context.as_dict(), separators=(",", ":"))
                    if work_context is not None
                    else ""
                ),
                json.dumps([operator_code.value for operator_code in OperatorCode]),
                capacity_pool,
            ),
        )
        if not result:
            raise CapacityUnavailableError(capability)
        return self._capacity_lease_from_values(
            lease_id=lease_id,
            instance_id=str(result[0]),
            capability=capability,
            service_url=str(result[1]),
            acquired_at_ms=int(result[2]),
            expires_at_ms=int(result[3]),
            work_context_json=str(result[4]),
            capacity_pool=str(result[5]) if len(result) > 5 else capacity_pool,
        )

    def bind_lease_context(
        self,
        lease_id: str,
        work_context: WorkContext,
    ) -> CapacityLease:
        context_json = json.dumps(work_context.as_dict(), separators=(",", ":"))
        result = cast(
            list[Any],
            self._client.eval(
                _BIND_LEASE_CONTEXT_SCRIPT,
                1,
                f"{self._prefix}lease:{lease_id}",
                f"{self._prefix}leases:",
                lease_id,
                f"{self._prefix}instance:",
                f"{self._prefix}heartbeat:",
                context_json,
            ),
        )
        if not result or int(result[0]) == 0:
            raise CapacityLeaseNotFoundError(lease_id)
        if int(result[0]) == -1:
            raise CapacityLeaseContextConflictError(lease_id)
        return self._capacity_lease_from_values(
            lease_id=lease_id,
            instance_id=str(result[1]),
            capability=str(result[2]),
            service_url=str(result[3]),
            acquired_at_ms=int(result[4]),
            expires_at_ms=int(result[5]),
            work_context_json=str(result[6]),
            capacity_pool=work_context.capacity_pool,
        )

    def release(self, lease_id: str) -> None:
        released = self._client.eval(
            _RELEASE_SCRIPT,
            1,
            f"{self._prefix}lease:{lease_id}",
            f"{self._prefix}leases:",
            lease_id,
            f"{self._prefix}capacity-released",
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
                f"{self._prefix}lease:",
            ),
        )

    def list_active_leases(self, instance_id: str) -> OperatorActiveLeases:
        result = cast(
            list[Any],
            self._client.eval(
                _LIST_ACTIVE_LEASES_SCRIPT,
                2,
                self._instance_key(instance_id),
                f"{self._prefix}leases:{instance_id}",
                f"{self._prefix}lease:",
            ),
        )
        if not result:
            raise OperatorInstanceNotFoundError(instance_id)
        reported_inflight = int(result[0])
        leases: list[ActiveCapacityLease] = []
        for encoded in result[1:]:
            raw = cast(dict[str, Any], json.loads(str(encoded)))
            context_json = str(raw.get("work_context") or "")
            work_context = self._work_context_from_json(context_json)
            leases.append(
                ActiveCapacityLease(
                    lease_id=str(raw["lease_id"]),
                    instance_id=str(raw["instance_id"]),
                    capability=str(raw["capability"]),
                    service_url=str(raw["service_url"]),
                    acquired_at=datetime.fromtimestamp(
                        int(raw["acquired_at"]) / 1000,
                        tz=UTC,
                    ),
                    expires_at=datetime.fromtimestamp(
                        int(raw["expires_at"]) / 1000,
                        tz=UTC,
                    ),
                    context_status=(
                        LeaseContextStatus.BOUND
                        if work_context is not None
                        else LeaseContextStatus.UNBOUND
                    ),
                    work_context=work_context,
                    capacity_pool=str(raw.get("capacity_pool") or "default"),
                )
            )
        active_count = len(leases)
        return OperatorActiveLeases(
            instance_id=instance_id,
            active_lease_count=active_count,
            reported_inflight=reported_inflight,
            attribution_difference=reported_inflight - active_count,
            leases=tuple(leases),
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
            ),
        )
        if not result:
            raise CapacityLeaseNotFoundError(lease_id)
        return self._capacity_lease_from_values(
            lease_id=lease_id,
            instance_id=str(result[0]),
            capability=str(result[1]),
            service_url=str(result[2]),
            acquired_at_ms=int(result[3]),
            expires_at_ms=int(result[4]),
            work_context_json=str(result[5]),
            capacity_pool=str(result[6]) if len(result) > 6 else "default",
        )

    @staticmethod
    def _work_context_from_json(value: str) -> WorkContext | None:
        if not value:
            return None
        raw = cast(dict[str, Any], json.loads(value))
        return WorkContext(**raw)

    @classmethod
    def _capacity_lease_from_values(
        cls,
        *,
        lease_id: str,
        instance_id: str,
        capability: str,
        service_url: str,
        acquired_at_ms: int,
        expires_at_ms: int,
        work_context_json: str,
        capacity_pool: str = "default",
    ) -> CapacityLease:
        return CapacityLease(
            lease_id=lease_id,
            instance_id=instance_id,
            capability=capability,
            service_url=service_url,
            acquired_at=datetime.fromtimestamp(acquired_at_ms / 1000, tz=UTC),
            expires_at=datetime.fromtimestamp(expires_at_ms / 1000, tz=UTC),
            work_context=cls._work_context_from_json(work_context_json),
            capacity_pool=capacity_pool,
        )

    def _get_instance(self, instance_id: str) -> OperatorInstance:
        raw = cast(dict[str, str], self._client.hgetall(self._instance_key(instance_id)))
        if not raw:
            raise OperatorInstanceNotFoundError(instance_id)
        lifecycle = OperatorLifecycle(raw["lifecycle"])
        if not self._client.exists(self._heartbeat_key(instance_id)):
            lifecycle = OperatorLifecycle.OFFLINE
        try:
            operator_code = OperatorCode(raw["operator_code"])
        except ValueError as exc:
            raise OperatorInstanceNotFoundError(instance_id) from exc
        return OperatorInstance(
            instance_id=instance_id,
            operator_code=operator_code,
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
            capacity_pools=json.loads(raw.get("capacity_pools") or "{}"),
            inflight_by_pool=json.loads(raw.get("inflight_by_pool") or "{}"),
        )
