from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import psycopg
from aiokafka.admin import AIOKafkaAdminClient  # type: ignore[import-untyped]
from aiokafka.structs import TopicPartition  # type: ignore[import-untyped]
from psycopg import sql
from redis import Redis
from sqlalchemy import Engine, create_engine, text

from packages.operator_registry_client.client import (
    OperatorRegistryClient,
    OperatorRegistryClientConfig,
    OperatorRuntimeStatus,
)
from packages.platform_common.kafka import (
    AioKafkaConsumerAdapter,
    AioKafkaProducerAdapter,
    KafkaMessage,
    KafkaTopicManager,
)
from packages.platform_common.operator_registry import (
    OperatorCode,
    OperatorInstance,
    OperatorInstanceNotFoundError,
    OperatorLifecycle,
)
from packages.platform_common.redis_operator_registry import RedisOperatorRegistry
from packages.platform_common.repository import (
    CourseRepository,
    NodeWrite,
    OutboxRecord,
    OutboxStateRecord,
    RepositoryNotFoundError,
    TaskTypeWrite,
)
from packages.platform_contracts.status import NodeStatus, Priority, TaskType
from scripts.milestone_2b_case_catalog import CaseDefinition

from .base import CaseContext, CaseOutcome
from .deployment import (
    FoundationCaseSpec,
    _spec,
    run_foundation_case,
    run_foundation_cleanup,
)
from .safety import ResourceSpec


def _infrastructure_spec(title: str, expected: str) -> FoundationCaseSpec:
    return _spec(
        title,
        expected,
        safety="isolated_mutation",
        timeout_seconds=300,
        mode="controlled_input",
    )


CASE_SPECS: Mapping[str, FoundationCaseSpec] = {
    "INF-001": _infrastructure_spec(
        "PostgreSQL 未启动", "control 和 orchestrator readiness 失败"
    ),
    "INF-002": _infrastructure_spec(
        "PostgreSQL 账号或密码错误", "readiness 显示中文依赖原因"
    ),
    "INF-003": _infrastructure_spec("数据库迁移缺失", "readiness 失败，不接受课程任务"),
    "INF-004": _infrastructure_spec("Redis 未启动", "control readiness 失败且不能发租约"),
    "INF-005": _infrastructure_spec("Redis 重启导致临时注册态消失", "算子重新注册和心跳后恢复"),
    "INF-006": _infrastructure_spec("Kafka 未启动", "orchestrator readiness 失败"),
    "INF-007": _infrastructure_spec("Kafka Topic 缺失且禁止自动创建", "启动校验失败"),
    "INF-008": _infrastructure_spec("Kafka 消息重复投递", "DAG 和节点保持幂等"),
    "INF-009": _infrastructure_spec("消费处理失败", "不提交 offset，恢复后重投"),
    "INF-010": _infrastructure_spec("Publisher 发送失败", "Outbox 保持待发布并累计尝试次数"),
    "INF-011": _infrastructure_spec(
        "Publisher 发布成功但标记前退出", "允许重复事件但不重复 DAG"
    ),
    "INF-012": _infrastructure_spec("Consumer 提交 offset 前退出", "重启后幂等重放"),
    "INF-013": _infrastructure_spec(
        "MongoDB 未启动",
        "FaceRec readiness 或人物管理失败，不影响离线非人脸泳道",
    ),
    "INF-014": _infrastructure_spec(
        "MongoDB 认证失败", "返回明确依赖错误，不创建空人物记录"
    ),
    "INF-015": _infrastructure_spec(
        "MongoDB 中 embedding 缺失或维度错误", "识别跳过或拒绝坏记录并记录原因"
    ),
    "INF-016": _infrastructure_spec("Kafka 消息包含媒体字节或 Base64", "契约拒绝消息"),
}


def _component(context: CaseContext, case: CaseDefinition) -> str:
    return f"{case.case_id.lower()}-{context.run_id}"


def _database_name(context: CaseContext, case: CaseDefinition) -> str:
    safe_run = context.run_id.replace("-", "_")
    safe_case = case.case_id.lower().replace("-", "_")
    return f"m2b_{len(context.run_id)}_{safe_run}_{safe_case}_test"


def _topic_name(context: CaseContext, case: CaseDefinition) -> str:
    return f"m2b.{context.run_id}.{case.case_id.lower()}"


def _redis_prefix(context: CaseContext, case: CaseDefinition) -> str:
    return f"m2b:{context.run_id}:{case.case_id.lower()}:"


def _mongodb_database_name(context: CaseContext, case: CaseDefinition) -> str:
    safe_run = context.run_id.replace("-", "_")
    safe_case = case.case_id.lower().replace("-", "_")
    return f"m2b_{len(context.run_id)}_{safe_run}_{safe_case}_mongo_test"


def _infrastructure_scenario(
    context: CaseContext, case: CaseDefinition
) -> dict[str, Any]:
    return {
        "mutation": {"case": case.case_id},
        "control_url": "http://127.0.0.1:18100",
        "orchestrator_url": "http://127.0.0.1:18101",
        "facerec_url": "http://127.0.0.1:18003",
        "database": _database_name(context, case),
        "kafka_topic": _topic_name(context, case),
        "kafka_group": _topic_name(context, case),
        "redis_prefix": _redis_prefix(context, case),
        "mongodb_database": _mongodb_database_name(context, case),
        "mongodb_credentials": "m2b_test_invalid:m2b_test_invalid",
        "component": _component(context, case),
    }


def _infrastructure_resources(
    context: CaseContext, case: CaseDefinition
) -> tuple[ResourceSpec, ...]:
    topic = _topic_name(context, case)
    return (
        ResourceSpec("database", _database_name(context, case)),
        ResourceSpec("mongodb_database", _mongodb_database_name(context, case)),
        ResourceSpec("kafka_topic", topic),
        ResourceSpec("kafka_group", topic),
        ResourceSpec("redis_prefix", _redis_prefix(context, case)),
    )


async def _run(
    context: CaseContext, case: CaseDefinition, case_id: str
) -> CaseOutcome:
    return await run_foundation_case(
        context=context,
        case=case,
        case_id=case_id,
        group="infrastructure",
        spec=CASE_SPECS[case_id],
        scenario_builder=_infrastructure_scenario,
        resource_builder=_infrastructure_resources,
    )


async def inf_001(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "INF-001")


async def inf_002(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "INF-002")


async def inf_003(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "INF-003")


async def inf_004(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "INF-004")


async def inf_005(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "INF-005")


async def inf_006(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "INF-006")


async def inf_007(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "INF-007")


async def inf_008(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "INF-008")


async def inf_009(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "INF-009")


async def inf_010(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "INF-010")


async def inf_011(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "INF-011")


async def inf_012(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "INF-012")


async def inf_013(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "INF-013")


async def inf_014(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "INF-014")


async def inf_015(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "INF-015")


async def inf_016(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "INF-016")


async def cleanup(context: CaseContext, case: CaseDefinition) -> None:
    spec = CASE_SPECS.get(case.case_id)
    if spec is None:
        raise ValueError("infrastructure cleanup case is not registered")
    await run_foundation_cleanup(
        context=context,
        case=case,
        group="infrastructure",
        spec=spec,
    )


for _case_id in CASE_SPECS:
    globals()[_case_id.lower().replace("-", "_")].cleanup = cleanup


def _expected_names(run_id: str, case_id: str) -> dict[str, str]:
    safe_run = run_id.replace("-", "_")
    safe_case = case_id.lower().replace("-", "_")
    topic = f"m2b.{run_id}.{case_id.lower()}"
    return {
        "database": f"m2b_{len(run_id)}_{safe_run}_{safe_case}_test",
        "kafka_topic": topic,
        "kafka_group": topic,
        "redis_prefix": f"m2b:{run_id}:{case_id.lower()}:",
        "mongodb_database": (
            f"m2b_{len(run_id)}_{safe_run}_{safe_case}_mongo_test"
        ),
        "component": f"{case_id.lower()}-{run_id}",
    }


def _require_scenario(case_id: str, scenario: Mapping[str, Any]) -> None:
    run_id = scenario.get("run_id")
    if not isinstance(run_id, str):
        raise ValueError("当前 run_id 缺失")
    expected = _expected_names(run_id, case_id)
    mismatches = [
        field for field, value in expected.items() if scenario.get(field) != value
    ]
    if mismatches:
        raise ValueError(
            f"资源不属于当前 run：{', '.join(sorted(mismatches))}"
        )
    fixed_urls = {
        "control_url": "http://127.0.0.1:18100",
        "orchestrator_url": "http://127.0.0.1:18101",
        "facerec_url": "http://127.0.0.1:18003",
    }
    if any(scenario.get(field) != value for field, value in fixed_urls.items()):
        raise ValueError("服务地址未绑定固定本机验收端口")
    if scenario.get("mongodb_credentials") != (
        "m2b_test_invalid:m2b_test_invalid"
    ):
        raise ValueError("MongoDB 反例没有使用固定错误凭据")


