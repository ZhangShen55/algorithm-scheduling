from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from aiokafka.errors import KafkaConnectionError
from fastapi.testclient import TestClient

from orchestrator_service.app.application.factory import create_app
from orchestrator_service.app.core.config import OrchestratorSettings
from orchestrator_service.app.infrastructure.runtime import (
    CourseCommandConsumerLoop,
    OrchestratorResources,
    OrchestratorRuntime,
)
from packages.platform_common.kafka import KafkaMessage


class FakeRepository:
    def claim_outbox_events(self, batch_size: int) -> list[Any]:
        assert batch_size > 0
        return []

    def mark_outbox_published(self, *args: object) -> None:
        raise AssertionError(args)

    def mark_outbox_failed(self, *args: object) -> None:
        raise AssertionError(args)

    def list_dispatch_capabilities(self) -> list[str]:
        return []

    def list_stale_claimed_nodes(self, claimed_before: object) -> list[Any]:
        del claimed_before
        return []

    def list_running_ppt_slice_nodes(self) -> list[Any]:
        return []

    def list_running_visual_nodes(self) -> list[Any]:
        return []

    def resume_visual_nodes(self) -> int:
        return 0

    def claim_ready_visual_node(self, worker_id: str) -> None:
        assert worker_id
        return None

    def count_courses(self) -> int:
        return 0


class FakeEngine:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True
        self.events.append("engine.dispose")


class FakeProducer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True
        self.events.append("producer.start")

    async def stop(self) -> None:
        self.stopped = True
        self.events.append("producer.stop")

    async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> object:
        del topic, value, key
        return object()


class FakeConsumer:
    def __init__(
        self,
        events: list[str],
        *,
        poll_error: Exception | None = None,
        lag_error: Exception | None = None,
        messages: list[KafkaMessage] | None = None,
        poll_errors: list[Exception] | None = None,
    ) -> None:
        self.events = events
        self.poll_error = poll_error
        self.lag_error = lag_error
        self.messages = list(messages or [])
        self.poll_errors = list(poll_errors or [])
        self.started = False
        self.stopped = False
        self.committed: list[KafkaMessage] = []

    async def start(self) -> None:
        self.started = True
        self.events.append("consumer.start")

    async def stop(self) -> None:
        self.stopped = True
        self.events.append("consumer.stop")

    async def poll(self, *, timeout_seconds: float) -> list[KafkaMessage]:
        await asyncio.sleep(min(timeout_seconds, 0.01))
        if self.poll_errors:
            raise self.poll_errors.pop(0)
        if self.poll_error is not None:
            raise self.poll_error
        messages, self.messages = self.messages, []
        return messages

    async def commit(self, message: KafkaMessage) -> None:
        self.committed.append(message)

    async def lag(self) -> dict[str, int]:
        if self.lag_error is not None:
            raise self.lag_error
        return {"algorithm.course.commands:0": 0}


class FakeTopicManager:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def ensure_topics(self) -> tuple[str, ...]:
        self.events.append("topics.ensure")
        return ()

    async def validate_topics(self) -> None:
        self.events.append("topics.validate")


class FakeHttpClient:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.closed = False

    async def get(self, path: str) -> httpx.Response:
        assert path == "/health"
        return httpx.Response(200, request=httpx.Request("GET", "http://control/health"))

    async def post(self, *args: object, **kwargs: object) -> httpx.Response:
        raise AssertionError((args, kwargs))

    async def aclose(self) -> None:
        self.closed = True
        self.events.append("http.close")


class RecordingInitializer:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.values: list[bytes] = []

    async def handle(self, value: bytes) -> list[Any]:
        self.values.append(value)
        if self.error is not None:
            raise self.error
        return []


class ScriptedExecutor:
    def __init__(self, first_error: Exception) -> None:
        self.first_error = first_error
        self.calls = 0
        self.retried = asyncio.Event()

    async def run_once(self) -> int:
        self.calls += 1
        if self.calls == 1:
            raise self.first_error
        self.retried.set()
        return 0


def _settings(tmp_path: Path) -> OrchestratorSettings:
    return OrchestratorSettings.model_validate(
        {
            "storage": {
                "course_root": tmp_path / "course",
                "result_root": tmp_path / "result",
            },
            "kafka": {"poll_timeout_seconds": 0.02},
            "outbox": {"poll_interval_seconds": 0.02},
            "worker": {
                "claim_poll_interval_seconds": 0.02,
                "shutdown_timeout_seconds": 1.0,
                "transient_error_base_delay_seconds": 0.01,
                "transient_error_max_delay_seconds": 0.02,
            },
            "readiness": {"dependency_timeout_seconds": 0.5},
        }
    )


