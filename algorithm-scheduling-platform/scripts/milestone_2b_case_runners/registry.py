from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import psycopg
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from deploy.scripts.verify_operator_registration import validate_instances
from packages.operator_registry_client.client import (
    OperatorRegistryClient,
    OperatorRegistryClientConfig,
    OperatorRuntimeStatus,
)
from packages.platform_common.operator_audit_repository import (
    OperatorAuditRepository,
    OperatorInstanceEvent,
)
from packages.platform_common.operator_operations import (
    build_operator_capacity_snapshot,
)
from packages.platform_common.operator_registry import (
    CapacityLeaseNotFoundError,
    CapacityUnavailableError,
    OperatorCode,
    OperatorInstance,
    OperatorLifecycle,
)
from packages.platform_common.redis_operator_registry import RedisOperatorRegistry
from packages.platform_contracts.status import NodeStatus
from scripts.milestone_2b_case_catalog import CaseDefinition

from .base import CaseContext, CaseOutcome
from .deployment import (
    FoundationCaseSpec,
    _spec,
    run_foundation_case,
    run_foundation_cleanup,
)
from .infrastructure import _isolated_database
from .safety import ResourceSpec


def _import_workspace_module(name: str) -> Any:
    workspace_root = str(Path(__file__).resolve().parents[3])
    inserted = workspace_root not in sys.path
    if inserted:
        sys.path.insert(0, workspace_root)
    try:
        return importlib.import_module(name)
    finally:
        if inserted:
            sys.path.remove(workspace_root)


def _registry_spec(title: str, expected: str) -> FoundationCaseSpec:
    return _spec(
        title,
        expected,
        safety="isolated_mutation",
        timeout_seconds=120,
        mode="canonical_runtime",
    )


CASE_SPECS: Mapping[str, FoundationCaseSpec] = {
    "REG-001": _registry_spec("只注册但尚未首次心跳", "实例不可路由"),
    "REG-002": _registry_spec("心跳 model_ready=false", "实例不可获得新租约"),
    "REG-003": _registry_spec("心跳超过 TTL", "实例自动离线"),
    "REG-004": _registry_spec("心跳 inflight 为负数", "control 拒绝请求"),
    "REG-005": _registry_spec("注册容量为 0", "control 拒绝注册"),
    "REG-006": _registry_spec("注册未知 operator_code", "control 拒绝注册"),
    "REG-007": _registry_spec("注册能力与算子编码不匹配", "部署契约检查失败"),
    "REG-008": _registry_spec("service URL 不是 HTTP 或 HTTPS", "control 拒绝注册"),
    "REG-009": _registry_spec("service URL 指向错误容器", "实例健康或真实调用失败，不算可用"),
    "REG-010": _registry_spec("租约达到声明容量", "后续请求不得超发给该实例"),
    "REG-011": _registry_spec("释放不存在的租约", "返回明确错误，不影响其他租约"),
    "REG-012": _registry_spec("续约不存在或已过期租约", "返回明确错误，不重建租约"),
    "REG-013": _registry_spec("实例进入 DRAINING", "拒绝新租约，已有请求允许结束"),
    "REG-014": _registry_spec("DRAINING 实例重启", "不得未经控制恢复为可路由状态"),
    "REG-015": _registry_spec("实例注销时仍有租约", "租约按规则失效且留下审计事实"),
    "REG-016": _registry_spec("同能力三个实例中一个离线", "请求只分发给剩余实例"),
    "REG-017": _registry_spec("同能力全部实例离线", "离线节点进入状态 30，在线返回容量错误"),
    "REG-018": _registry_spec(
        "control-service 暂时不可用",
        "算子继续运行但注册和心跳重试，不能伪报成功",
    ),
    "REG-019": _registry_spec("心跳上报 inflight 与租约数持续矛盾", "运维证据标记异常"),
    "REG-020": _registry_spec(
        "调用方停止续租后 TTL 回收",
        "旧租约失效并释放容量，心跳差异只用于观测",
    ),
}
_POSTGRES_AUDIT_CASES = frozenset({"REG-014", "REG-015"})
_DEFAULT_OPERATOR_REGISTRY_TOKEN = "local-development-registry-token"


def _operator_registry_token() -> str:
    return os.getenv(
        "OPERATOR_REGISTRY_TOKEN",
        _DEFAULT_OPERATOR_REGISTRY_TOKEN,
    )


def _redis_prefix(context: CaseContext, case: CaseDefinition) -> str:
    return f"m2b:{context.run_id}:{case.case_id.lower()}:registry:"


def _instance_id(context: CaseContext, case: CaseDefinition) -> str:
    return (
        f"m2b-{len(context.run_id)}-{context.run_id}-"
        f"{case.case_id.lower()}-instance"
    )


def _registry_database_name(context: CaseContext, case: CaseDefinition) -> str:
    safe_run = context.run_id.replace("-", "_")
    safe_case = case.case_id.lower().replace("-", "_")
    return f"m2b_{len(context.run_id)}_{safe_run}_{safe_case}_test"


