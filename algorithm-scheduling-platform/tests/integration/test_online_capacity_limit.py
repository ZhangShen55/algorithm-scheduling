from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from control_service.app.api.control import create_control_app
from fastapi import FastAPI
from redis import Redis

from packages.platform_common.operator_registry import OperatorCode, OperatorInstance
from packages.platform_common.redis_operator_registry import RedisOperatorRegistry

pytestmark = pytest.mark.integration
TEST_REDIS_URL = "redis://127.0.0.1:6379/15"


@asynccontextmanager
async def _control_with_one_vbas() -> AsyncIterator[tuple[FastAPI, Redis]]:
    redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    prefix = f"algorithm-platform:test:online-limit:{uuid4().hex}:"
    try:
        redis.ping()
    except Exception as exc:
        redis.close()
        pytest.skip(f"Redis 集成测试环境不可用: {exc}")
    registry = RedisOperatorRegistry(redis, heartbeat_ttl_seconds=30, key_prefix=prefix)
    registry.register(
        OperatorInstance(
            instance_id="vbas-gpu0",
            operator_code=OperatorCode.VBAS,
            capabilities=["person_count", "student_behavior", "teacher_behavior"],
            service_url="http://vbas-gpu0:8981",
            declared_capacity=24,
            capacity_pools={"offline": 1, "online": 24},
        )
    )
    registry.heartbeat(
        "vbas-gpu0",
        inflight=0,
        model_ready=True,
        inflight_by_pool={"offline": 0, "online": 0},
    )
    try:
        yield create_control_app(repository=object(), operator_registry=registry), redis
    finally:
        keys = list(redis.scan_iter(match=f"{prefix}*", count=100))
        if keys:
            redis.delete(*keys)
        redis.close()


@pytest.mark.asyncio
async def test_control_issues_only_24_online_leases_for_24_plus_24_vbas() -> None:
    async with _control_with_one_vbas() as (app, _redis):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://control.test",
        ) as client:
            lease_ids: list[str] = []
            for _ in range(24):
                response = await client.post(
                    "/internal/operator-instances/lease",
                    json={
                        "capability": "person_count",
                        "capacity_pool": "online",
                        "ttl_seconds": 60,
                    },
                )
                assert response.status_code == 200
                lease_ids.append(response.json()["lease_id"])

            saturated = await client.post(
                "/internal/operator-instances/lease",
                json={
                    "capability": "person_count",
                    "capacity_pool": "online",
                    "ttl_seconds": 60,
                },
            )
            assert saturated.status_code == 503
            assert "暂无可用算子容量" in saturated.json()["detail"]

            for lease_id in lease_ids:
                released = await client.post(
                    "/internal/operator-instances/release",
                    json={"lease_id": lease_id},
                )
                assert released.status_code == 200