def _runtime(
    tmp_path: Path,
    *,
    consumer_factory: Callable[[list[str]], FakeConsumer] | None = None,
) -> tuple[OrchestratorRuntime, OrchestratorResources, list[str]]:
    events: list[str] = []
    consumer = (
        consumer_factory(events) if consumer_factory is not None else FakeConsumer(events)
    )
    resources = OrchestratorResources(
        engine=FakeEngine(events),
        repository=FakeRepository(),
        http_client=FakeHttpClient(events),
        producer=FakeProducer(events),
        consumer=consumer,
        visual_event_consumer=FakeConsumer(events),
        topic_manager=FakeTopicManager(events),
    )
    runtime = OrchestratorRuntime(
        _settings(tmp_path),
        resource_factory=lambda _: resources,
    )
    return runtime, resources, events


def test_lifespan_starts_required_loops_reports_ready_and_closes_resources(
    tmp_path: Path,
) -> None:
    runtime, resources, events = _runtime(tmp_path)
    app = create_app(_settings(tmp_path), runtime=runtime)

    assert app.state.runtime_loops_started is False
    assert events == []

    with TestClient(app) as client:
        readiness = client.get("/ops/readiness")
        assert readiness.status_code == 200
        assert readiness.json()["status"] == "ready"
        assert app.state.runtime_loops_started is True
        assert set(app.state.runtime_tasks) == {
            "outbox_publisher",
            "course_consumer",
            "node_executor",
            "visual_dispatcher",
            "visual_event_consumer",
            "ppt_reconcile",
        }
        assert app.state.ppt_terminal_handler is not None

    assert app.state.runtime_loops_started is False
    assert resources.producer.stopped is True
    assert resources.consumer.stopped is True
    assert resources.http_client.closed is True
    assert resources.engine.disposed is True
    assert events[-4:] == [
        "consumer.stop",
        "producer.stop",
        "http.close",
        "engine.dispose",
    ]