def _registry_scenario(
    context: CaseContext, case: CaseDefinition
) -> dict[str, Any]:
    scenario = {
        "mutation": {"case": case.case_id},
        "control_url": "http://127.0.0.1:18100",
        "redis_prefix": _redis_prefix(context, case),
        "instance_id": _instance_id(context, case),
        "registration_checker": "deploy/scripts/verify_operator_registration.py",
    }
    if case.case_id in _POSTGRES_AUDIT_CASES:
        scenario["database"] = _registry_database_name(context, case)
    return scenario


def _registry_resources(
    context: CaseContext, case: CaseDefinition
) -> tuple[ResourceSpec, ...]:
    resources = [ResourceSpec("redis_prefix", _redis_prefix(context, case))]
    if case.case_id in _POSTGRES_AUDIT_CASES:
        resources.append(
            ResourceSpec("database", _registry_database_name(context, case))
        )
    return tuple(resources)


async def _run(
    context: CaseContext, case: CaseDefinition, case_id: str
) -> CaseOutcome:
    return await run_foundation_case(
        context=context,
        case=case,
        case_id=case_id,
        group="registry",
        spec=CASE_SPECS[case_id],
        scenario_builder=_registry_scenario,
        resource_builder=_registry_resources,
    )


async def reg_001(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-001")


async def reg_002(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-002")


async def reg_003(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-003")


async def reg_004(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-004")


async def reg_005(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-005")


async def reg_006(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-006")


async def reg_007(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-007")


async def reg_008(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-008")


async def reg_009(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-009")


async def reg_010(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-010")


async def reg_011(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-011")


async def reg_012(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-012")


async def reg_013(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-013")


async def reg_014(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-014")


async def reg_015(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-015")


async def reg_016(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-016")


async def reg_017(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-017")


async def reg_018(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-018")


async def reg_019(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-019")


async def reg_020(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "REG-020")


async def cleanup(context: CaseContext, case: CaseDefinition) -> None:
    spec = CASE_SPECS.get(case.case_id)
    if spec is None:
        raise ValueError("registry cleanup case is not registered")
    await run_foundation_cleanup(
        context=context,
        case=case,
        group="registry",
        spec=spec,
    )


for _case_id in CASE_SPECS:
    globals()[_case_id.lower().replace("-", "_")].cleanup = cleanup


def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
) -> tuple[int, Any]:
    body = None
    headers = {
        "Accept": "application/json",
        "X-Operator-Registry-Token": _operator_registry_token(),
    }
    if payload is not None:
        body = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=3.0) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            document = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            document = raw.decode("utf-8", errors="replace")
        return exc.code, document


def _instance(
    instance_id: str,
    *,
    capacity: int = 1,
    service_url: str = "http://127.0.0.1:18981",
) -> OperatorInstance:
    return OperatorInstance(
        instance_id=instance_id,
        operator_code=OperatorCode.VBAS,
        capabilities=["teacher_behavior"],
        service_url=service_url,
        declared_capacity=capacity,
        labels={"gpu": "0"},
        model_ready=False,
    )


def _require_scenario(
    scenario: Mapping[str, Any],
) -> tuple[str, str, str, str | None]:
    control_url = scenario.get("control_url")
    prefix = scenario.get("redis_prefix")
    instance_id = scenario.get("instance_id")
    run_id = scenario.get("run_id")
    case_id = scenario.get("case_id")
    database = scenario.get("database")
    if (
        not isinstance(control_url, str)
        or control_url != "http://127.0.0.1:18100"
        or not isinstance(prefix, str)
        or not isinstance(run_id, str)
        or not isinstance(case_id, str)
        or prefix != f"m2b:{run_id}:{case_id.lower()}:registry:"
        or not isinstance(instance_id, str)
        or not instance_id.startswith(f"m2b-{len(run_id)}-{run_id}-")
    ):
        raise ValueError("注册场景未绑定当前 run 的 control/prefix/instance")
    expected_database = (
        f"m2b_{len(run_id)}_{run_id.replace('-', '_')}_"
        f"{case_id.lower().replace('-', '_')}_test"
    )
    if case_id in _POSTGRES_AUDIT_CASES:
        if database != expected_database:
            raise ValueError("持久审计场景未绑定当前 case 的 _test 数据库")
        return control_url, prefix, instance_id, cast(str, database)
    if database is not None:
        raise ValueError("非审计场景不得声明 PostgreSQL 数据库")
    return control_url, prefix, instance_id, None


def _require_control(control_url: str) -> dict[str, Any]:
    try:
        status, document = _http_json(f"{control_url}/ops/readiness")
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise ValueError(f"control API 不可达：{exc}") from exc
    if status not in {200, 503} or not isinstance(document, dict):
        raise ValueError("control readiness 没有返回结构化状态")
    return document


def _cleanup_prefix(client: Redis, prefix: str) -> None:
    keys = list(client.scan_iter(match=f"{prefix}*", count=100))
    if keys:
        client.delete(*keys)


def _check_reg_001(
    registry: RedisOperatorRegistry, instance_id: str, _: str
) -> dict[str, Any]:
    registered = registry.register(_instance(instance_id))
    try:
        registry.lease("teacher_behavior", 30)
    except CapacityUnavailableError as exc:
        return {
            "registered_instance_id": registered.instance_id,
            "registered_lifecycle": registered.lifecycle.value,
            "registered_model_ready": registered.model_ready,
            "lease_rejection": type(exc).__name__,
        }
    raise ValueError("未首次心跳实例仍获得租约")


def _check_reg_002(
    registry: RedisOperatorRegistry, instance_id: str, _: str
) -> dict[str, Any]:
    registry.register(_instance(instance_id))
    heartbeat = registry.heartbeat(instance_id, inflight=0, model_ready=False)
    try:
        registry.lease("teacher_behavior", 30)
    except CapacityUnavailableError as exc:
        return {
            "model_ready": heartbeat.model_ready,
            "new_lease": None,
            "lease_rejection": type(exc).__name__,
        }
    raise ValueError("model_ready=false 实例仍获得新租约")


def _check_reg_003(
    registry: RedisOperatorRegistry, instance_id: str, _: str
) -> dict[str, Any]:
    registry.register(_instance(instance_id))
    registry.heartbeat(instance_id, inflight=0, model_ready=True)
    started_at = time.monotonic()
    time.sleep(1.1)
    elapsed_seconds = time.monotonic() - started_at
    lifecycle = registry.list_instances()[0].lifecycle
    if lifecycle is not OperatorLifecycle.OFFLINE:
        raise ValueError("心跳 TTL 后实例未自动离线")
    return {"elapsed_seconds": elapsed_seconds, "lifecycle": lifecycle.value}


def _api_rejection(control_url: str, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    status, document = _http_json(f"{control_url}{path}", method="POST", payload=payload)
    rendered = json.dumps(document, ensure_ascii=False)
    if status == 404:
        raise ValueError(f"control stable route is missing: {path}")
    if status not in {400, 409, 422} or not rendered:
        raise ValueError(f"control 未明确拒绝异常输入：HTTP {status}")
    return {"http_status": status, "response": document}


def _registration_payload(instance_id: str) -> dict[str, Any]:
    return {
        "instance_id": instance_id,
        "operator_code": "vbas",
        "capabilities": ["teacher_behavior"],
        "service_url": "http://127.0.0.1:18981",
        "declared_capacity": 1,
        "labels": {"gpu": "0"},
    }


def _check_reg_004(
    registry: RedisOperatorRegistry, instance_id: str, control_url: str
) -> dict[str, Any]:
    del registry
    payload = {"instance_id": instance_id, "inflight": -1, "model_ready": True}
    return _api_rejection(control_url, "/api/operator-instances/heartbeat", payload)


def _check_reg_005(
    registry: RedisOperatorRegistry, instance_id: str, control_url: str
) -> dict[str, Any]:
    del registry
    payload = _registration_payload(instance_id)
    payload["declared_capacity"] = 0
    return _api_rejection(control_url, "/api/operator-instances/register", payload)


def _check_reg_006(
    registry: RedisOperatorRegistry, instance_id: str, control_url: str
) -> dict[str, Any]:
    del registry
    payload = _registration_payload(instance_id)
    payload["operator_code"] = "unknown_operator"
    return _api_rejection(control_url, "/api/operator-instances/register", payload)


def _check_reg_007(
    registry: RedisOperatorRegistry, instance_id: str, _control_url: str
) -> dict[str, Any]:
    del registry
    contract = {
        instance_id: {
            "operator_code": "vbas",
            "capabilities": {"teacher_behavior"},
            "service_url": "http://127.0.0.1:18981",
            "declared_capacity": 1,
            "gpu": "0",
        }
    }
    row = {
        **_registration_payload(instance_id),
        "capabilities": ["asr_offline"],
        "lifecycle": "ONLINE",
        "model_ready": True,
        "inflight": 0,
        "last_heartbeat_at": "2026-08-18T00:00:00+00:00",
    }
    issues, observed = validate_instances([row], contract)
    if f"{instance_id} capability 不匹配" not in issues:
        raise ValueError("注册能力与编码不匹配未被契约 checker 识别")
    return {
        "issues": issues,
        "validator_observed_instance_ids": sorted(observed),
        "validator_observed_count": len(observed),
    }


def _check_reg_008(
    registry: RedisOperatorRegistry, instance_id: str, control_url: str
) -> dict[str, Any]:
    del registry
    payload = _registration_payload(instance_id)
    payload["service_url"] = "ftp://invalid.example/operator"
    return _api_rejection(control_url, "/api/operator-instances/register", payload)


def _check_reg_009(
    registry: RedisOperatorRegistry, instance_id: str, _: str
) -> dict[str, Any]:
    wrong_url = "http://127.0.0.1:18981"
    audited_module = _import_workspace_module(
        "control_service.app.infrastructure.audited_operator_registry"
    )

    health_paths: list[str] = []
    metadata_identity = {
        "instance_id": "ocr-gpu0",
        "operator_code": "ocr",
        "capabilities": ["ocr"],
        "model_version": "ocr-v6",
        "api_version": "v1",
    }

    def healthy_wrong_operator(request: httpx.Request) -> httpx.Response:
        health_paths.append(request.url.path)
        if request.url.path == "/ops/health":
            return httpx.Response(200, json={"status": "alive"})
        if request.url.path == "/ops/metadata":
            return httpx.Response(200, json=metadata_identity)
        raise ValueError("算子健康检查使用了非稳定运维路径")

    with httpx.Client(
        transport=httpx.MockTransport(healthy_wrong_operator)
    ) as http_client:
        audited_registry = audited_module.AuditedOperatorRegistry(
            registry,
            _InMemoryOperatorAudit(),
            heartbeat_audit_interval_seconds=60,
            health_checker=audited_module.HttpOperatorHealthChecker(
                timeout_seconds=0.1,
                trusted_service_urls={instance_id: wrong_url},
                http_client=http_client,
            ),
        )
        audited_registry.register(_instance(instance_id, service_url=wrong_url))
        heartbeat = audited_registry.heartbeat(
            instance_id,
            inflight=0,
            model_ready=True,
        )
        try:
            audited_registry.lease("teacher_behavior", 30)
        except CapacityUnavailableError as exc:
            if heartbeat.model_ready:
                raise ValueError("身份不匹配实例被标记为 model_ready") from None
            return {
                "production_health_gate": "HttpOperatorHealthChecker",
                "health_verified": heartbeat.model_ready,
                "health_paths": health_paths,
                "metadata_identity": metadata_identity,
                "lease_rejection": type(exc).__name__,
            }
    raise ValueError("错误但健康的算子身份不匹配后仍获得租约")


def _ready(
    registry: RedisOperatorRegistry, instance_id: str, capacity: int = 1
) -> OperatorInstance:
    registry.register(_instance(instance_id, capacity=capacity))
    return registry.heartbeat(instance_id, inflight=0, model_ready=True)


def _check_reg_010(
    registry: RedisOperatorRegistry, instance_id: str, _: str
) -> dict[str, Any]:
    ready = _ready(registry, instance_id)
    lease = registry.lease("teacher_behavior", 30)
    try:
        registry.lease("teacher_behavior", 30)
    except CapacityUnavailableError as exc:
        return {
            "capacity": ready.declared_capacity,
            "active_lease": lease.lease_id,
            "second_lease_rejection": type(exc).__name__,
        }
    raise ValueError("声明容量已满仍超发租约")


def _check_reg_011(
    registry: RedisOperatorRegistry, instance_id: str, _: str
) -> dict[str, Any]:
    _ready(registry, instance_id)
    lease = registry.lease("teacher_behavior", 30)
    try:
        registry.release("missing-lease")
    except CapacityLeaseNotFoundError as exc:
        renewed = registry.renew(lease.lease_id, 30)
        if renewed.lease_id != lease.lease_id:
            raise ValueError("释放不存在租约影响了其他租约") from exc
        return {
            "missing_lease_rejection": type(exc).__name__,
            "existing_lease": lease.lease_id,
            "renewed_lease": renewed.lease_id,
        }
    raise ValueError("释放不存在租约没有返回明确错误")


def _check_reg_012(
    registry: RedisOperatorRegistry, instance_id: str, _: str
) -> dict[str, Any]:
    _ready(registry, instance_id)
    try:
        registry.renew("missing-lease", 30)
    except CapacityLeaseNotFoundError as exc:
        return {"renewal_rejection": type(exc).__name__}
    raise ValueError("不存在租约被续约或重建")


def _check_reg_013(
    registry: RedisOperatorRegistry, instance_id: str, _: str
) -> dict[str, Any]:
    _ready(registry, instance_id)
    existing = registry.lease("teacher_behavior", 30)
    registry.set_lifecycle(instance_id, OperatorLifecycle.DRAINING)
    try:
        registry.lease("teacher_behavior", 30)
    except CapacityUnavailableError:
        renewed = registry.renew(existing.lease_id, 30)
        return {"lifecycle": "DRAINING", "new_lease": None, "existing_lease": renewed.lease_id}
    raise ValueError("DRAINING 实例仍获得新租约")


def _check_reg_014(
    registry: RedisOperatorRegistry, instance_id: str, restart_context: str
) -> dict[str, Any]:
    try:
        context = json.loads(restart_context)
    except json.JSONDecodeError as exc:
        raise ValueError("REG-014 restart context 不是 JSON") from exc
    if not isinstance(context, dict) or set(context) != {
        "database",
        "redis_prefix",
    }:
        raise ValueError("REG-014 restart context 字段不完整")
    database = context["database"]
    prefix = context["redis_prefix"]
    if not isinstance(database, str) or not database.endswith("_test"):
        raise ValueError("REG-014 拒绝使用非 _test PostgreSQL 数据库")
    if not isinstance(prefix, str) or not prefix.endswith(":reg-014:registry:"):
        raise ValueError("REG-014 Redis prefix 不属于当前 case")

    with _isolated_database({"database": database}, migrate=True) as engine:
        audited_module = _import_workspace_module(
            "control_service.app.infrastructure.audited_operator_registry"
        )
        first_audit = OperatorAuditRepository(engine)
        first_registry = audited_module.AuditedOperatorRegistry(
            registry,
            first_audit,
            heartbeat_audit_interval_seconds=60,
            health_checker=_AlwaysHealthy(),
        )
        instance = _instance(instance_id)
        first_registry.register(instance)
        first_registry.heartbeat(instance_id, inflight=0, model_ready=True)
        first_registry.set_lifecycle(instance_id, OperatorLifecycle.DRAINING)
        first_registry.unregister(instance_id)

        restart_client = Redis.from_url(
            "redis://127.0.0.1:6379/15", decode_responses=True
        )
        try:
            restart_client.ping()
            _cleanup_prefix(restart_client, prefix)
            restarted_realtime = RedisOperatorRegistry(
                restart_client,
                heartbeat_ttl_seconds=1,
                key_prefix=prefix,
            )
            restarted_audit = OperatorAuditRepository(engine)
            restarted_registry = audited_module.AuditedOperatorRegistry(
                restarted_realtime,
                restarted_audit,
                heartbeat_audit_interval_seconds=60,
                health_checker=_AlwaysHealthy(),
            )
            restarted_registry.register(instance)
            heartbeat = restarted_registry.heartbeat(
                instance_id,
                inflight=0,
                model_ready=True,
            )
            persisted_audit = OperatorAuditRepository(engine)
            desired_lifecycle = persisted_audit.get_desired_lifecycle(instance_id)
            if (
                desired_lifecycle is not OperatorLifecycle.DRAINING
                or heartbeat.lifecycle is not desired_lifecycle
            ):
                raise ValueError("DRAINING 意图未从 PostgreSQL 恢复到新 Redis")
            try:
                restarted_registry.lease("teacher_behavior", 30)
            except CapacityUnavailableError as exc:
                lease_rejection = type(exc).__name__
            else:
                raise ValueError("DRAINING 实例在 Redis 丢失后未经控制获得租约")

            restarted_registry.set_lifecycle(
                instance_id, OperatorLifecycle.ONLINE
            )
            lease = restarted_registry.lease("teacher_behavior", 30)
            return {
                "production_registry": type(restarted_registry).__name__,
                "audit_repository": type(persisted_audit).__name__,
                "audit_database": database,
                "redis_prefix_deleted_before_restart": prefix,
                "desired_lifecycle_after_reregistration": desired_lifecycle.value,
                "lease_rejection": lease_rejection,
                "online_lease_instance_id": lease.instance_id,
            }
        finally:
            restart_client.close()


def _check_reg_015(
    registry: RedisOperatorRegistry, instance_id: str, database: str
) -> dict[str, Any]:
    if not database.endswith("_test"):
        raise ValueError("REG-015 拒绝使用非 _test PostgreSQL 数据库")
    with _isolated_database({"database": database}, migrate=True) as engine:
        audit = OperatorAuditRepository(engine)
        audited_module = _import_workspace_module(
            "control_service.app.infrastructure.audited_operator_registry"
        )
        audited_registry = audited_module.AuditedOperatorRegistry(
            registry,
            audit,
            heartbeat_audit_interval_seconds=60,
            health_checker=_AlwaysHealthy(),
        )
        audited_registry.register(_instance(instance_id))
        audited_registry.heartbeat(instance_id, inflight=0, model_ready=True)
        lease = audited_registry.lease("teacher_behavior", 30)
        audited_registry.unregister(instance_id)
        persisted_audit = OperatorAuditRepository(engine)
        events = persisted_audit.list_events(instance_id)
        try:
            audited_registry.renew(lease.lease_id, 30)
        except CapacityLeaseNotFoundError as exc:
            unregistered = next(
                (event for event in events if event.event_type == "UNREGISTERED"),
                None,
            )
            if unregistered is None:
                raise ValueError("注销实例没有留下 UNREGISTERED 审计事实") from None
            return {
                "production_registry": type(audited_registry).__name__,
                "audit_repository": type(persisted_audit).__name__,
                "audit_database": database,
                "audit_event_count_after_unregister": len(events),
                "lease_renewal_rejection": type(exc).__name__,
                "audit_event_type": unregistered.event_type,
                "audit_source": unregistered.event_payload["source"],
            }
        raise ValueError("注销实例的旧租约仍可续约")


class _InMemoryOperatorAudit:
    def __init__(self) -> None:
        self._events: list[OperatorInstanceEvent] = []
        self._desired_lifecycles: dict[str, OperatorLifecycle] = {}

    def record_registration(self, instance: OperatorInstance) -> None:
        self._desired_lifecycles.setdefault(instance.instance_id, instance.lifecycle)

    def get_desired_lifecycle(self, instance_id: str) -> OperatorLifecycle:
        return self._desired_lifecycles[instance_id]

    def record_heartbeat_summary(
        self,
        instance_id: str,
        *,
        inflight: int,
        model_ready: bool,
        min_interval_seconds: float,
    ) -> bool:
        del instance_id, inflight, model_ready, min_interval_seconds
        return False

    def record_lifecycle(
        self,
        instance_id: str,
        lifecycle: OperatorLifecycle,
        *,
        source: str,
        reason: str | None = None,
    ) -> bool:
        del source, reason
        previous = self._desired_lifecycles.get(instance_id)
        self._desired_lifecycles[instance_id] = lifecycle
        return previous is not lifecycle

    def record_unregistration(self, instance_id: str, *, source: str) -> bool:
        self._events.append(
            OperatorInstanceEvent(
                id=len(self._events) + 1,
                instance_id=instance_id,
                event_type="UNREGISTERED",
                event_payload={
                    "lifecycle": OperatorLifecycle.OFFLINE.value,
                    "source": source,
                },
                occurred_at=datetime.now(UTC),
            )
        )
        return True

    def list_events(
        self,
        instance_id: str,
        *,
        limit: int = 100,
    ) -> list[OperatorInstanceEvent]:
        return [
            event
            for event in reversed(self._events)
            if event.instance_id == instance_id
        ][:limit]


class _AlwaysHealthy:
    def check(self, instance: OperatorInstance) -> bool:
        del instance
        return True


def _check_reg_016(
    registry: RedisOperatorRegistry, instance_id: str, _: str
) -> dict[str, Any]:
    ids = [f"{instance_id}-{index}" for index in range(3)]
    for value in ids:
        _ready(registry, value)
    registry.set_lifecycle(ids[0], OperatorLifecycle.OFFLINE)
    selected = {registry.lease("teacher_behavior", 30).instance_id for _ in range(2)}
    if ids[0] in selected or selected != set(ids[1:]):
        raise ValueError("请求未只分发给剩余两个实例")
    return {"offline": ids[0], "selected": sorted(selected)}


def _check_reg_017(
    registry: RedisOperatorRegistry, instance_id: str, _: str
) -> dict[str, Any]:
    ids = [f"{instance_id}-{index}" for index in range(3)]
    for value in ids:
        _ready(registry, value)
        registry.set_lifecycle(value, OperatorLifecycle.OFFLINE)
    return asyncio.run(_run_offline_dispatch(registry))


class _RecordingDispatchRepository:
    def __init__(self) -> None:
        self.node_status: int | None = None
        self.aggregated = False

    def defer_capability_nodes(self, capability: str) -> int:
        del capability
        self.node_status = NodeStatus.WAITING_OPERATOR.value
        return 1

    def resume_capability_nodes(self, capability: str) -> int:
        raise AssertionError(f"离线能力不应恢复节点: {capability}")

    def aggregate_capability_task_types(self, capability: str) -> object:
        del capability
        self.aggregated = True
        return None

    def claim_ready_node(self, capability: str, worker_id: str) -> None:
        raise AssertionError(f"离线能力不应领取节点: {capability}/{worker_id}")


async def _run_offline_dispatch(
    registry: RedisOperatorRegistry,
) -> dict[str, Any]:
    capacity_api_status: int | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal capacity_api_status
        payload = json.loads(request.content)
        capability = str(payload["capability"])
        try:
            registry.lease(capability, int(payload["ttl_seconds"]))
        except CapacityUnavailableError:
            capacity_api_status = 503
            return httpx.Response(
                503,
                json={"detail": f"暂无可用算子容量: {capability}"},
            )
        raise ValueError("全部离线时容量 API 仍发出租约")

    repository = _RecordingDispatchRepository()
    dispatcher_module = _import_workspace_module(
        "orchestrator_service.app.application.dispatcher"
    )
    control_client_module = _import_workspace_module(
        "orchestrator_service.app.infrastructure.control_client"
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://control-service:18100",
    ) as http_client:
        capacity_client = control_client_module.ControlLeaseClient(
            http_client,
            default_ttl_seconds=30,
        )
        dispatcher = dispatcher_module.LeaseAwareDispatcher(repository, capacity_client)
        reservation = await dispatcher.reserve_next(
            "teacher_behavior",
            "m2b-reg-017-worker",
        )
    if (
        reservation is not None
        or capacity_api_status != 503
        or repository.node_status != NodeStatus.WAITING_OPERATOR.value
        or not repository.aggregated
    ):
        raise ValueError("全部实例离线未进入等待算子状态")
    return {
        "production_dispatcher": type(dispatcher).__name__,
        "production_capacity_client": type(capacity_client).__name__,
        "capacity_api_status": capacity_api_status,
        "offline_status": repository.node_status,
        "reservation": reservation,
        "verification_scope": "component-level",
        "running_e2e_validated": False,
    }


def _check_reg_018(
    registry: RedisOperatorRegistry, instance_id: str, _: str
) -> dict[str, Any]:
    del registry
    return asyncio.run(_run_registry_client_recovery(instance_id))


async def _run_registry_client_recovery(instance_id: str) -> dict[str, Any]:
    requests: list[str] = []
    registration_attempts = 0
    heartbeat_attempts = 0
    registration_failed = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal heartbeat_attempts, registration_attempts
        requests.append(request.url.path)
        if request.url.path.endswith("/register"):
            registration_attempts += 1
            if registration_attempts == 1:
                registration_failed.set()
                return httpx.Response(503, json={"detail": "control unavailable"})
            return httpx.Response(201, json={"instance_id": instance_id})
        if request.url.path.endswith("/heartbeat"):
            heartbeat_attempts += 1
            if heartbeat_attempts == 1:
                return httpx.Response(503, json={"detail": "control unavailable"})
        return httpx.Response(200, json={"status": "ok"})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OperatorRegistryClient(
        OperatorRegistryClientConfig(
            control_service_url="http://control-service:18100",
            instance_id=instance_id,
            operator_code="vbas",
            capabilities=["teacher_behavior"],
            service_url="http://vbas:8981",
            declared_capacity=1,
            management_token=_operator_registry_token(),
            heartbeat_interval_seconds=0.01,
        ),
        status_provider=lambda: OperatorRuntimeStatus(
            inflight=0,
            model_ready=True,
        ),
        http_client=http_client,
    )
    start_task = asyncio.create_task(client.start())
    try:
        await asyncio.wait_for(registration_failed.wait(), timeout=0.5)
        await asyncio.sleep(0)
        reported_success_before_recovery = (
            start_task.done() and start_task.exception() is None
        )
        await asyncio.wait_for(start_task, timeout=0.5)
        reported_success_after_recovery = (
            start_task.done() and start_task.exception() is None
        )
        expected_recovery = [
            "/api/operator-instances/register",
            "/api/operator-instances/register",
            "/api/operator-instances/heartbeat",
            "/api/operator-instances/register",
            "/api/operator-instances/heartbeat",
        ]
        if (
            reported_success_before_recovery
            or not reported_success_after_recovery
            or registration_attempts < 2
            or heartbeat_attempts < 2
            or requests[:5] != expected_recovery
        ):
            raise ValueError("注册客户端未完成失败后重注册和首次心跳恢复")
        return {
            "production_client": type(client).__name__,
            "registration_attempts": registration_attempts,
            "heartbeat_attempts": heartbeat_attempts,
            "reported_success_before_recovery": reported_success_before_recovery,
            "reported_success_after_recovery": reported_success_after_recovery,
            "request_sequence": requests,
        }
    finally:
        if not start_task.done():
            start_task.cancel()
        if start_task.done() and not start_task.cancelled() and start_task.exception() is None:
            await client.stop()
        await client.aclose()


def _check_reg_019(
    registry: RedisOperatorRegistry, instance_id: str, _: str
) -> dict[str, Any]:
    registry.register(_instance(instance_id, capacity=2))
    observations: list[dict[str, Any]] = []
    for _sample in range(2):
        registry.heartbeat(instance_id, inflight=2, model_ready=True)
        snapshots = build_operator_capacity_snapshot(registry)
        snapshot = next(
            (item for item in snapshots if item.instance_id == instance_id),
            None,
        )
        if snapshot is None:
            raise ValueError("运维容量快照缺少目标实例")
        observations.append(
            {
                "reported_inflight": snapshot.reported_inflight,
                "active_lease_count": snapshot.active_lease_count,
                "capacity_mismatch": snapshot.capacity_mismatch,
            }
        )
    persistent_mismatch = all(
        observation["capacity_mismatch"] for observation in observations
    )
    if not persistent_mismatch:
        raise ValueError("连续运维容量快照未保留 inflight 与租约矛盾")
    return {
        "production_snapshot": build_operator_capacity_snapshot.__name__,
        "persistent_capacity_mismatch": persistent_mismatch,
        "snapshots": observations,
    }


def _check_reg_020(
    registry: RedisOperatorRegistry, instance_id: str, _: str
) -> dict[str, Any]:
    _ready(registry, instance_id)
    first = registry.lease("teacher_behavior", 1)
    time.sleep(1.1)
    heartbeat = registry.heartbeat(instance_id, inflight=1, model_ready=True)
    expired_snapshot = registry.list_active_leases(instance_id)
    capacity_mismatch = expired_snapshot.attribution_difference != 0
    try:
        registry.renew(first.lease_id, 30)
    except CapacityLeaseNotFoundError as exc:
        renewal_rejection = type(exc).__name__
    else:
        raise ValueError("已过期旧租约仍可续租")
    replacement = registry.lease("teacher_behavior", 30)
    if (
        expired_snapshot.active_lease_count != 0
        or expired_snapshot.reported_inflight != 1
        or not capacity_mismatch
    ):
        raise ValueError("过期租约清理后未正确暴露心跳差异")
    return {
        "first_lease": first.lease_id,
        "expired_lease_renewal_rejection": renewal_rejection,
        "reported_inflight": heartbeat.inflight,
        "expired_active_lease_count": expired_snapshot.active_lease_count,
        "capacity_mismatch": capacity_mismatch,
        "replacement_lease": replacement.lease_id,
    }


RegistryChecker = Callable[[RedisOperatorRegistry, str, str], dict[str, Any]]
_REGISTRY_CHECKERS: Mapping[str, RegistryChecker] = {
    "REG-001": _check_reg_001,
    "REG-002": _check_reg_002,
    "REG-003": _check_reg_003,
    "REG-004": _check_reg_004,
    "REG-005": _check_reg_005,
    "REG-006": _check_reg_006,
    "REG-007": _check_reg_007,
    "REG-008": _check_reg_008,
    "REG-009": _check_reg_009,
    "REG-010": _check_reg_010,
    "REG-011": _check_reg_011,
    "REG-012": _check_reg_012,
    "REG-013": _check_reg_013,
    "REG-014": _check_reg_014,
    "REG-015": _check_reg_015,
    "REG-016": _check_reg_016,
    "REG-017": _check_reg_017,
    "REG-018": _check_reg_018,
    "REG-019": _check_reg_019,
    "REG-020": _check_reg_020,
}


def evaluate_scenario(case_id: str, scenario: Mapping[str, Any]) -> dict[str, Any]:
    spec = CASE_SPECS.get(case_id)
    checker = _REGISTRY_CHECKERS.get(case_id)
    mutation = scenario.get("mutation")
    if (
        spec is None
        or checker is None
        or scenario.get("schema_version") != 1
        or scenario.get("case_id") != case_id
        or scenario.get("mode") != spec.mode
        or not isinstance(mutation, dict)
        or mutation.get("case") != case_id
    ):
        return {
            "case_id": case_id,
            "status": "失败",
            "reason": "注册输入与固定 checker 不匹配",
            "observed": {"input_valid": False},
        }
    client: Redis | None = None
    prefix = ""
    try:
        control_url, prefix, instance_id, database = _require_scenario(scenario)
        control_cases = {"REG-004", "REG-005", "REG-006", "REG-008"}
        redis_cases = set(CASE_SPECS) - {
            "REG-004",
            "REG-005",
            "REG-006",
            "REG-007",
            "REG-008",
            "REG-018",
        }
        control = _require_control(control_url) if case_id in control_cases else None
        if case_id in redis_cases:
            client = Redis.from_url(
                "redis://127.0.0.1:6379/15", decode_responses=True
            )
            client.ping()
            _cleanup_prefix(client, prefix)
            registry = RedisOperatorRegistry(
                client, heartbeat_ttl_seconds=1, key_prefix=prefix
            )
        else:
            registry = None
        if case_id == "REG-014":
            checker_context = json.dumps(
                {"database": database, "redis_prefix": prefix},
                separators=(",", ":"),
            )
        elif case_id == "REG-015":
            checker_context = cast(str, database)
        else:
            checker_context = control_url
        observed = checker(
            cast(RedisOperatorRegistry, registry), instance_id, checker_context
        )
        observed = {"checker": checker.__name__, **observed}
        if control is not None:
            observed["control_readiness"] = control
    except (
        OSError,
        RuntimeError,
        ValueError,
        RedisError,
        psycopg.Error,
        SQLAlchemyError,
        urllib.error.URLError,
    ) as exc:
        return {
            "case_id": case_id,
            "status": "失败",
            "reason": f"注册 checker 未观察到目标状态：{exc}",
            "observed": {"checker": getattr(checker, "__name__", None), "detail": str(exc)},
        }
    finally:
        if client is not None:
            try:
                _cleanup_prefix(client, prefix)
            finally:
                client.close()
    return {"case_id": case_id, "status": spec.status, "reason": spec.reason, "observed": observed}


def checker_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--check", required=True, choices=sorted(CASE_SPECS))
    parser.add_argument("--input", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        document = json.loads(arguments.input.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("checker input must be a JSON object")
        result = evaluate_scenario(arguments.check, document)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        result = {
            "case_id": arguments.check,
            "status": "失败",
            "reason": f"注册 checker 输入失败：{exc}",
            "observed": {"input_valid": False},
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "通过" else 1


if __name__ == "__main__":
    raise SystemExit(checker_main())