_PLATFORM_ROOT = Path(__file__).resolve().parents[2]
_WORKSPACE_ROOT = _PLATFORM_ROOT.parent
_POSTGRES_ADMIN_DSN = "postgresql://algorithm:algorithm@127.0.0.1:5432/postgres"
_KAFKA_BOOTSTRAP = ["127.0.0.1:9092"]
_FACEREC_PROBE_RESULT_MARKER = "@@M2B_FACEREC_PROBE_RESULT_V1@@"

_ROOT_READINESS_PROBE = r'''
import asyncio
import json
import socket
import sys
from contextlib import contextmanager
from types import SimpleNamespace

probe = sys.argv[1]

if probe in {"postgres_down", "postgres_auth", "redis_down", "schema_missing"}:
    from control_service.app.infrastructure.runtime import ControlReadinessChecker

if probe in {"postgres_down", "kafka_down"}:
    from orchestrator_service.app.infrastructure.runtime import OrchestratorRuntime

if probe == "postgres_down":
    from packages.platform_common.repository import CourseRepository
    from sqlalchemy import create_engine


def orchestrator_runtime(resources):
    runtime = object.__new__(OrchestratorRuntime)
    runtime.settings = SimpleNamespace(
        readiness=SimpleNamespace(dependency_timeout_seconds=2.0)
    )
    runtime.resources = resources
    return runtime


if probe == "postgres_down":
    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    guard.bind(("127.0.0.1", 0))
    postgres_endpoint = f"127.0.0.1:{guard.getsockname()[1]}"
    postgres_dsn = (
        "postgresql+psycopg://algorithm:algorithm@"
        f"{postgres_endpoint}/postgres"
    )
    engine = create_engine(
        postgres_dsn,
        connect_args={"connect_timeout": 1},
        pool_pre_ping=True,
    )
    try:
        control = ControlReadinessChecker(
            engine,
            None,
            postgres_dsn=postgres_dsn,
            dependency_timeout_seconds=2.0,
        )._check_postgresql()
        repository = CourseRepository(engine)
        runtime = orchestrator_runtime(SimpleNamespace(repository=repository))
        _, orchestrator = asyncio.run(runtime._check_postgres())
    finally:
        engine.dispose()
        guard.close()
    control_ready = bool(control.ready)
    orchestrator_ready = bool(orchestrator["ready"])
    result = {
        "ready": any((control_ready, orchestrator_ready)),
        "detail": control.detail,
        "control_ready": control_ready,
        "orchestrator_ready": orchestrator_ready,
        "control_readiness": "not_ready" if not control_ready else "ready",
        "orchestrator_readiness": (
            "not_ready" if not orchestrator_ready else "ready"
        ),
        "production_validator": "ControlReadinessChecker",
        "control_validator": "ControlReadinessChecker._check_postgresql",
        "orchestrator_repository": type(repository).__name__,
        "postgres_endpoint": postgres_endpoint,
    }
elif probe == "postgres_auth":
    from control_service.app.infrastructure import runtime as control_runtime

    def reject_authentication(*args, **kwargs):
        del args, kwargs
        raise control_runtime.psycopg.OperationalError(
            "PostgreSQL 认证失败: controlled invalid credentials"
        )

    control_runtime.psycopg.connect = reject_authentication
    check = ControlReadinessChecker(
        object(),
        None,
        postgres_dsn=(
            "postgresql+psycopg://algorithm:m2b_test_invalid@"
            "127.0.0.1:5432/m2b_auth_test"
        ),
        dependency_timeout_seconds=2.0,
    )._check_postgresql()
    result = {
        "ready": check.ready,
        "detail": check.detail,
        "production_validator": "ControlReadinessChecker._check_postgresql",
    }
elif probe == "redis_down":
    class DownRedis:
        def ping(self):
            raise RuntimeError("controlled Redis unavailable")

    check = ControlReadinessChecker(
        None,
        DownRedis(),
        dependency_timeout_seconds=2.0,
    )._check_redis()
    result = {
        "ready": check.ready,
        "detail": check.detail,
        "production_validator": "ControlReadinessChecker",
    }
elif probe == "schema_missing":
    class Result:
        def fetchall(self):
            return []

    class Connection:
        def execute(self, *args, **kwargs):
            return Result()

    @contextmanager
    def connection(*, statement_count):
        assert statement_count == 2
        yield Connection()

    checker = ControlReadinessChecker(
        object(),
        None,
        dependency_timeout_seconds=2.0,
    )
    checker._postgres_connection = connection
    check = checker._check_schema()
    result = {
        "ready": check.ready,
        "detail": check.detail,
        "production_validator": "ControlReadinessChecker._check_schema",
    }
elif probe == "kafka_down":
    class DownConsumer:
        async def lag(self):
            raise RuntimeError("controlled Kafka unavailable")

    runtime = orchestrator_runtime(SimpleNamespace(consumer=DownConsumer()))
    _, check = asyncio.run(runtime._check_kafka())
    result = {
        "ready": check["ready"],
        "detail": check["detail"],
        "production_validator": "OrchestratorRuntime._check_kafka",
    }
else:
    raise SystemExit(f"unknown root readiness probe: {probe}")

print(json.dumps(result, ensure_ascii=False, sort_keys=True))
'''

_FACEREC_MONGO_AUTH_FAILURE_HELPER = r'''
from collections.abc import Mapping, Sequence

from pymongo.errors import OperationFailure


def mongodb_authentication_failure_facts(error, *, related_errors=()):
    outer_error_type = type(error).__name__
    pending = [error]
    pending.extend(related_errors)
    visited = set()
    while pending:
        current = pending.pop()
        identity = id(current)
        if identity in visited:
            continue
        visited.add(identity)
        if isinstance(current, OperationFailure):
            details = current.details if isinstance(current.details, Mapping) else {}
            if current.code == 18 and details.get("codeName") == "AuthenticationFailed":
                return {
                    "authentication_error_type": outer_error_type,
                    "authentication_cause_type": type(current).__name__,
                    "authentication_error_code": current.code,
                    "authentication_error_code_name": details["codeName"],
                    "authentication_error_wrapped": current is not error,
                }
        for attribute in ("errors", "details"):
            nested = getattr(current, attribute, None)
            if isinstance(nested, Mapping):
                pending.extend(nested.values())
            elif isinstance(nested, Sequence) and not isinstance(
                nested, (str, bytes, bytearray)
            ):
                pending.extend(nested)
        for attribute in ("__cause__", "__context__"):
            nested = getattr(current, attribute, None)
            if nested is not None:
                pending.append(nested)
    return None
'''