def test_readiness_reports_required_loop_failure(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(
        tmp_path,
        consumer_factory=lambda events: FakeConsumer(
            events,
            poll_error=RuntimeError("Kafka 消费循环异常"),
        ),
    )
    app = create_app(_settings(tmp_path), runtime=runtime)

    with TestClient(app) as client:
        for _ in range(20):
            readiness = client.get("/ops/readiness")
            if readiness.status_code == 503:
                break
            time.sleep(0.01)

        assert readiness.status_code == 503
        assert "Kafka 消费循环异常" in str(readiness.json())


def test_lifespan_supervisor_recovers_transient_course_consumer_failure(
    tmp_path: Path,
) -> None:
    runtime, _, _ = _runtime(
        tmp_path,
        consumer_factory=lambda events: FakeConsumer(
            events,
            poll_errors=[KafkaConnectionError("Kafka 暂时不可用")],
        ),
    )
    app = create_app(_settings(tmp_path), runtime=runtime)

    with TestClient(app) as client:
        for _ in range(100):
            report = client.get("/ops/readiness")
            state = report.json()["checks"]["course_consumer"]
            if state["recoveries"] >= 1:
                break
            time.sleep(0.01)

        assert report.status_code == 200
        assert state["state"] == "running"
        assert state["transient_retries"] == 1
        assert state["recoveries"] == 1
        assert state["last_recovered_at"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("acquire", "release"))
async def test_executor_polling_retries_transient_control_transport_errors(
    tmp_path: Path,
    operation: str,
) -> None:
    runtime, _, _ = _runtime(tmp_path)
    request = httpx.Request(
        "POST",
        f"http://control/internal/operator-instances/{operation}",
    )
    executor = ScriptedExecutor(
        httpx.ConnectError(f"{operation} 暂时不可用", request=request)
    )

    task = asyncio.create_task(runtime._run_executor(executor))
    await asyncio.wait_for(executor.retried.wait(), timeout=0.5)

    assert executor.calls >= 2
    assert runtime.stop_event.is_set() is False
    assert task.done() is False

    runtime.stop_event.set()
    await asyncio.wait_for(task, timeout=0.5)


@pytest.mark.asyncio
async def test_executor_polling_keeps_fail_stop_for_non_transport_errors(
    tmp_path: Path,
) -> None:
    runtime, _, _ = _runtime(tmp_path)
    executor = ScriptedExecutor(RuntimeError("节点执行不变量损坏"))
    task = asyncio.create_task(runtime._run_executor(executor))
    task.add_done_callback(
        lambda completed: runtime._record_loop_exit("node_executor", completed)
    )

    with pytest.raises(RuntimeError, match="节点执行不变量损坏"):
        await task
    await asyncio.sleep(0)

    assert executor.calls == 1
    assert runtime.stop_event.is_set() is True
    assert runtime.loop_errors == {"node_executor": "节点执行不变量损坏"}


@pytest.mark.asyncio
async def test_executor_polling_keeps_fail_stop_for_non_retryable_transport_errors(
    tmp_path: Path,
) -> None:
    runtime, _, _ = _runtime(tmp_path)
    request = httpx.Request("POST", "ftp://control/internal/operator-instances/lease")
    executor = ScriptedExecutor(
        httpx.UnsupportedProtocol("不支持的控制面协议", request=request)
    )
    task = asyncio.create_task(runtime._run_executor(executor))
    task.add_done_callback(
        lambda completed: runtime._record_loop_exit("node_executor", completed)
    )

    with pytest.raises(httpx.UnsupportedProtocol, match="不支持的控制面协议"):
        await asyncio.wait_for(task, timeout=0.1)
    await asyncio.sleep(0)

    assert executor.calls == 1
    assert runtime.stop_event.is_set() is True
    assert runtime.loop_errors == {"node_executor": "不支持的控制面协议"}


def test_readiness_reports_kafka_dependency_failure(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(
        tmp_path,
        consumer_factory=lambda events: FakeConsumer(
            events,
            lag_error=RuntimeError("Kafka 依赖不可用"),
        ),
    )
    app = create_app(_settings(tmp_path), runtime=runtime)

    with TestClient(app) as client:
        readiness = client.get("/ops/readiness")

        assert readiness.status_code == 503
        kafka_check = readiness.json()["checks"]["kafka"]
        assert kafka_check["ready"] is False
        assert "依赖检查失败" in kafka_check["detail"]
        assert "Kafka 依赖不可用" in kafka_check["detail"]


@pytest.mark.asyncio
async def test_supervisor_recovers_transient_loop_and_records_recovery_time(
    tmp_path: Path,
) -> None:
    runtime, _, _ = _runtime(tmp_path)
    request = httpx.Request("GET", "http://control/health")
    calls = 0
    resumed = asyncio.Event()

    async def runner() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("control 暂时不可用", request=request)
        resumed.set()
        await runtime.stop_event.wait()

    task = asyncio.create_task(runtime._supervise_loop("course_consumer", runner))
    await asyncio.wait_for(resumed.wait(), timeout=0.5)

    state = runtime.loop_states["course_consumer"]
    assert state["state"] == "running"
    assert state["transient_retries"] == 1
    assert state["recoveries"] == 1
    assert isinstance(state["last_recovered_at"], str)
    assert state["last_transient_error"] is None

    runtime.stop_event.set()
    await asyncio.wait_for(task, timeout=0.5)


def test_start_validates_required_topics_when_auto_creation_is_disabled(
    tmp_path: Path,
) -> None:
    runtime, _, events = _runtime(tmp_path)
    settings = _settings(tmp_path).model_copy(
        update={
            "kafka": _settings(tmp_path).kafka.model_copy(
                update={"ensure_topics": False}
            )
        }
    )
    runtime.settings = settings
    app = create_app(settings, runtime=runtime)

    with TestClient(app):
        pass

    assert "topics.validate" in events
    assert "topics.ensure" not in events


@pytest.mark.asyncio
async def test_course_consumer_commits_only_after_pipeline_initialization() -> None:
    message = KafkaMessage(
        topic="algorithm.course.commands",
        partition=0,
        offset=5,
        key=b"event-1",
        value=b'{"event_id":"event-1"}',
        timestamp_ms=1_750_000_000_000,
    )
    consumer = FakeConsumer([], messages=[message])
    initializer = RecordingInitializer()
    loop = CourseCommandConsumerLoop(consumer, initializer, poll_timeout_seconds=0.01)

    handled = await loop.run_once()

    assert handled == 1
    assert initializer.values == [message.value]
    assert consumer.committed == [message]


@pytest.mark.asyncio
async def test_course_consumer_does_not_commit_failed_pipeline_initialization() -> None:
    message = KafkaMessage(
        topic="algorithm.course.commands",
        partition=0,
        offset=6,
        key=b"event-2",
        value=b'{"event_id":"event-2"}',
        timestamp_ms=1_750_000_000_001,
    )
    consumer = FakeConsumer([], messages=[message])
    initializer = RecordingInitializer(error=RuntimeError("DAG 初始化失败"))
    loop = CourseCommandConsumerLoop(consumer, initializer, poll_timeout_seconds=0.01)

    with pytest.raises(RuntimeError, match="DAG 初始化失败"):
        await loop.run_once()

    assert consumer.committed == []
