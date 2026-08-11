from __future__ import annotations

import asyncio
import io
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

import httpx
import psycopg
import pytest
import redis
from aiokafka import AIOKafkaConsumer  # type: ignore[import-untyped]
from aiokafka.admin import AIOKafkaAdminClient  # type: ignore[import-untyped]
from aiokafka.structs import TopicPartition  # type: ignore[import-untyped]
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from packages.platform_common.kafka import AioKafkaProducerAdapter

pytestmark = pytest.mark.integration

PLATFORM_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PLATFORM_ROOT.parent
PYTHON = PLATFORM_ROOT / ".venv" / "bin" / "python"
BOOTSTRAP_SERVERS = ["127.0.0.1:9092"]
DEFAULT_POSTGRES_TEMPLATE_DSN = (
    "postgresql+psycopg://algorithm:algorithm@127.0.0.1:5432/algorithm_control_milestone2a_test"
)


class MilestonePostgres(Protocol):
    @property
    def dsn(self) -> str: ...

    @property
    def raw_dsn(self) -> str: ...


@dataclass(frozen=True)
class StrictMilestone2APostgres:
    dsn: str
    raw_dsn: str


def _raw_psycopg_dsn(dsn: str) -> str:
    url = make_url(dsn)
    return url.set(drivername=url.drivername.split("+", 1)[0]).render_as_string(hide_password=False)


def _unique_database_name(template_dsn: str) -> str:
    template_name = make_url(template_dsn).database or "algorithm_control_milestone2a"
    base = re.sub(r"[^A-Za-z0-9_]+", "_", template_name.removesuffix("_test"))
    worker = re.sub(r"[^A-Za-z0-9_]+", "_", os.getenv("PYTEST_XDIST_WORKER", "main"))
    suffix = f"_{worker[:12]}_{uuid4().hex[:8]}_test"
    base = base[: 63 - len(suffix)].rstrip("_") or "milestone2a"
    database_name = f"{base}{suffix}"
    assert database_name.endswith("_test")
    assert len(database_name.encode("ascii")) <= 63
    return database_name


@pytest.fixture
def milestone2a_postgres() -> Iterator[StrictMilestone2APostgres]:
    template_dsn = os.getenv(
        "PLATFORM_MILESTONE2A_TEST_POSTGRES_DSN",
        DEFAULT_POSTGRES_TEMPLATE_DSN,
    )
    database_name = _unique_database_name(template_dsn)
    dsn = make_url(template_dsn).set(database=database_name).render_as_string(hide_password=False)
    raw_dsn = _raw_psycopg_dsn(dsn)
    admin_dsn = _raw_psycopg_dsn(
        os.getenv(
            "PLATFORM_TEST_POSTGRES_ADMIN_DSN",
            make_url(raw_dsn).set(database="postgres").render_as_string(hide_password=False),
        )
    )
    database_created = False
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        database_created = True
        migration_paths = sorted((PLATFORM_ROOT / "migrations").glob("*.sql"))
        assert migration_paths, "PostgreSQL 集成测试未找到迁移文件"
        with psycopg.connect(raw_dsn, autocommit=True) as connection:
            for migration_path in migration_paths:
                connection.execute(migration_path.read_text(encoding="utf-8"))
        yield StrictMilestone2APostgres(dsn=dsn, raw_dsn=raw_dsn)
    finally:
        if database_created:
            assert database_name.endswith("_test"), (
                f"里程碑 2A Harness 拒绝删除非 _test 数据库: {database_name!r}"
            )
            with psycopg.connect(admin_dsn, autocommit=True) as admin:
                admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (database_name,),
                )
                admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))


@dataclass
class UvicornRun:
    sequence: int
    pid: int
    health_response: dict[str, Any] | None = None
    stop_log: str | None = None
    exit_code: int | None = None
    forced_kill: bool = False
    unexpected_exit_before_stop: bool = False

    def evidence(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "pid": self.pid,
            "health_response": self.health_response,
            "stop_log": self.stop_log,
            "exit_code": self.exit_code,
            "forced_kill": self.forced_kill,
            "unexpected_exit_before_stop": self.unexpected_exit_before_stop,
        }