_FACEREC_READINESS_PROBE = (
    r'''
import asyncio
import json
import sys

probe = sys.argv[1]
RESULT_MARKER = "@@M2B_FACEREC_PROBE_RESULT_V1@@"
'''
    + _FACEREC_MONGO_AUTH_FAILURE_HELPER
    + r'''
from app.core.readiness import FaceRecReadiness

if probe == "mongodb_down":
    class DownDatabase:
        async def command(self, payload):
            assert payload == {"ping": 1}
            raise RuntimeError("controlled MongoDB unavailable")

    database = DownDatabase()
    readiness = FaceRecReadiness(
        database,
        object(),
        dlib_workers_ready=True,
        timeout_seconds=1.0,
    )
    ready = asyncio.run(readiness.check())
    result = {
        "ready": ready,
        "detail": "MongoDB readiness rejected controlled dependency failure",
        "database_ready": readiness.database_ready(),
        "authenticated": None,
        "person_lookup_attempts": 0,
        "person_write_attempts": 0,
        "empty_person_created": False,
        "persistence_error": None,
        "production_persistence_validator": None,
        "production_validator": "FaceRecReadiness",
    }
elif probe == "mongodb_auth":
    from urllib.parse import quote

    from motor.motor_asyncio import AsyncIOMotorClient
    from pymongo.errors import OperationFailure, ServerSelectionTimeoutError

    from app.core.config import settings
    from app.services.person import update_or_create_person

    database_name = sys.argv[2]
    invalid_username = sys.argv[3]
    invalid_password = sys.argv[4]
    admin_username = sys.argv[5]
    admin_password = sys.argv[6]

    def mongo_uri(username, password):
        return (
            f"mongodb://{quote(username, safe='')}:{quote(password, safe='')}@"
            f"{settings.db.host}:{settings.db.port}/{database_name}"
            f"?authSource={quote(settings.db.auth_source, safe='')}"
        )

    class CountingCollection:
        def __init__(self, collection):
            self._collection = collection
            self.lookup_attempts = 0
            self.write_attempts = 0

        async def find_one(self, *args, **kwargs):
            self.lookup_attempts += 1
            return await self._collection.find_one(*args, **kwargs)

        async def insert_one(self, *args, **kwargs):
            self.write_attempts += 1
            return await self._collection.insert_one(*args, **kwargs)

        async def update_one(self, *args, **kwargs):
            self.write_attempts += 1
            return await self._collection.update_one(*args, **kwargs)

    class CountingDatabase:
        def __init__(self, database):
            self.persons = CountingCollection(database["persons"])

        def __getitem__(self, name):
            if name != "persons":
                raise KeyError(name)
            return self.persons

    async def run_auth_probe():
        admin_client = AsyncIOMotorClient(
            mongo_uri(admin_username, admin_password),
            serverSelectionTimeoutMS=2000,
            connectTimeoutMS=2000,
            socketTimeoutMS=2000,
        )
        invalid_client = AsyncIOMotorClient(
            mongo_uri(invalid_username, invalid_password),
            serverSelectionTimeoutMS=2000,
            connectTimeoutMS=2000,
            socketTimeoutMS=2000,
        )
        try:
            await admin_client.admin.command({"ping": 1})
            await admin_client.drop_database(database_name)
            invalid_database = invalid_client.get_database(database_name)
            readiness = FaceRecReadiness(
                invalid_database,
                object(),
                dlib_workers_ready=True,
                timeout_seconds=1.0,
            )
            ready = await readiness.check()
            counted_database = CountingDatabase(invalid_database)
            try:
                await update_or_create_person(
                    counted_database,
                    {"number": "m2b-empty-record-probe"},
                )
            except (OperationFailure, ServerSelectionTimeoutError) as exc:
                server_descriptions = (
                    invalid_client.topology_description.server_descriptions()
                )
                topology_errors = [
                    description.error
                    for description in server_descriptions.values()
                    if description.error is not None
                ]
                authentication_facts = mongodb_authentication_failure_facts(
                    exc,
                    related_errors=topology_errors,
                )
                if authentication_facts is None:
                    raise
                persistence_error = f"MongoDB 认证失败: {exc}"
            else:
                raise RuntimeError("MongoDB invalid credentials were accepted")
            person_count = await admin_client.get_database(database_name)[
                "persons"
            ].count_documents({})
            return {
                **authentication_facts,
                "ready": ready,
                "detail": "MongoDB 认证失败: FaceRecReadiness database_ready=False",
                "database_ready": readiness.database_ready(),
                "authenticated": False,
                "person_lookup_attempts": counted_database.persons.lookup_attempts,
                "person_write_attempts": counted_database.persons.write_attempts,
                "empty_person_created": person_count != 0,
                "persistence_error": persistence_error,
                "person_count_after_auth_failure": person_count,
                "isolated_database": database_name,
                "production_persistence_validator": (
                    "app.services.person.update_or_create_person"
                ),
                "production_validator": "FaceRecReadiness",
            }
        finally:
            try:
                await admin_client.drop_database(database_name)
            finally:
                invalid_client.close()
                admin_client.close()

    result = asyncio.run(run_auth_probe())
else:
    raise SystemExit(f"unknown FaceRec readiness probe: {probe}")

print(RESULT_MARKER + json.dumps(result, ensure_ascii=False, sort_keys=True))
'''
)

_FACEREC_EMBEDDING_PROBE = r'''
import asyncio
import json
import sys
from urllib.parse import quote

import numpy as np
from bson.binary import Binary
from motor.motor_asyncio import AsyncIOMotorClient

from app.core.ai_engine import find_best_match_embedding
from app.core.config import settings
from app.core.embedding_matching import filter_candidate_embeddings
from app.services.person import get_targets_embeddings

probe = sys.argv[1]
RESULT_MARKER = "@@M2B_FACEREC_PROBE_RESULT_V1@@"
if probe != "mongodb_embedding":
    raise SystemExit(f"unknown FaceRec embedding probe: {probe}")
database_name = sys.argv[2]
admin_username = sys.argv[5]
admin_password = sys.argv[6]

uri = (
    f"mongodb://{quote(admin_username, safe='')}:{quote(admin_password, safe='')}@"
    f"{settings.db.host}:{settings.db.port}/{database_name}"
    f"?authSource={quote(settings.db.auth_source, safe='')}"
)

async def run_embedding_probe():
    client = AsyncIOMotorClient(
        uri,
        serverSelectionTimeoutMS=2000,
        connectTimeoutMS=2000,
        socketTimeoutMS=2000,
    )
    targets = ["missing", "wrong-dimension"]
    try:
        await client.admin.command({"ping": 1})
        await client.drop_database(database_name)
        database = client.get_database(database_name)
        await database["persons"].insert_many(
            [
                {"number": "missing", "name": "missing"},
                {
                    "number": "wrong-dimension",
                    "name": "wrong-dimension",
                    "embedding": Binary(b"\x00\x00\x00\x00"),
                },
            ]
        )
        queried = await get_targets_embeddings(database, targets)
        by_number = {document.get("number"): document for document in queried}
        candidates = [by_number[target] for target in targets]
        best_similarity, best_match = find_best_match_embedding(
            np.zeros(512, dtype=np.float32),
            candidates,
        )
        vectors, documents, rejections = filter_candidate_embeddings(candidates)
        return {
            "production_validator": "filter_candidate_embeddings",
            "production_candidate_query": (
                "app.services.person.get_targets_embeddings"
            ),
            "production_recognition_validator": (
                "app.core.ai_engine.find_best_match_embedding"
            ),
            "isolated_database": database_name,
            "queried_records": len(queried),
            "valid_records": len(documents),
            "valid_vectors": len(vectors),
            "rejections": rejections,
            "recognition_skipped_bad_records": (
                best_similarity == 0.0
                and best_match is None
                and len(rejections) == len(candidates)
            ),
        }
    finally:
        try:
            await client.drop_database(database_name)
        finally:
            client.close()

print(
    RESULT_MARKER
    + json.dumps(asyncio.run(run_embedding_probe()), ensure_ascii=False, sort_keys=True)
)
'''

_ROOT_MESSAGE_CONTRACT_PROBE = r'''
import asyncio
import json

from vision_orchestrator_service.app.application.events import VisualCommandProcessor

side_effects = []


class Analyzer:
    async def analyze(self, command, progress):
        del command, progress
        side_effects.append("analyze")
        return {}


class Repository:
    def update_node_progress(self, *args, **kwargs):
        del args, kwargs
        side_effects.append("update_node_progress")

    def complete_node(self, *args, **kwargs):
        del args, kwargs
        side_effects.append("complete_node")


class Producer:
    async def send_and_wait(self, *args, **kwargs):
        del args, kwargs
        side_effects.append("send_and_wait")


payload = {
    "event_id": "00000000-0000-0000-0000-000000000016",
    "event_type": "VISUAL_ANALYSIS_REQUESTED",
    "payload": {
        "task_id": "m2b-task",
        "task_type": "TEACHER_BEHAVIOR",
        "node_id": 1,
        "submission_id": "m2b-submission",
        "local_video_path": "/data/course/test/teacher.mp4",
        "priority": "NORMAL",
        "video_bytes": "AA==",
    },
}
processor = VisualCommandProcessor(
    Analyzer(),
    Repository(),
    Producer(),
    event_topic="algorithm.visual.events",
)
try:
    asyncio.run(
        processor.handle(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        )
    )
except ValueError as exc:
    detail = str(exc)
else:
    raise RuntimeError("production visual command processor accepted media bytes")

print(json.dumps({
    "contract_rejection": detail,
    "media_published": False,
    "production_validator": "VisualCommandProcessor.handle",
    "side_effects": side_effects,
}, ensure_ascii=False, sort_keys=True))
'''


def _facerec_probe_credentials(
    case_id: str, scenario: Mapping[str, Any]
) -> tuple[str, str, str, str, str]:
    _require_scenario(case_id, scenario)
    database_name = scenario.get("mongodb_database")
    invalid_credentials = scenario.get("mongodb_credentials")
    if not isinstance(database_name, str) or not database_name:
        raise ValueError("FaceRec probe isolated MongoDB database is missing")
    if (
        not isinstance(invalid_credentials, str)
        or invalid_credentials.count(":") != 1
    ):
        raise ValueError("FaceRec probe invalid MongoDB credentials are malformed")
    invalid_username, invalid_password = invalid_credentials.split(":", 1)
    admin_username = os.getenv("MONGO_ROOT_USERNAME", "root")
    admin_password = os.getenv("MONGO_ROOT_PASSWORD", "root")
    if any(
        not value or "\0" in value
        for value in (
            invalid_username,
            invalid_password,
            admin_username,
            admin_password,
        )
    ):
        raise ValueError("FaceRec probe MongoDB credentials must be non-empty")
    if (invalid_username, invalid_password) == (admin_username, admin_password):
        raise ValueError(
            "FaceRec probe invalid credentials must differ from admin credentials"
        )
    return (
        database_name,
        invalid_username,
        invalid_password,
        admin_username,
        admin_password,
    )


