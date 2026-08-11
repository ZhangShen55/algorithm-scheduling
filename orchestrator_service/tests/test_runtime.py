from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
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
        messages: list[KafkaMessage] | None = None,
    ) -> None:
        self.events = events
        self.poll_error = poll_error
        self.messages = list(messages or [])
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
        if self.poll_error is not None:
            raise self.poll_error
        messages, self.messages = self.messages, []
        return messages

    async def commit(self, message: KafkaMessage) -> None:
        self.committed.append(message)

    async def lag(self) -> dict[str, int]:
        return {"algorithm.course.commands:0": 0}


class FakeTopicManager:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def ensure_topics(self) -> tuple[str, ...]:
        self.events.append("topics.ensure")
        return ()


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
        }

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
