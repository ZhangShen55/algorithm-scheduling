from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol
from uuid import uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import Engine, create_engine

from packages.platform_common.kafka import (
    AioKafkaConsumerAdapter,
    AioKafkaProducerAdapter,
    KafkaMessage,
    KafkaTopicManager,
)
from packages.platform_common.repository import CourseRepository

from ..application.dispatcher import LeaseAwareDispatcher
from ..application.executor import NodeExecutor
from ..application.outbox import OutboxPublisher
from ..application.pipeline import PipelineInitializer
from ..core.config import OrchestratorSettings
from .contract_stub import ContractStubAdapter
from .control_client import ControlLeaseClient


class CourseConsumer(Protocol):
    async def poll(self, *, timeout_seconds: float) -> list[KafkaMessage]: ...

    async def commit(self, message: KafkaMessage) -> None: ...


class PipelineHandler(Protocol):
    async def handle(self, value: bytes) -> object: ...


class CourseCommandConsumerLoop:
    def __init__(
        self,
        consumer: CourseConsumer,
        initializer: PipelineHandler,
        *,
        poll_timeout_seconds: float,
    ) -> None:
        self._consumer = consumer
        self._initializer = initializer
        self._poll_timeout_seconds = poll_timeout_seconds

    async def run_once(self) -> int:
        messages = await self._consumer.poll(
            timeout_seconds=self._poll_timeout_seconds
        )
        handled = 0
        for message in messages:
            await self._initializer.handle(message.value)
            await self._consumer.commit(message)
            handled += 1
        return handled

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self.run_once()


@dataclass(slots=True)
class OrchestratorResources:
    engine: Any
    repository: Any
    http_client: Any
    producer: Any
    consumer: Any
    topic_manager: Any


ResourceFactory = Callable[[OrchestratorSettings], OrchestratorResources]