@dataclass
class UvicornProcess:
    app: str
    port: int
    env: dict[str, str]
    cwd: Path = WORKSPACE_ROOT
    process: subprocess.Popen[str] | None = None
    runs: list[UvicornRun] = field(default_factory=list)

    def start(self) -> UvicornRun:
        if self.process is not None:
            raise RuntimeError(f"Uvicorn 已启动: {self.app}")
        process_env = os.environ.copy()
        process_env.update(self.env)
        self.process = subprocess.Popen(
            [
                str(PYTHON),
                "-m",
                "uvicorn",
                self.app,
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--workers",
                "1",
                "--log-level",
                "warning",
            ],
            cwd=self.cwd,
            env=process_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        run = UvicornRun(sequence=len(self.runs) + 1, pid=self.process.pid)
        self.runs.append(run)
        run.health_response = _wait_http(
            f"http://127.0.0.1:{self.port}/health",
            timeout_seconds=20,
            process=self,
        )
        return run

    def stop(self) -> UvicornRun | None:
        if self.process is None:
            return None
        process = self.process
        run = self.runs[-1]
        assert run.pid == process.pid
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                run.forced_kill = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
        else:
            run.unexpected_exit_before_stop = True
        output = process.stdout.read() if process.stdout is not None else ""
        run.stop_log = output
        run.exit_code = process.returncode
        self.process = None
        return run


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http(
    url: str,
    *,
    timeout_seconds: float,
    process: UvicornProcess | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process is not None and process.process is not None:
            if process.process.poll() is not None:
                stopped_run = process.stop()
                output = stopped_run.stop_log if stopped_run is not None else ""
                raise AssertionError(f"Uvicorn 在等待 {url} 时退出:\n{output}")
        try:
            response = httpx.get(url, timeout=1)
            if response.status_code == 200:
                payload = response.json()
                return payload if isinstance(payload, dict) else {"payload": payload}
            last_error = RuntimeError(
                f"HTTP {response.status_code}: {response.text[:200]}"
            )
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
        time.sleep(0.05)
    raise AssertionError(f"等待 HTTP 端点超时: {url}; last_error={last_error!r}")


def test_wait_http_rejects_non_200_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *args, **kwargs: httpx.Response(
            404,
            json={"detail": "Not Found"},
        ),
    )

    with pytest.raises(AssertionError, match="等待 HTTP 端点超时"):
        _wait_http("http://stub.invalid/health", timeout_seconds=0.01)


class _RaceProcess:
    pid = 12345
    returncode: int | None = None
    stdout = io.StringIO("race-stop-log")

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float) -> int:
        self.returncode = 0
        return 0


def test_uvicorn_stop_tolerates_process_lookup_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = UvicornProcess("tests.fake:app", 12345, {})
    process.process = _RaceProcess()  # type: ignore[assignment]
    process.runs.append(UvicornRun(sequence=1, pid=12345))
    monkeypatch.setattr(os, "killpg", lambda *args: (_ for _ in ()).throw(ProcessLookupError()))

    run = process.stop()

    assert run is not None
    assert run.forced_kill is False
    assert run.unexpected_exit_before_stop is False
    assert run.exit_code == 0


class _ExitedProcess(_RaceProcess):
    returncode = 7

    def poll(self) -> int:
        return 7


def test_uvicorn_stop_marks_process_that_exited_before_stop() -> None:
    process = UvicornProcess("tests.fake:app", 12345, {})
    process.process = _ExitedProcess()  # type: ignore[assignment]
    process.runs.append(UvicornRun(sequence=1, pid=12345))

    run = process.stop()

    assert run is not None
    assert run.unexpected_exit_before_stop is True
    assert run.forced_kill is False
    assert run.exit_code == 7


def test_uvicorn_run_rejects_unknown_nonzero_exit_code() -> None:
    run = UvicornRun(
        sequence=1,
        pid=12345,
        stop_log="unknown-exit",
        exit_code=7,
    )

    with pytest.raises(AssertionError, match="不允许的退出码"):
        _assert_clean_uvicorn_run(run)


def _assert_clean_uvicorn_run(run: UvicornRun) -> None:
    assert run.stop_log is not None
    assert "Traceback" not in run.stop_log
    assert run.forced_kill is False
    assert run.unexpected_exit_before_stop is False
    assert run.exit_code in {0, -signal.SIGTERM}, (
        f"Uvicorn 不允许的退出码: {run.exit_code}"
    )


def test_cleanup_steps_continue_after_an_earlier_failure() -> None:
    calls: list[str] = []

    def fail() -> None:
        calls.append("first")
        raise RuntimeError("first cleanup failed")

    def succeed() -> None:
        calls.append("second")

    errors = _run_cleanup_steps((("first", fail), ("second", succeed)))

    assert calls == ["first", "second"]
    assert len(errors) == 1
    assert errors[0][0] == "first"