def _decode_facerec_probe_result(stdout: str) -> dict[str, Any]:
    frames = [
        line[len(_FACEREC_PROBE_RESULT_MARKER) :]
        for line in stdout.splitlines()
        if line.startswith(_FACEREC_PROBE_RESULT_MARKER)
    ]
    if len(frames) != 1:
        raise ValueError("FaceRec probe must return exactly one result frame")

    def reject_non_standard_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    try:
        result = json.loads(
            frames[0],
            parse_constant=reject_non_standard_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("FaceRec probe result frame is not strict JSON") from exc
    if not isinstance(result, dict):
        raise ValueError("FaceRec probe result frame is not a JSON object")
    return cast(dict[str, Any], result)


def _run_facerec_container_probe(
    *,
    case_id: str,
    probe: str,
    script: str,
    scenario: Mapping[str, Any],
) -> dict[str, Any]:
    credentials = _facerec_probe_credentials(case_id, scenario)
    registry_token = os.getenv("OPERATOR_REGISTRY_TOKEN")
    if registry_token is None or not registry_token.strip():
        raise ValueError("OPERATOR_REGISTRY_TOKEN is required for FaceRec Compose")
    inherited_environment = os.environ.copy()
    compose_command = [
        "docker",
        "compose",
        "-f",
        str(_PLATFORM_ROOT / "deploy/docker-compose.operators.yml"),
        "--profile",
        "gpu0",
        "ps",
        "-q",
        "facerec-gpu0",
    ]
    resolved = subprocess.run(
        compose_command,
        cwd=_PLATFORM_ROOT,
        env=inherited_environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    if resolved.returncode != 0:
        raise ValueError(
            "canonical FaceRec container resolution failed: "
            + resolved.stderr.strip()
        )
    container_ids = [line.strip() for line in resolved.stdout.splitlines() if line.strip()]
    if (
        len(container_ids) != 1
        or re.fullmatch(r"[0-9a-f]{64}", container_ids[0]) is None
    ):
        raise ValueError("canonical FaceRec container was not uniquely resolved")
    completed = subprocess.run(
        [
            "docker",
            "exec",
            container_ids[0],
            "python3",
            "-c",
            script,
            probe,
            *credentials,
        ],
        cwd=_PLATFORM_ROOT,
        env=inherited_environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        raise ValueError("FaceRec container probe failed: " + completed.stderr.strip())
    return _decode_facerec_probe_result(completed.stdout)


def _run_production_readiness_probe(
    name: str, scenario: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    if name == "mongodb_auth":
        if scenario is None:
            raise ValueError("MongoDB authentication probe requires its scenario")
        result = _run_facerec_container_probe(
            case_id="INF-014",
            probe=name,
            script=_FACEREC_READINESS_PROBE,
            scenario=scenario,
        )
    elif name == "mongodb_down":
        script = _FACEREC_READINESS_PROBE
        cwd = _WORKSPACE_ROOT / "facerec"
    elif name in {
        "postgres_down",
        "postgres_auth",
        "redis_down",
        "schema_missing",
        "kafka_down",
    }:
        script = _ROOT_READINESS_PROBE
        cwd = _WORKSPACE_ROOT
    else:
        raise ValueError(f"unknown production readiness probe: {name}")
    if name != "mongodb_auth":
        completed = subprocess.run(
            [sys.executable, "-c", script, name],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        if completed.returncode != 0:
            raise ValueError(
                f"production readiness probe failed: {completed.stderr.strip()}"
            )
        if name == "mongodb_down":
            result = _decode_facerec_probe_result(completed.stdout)
        else:
            try:
                result = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "production readiness probe did not return strict JSON"
                ) from exc
    if not isinstance(result, dict) or result.get("ready") is not False:
        raise ValueError("production readiness probe did not reject controlled failure")
    if name == "postgres_down" and (
        result.get("control_ready") is not False
        or result.get("orchestrator_ready") is not False
        or result.get("ready")
        is not any((result["control_ready"], result["orchestrator_ready"]))
    ):
        raise ValueError("PostgreSQL readiness probe evidence is incomplete")
    if name == "mongodb_auth" and (
        scenario is None
        or result.get("production_persistence_validator")
        != "app.services.person.update_or_create_person"
        or result.get("person_lookup_attempts") != 1
        or result.get("person_write_attempts") != 0
        or result.get("empty_person_created") is not False
        or result.get("authentication_error_type")
        not in {"OperationFailure", "ServerSelectionTimeoutError"}
        or result.get("authentication_cause_type") != "OperationFailure"
        or result.get("authentication_error_code") != 18
        or result.get("authentication_error_code_name") != "AuthenticationFailed"
        or result.get("authentication_error_wrapped")
        is not (result.get("authentication_error_type") == "ServerSelectionTimeoutError")
        or result.get("person_count_after_auth_failure") != 0
        or result.get("isolated_database") != scenario.get("mongodb_database")
        or not isinstance(result.get("persistence_error"), str)
    ):
        raise ValueError("FaceRec authentication persistence evidence is incomplete")
    return result


def _run_production_message_contract_probe() -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-c", _ROOT_MESSAGE_CONTRACT_PROBE],
        cwd=_WORKSPACE_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if completed.returncode != 0:
        raise ValueError(
            f"production message contract probe failed: {completed.stderr.strip()}"
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "production message contract probe did not return strict JSON"
        ) from exc
    if (
        not isinstance(result, dict)
        or result.get("production_validator") != "VisualCommandProcessor.handle"
        or result.get("media_published") is not False
        or result.get("side_effects") != []
    ):
        raise ValueError("production message contract probe evidence is incomplete")
    return result



def _run_production_embedding_probe(
    scenario: Mapping[str, Any],
) -> dict[str, Any]:
    result = _run_facerec_container_probe(
        case_id="INF-015",
        probe="mongodb_embedding",
        script=_FACEREC_EMBEDDING_PROBE,
        scenario=scenario,
    )
    expected_rejections = [
        {"record": "missing", "reason": "embedding_missing"},
        {
            "record": "wrong-dimension",
            "reason": "embedding_dimension_invalid",
        },
    ]
    if (
        not isinstance(result, dict)
        or result.get("production_validator") != "filter_candidate_embeddings"
        or result.get("production_candidate_query")
        != "app.services.person.get_targets_embeddings"
        or result.get("production_recognition_validator")
        != "app.core.ai_engine.find_best_match_embedding"
        or result.get("isolated_database") != scenario.get("mongodb_database")
        or result.get("queried_records") != 2
        or result.get("valid_records") != 0
        or result.get("valid_vectors") != 0
        or result.get("rejections") != expected_rejections
        or result.get("recognition_skipped_bad_records") is not True
    ):
        raise ValueError("FaceRec embedding probe evidence is incomplete")
    return result


def _postgres_dsn(database: str) -> str:
    return f"postgresql+psycopg://algorithm:algorithm@127.0.0.1:5432/{database}"


def _raw_postgres_dsn(
    database: str, *, password: str = "algorithm"
) -> str:
    return f"postgresql://algorithm:{password}@127.0.0.1:5432/{database}"


def _drop_isolated_database(
    admin: psycopg.Connection[Any], database: str
) -> None:
    if re.fullmatch(r"m2b_[a-z0-9_]+_test", database) is None:
        raise ValueError("拒绝清理非当前 run 的 PostgreSQL 数据库")
    admin.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid()",
        (database,),
    )
    admin.execute(
        sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database))
    )


@contextmanager
def _isolated_database(
    scenario: Mapping[str, Any], *, migrate: bool
) -> Iterator[Engine]:
    database = str(scenario["database"])
    if not database.endswith("_test"):
        raise ValueError("PostgreSQL 隔离数据库必须以 _test 结尾")
    created = False
    engine: Engine | None = None
    try:
        with psycopg.connect(_POSTGRES_ADMIN_DSN, autocommit=True) as admin:
            _drop_isolated_database(admin, database)
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        created = True
        if migrate:
            migration_paths = sorted((_PLATFORM_ROOT / "migrations").glob("*.sql"))
            if not migration_paths:
                raise ValueError("PostgreSQL 迁移文件缺失")
            with psycopg.connect(
                _raw_postgres_dsn(database), autocommit=True
            ) as connection:
                for migration_path in migration_paths:
                    connection.execute(migration_path.read_text(encoding="utf-8"))
        engine = create_engine(_postgres_dsn(database))
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with psycopg.connect(_POSTGRES_ADMIN_DSN, autocommit=True) as admin:
                _drop_isolated_database(admin, database)


def _registry_instance(instance_id: str) -> OperatorInstance:
    return OperatorInstance(
        instance_id=instance_id,
        operator_code=OperatorCode.VBAS,
        capabilities=["teacher_behavior"],
        service_url="http://127.0.0.1:18981",
        declared_capacity=1,
        labels={"gpu": "0"},
        model_ready=False,
    )


def _cleanup_redis_prefix(client: Redis, prefix: str) -> None:
    keys = list(client.scan_iter(match=f"{prefix}*", count=100))
    if keys:
        client.delete(*keys)


async def _delete_kafka_group(group: str) -> None:
    completed = await asyncio.to_thread(
        subprocess.run,
        [
            "docker",
            "compose",
            "-f",
            str(_PLATFORM_ROOT / "deploy/docker-compose.infrastructure.yml"),
            "exec",
            "-T",
            "kafka",
            "/opt/kafka/bin/kafka-consumer-groups.sh",
            "--bootstrap-server",
            "localhost:9092",
            "--delete",
            "--group",
            group,
        ],
        cwd=_PLATFORM_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "does not exist" not in completed.stderr:
        raise ValueError(
            f"Kafka 隔离 group 清理失败：{completed.stderr.strip()}"
        )


async def _reset_kafka_resources(topic: str, group: str) -> None:
    admin = AIOKafkaAdminClient(
        bootstrap_servers=_KAFKA_BOOTSTRAP,
        client_id=f"m2b-reset-{uuid4().hex[:8]}",
    )
    await admin.start()
    try:
        groups = {str(row[0]) for row in await admin.list_consumer_groups()}
        if group in groups:
            await _delete_kafka_group(group)
        if topic in await admin.list_topics():
            await admin.delete_topics([topic])
            deadline = asyncio.get_running_loop().time() + 10
            while topic in await admin.list_topics():
                if asyncio.get_running_loop().time() >= deadline:
                    raise ValueError("Kafka 隔离 topic 删除超时")
                await asyncio.sleep(0.05)
    finally:
        await admin.close()


async def _prepare_kafka_resources(topic: str, group: str) -> None:
    await _reset_kafka_resources(topic, group)
    manager = KafkaTopicManager(
        bootstrap_servers=_KAFKA_BOOTSTRAP,
        client_id=f"m2b-create-{uuid4().hex[:8]}",
        topics=(topic,),
    )
    missing = await manager.ensure_topics()
    if missing != (topic,):
        raise ValueError("Kafka 隔离 topic 未从缺失状态创建")


async def _poll_messages(
    consumer: AioKafkaConsumerAdapter, count: int
) -> list[KafkaMessage]:
    messages: list[KafkaMessage] = []
    deadline = asyncio.get_running_loop().time() + 10
    while len(messages) < count and asyncio.get_running_loop().time() < deadline:
        messages.extend(await consumer.poll(timeout_seconds=0.25))
    if len(messages) < count:
        raise ValueError(f"Kafka 只收到 {len(messages)}/{count} 条隔离消息")
    return messages[:count]


def _create_asr_task(repository: CourseRepository, task_id: str) -> str:
    records = repository.create_task_types(
        task_id=task_id,
        writes=[TaskTypeWrite(TaskType.ASR)],
    )
    if len(records) != 1 or not records[0].submission_id:
        raise ValueError("ASR 测试任务没有生成唯一 submission_id")
    return records[0].submission_id


def _initialize_asr_nodes(repository: CourseRepository, task_id: str) -> tuple[int, ...]:
    nodes = repository.initialize_pipeline(
        task_id,
        TaskType.ASR,
        [
            NodeWrite(
                "ASR_TRANSCRIPTION",
                NodeStatus.PENDING,
                Priority.NORMAL,
                "等待离线语音转写",
                "asr_offline",
            ),
            NodeWrite(
                "COURSE_OVERVIEW",
                NodeStatus.WAITING_PREREQUISITE,
                Priority.NORMAL,
                "等待语音转写完成",
                "text_analysis",
                ("ASR_TRANSCRIPTION",),
            ),
        ],
    )
    return tuple(node.id for node in nodes)


@contextmanager
def _reopened_repository(database: str) -> Iterator[CourseRepository]:
    if not database.endswith("_test"):
        raise ValueError("PostgreSQL 重连拒绝非 _test 数据库")
    engine = create_engine(_postgres_dsn(database))
    try:
        yield CourseRepository(engine)
    finally:
        engine.dispose()


def _outbox_envelope(record: OutboxRecord) -> bytes:
    return json.dumps(
        {
            "event_id": str(record.event_id),
            "aggregate_type": record.aggregate_type,
            "aggregate_id": record.aggregate_id,
            "event_type": record.event_type,
            "payload": record.payload,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def _course_event(task_id: str, event_id: UUID, submission_id: str) -> bytes:
    return json.dumps(
        {
            "event_id": str(event_id),
            "aggregate_type": "course_task_type",
            "aggregate_id": f"{task_id}:ASR",
            "event_type": "COURSE_TASK_REQUESTED",
            "payload": {
                "submission_id": submission_id,
                "task_id": task_id,
                "task_type": "ASR",
                "priority": "NORMAL",
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def _outbox_evidence(state: OutboxStateRecord) -> dict[str, Any]:
    return {
        "event_id": str(state.event_id),
        "published_at": (
            state.published_at.isoformat() if state.published_at is not None else None
        ),
        "publish_attempts": state.publish_attempts,
        "last_error": state.last_error,
        "claim_token": (
            str(state.claim_token) if state.claim_token is not None else None
        ),
        "claimed_at": (
            state.claimed_at.isoformat() if state.claimed_at is not None else None
        ),
    }


def _node_ids(repository: CourseRepository, task_id: str) -> tuple[int, ...]:
    task_types = repository.list_task_types(task_id)
    if len(task_types) != 1 or task_types[0].task_type is not TaskType.ASR:
        raise ValueError("隔离任务没有唯一 ASR task type")
    return tuple(node.id for node in repository.list_nodes(task_types[0].id))


def _metadata_offset(metadata: object) -> int:
    offset = getattr(metadata, "offset", None)
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("Kafka send_and_wait 未返回有效 offset")
    return offset


async def _committed_offset(group: str, message: KafkaMessage) -> int | None:
    admin = AIOKafkaAdminClient(
        bootstrap_servers=_KAFKA_BOOTSTRAP,
        client_id=f"m2b-offset-{uuid4().hex[:8]}",
    )
    await admin.start()
    try:
        offsets = await admin.list_consumer_group_offsets(group)
    finally:
        await admin.close()
    metadata = offsets.get(TopicPartition(message.topic, message.partition))
    if metadata is None or int(metadata.offset) < 0:
        return None
    return int(metadata.offset)


async def _run_until_pipeline_failure(
    loop: Any,
) -> RepositoryNotFoundError:
    deadline = asyncio.get_running_loop().time() + 10
    while asyncio.get_running_loop().time() < deadline:
        try:
            handled = await loop.run_once()
        except RepositoryNotFoundError as exc:
            return exc
        if handled:
            raise ValueError("缺失 PostgreSQL 任务事实时消息被错误提交")
    raise ValueError("Kafka Consumer 未观察到受控 PostgreSQL 处理失败")


async def _publish(
    producer: AioKafkaProducerAdapter,
    topic: str,
    value: bytes,
    event_id: UUID,
) -> int:
    metadata = await producer.send_and_wait(
        topic,
        value,
        str(event_id).encode(),
    )
    return _metadata_offset(metadata)


async def _real_pipeline_duplicate(
    scenario: Mapping[str, Any], repository: CourseRepository
) -> dict[str, Any]:
    from orchestrator_service.app.application.pipeline import PipelineInitializer

    database = str(scenario["database"])
    topic = str(scenario["kafka_topic"])
    group = str(scenario["kafka_group"])
    task_id = f"{scenario['component']}-task"
    event_id = uuid4()
    submission_id = _create_asr_task(repository, task_id)
    envelope = _course_event(task_id, event_id, submission_id)
    producer = AioKafkaProducerAdapter(
        bootstrap_servers=_KAFKA_BOOTSTRAP,
        client_id=f"m2b-inf008-producer-{uuid4().hex[:8]}",
    )
    await producer.start()
    try:
        sent_offsets = [
            await _publish(producer, topic, envelope, event_id),
            await _publish(producer, topic, envelope, event_id),
        ]
    finally:
        await producer.stop()

    consumer = AioKafkaConsumerAdapter(
        topics=[topic],
        bootstrap_servers=_KAFKA_BOOTSTRAP,
        group_id=group,
        client_id=f"m2b-inf008-consumer-{uuid4().hex[:8]}",
        max_poll_records=2,
    )
    await consumer.start()
    try:
        messages = await _poll_messages(consumer, 2)
        node_snapshots: list[tuple[int, ...]] = []
        for message in messages:
            with _reopened_repository(database) as reopened:
                nodes = await PipelineInitializer(reopened).handle(message.value)
                node_snapshots.append(tuple(node.id for node in nodes))
            await consumer.commit(message)
        committed = await _committed_offset(group, messages[-1])
    finally:
        await consumer.stop()
    if node_snapshots[0] != node_snapshots[1]:
        raise ValueError("重复 Kafka delivery 创建了重复 DAG")
    if committed != messages[-1].offset + 1:
        raise ValueError("重复 Kafka delivery 的最终 offset 未提交")
    with _reopened_repository(database) as persisted:
        persisted_ids = _node_ids(persisted, task_id)
        repository_name = type(persisted).__name__
    if persisted_ids != node_snapshots[-1]:
        raise ValueError("PostgreSQL 重连后的 DAG 与消费结果不一致")
    return {
        "database": database,
        "kafka_topic": topic,
        "kafka_group": group,
        "postgres_repository": repository_name,
        "kafka_producer": type(producer).__name__,
        "kafka_consumer": type(consumer).__name__,
        "sent_offsets": sent_offsets,
        "delivered_offsets": [message.offset for message in messages],
        "committed_offset": committed,
        "dag_node_ids": list(node_snapshots[0]),
        "replayed_dag_node_ids": list(node_snapshots[1]),
        "duplicate_nodes": node_snapshots[0] != node_snapshots[1],
    }


async def _real_consumer_failure_replay(
    scenario: Mapping[str, Any], repository: CourseRepository, engine: Engine
) -> dict[str, Any]:
    from orchestrator_service.app.application.pipeline import PipelineInitializer
    from orchestrator_service.app.infrastructure.runtime import (
        CourseCommandConsumerLoop,
    )

    database = str(scenario["database"])
    topic = str(scenario["kafka_topic"])
    group = str(scenario["kafka_group"])
    task_id = f"{scenario['component']}-task"
    event_id = uuid4()
    submission_id = str(uuid4())
    envelope = _course_event(task_id, event_id, submission_id)
    producer = AioKafkaProducerAdapter(
        bootstrap_servers=_KAFKA_BOOTSTRAP,
        client_id=f"m2b-inf009-producer-{uuid4().hex[:8]}",
    )
    await producer.start()
    try:
        sent_offset = await _publish(producer, topic, envelope, event_id)
    finally:
        await producer.stop()

    first_consumer = AioKafkaConsumerAdapter(
        topics=[topic],
        bootstrap_servers=_KAFKA_BOOTSTRAP,
        group_id=group,
        client_id=f"m2b-inf009-first-{uuid4().hex[:8]}",
        max_poll_records=1,
    )
    await first_consumer.start()
    try:
        with _reopened_repository(database) as first_repository:
            failure = await _run_until_pipeline_failure(
                CourseCommandConsumerLoop(
                    first_consumer,
                    PipelineInitializer(first_repository),
                    poll_timeout_seconds=0.25,
                )
            )
        failed_message = KafkaMessage(
            topic=topic,
            partition=0,
            offset=sent_offset,
            key=str(event_id).encode(),
            value=envelope,
            timestamp_ms=None,
        )
        committed_before = await _committed_offset(group, failed_message)
    finally:
        await first_consumer.stop()
    if committed_before is not None:
        raise ValueError("消费处理失败后 offset 被错误提交")

    _create_asr_task(repository, task_id)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE course_task_types "
                "SET submission_id = CAST(:submission_id AS uuid) "
                "WHERE task_id = :task_id AND task_type = 'ASR'"
            ),
            {"submission_id": submission_id, "task_id": task_id},
        )
    replay_consumer = AioKafkaConsumerAdapter(
        topics=[topic],
        bootstrap_servers=_KAFKA_BOOTSTRAP,
        group_id=group,
        client_id=f"m2b-inf009-replay-{uuid4().hex[:8]}",
        max_poll_records=1,
    )
    await replay_consumer.start()
    try:
        messages = await _poll_messages(replay_consumer, 1)
        replayed = messages[0]
        with _reopened_repository(database) as replay_repository:
            nodes = await PipelineInitializer(replay_repository).handle(replayed.value)
            repository_name = type(replay_repository).__name__
        await replay_consumer.commit(replayed)
        committed_after = await _committed_offset(group, replayed)
    finally:
        await replay_consumer.stop()
    if replayed.offset != sent_offset:
        raise ValueError("处理恢复后没有重投原 Kafka offset")
    if committed_after != replayed.offset + 1:
        raise ValueError("处理恢复后 Kafka offset 未提交")
    return {
        "database": database,
        "kafka_topic": topic,
        "kafka_group": group,
        "postgres_repository": repository_name,
        "kafka_producer": type(producer).__name__,
        "kafka_consumer": type(replay_consumer).__name__,
        "failure_type": type(failure).__name__,
        "failure": str(failure),
        "failed_offset": sent_offset,
        "committed_offset_before_recovery": committed_before,
        "redelivered_offset": replayed.offset,
        "committed_offset_after_recovery": committed_after,
        "dag_node_ids": [node.id for node in nodes],
    }


async def _real_outbox_failure(
    scenario: Mapping[str, Any], repository: CourseRepository, engine: Engine
) -> dict[str, Any]:
    from orchestrator_service.app.application.outbox import OutboxPublisher

    database = str(scenario["database"])
    topic = str(scenario["kafka_topic"])
    group = str(scenario["kafka_group"])
    task_id = f"{scenario['component']}-task"
    _create_asr_task(repository, task_id)
    with engine.begin() as connection:
        event_id = connection.execute(
            text(
                "SELECT event_id FROM outbox_events "
                "WHERE aggregate_id = :aggregate_id"
            ),
            {"aggregate_id": f"{task_id}:ASR"},
        ).scalar_one()
        connection.execute(
            text(
                "UPDATE outbox_events "
                "SET payload = CAST(:payload AS jsonb) "
                "WHERE event_id = :event_id"
            ),
            {
                "event_id": event_id,
                "payload": json.dumps(
                    {
                        "submission_id": f"submission-{event_id.hex[:12]}",
                        "task_id": task_id,
                        "task_type": "ASR",
                        "priority": "NORMAL",
                        "oversized_test_payload": "x" * 1_200_000,
                    },
                    separators=(",", ":"),
                ),
            },
        )
    producer = AioKafkaProducerAdapter(
        bootstrap_servers=_KAFKA_BOOTSTRAP,
        client_id=f"m2b-inf010-producer-{uuid4().hex[:8]}",
    )
    await producer.start()
    try:
        with _reopened_repository(database) as publisher_repository:
            publisher = OutboxPublisher(
                publisher_repository,
                producer,
                topic=topic,
                batch_size=1,
                poll_interval_seconds=0.01,
            )
            published = await publisher.publish_once()
            repository_name = type(publisher_repository).__name__
    finally:
        await producer.stop()
    with _reopened_repository(database) as persisted_repository:
        state = persisted_repository.get_outbox_state(event_id)
    if publisher.metrics.last_error_type not in {
        "MessageSizeTooLargeError",
        "RecordTooLargeError",
    }:
        raise ValueError(
            "Outbox 失败不是生产 Kafka send 的 record-too-large 错误: "
            f"{publisher.metrics.last_error_type}"
        )
    if (
        published != 0
        or state.published_at is not None
        or state.publish_attempts != 1
        or not state.last_error
    ):
        raise ValueError("Outbox send 失败事实未持久化")
    return {
        "database": database,
        "kafka_topic": topic,
        "kafka_group": group,
        "postgres_repository": repository_name,
        "kafka_producer": type(producer).__name__,
        "outbox_publisher": type(publisher).__name__,
        "send_error_type": publisher.metrics.last_error_type,
        "publish_once_returned": published,
        "published_count": publisher.metrics.published_total,
        "failed_total": publisher.metrics.failed_total,
        "outbox": _outbox_evidence(state),
    }


async def _real_publisher_duplicate(
    scenario: Mapping[str, Any], repository: CourseRepository, engine: Engine
) -> dict[str, Any]:
    from orchestrator_service.app.application.outbox import OutboxPublisher
    from orchestrator_service.app.application.pipeline import PipelineInitializer

    database = str(scenario["database"])
    topic = str(scenario["kafka_topic"])
    group = str(scenario["kafka_group"])
    task_id = f"{scenario['component']}-task"
    _create_asr_task(repository, task_id)
    first_record = repository.claim_outbox_events(1)[0]
    envelope = _outbox_envelope(first_record)
    producer = AioKafkaProducerAdapter(
        bootstrap_servers=_KAFKA_BOOTSTRAP,
        client_id=f"m2b-inf011-producer-{uuid4().hex[:8]}",
    )
    await producer.start()
    try:
        first_send_offset = await _publish(
            producer,
            topic,
            envelope,
            first_record.event_id,
        )
        with _reopened_repository(database) as before_repository:
            before_recovery = before_repository.get_outbox_state(first_record.event_id)
        if (
            before_recovery.published_at is not None
            or before_recovery.publish_attempts != 0
            or before_recovery.claim_token is None
        ):
            raise ValueError("Kafka send 后退出边界错误地标记了 Outbox")
        with engine.begin() as connection:
            updated = connection.execute(
                text(
                    "UPDATE outbox_events "
                    "SET claimed_at = now() - interval '6 minutes' "
                    "WHERE event_id = :event_id AND published_at IS NULL"
                ),
                {"event_id": first_record.event_id},
            ).rowcount
        if updated != 1:
            raise ValueError("无法推进受控 Outbox claim 过期事实")
        with _reopened_repository(database) as restarted_repository:
            restarted_publisher = OutboxPublisher(
                restarted_repository,
                producer,
                topic=topic,
                batch_size=1,
                poll_interval_seconds=0.01,
            )
            second_published = await restarted_publisher.publish_once()
            repository_name = type(restarted_repository).__name__
    finally:
        await producer.stop()
    with _reopened_repository(database) as persisted_repository:
        after_recovery = persisted_repository.get_outbox_state(first_record.event_id)
    if (
        second_published != 1
        or after_recovery.published_at is None
        or after_recovery.publish_attempts != 1
        or after_recovery.last_error is not None
    ):
        raise ValueError("Publisher 重启后 Outbox 未按生产路径完成")

    consumer = AioKafkaConsumerAdapter(
        topics=[topic],
        bootstrap_servers=_KAFKA_BOOTSTRAP,
        group_id=group,
        client_id=f"m2b-inf011-consumer-{uuid4().hex[:8]}",
        max_poll_records=2,
    )
    await consumer.start()
    try:
        messages = await _poll_messages(consumer, 2)
        node_snapshots: list[tuple[int, ...]] = []
        delivered_event_ids: list[str] = []
        for message in messages:
            delivered_event_ids.append(str(json.loads(message.value)["event_id"]))
            with _reopened_repository(database) as reopened:
                nodes = await PipelineInitializer(reopened).handle(message.value)
                node_snapshots.append(tuple(node.id for node in nodes))
            await consumer.commit(message)
        committed = await _committed_offset(group, messages[-1])
    finally:
        await consumer.stop()
    if (
        len(set(delivered_event_ids)) != 1
        or delivered_event_ids[0] != str(first_record.event_id)
        or node_snapshots[0] != node_snapshots[1]
    ):
        raise ValueError("Publisher 重投没有保持 event/DAG 幂等")
    return {
        "database": database,
        "kafka_topic": topic,
        "kafka_group": group,
        "postgres_repository": repository_name,
        "kafka_producer": type(producer).__name__,
        "kafka_consumer": type(consumer).__name__,
        "first_send_offset": first_send_offset,
        "delivered_offsets": [message.offset for message in messages],
        "delivered_event_ids": delivered_event_ids,
        "committed_offset": committed,
        "outbox_before_recovery": _outbox_evidence(before_recovery),
        "outbox_after_recovery": _outbox_evidence(after_recovery),
        "dag_node_ids": list(node_snapshots[0]),
        "replayed_dag_node_ids": list(node_snapshots[1]),
        "duplicate_dag": node_snapshots[0] != node_snapshots[1],
    }


async def _real_consumer_commit_exit(
    scenario: Mapping[str, Any], repository: CourseRepository
) -> dict[str, Any]:
    from orchestrator_service.app.application.pipeline import PipelineInitializer

    database = str(scenario["database"])
    topic = str(scenario["kafka_topic"])
    group = str(scenario["kafka_group"])
    task_id = f"{scenario['component']}-task"
    event_id = uuid4()
    submission_id = _create_asr_task(repository, task_id)
    envelope = _course_event(task_id, event_id, submission_id)
    producer = AioKafkaProducerAdapter(
        bootstrap_servers=_KAFKA_BOOTSTRAP,
        client_id=f"m2b-inf012-producer-{uuid4().hex[:8]}",
    )
    await producer.start()
    try:
        sent_offset = await _publish(producer, topic, envelope, event_id)
    finally:
        await producer.stop()
    first_consumer = AioKafkaConsumerAdapter(
        topics=[topic],
        bootstrap_servers=_KAFKA_BOOTSTRAP,
        group_id=group,
        client_id=f"m2b-inf012-first-{uuid4().hex[:8]}",
        max_poll_records=1,
    )
    await first_consumer.start()
    try:
        first_message = (await _poll_messages(first_consumer, 1))[0]
        with _reopened_repository(database) as first_repository:
            first_nodes = await PipelineInitializer(first_repository).handle(
                first_message.value
            )
        committed_before = await _committed_offset(group, first_message)
    finally:
        await first_consumer.stop()
    if committed_before is not None:
        raise ValueError("Consumer 退出前 offset 被错误提交")

    replay_consumer = AioKafkaConsumerAdapter(
        topics=[topic],
        bootstrap_servers=_KAFKA_BOOTSTRAP,
        group_id=group,
        client_id=f"m2b-inf012-replay-{uuid4().hex[:8]}",
        max_poll_records=1,
    )
    await replay_consumer.start()
    try:
        replayed = (await _poll_messages(replay_consumer, 1))[0]
        with _reopened_repository(database) as replay_repository:
            replayed_nodes = await PipelineInitializer(replay_repository).handle(
                replayed.value
            )
            repository_name = type(replay_repository).__name__
        await replay_consumer.commit(replayed)
        committed_after = await _committed_offset(group, replayed)
    finally:
        await replay_consumer.stop()
    first_ids = tuple(node.id for node in first_nodes)
    replayed_ids = tuple(node.id for node in replayed_nodes)
    if first_message.offset != replayed.offset or first_ids != replayed_ids:
        raise ValueError("Consumer 重启没有幂等重放原消息")
    if committed_after != replayed.offset + 1:
        raise ValueError("Consumer 重放成功后 offset 未提交")
    return {
        "database": database,
        "kafka_topic": topic,
        "kafka_group": group,
        "postgres_repository": repository_name,
        "kafka_producer": type(producer).__name__,
        "kafka_consumer": type(replay_consumer).__name__,
        "sent_offset": sent_offset,
        "first_delivered_offset": first_message.offset,
        "committed_offset_before_exit": committed_before,
        "replayed_offset": replayed.offset,
        "committed_offset_after_replay": committed_after,
        "dag_node_ids": list(first_ids),
        "replayed_dag_node_ids": list(replayed_ids),
        "duplicate_dag": first_ids != replayed_ids,
    }


async def _execute_real_flow_probe(
    name: str, scenario: Mapping[str, Any]
) -> dict[str, Any]:
    topic = str(scenario["kafka_topic"])
    group = str(scenario["kafka_group"])
    await _prepare_kafka_resources(topic, group)
    try:
        with _isolated_database(scenario, migrate=True) as engine:
            repository = CourseRepository(engine)
            if name == "pipeline_duplicate":
                return await _real_pipeline_duplicate(scenario, repository)
            if name == "consumer_failure_replay":
                return await _real_consumer_failure_replay(
                    scenario,
                    repository,
                    engine,
                )
            if name == "outbox_failure":
                return await _real_outbox_failure(scenario, repository, engine)
            if name == "publisher_duplicate":
                return await _real_publisher_duplicate(scenario, repository, engine)
            if name == "consumer_commit_exit":
                return await _real_consumer_commit_exit(scenario, repository)
            raise ValueError(f"unknown real infrastructure flow: {name}")
    finally:
        await _reset_kafka_resources(topic, group)


def _run_real_flow_probe(
    name: str, scenario: Mapping[str, Any]
) -> dict[str, Any]:
    source = (
        "import asyncio,importlib.util,json,sys;"
        "from pathlib import Path;"
        "service_root=Path.cwd().parent/'orchestrator_service';"
        "spec=importlib.util.spec_from_file_location("
        "'orchestrator_service',service_root/'__init__.py',"
        "submodule_search_locations=[str(service_root)]);"
        "assert spec is not None and spec.loader is not None;"
        "service=importlib.util.module_from_spec(spec);"
        "sys.modules['orchestrator_service']=service;"
        "spec.loader.exec_module(service);"
        "from scripts.milestone_2b_case_runners.infrastructure "
        "import _execute_real_flow_probe;"
        "result=asyncio.run(_execute_real_flow_probe(sys.argv[1],json.loads(sys.argv[2])));"
        "print(json.dumps(result,ensure_ascii=False,sort_keys=True))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", source, name, json.dumps(dict(scenario))],
        cwd=_PLATFORM_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=280,
    )
    if completed.returncode != 0:
        raise ValueError(
            "real PostgreSQL/Kafka flow failed: " + completed.stderr.strip()
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("real PostgreSQL/Kafka flow did not return strict JSON") from exc
    if (
        not isinstance(result, dict)
        or result.get("database") != scenario.get("database")
        or result.get("kafka_topic") != scenario.get("kafka_topic")
        or result.get("kafka_group") != scenario.get("kafka_group")
    ):
        raise ValueError("real PostgreSQL/Kafka flow evidence is incomplete")
    return cast(dict[str, Any], result)


def _check_inf_001(scenario: Mapping[str, Any]) -> dict[str, Any]:
    del scenario
    return _run_production_readiness_probe("postgres_down")


def _check_inf_002(scenario: Mapping[str, Any]) -> dict[str, Any]:
    del scenario
    return _run_production_readiness_probe("postgres_auth")


def _check_inf_003(scenario: Mapping[str, Any]) -> dict[str, Any]:
    del scenario
    return _run_production_readiness_probe("schema_missing")


def _check_inf_004(_: Mapping[str, Any]) -> dict[str, Any]:
    return _run_production_readiness_probe("redis_down")


def _check_inf_005(scenario: Mapping[str, Any]) -> dict[str, Any]:
    prefix = str(scenario["redis_prefix"])
    instance_id = f"{scenario['component']}-instance"
    client = Redis.from_url("redis://127.0.0.1:6379/15", decode_responses=True)
    try:
        client.ping()
        _cleanup_redis_prefix(client, prefix)
        registry = RedisOperatorRegistry(
            client, heartbeat_ttl_seconds=15, key_prefix=prefix
        )

        async def check() -> dict[str, Any]:
            request_sequence: list[str] = []
            registration_count = 0
            recovered = asyncio.Event()

            def handler(request: httpx.Request) -> httpx.Response:
                nonlocal registration_count
                request_sequence.append(request.url.path)
                payload = json.loads(request.content)
                if request.url.path.endswith("/register"):
                    registration_count += 1
                    registered = registry.register(
                        _registry_instance(str(payload["instance_id"]))
                    )
                    return httpx.Response(
                        201, json={"instance_id": registered.instance_id}
                    )
                if request.url.path.endswith("/heartbeat"):
                    try:
                        heartbeat = registry.heartbeat(
                            str(payload["instance_id"]),
                            inflight=int(payload["inflight"]),
                            model_ready=bool(payload["model_ready"]),
                        )
                    except OperatorInstanceNotFoundError:
                        return httpx.Response(404, json={"detail": "instance missing"})
                    if registration_count > 1:
                        recovered.set()
                    return httpx.Response(
                        200, json={"instance_id": heartbeat.instance_id}
                    )
                if request.url.path.endswith("/lifecycle"):
                    updated = registry.set_lifecycle(
                        str(payload["instance_id"]),
                        OperatorLifecycle(str(payload["lifecycle"])),
                    )
                    return httpx.Response(
                        200, json={"instance_id": updated.instance_id}
                    )
                if request.url.path.endswith("/unregister"):
                    registry.unregister(str(payload["instance_id"]))
                    return httpx.Response(200, json={"status": "unregistered"})
                raise AssertionError(request.url.path)

            http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            registry_client = OperatorRegistryClient(
                OperatorRegistryClientConfig(
                    control_service_url="http://control-service:18100",
                    instance_id=instance_id,
                    operator_code="vbas",
                    capabilities=["teacher_behavior"],
                    service_url="http://127.0.0.1:18981",
                    declared_capacity=1,
                    management_token=os.getenv(
                        "OPERATOR_REGISTRY_TOKEN",
                        "local-development-registry-token",
                    ),
                    labels={"gpu": "0"},
                    heartbeat_interval_seconds=0.01,
                ),
                status_provider=lambda: OperatorRuntimeStatus(
                    inflight=0, model_ready=True
                ),
                http_client=http_client,
            )
            try:
                await registry_client.start()
                initial_instances = registry.list_instances()
                if not initial_instances:
                    raise ValueError("Redis 临时注册态建立失败")
                _cleanup_redis_prefix(client, prefix)
                lost_instances = registry.list_instances()
                if lost_instances:
                    raise ValueError("Redis 临时注册态清除后仍存在")
                await asyncio.wait_for(recovered.wait(), timeout=1)
                recovered_instances = registry.list_instances()
                recovered_heartbeat = next(
                    instance
                    for instance in recovered_instances
                    if instance.instance_id == instance_id
                )
                lease = registry.lease("teacher_behavior", 30)
                return {
                    "production_client": type(registry_client).__name__,
                    "request_sequence": request_sequence,
                    "initial_instance_ids": [
                        instance.instance_id for instance in initial_instances
                    ],
                    "instance_ids_after_loss": [
                        instance.instance_id for instance in lost_instances
                    ],
                    "recovered_instance_ids": [
                        instance.instance_id for instance in recovered_instances
                    ],
                    "recovered_heartbeat_instance_id": (
                        recovered_heartbeat.instance_id
                    ),
                    "lease_id": lease.lease_id,
                    "lease_instance_id": lease.instance_id,
                }
            finally:
                await registry_client.stop()
                await registry_client.aclose()

        return asyncio.run(check())
    finally:
        _cleanup_redis_prefix(client, prefix)
        client.close()


def _check_inf_006(_: Mapping[str, Any]) -> dict[str, Any]:
    return _run_production_readiness_probe("kafka_down")


def _check_inf_007(scenario: Mapping[str, Any]) -> dict[str, Any]:
    async def check() -> dict[str, Any]:
        topic = str(scenario["kafka_topic"])
        manager = KafkaTopicManager(
            bootstrap_servers=_KAFKA_BOOTSTRAP,
            client_id=f"m2b-missing-{uuid4().hex[:8]}",
            topics=(topic,),
        )
        try:
            await manager.validate_topics()
        except RuntimeError as exc:
            return {
                "topic": topic,
                "startup_validation": "failed",
                "validation_rejection": type(exc).__name__,
                "topic_manager": type(manager).__name__,
                "dependency_reason": str(exc),
            }
        raise ValueError("缺失 Kafka Topic 未被生产启动校验拒绝")

    return asyncio.run(check())


def _check_inf_008(scenario: Mapping[str, Any]) -> dict[str, Any]:
    return _run_real_flow_probe("pipeline_duplicate", scenario)


def _check_inf_009(scenario: Mapping[str, Any]) -> dict[str, Any]:
    return _run_real_flow_probe("consumer_failure_replay", scenario)


def _check_inf_010(scenario: Mapping[str, Any]) -> dict[str, Any]:
    return _run_real_flow_probe("outbox_failure", scenario)


def _check_inf_011(scenario: Mapping[str, Any]) -> dict[str, Any]:
    return _run_real_flow_probe("publisher_duplicate", scenario)


def _check_inf_012(scenario: Mapping[str, Any]) -> dict[str, Any]:
    return _run_real_flow_probe("consumer_commit_exit", scenario)


def _check_inf_013(_: Mapping[str, Any]) -> dict[str, Any]:
    return _run_production_readiness_probe("mongodb_down")


def _check_inf_014(scenario: Mapping[str, Any]) -> dict[str, Any]:
    return _run_production_readiness_probe("mongodb_auth", scenario)


def _check_inf_015(scenario: Mapping[str, Any]) -> dict[str, Any]:
    return _run_production_embedding_probe(scenario)


def _check_inf_016(scenario: Mapping[str, Any]) -> dict[str, Any]:
    del scenario
    return _run_production_message_contract_probe()


InfrastructureChecker = Callable[[Mapping[str, Any]], dict[str, Any]]
_INFRASTRUCTURE_CHECKERS: Mapping[str, InfrastructureChecker] = {
    "INF-001": _check_inf_001,
    "INF-002": _check_inf_002,
    "INF-003": _check_inf_003,
    "INF-004": _check_inf_004,
    "INF-005": _check_inf_005,
    "INF-006": _check_inf_006,
    "INF-007": _check_inf_007,
    "INF-008": _check_inf_008,
    "INF-009": _check_inf_009,
    "INF-010": _check_inf_010,
    "INF-011": _check_inf_011,
    "INF-012": _check_inf_012,
    "INF-013": _check_inf_013,
    "INF-014": _check_inf_014,
    "INF-015": _check_inf_015,
    "INF-016": _check_inf_016,
}


def evaluate_scenario(case_id: str, scenario: Mapping[str, Any]) -> dict[str, Any]:
    spec = CASE_SPECS.get(case_id)
    checker = _INFRASTRUCTURE_CHECKERS.get(case_id)
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
            "reason": "基础设施输入与固定 checker 不匹配",
            "observed": {"input_valid": False},
        }
    try:
        _require_scenario(case_id, scenario)
    except ValueError as exc:
        return {
            "case_id": case_id,
            "status": "失败",
            "reason": f"基础设施输入未绑定当前 run：{exc}",
            "observed": {"input_valid": False, "detail": str(exc)},
        }
    try:
        observed = checker(scenario)
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "case_id": case_id,
            "status": "失败",
            "reason": f"基础设施 checker 未观察到目标状态：{exc}",
            "observed": {
                "input_valid": True,
                "checker": checker.__name__,
                "detail": str(exc),
            },
        }
    return {
        "case_id": case_id,
        "status": spec.status,
        "reason": spec.reason,
        "observed": {"checker": checker.__name__, **observed},
    }


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
            "reason": f"基础设施 checker 输入失败：{exc}",
            "observed": {"input_valid": False},
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "通过" else 1


if __name__ == "__main__":
    raise SystemExit(checker_main())