class OrchestratorRuntime:
    REQUIRED_LOOPS = ("outbox_publisher", "course_consumer", "node_executor")

    def __init__(
        self,
        settings: OrchestratorSettings,
        *,
        resource_factory: ResourceFactory | None = None,
    ) -> None:
        self.settings = settings
        self._resource_factory = resource_factory or self._build_resources
        self.resources: OrchestratorResources | None = None
        self.stop_event = asyncio.Event()
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.loop_errors: dict[str, str] = {}
        self._started = False
        self._producer_started = False
        self._consumer_started = False
        self._app: FastAPI | None = None

    def attach(self, app: FastAPI) -> None:
        self._app = app
        app.state.orchestrator_runtime = self
        app.state.runtime_loops_started = self._started
        app.state.runtime_tasks = dict(self.tasks)
        app.state.course_repository = (
            self.resources.repository if self.resources is not None else None
        )

    def _build_resources(self, settings: OrchestratorSettings) -> OrchestratorResources:
        postgres = settings.postgres
        engine: Engine = create_engine(
            postgres.dsn,
            pool_size=postgres.pool_size,
            max_overflow=postgres.max_overflow,
            pool_timeout=postgres.pool_timeout_seconds,
            pool_pre_ping=postgres.pool_pre_ping,
        )
        http_client = httpx.AsyncClient(
            base_url=settings.control.base_url,
            timeout=settings.control.request_timeout_seconds,
        )
        producer = AioKafkaProducerAdapter(
            bootstrap_servers=settings.kafka.bootstrap_servers,
            client_id=f"{settings.kafka.client_id}-producer",
            acks=settings.kafka.acks,
        )
        consumer = AioKafkaConsumerAdapter(
            topics=[settings.kafka.course_command_topic],
            bootstrap_servers=settings.kafka.bootstrap_servers,
            group_id=settings.kafka.course_consumer_group,
            client_id=f"{settings.kafka.client_id}-course-consumer",
            max_poll_records=settings.kafka.max_poll_records,
            auto_offset_reset=settings.kafka.auto_offset_reset,
        )
        topic_manager = KafkaTopicManager(
            bootstrap_servers=settings.kafka.bootstrap_servers,
            client_id=f"{settings.kafka.client_id}-topic-admin",
            topics=(
                settings.kafka.course_command_topic,
                settings.kafka.visual_command_topic,
                settings.kafka.visual_event_topic,
            ),
            partitions=settings.kafka.topic_partitions,
            replication_factor=settings.kafka.topic_replication_factor,
        )
        return OrchestratorResources(
            engine=engine,
            repository=CourseRepository(engine),
            http_client=http_client,
            producer=producer,
            consumer=consumer,
            topic_manager=topic_manager,
        )

    async def start(self, app: FastAPI) -> None:
        if self._started:
            return
        self.stop_event = asyncio.Event()
        self.loop_errors.clear()
        self.resources = self._resource_factory(self.settings)
        try:
            if self.settings.kafka.ensure_topics:
                await self.resources.topic_manager.ensure_topics()
            else:
                await self.resources.topic_manager.validate_topics()
            await self.resources.producer.start()
            self._producer_started = True
            await self.resources.consumer.start()
            self._consumer_started = True
            self._start_loops()
            self._started = True
            self.attach(app)
        except BaseException:
            await self.stop(app)
            raise

    def _start_loops(self) -> None:
        assert self.resources is not None
        repository = self.resources.repository
        outbox = OutboxPublisher(
            repository,
            self.resources.producer,
            topic=self.settings.kafka.course_command_topic,
            batch_size=self.settings.outbox.batch_size,
            poll_interval_seconds=self.settings.outbox.poll_interval_seconds,
        )
        consumer_loop = CourseCommandConsumerLoop(
            self.resources.consumer,
            PipelineInitializer(repository),
            poll_timeout_seconds=self.settings.kafka.poll_timeout_seconds,
        )
        lease_client = ControlLeaseClient(
            self.resources.http_client,
            default_ttl_seconds=self.settings.control.default_lease_ttl_seconds,
        )
        dispatcher = LeaseAwareDispatcher(repository, lease_client)
        executor = NodeExecutor(
            repository,
            dispatcher,
            ContractStubAdapter(self.resources.http_client),
            worker_id=self.settings.worker.worker_id or f"orchestrator-{uuid4().hex[:12]}",
            concurrency=self.settings.worker.node_concurrency,
        )
        self.tasks = {
            "outbox_publisher": asyncio.create_task(
                outbox.run(self.stop_event),
                name="orchestrator-outbox-publisher",
            ),
            "course_consumer": asyncio.create_task(
                consumer_loop.run(self.stop_event),
                name="orchestrator-course-consumer",
            ),
            "node_executor": asyncio.create_task(
                self._run_executor(executor),
                name="orchestrator-node-executor",
            ),
        }
        for name, task in self.tasks.items():
            task.add_done_callback(partial(self._record_loop_exit, name))

    async def _run_executor(self, executor: NodeExecutor) -> None:
        while not self.stop_event.is_set():
            executed = await executor.run_once()
            if executed > 0:
                continue
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=self.settings.worker.claim_poll_interval_seconds,
                )
            except TimeoutError:
                continue

    def _record_loop_exit(self, name: str, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self.loop_errors[name] = str(error)
            self.stop_event.set()
        elif not self.stop_event.is_set():
            self.loop_errors[name] = "必需后台循环意外退出"
            self.stop_event.set()

    async def stop(self, app: FastAPI) -> None:
        self.stop_event.set()
        if self.tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self.tasks.values(), return_exceptions=True),
                    timeout=self.settings.worker.shutdown_timeout_seconds,
                )
            except TimeoutError:
                for task in self.tasks.values():
                    task.cancel()
                await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        if self.resources is not None:
            try:
                if self._consumer_started:
                    await self.resources.consumer.stop()
            finally:
                try:
                    if self._producer_started:
                        await self.resources.producer.stop()
                finally:
                    try:
                        await self.resources.http_client.aclose()
                    finally:
                        self.resources.engine.dispose()
        self.tasks = {}
        self.resources = None
        self._producer_started = False
        self._consumer_started = False
        self._started = False
        self.attach(app)

    async def readiness(self) -> dict[str, object]:
        checks: dict[str, dict[str, object]] = {}
        if not self._started or self.resources is None:
            checks["runtime"] = {
                "ready": False,
                "detail": "orchestrator 运行时尚未启动",
            }
            return {"status": "not_ready", "checks": checks}

        for name in self.REQUIRED_LOOPS:
            task = self.tasks.get(name)
            error = self.loop_errors.get(name)
            ready = task is not None and not task.done() and error is None
            checks[name] = {
                "ready": ready,
                "detail": error or ("后台循环运行中" if ready else "后台循环未运行"),
            }

        dependency_checks = await asyncio.gather(
            self._check_postgres(),
            self._check_kafka(),
            self._check_control(),
        )
        checks.update(dict(dependency_checks))
        ready = all(bool(check["ready"]) for check in checks.values())
        return {"status": "ready" if ready else "not_ready", "checks": checks}

    async def _check_postgres(self) -> tuple[str, dict[str, object]]:
        resources = self.resources
        assert resources is not None
        return await self._dependency_check(
            "postgresql",
            lambda: asyncio.to_thread(resources.repository.count_courses),
            "PostgreSQL 连接正常",
        )

    async def _check_kafka(self) -> tuple[str, dict[str, object]]:
        assert self.resources is not None
        return await self._dependency_check(
            "kafka",
            self.resources.consumer.lag,
            "Kafka Consumer 连接正常",
        )

    async def _check_control(self) -> tuple[str, dict[str, object]]:
        resources = self.resources
        assert resources is not None

        async def probe() -> None:
            response = await resources.http_client.get("/health")
            response.raise_for_status()

        return await self._dependency_check(
            "control_service",
            probe,
            "control-service 连接正常",
        )

    async def _dependency_check(
        self,
        name: str,
        operation: Callable[[], Any],
        success_detail: str,
    ) -> tuple[str, dict[str, object]]:
        try:
            result = operation()
            if hasattr(result, "__await__"):
                await asyncio.wait_for(
                    result,
                    timeout=self.settings.readiness.dependency_timeout_seconds,
                )
        except Exception as exc:
            return name, {"ready": False, "detail": f"依赖检查失败: {exc}"}
        return name, {"ready": True, "detail": success_detail}

    @asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncIterator[None]:
        await self.start(app)
        try:
            yield
        finally:
            await self.stop(app)