CleanupError = tuple[str, Exception]


def _run_cleanup_steps(
    steps: tuple[tuple[str, Callable[[], object]], ...],
) -> list[CleanupError]:
    errors: list[CleanupError] = []
    for name, operation in steps:
        try:
            operation()
        except Exception as exc:
            errors.append((name, exc))
    return errors


async def _run_async_cleanup_steps(
    steps: tuple[tuple[str, Callable[[], Awaitable[object]]], ...],
) -> list[CleanupError]:
    errors: list[CleanupError] = []
    for name, operation in steps:
        try:
            await operation()
        except Exception as exc:
            errors.append((name, exc))
    return errors


def _report_cleanup_errors(
    errors: list[CleanupError],
    original_error: BaseException | None,
) -> None:
    if not errors:
        return
    if original_error is not None:
        for name, error in errors:
            original_error.add_note(f"清理步骤 {name} 失败: {error!r}")
        return
    raise ExceptionGroup(
        "里程碑 2A Harness 清理失败",
        [RuntimeError(f"{name}: {error!r}") for name, error in errors],
    )


@pytest.mark.asyncio
async def test_runtime_topic_cleanup_rejects_non_test_topic_before_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailIfConstructed:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("Kafka Admin 不应被构造")

    monkeypatch.setattr(sys.modules[__name__], "AIOKafkaAdminClient", FailIfConstructed)

    with pytest.raises(AssertionError, match="拒绝删除非里程碑 2A Topic"):
        await _delete_topics("algorithm.course.commands")


def _poll(
    operation: Callable[[], Any],
    predicate: Callable[[Any], bool],
    *,
    timeout_seconds: float = 20,
    interval_seconds: float = 0.05,
    message: str,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    last_value: Any = None
    while time.monotonic() < deadline:
        last_value = operation()
        if predicate(last_value):
            return last_value
        time.sleep(interval_seconds)
    raise AssertionError(f"{message}; last_value={last_value!r}")


def _task(payload: dict[str, Any], task_type: str = "ASR") -> dict[str, Any]:
    return next(item for item in payload["data"]["tasks"] if item["task_type"] == task_type)


def _status_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    task = _task(payload)
    return {
        "task_status": task["status"],
        "nodes": {node["node_code"]: node["status"] for node in task["nodes"]},
    }


def _infrastructure_evidence(engine: Any) -> dict[str, Any]:
    compose = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(PLATFORM_ROOT / "deploy" / "docker-compose.infrastructure.yml"),
            "ps",
            "--format",
            "json",
        ],
        cwd=PLATFORM_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    containers = [json.loads(line) for line in compose.stdout.splitlines() if line]
    required = {"postgres", "redis", "kafka"}
    observed = {container["Service"] for container in containers}
    assert required <= observed
    assert all(
        container["State"] == "running" and container["Health"] == "healthy"
        for container in containers
        if container["Service"] in required
    )
    with engine.connect() as connection:
        postgres_version = connection.execute(text("SHOW server_version")).scalar_one()
    redis_client = redis.Redis.from_url("redis://127.0.0.1:6379/14", decode_responses=True)
    try:
        server_info = cast(dict[str, Any], redis_client.info("server"))
        redis_version = str(server_info["redis_version"])
    finally:
        redis_client.close()
    return {
        "containers": [
            {
                "service": container["Service"],
                "image": container["Image"],
                "state": container["State"],
                "health": container["Health"],
            }
            for container in containers
            if container["Service"] in required
        ],
        "postgres_server_version": postgres_version,
        "redis_server_version": redis_version,
    }


async def _group_offsets(group_id: str) -> dict[str, int]:
    admin = AIOKafkaAdminClient(
        bootstrap_servers=BOOTSTRAP_SERVERS,
        client_id=f"milestone-2a-evidence-{uuid4().hex[:8]}",
    )
    await admin.start()
    try:
        offsets = await admin.list_consumer_group_offsets(group_id)
        return {
            f"{partition.topic}:{partition.partition}": int(metadata.offset)
            for partition, metadata in offsets.items()
        }
    finally:
        await admin.close()


async def _topic_end_offsets(topic: str) -> dict[str, int]:
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=BOOTSTRAP_SERVERS,
        client_id=f"milestone-2a-end-offset-{uuid4().hex[:8]}",
        enable_auto_commit=False,
    )
    await consumer.start()
    try:
        partitions = consumer.partitions_for_topic(topic)
        assert partitions is not None, f"Kafka Topic 不存在: {topic}"
        topic_partitions = [TopicPartition(topic, partition) for partition in partitions]
        offsets = await consumer.end_offsets(topic_partitions)
        return {
            f"{partition.topic}:{partition.partition}": int(offset)
            for partition, offset in offsets.items()
        }
    finally:
        await consumer.stop()


def _lease_snapshot(client: redis.Redis, prefix: str) -> dict[str, Any]:
    lease_keys = list(client.scan_iter(match=f"{prefix}lease:*"))
    instance_lease_sets = list(client.scan_iter(match=f"{prefix}leases:*"))
    return {
        "lease_keys": lease_keys,
        "instance_lease_counts": {
            key: cast(int, client.scard(key)) for key in instance_lease_sets
        },
    }


def _leases_released(snapshot: dict[str, Any]) -> bool:
    return not snapshot["lease_keys"] and all(
        count == 0 for count in snapshot["instance_lease_counts"].values()
    )


async def _consumer_groups() -> set[str]:
    admin = AIOKafkaAdminClient(
        bootstrap_servers=BOOTSTRAP_SERVERS,
        client_id=f"milestone-2a-group-list-{uuid4().hex[:8]}",
    )
    await admin.start()
    try:
        return {str(row[0]) for row in await admin.list_consumer_groups()}
    finally:
        await admin.close()


async def _delete_consumer_group(group_id: str) -> bool:
    if re.fullmatch(r"algorithm-test-milestone2a-[0-9a-f]{32}", group_id) is None:
        raise AssertionError(f"拒绝删除非里程碑 2A Consumer Group: {group_id}")
    if group_id not in await _consumer_groups():
        return False
    await asyncio.to_thread(
        subprocess.run,
        [
            "docker",
            "compose",
            "-f",
            str(PLATFORM_ROOT / "deploy" / "docker-compose.infrastructure.yml"),
            "exec",
            "-T",
            "kafka",
            "/opt/kafka/bin/kafka-consumer-groups.sh",
            "--bootstrap-server",
            "localhost:9092",
            "--delete",
            "--group",
            group_id,
        ],
        cwd=PLATFORM_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    deadline = asyncio.get_running_loop().time() + 10
    while asyncio.get_running_loop().time() < deadline:
        if group_id not in await _consumer_groups():
            return True
        await asyncio.sleep(0.05)
    raise AssertionError(f"Kafka Consumer Group 删除后仍存在: {group_id}")


@pytest.mark.asyncio
async def test_consumer_group_cleanup_rejects_non_harness_group() -> None:
    with pytest.raises(AssertionError, match="拒绝删除非里程碑 2A"):
        await _delete_consumer_group("algorithm-production-consumer")


async def _publish_duplicate(topic: str, envelope: dict[str, Any]) -> None:
    producer = AioKafkaProducerAdapter(
        bootstrap_servers=BOOTSTRAP_SERVERS,
        client_id=f"milestone-2a-duplicate-{uuid4().hex[:8]}",
    )
    await producer.start()
    try:
        await producer.send_and_wait(
            topic,
            json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode(),
            str(envelope["event_id"]).encode(),
        )
    finally:
        await producer.stop()


async def _delete_topics(*topics: str) -> None:
    if len(topics) != 3:
        raise AssertionError(f"拒绝删除非里程碑 2A Topic 集合: {topics!r}")
    base_match = re.fullmatch(
        r"algorithm\.test\.milestone2a\.runtime\.([0-9a-f]{32})",
        topics[0],
    )
    if base_match is None:
        raise AssertionError(f"拒绝删除非里程碑 2A Topic: {topics[0]}")
    expected = (
        topics[0],
        f"{topics[0]}.visual.commands",
        f"{topics[0]}.visual.events",
    )
    if topics != expected:
        raise AssertionError(f"拒绝删除 UUID 不一致的里程碑 2A Topic: {topics!r}")
    admin = AIOKafkaAdminClient(
        bootstrap_servers=BOOTSTRAP_SERVERS,
        client_id=f"milestone-2a-cleanup-{uuid4().hex[:8]}",
    )
    await admin.start()
    try:
        existing = await admin.list_topics()
        selected = [topic for topic in topics if topic in existing]
        if selected:
            await admin.delete_topics(selected)
        deadline = asyncio.get_running_loop().time() + 10
        while asyncio.get_running_loop().time() < deadline:
            remaining = set(topics) & set(await admin.list_topics())
            if not remaining:
                return
            await asyncio.sleep(0.05)
        raise AssertionError(f"Kafka Topic 删除后仍存在: {sorted(remaining)}")
    finally:
        await admin.close()


async def _cleanup_kafka_resources(group_id: str, *topics: str) -> bool:
    result: dict[str, bool] = {"group_deleted": False}

    async def delete_group() -> None:
        result["group_deleted"] = await _delete_consumer_group(group_id)

    errors = await _run_async_cleanup_steps(
        (
            ("consumer_group", delete_group),
            ("topics", lambda: _delete_topics(*topics)),
        )
    )
    if group_id in await _consumer_groups():
        errors.append(
            ("consumer_group_verify", RuntimeError(f"Group 仍存在: {group_id}"))
        )
    _report_cleanup_errors(errors, None)
    return result["group_deleted"]


@contextmanager
def _services(
    postgres: MilestonePostgres,
) -> Iterator[tuple[UvicornProcess, UvicornProcess, UvicornProcess, dict[str, Any]]]:
    suffix = uuid4().hex
    control_port, orchestrator_port, stub_port = (_free_port() for _ in range(3))
    redis_prefix = f"milestone-2a:{suffix}:"
    topic = f"algorithm.test.milestone2a.runtime.{suffix}"
    visual_command_topic = f"{topic}.visual.commands"
    visual_event_topic = f"{topic}.visual.events"
    group = f"algorithm-test-milestone2a-{suffix}"
    storage = tempfile.TemporaryDirectory(prefix=f"milestone-2a-{suffix[:8]}-")
    course_root = str(Path(storage.name) / "course")
    result_root = str(Path(storage.name) / "result")
    storage_env = {
        "PLATFORM_COURSE_ROOT": course_root,
        "PLATFORM_RESULT_ROOT": result_root,
    }
    control = UvicornProcess(
        "control_service.app.main:app",
        control_port,
        {
            "CONTROL_POSTGRES__DSN": postgres.dsn,
            "CONTROL_REDIS__URL": "redis://127.0.0.1:6379/14",
            "CONTROL_REDIS__KEY_PREFIX": redis_prefix,
            "CONTROL_REDIS__HEARTBEAT_TTL_SECONDS": "30",
            "CONTROL_SERVICE__ENVIRONMENT": "test",
            **storage_env,
        },
    )
    stub = UvicornProcess(
        "tests.stubs.operator_stub:app",
        stub_port,
        {"MILESTONE_2A_STUB_DELAY_SECONDS": "0.25", **storage_env},
        cwd=PLATFORM_ROOT,
    )
    orchestrator_env = {
        "ORCHESTRATOR_POSTGRES__DSN": postgres.dsn,
        "ORCHESTRATOR_KAFKA__BOOTSTRAP_SERVERS": json.dumps(BOOTSTRAP_SERVERS),
        "ORCHESTRATOR_KAFKA__COURSE_COMMAND_TOPIC": topic,
        "ORCHESTRATOR_KAFKA__VISUAL_COMMAND_TOPIC": visual_command_topic,
        "ORCHESTRATOR_KAFKA__VISUAL_EVENT_TOPIC": visual_event_topic,
        "ORCHESTRATOR_KAFKA__COURSE_CONSUMER_GROUP": group,
        "ORCHESTRATOR_KAFKA__CLIENT_ID": f"milestone-2a-{suffix[:8]}",
        "ORCHESTRATOR_KAFKA__POLL_TIMEOUT_SECONDS": "0.05",
        "ORCHESTRATOR_OUTBOX__POLL_INTERVAL_SECONDS": "0.05",
        "ORCHESTRATOR_WORKER__CLAIM_POLL_INTERVAL_SECONDS": "0.05",
        "ORCHESTRATOR_WORKER__NODE_CONCURRENCY": "2",
        "ORCHESTRATOR_CONTROL__BASE_URL": f"http://127.0.0.1:{control_port}",
        "ORCHESTRATOR_CONTROL__DEFAULT_LEASE_TTL_SECONDS": "10",
        "ORCHESTRATOR_SERVICE__ENVIRONMENT": "test",
        "ORCHESTRATOR_STORAGE__COURSE_ROOT": course_root,
        "ORCHESTRATOR_STORAGE__RESULT_ROOT": result_root,
        **storage_env,
    }
    orchestrator = UvicornProcess(
        "orchestrator_service.app.main:app",
        orchestrator_port,
        orchestrator_env,
    )
    metadata: dict[str, Any] = {
        "suffix": suffix,
        "redis_prefix": redis_prefix,
        "topic": topic,
        "visual_command_topic": visual_command_topic,
        "visual_event_topic": visual_event_topic,
        "group": group,
        "control_url": f"http://127.0.0.1:{control_port}",
        "orchestrator_url": f"http://127.0.0.1:{orchestrator_port}",
        "stub_url": f"http://127.0.0.1:{stub_port}",
    }
    logs: list[str] = []
    original_error: BaseException | None = None
    try:
        control.start()
        stub.start()
        yield control, orchestrator, stub, metadata
    except BaseException as exc:
        original_error = exc
        raise
    finally:
        def cleanup_redis() -> None:
            redis_client = redis.Redis.from_url(
                "redis://127.0.0.1:6379/14", decode_responses=True
            )
            try:
                keys = list(redis_client.scan_iter(match=f"{redis_prefix}*"))
                if keys:
                    redis_client.delete(*keys)
            finally:
                redis_client.close()

        def cleanup_kafka() -> None:
            metadata["consumer_group_deleted"] = asyncio.run(
                _cleanup_kafka_resources(
                    group,
                    topic,
                    visual_command_topic,
                    visual_event_topic,
                )
            )

        cleanup_errors = _run_cleanup_steps(
            (
                ("orchestrator.stop", orchestrator.stop),
                ("stub.stop", stub.stop),
                ("control.stop", control.stop),
                ("redis", cleanup_redis),
                ("kafka", cleanup_kafka),
                ("storage", storage.cleanup),
            )
        )
        logs.extend(
            run.stop_log or ""
            for service in (orchestrator, stub, control)
            for run in service.runs
        )
        if any("Traceback" in output for output in logs):
            cleanup_errors.append(
                ("uvicorn_logs", RuntimeError("Uvicorn 日志包含 Traceback"))
            )
        _report_cleanup_errors(cleanup_errors, original_error)


def test_real_milestone_2a_runtime_closes_and_recovers(
    milestone2a_postgres: StrictMilestone2APostgres,
) -> None:
    engine = create_engine(milestone2a_postgres.dsn)
    with _services(milestone2a_postgres) as (control, orchestrator, stub, metadata):
        control_url = metadata["control_url"]
        evidence: dict[str, Any] = {
            "infrastructure": _infrastructure_evidence(engine),
            "isolation": {
                "postgres_database": engine.url.database,
                "redis_database": 14,
                "redis_prefix": metadata["redis_prefix"],
                "kafka_topic": metadata["topic"],
                "kafka_group": metadata["group"],
            },
            "status_trajectories": {},
        }
        assert str(engine.url.database).endswith("_test")

        submissions = (
            ("normal", "NORMAL"),
            ("urgent", "URGENT"),
        )
        task_ids: dict[str, str] = {}
        for label, priority in submissions:
            task_id = f"milestone-2a-{label}-{metadata['suffix'][:10]}"
            task_ids[label] = task_id
            response = httpx.post(
                f"{control_url}/api/course-jobs",
                json={
                    "task_id": task_id,
                    "task_types": ["ASR"],
                    "priority": priority,
                    "teacher_video_path": "https://example.invalid/course.mp4",
                    "asr_options": {"language": "zh"},
                },
                timeout=5,
            )
            response.raise_for_status()
            assert response.json()["code"] == 0
            evidence.setdefault("submissions", []).append(response.json())

        first_orchestrator_run = orchestrator.start()
        first_readiness = _wait_http(
            f"{metadata['orchestrator_url']}/ops/readiness",
            timeout_seconds=20,
            process=orchestrator,
        )

        for label, task_id in task_ids.items():
            trajectory: list[dict[str, Any]] = []

            def query(
                selected_task_id: str = task_id,
                selected_trajectory: list[dict[str, Any]] = trajectory,
            ) -> dict[str, Any]:
                payload: dict[str, Any] = httpx.get(
                    f"{control_url}/api/course-jobs/{selected_task_id}", timeout=2
                ).json()
                snapshot = _status_snapshot(payload)
                if not selected_trajectory or selected_trajectory[-1] != snapshot:
                    selected_trajectory.append(snapshot)
                return payload

            _poll(
                query,
                lambda payload: (
                    _task(payload)["nodes"] and _task(payload)["nodes"][0]["status"] == 30
                ),
                message=f"{label} ASR 节点未进入无实例状态 30",
            )
            evidence["status_trajectories"][label] = trajectory

        with engine.connect() as connection:
            outbox_rows = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT event_id, aggregate_id, event_type, payload, "
                        "published_at, publish_attempts FROM outbox_events "
                        "ORDER BY created_at, event_id"
                    )
                ).mappings()
            ]
        assert len(outbox_rows) == 2
        assert all(row["published_at"] is not None for row in outbox_rows)
        evidence["outbox_before_restart"] = [
            {
                key: str(value) if key in {"event_id", "published_at"} else value
                for key, value in row.items()
            }
            for row in outbox_rows
        ]

        offsets_before = asyncio.run(_group_offsets(metadata["group"]))
        assert offsets_before[f"{metadata['topic']}:0"] == 2
        evidence["kafka_offsets_before_restart"] = offsets_before

        stopped_first_run = orchestrator.stop()
        assert stopped_first_run is first_orchestrator_run
        assert stopped_first_run.stop_log is not None
        assert "Traceback" not in stopped_first_run.stop_log
        duplicate_envelope = {
            "event_id": str(outbox_rows[0]["event_id"]),
            "aggregate_type": "COURSE_JOB",
            "aggregate_id": outbox_rows[0]["aggregate_id"],
            "event_type": outbox_rows[0]["event_type"],
            "payload": outbox_rows[0]["payload"],
        }
        asyncio.run(_publish_duplicate(metadata["topic"], duplicate_envelope))
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE outbox_events SET published_at = NULL, claim_token = NULL, "
                    "claimed_at = NULL WHERE event_id = :event_id"
                ),
                {"event_id": outbox_rows[0]["event_id"]},
            )

        second_orchestrator_run = orchestrator.start()
        assert first_orchestrator_run.pid != second_orchestrator_run.pid
        second_readiness = _wait_http(
            f"{metadata['orchestrator_url']}/ops/readiness",
            timeout_seconds=20,
            process=orchestrator,
        )

        def query_outbox_after_restart() -> list[dict[str, Any]]:
            with engine.connect() as connection:
                return [
                    dict(row)
                    for row in connection.execute(
                        text(
                            "SELECT event_id, published_at, publish_attempts "
                            "FROM outbox_events ORDER BY created_at, event_id"
                        )
                    ).mappings()
                ]

        outbox_after = _poll(
            query_outbox_after_restart,
            lambda rows: (
                all(row["published_at"] is not None for row in rows)
                and max(row["publish_attempts"] for row in rows) >= 2
            ),
            message="orchestrator 重启后 Outbox 未完成重新发布",
        )
        evidence["outbox_after_restart"] = [
            {key: str(value) for key, value in row.items()} for row in outbox_after
        ]

        topic_partition = f"{metadata['topic']}:0"

        def kafka_offsets() -> dict[str, dict[str, int]]:
            return {
                "committed": asyncio.run(_group_offsets(metadata["group"])),
                "end": asyncio.run(_topic_end_offsets(metadata["topic"])),
            }

        offsets_after_restart = _poll(
            kafka_offsets,
            lambda offsets: (
                offsets["committed"].get(topic_partition) == 4
                and offsets["end"].get(topic_partition) == 4
            ),
            message="orchestrator 重启后的 committed/end offset 未精确收敛到 4",
        )
        assert offsets_after_restart["committed"][topic_partition] == 4
        assert offsets_after_restart["end"][topic_partition] == 4
        evidence["kafka_offsets_after_restart"] = offsets_after_restart
        time.sleep(0.25)
        quiet_offsets = kafka_offsets()
        assert quiet_offsets["committed"][topic_partition] == 4
        assert quiet_offsets["end"][topic_partition] == 4
        evidence["kafka_offsets_after_quiet_window"] = quiet_offsets
        with engine.connect() as connection:
            counts = dict(
                connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM course_task_types) AS task_type_count, "
                        "(SELECT count(*) FROM task_nodes) AS node_count"
                    )
                )
                .mappings()
                .one()
            )
        assert counts == {"task_type_count": 2, "node_count": 4}
        evidence["idempotency_counts"] = counts

        for operator_code, capability in (
            ("asr_offline", "asr_offline"),
            ("text_analysis", "text_analysis"),
        ):
            instance_id = f"{operator_code}-{metadata['suffix'][:8]}"
            registration = httpx.post(
                f"{control_url}/api/operator-instances/register",
                json={
                    "instance_id": instance_id,
                    "operator_code": operator_code,
                    "capabilities": [capability],
                    "service_url": metadata["stub_url"],
                    "declared_capacity": 1,
                    "labels": {"test": "milestone-2a"},
                },
                timeout=5,
            )
            assert registration.status_code == 201
            assert registration.json()["lifecycle"] == "OFFLINE"
            heartbeat = httpx.post(
                f"{control_url}/api/operator-instances/heartbeat",
                json={"instance_id": instance_id, "inflight": 0, "model_ready": True},
                timeout=5,
            )
            heartbeat.raise_for_status()
            assert heartbeat.json()["lifecycle"] == "ONLINE"

        def query_until_completed() -> dict[str, dict[str, Any]]:
            payloads: dict[str, dict[str, Any]] = {}
            for label, task_id in task_ids.items():
                payload = httpx.get(f"{control_url}/api/course-jobs/{task_id}", timeout=2).json()
                snapshot = _status_snapshot(payload)
                trajectory = evidence["status_trajectories"][label]
                if trajectory[-1] != snapshot:
                    trajectory.append(snapshot)
                payloads[label] = payload
            return payloads

        completed_payloads = _poll(
            query_until_completed,
            lambda payloads: all(
                _task(payload)["status"] == 60
                and [node["status"] for node in _task(payload)["nodes"]] == [60, 60]
                for payload in payloads.values()
            ),
            timeout_seconds=30,
            interval_seconds=0.02,
            message="NORMAL/URGENT 任务未由运行中 Worker 推进到 60",
        )

        calls_response = httpx.get(f"{metadata['stub_url']}/ops/calls", timeout=5)
        calls_response.raise_for_status()
        calls = calls_response.json()
        assert len(calls) == 4
        asr_calls = [call for call in calls if call["node_code"] == "ASR_TRANSCRIPTION"]
        assert [call["task_id"] for call in asr_calls] == [
            task_ids["urgent"],
            task_ids["normal"],
        ]
        assert {call["node_code"] for call in calls} == {
            "ASR_TRANSCRIPTION",
            "COURSE_OVERVIEW",
        }
        assert all(
            node["result"]["stub"] is True
            for payload in completed_payloads.values()
            for node in _task(payload)["nodes"]
        )
        evidence["stub_calls"] = calls
        evidence["final_queries"] = completed_payloads

        for label, trajectory in evidence["status_trajectories"].items():
            node_states = [set(item["nodes"].values()) for item in trajectory]
            assert any(30 in states for states in node_states), (label, trajectory)
            assert any(50 in states for states in node_states), (label, trajectory)
            assert trajectory[-1]["task_status"] == 60
            assert set(trajectory[-1]["nodes"].values()) == {60}

        redis_client = redis.Redis.from_url(
            "redis://127.0.0.1:6379/14", decode_responses=True
        )
        try:
            lease_release = _poll(
                lambda: _lease_snapshot(redis_client, metadata["redis_prefix"]),
                _leases_released,
                timeout_seconds=10,
                interval_seconds=0.02,
                message="任务终态 60 后租约未在截止时间内释放",
            )
            assert _leases_released(lease_release)
            evidence["lease_release"] = lease_release
        finally:
            redis_client.close()
        final_readiness = _wait_http(
            f"{metadata['orchestrator_url']}/ops/readiness",
            timeout_seconds=20,
            process=orchestrator,
        )
        evidence["service_probes"] = {
            "control_health": control.runs[0].health_response,
            "stub_health": stub.runs[0].health_response,
            "orchestrator_health": [
                first_orchestrator_run.health_response,
                second_orchestrator_run.health_response,
            ],
            "orchestrator_readiness": [first_readiness, second_readiness],
            "orchestrator_final_readiness": final_readiness,
        }

    assert len(orchestrator.runs) == 2
    assert [run.sequence for run in orchestrator.runs] == [1, 2]
    assert len({run.pid for run in orchestrator.runs}) == 2
    all_runs = [*control.runs, *orchestrator.runs, *stub.runs]
    for run in all_runs:
        _assert_clean_uvicorn_run(run)
    evidence["orchestrator_processes"] = [
        run.evidence() for run in orchestrator.runs
    ]
    remaining_groups = asyncio.run(_consumer_groups())
    assert metadata["group"] not in remaining_groups
    assert metadata["consumer_group_deleted"] is True
    evidence["cleanup"] = {
        "consumer_group": metadata["group"],
        "consumer_group_deleted": metadata["consumer_group_deleted"],
        "consumer_group_present_after_cleanup": False,
    }
    report_root = Path(
        os.environ.get(
            "MILESTONE_2A_REPORT_DIR",
            PLATFORM_ROOT / "harness" / "reports" / "milestone-2a",
        )
    )
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / f"{metadata['suffix']}.json"
    report_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    assert report_path.is_file()
    engine.dispose()
