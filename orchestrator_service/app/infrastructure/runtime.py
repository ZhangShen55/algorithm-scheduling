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
from packages.platform_common.media import FFprobeMediaInspector, MediaDownloader
from packages.platform_common.repository import CourseRepository

from ..application.dispatcher import LeaseAwareDispatcher
from ..application.executor import NodeExecutor
from ..application.lifecycle import TerminalWorkspaceCleaner
from ..application.outbox import OutboxPublisher
from ..application.pipeline import PipelineInitializer
from ..application.vision_events import (
    VisualCommandPublisher,
    VisualEventConsumerLoop,
    VisualEventProcessor,
    VisualNodeCoordinator,
)
from ..core.config import OrchestratorSettings
from ..domain.ppt_work import PptWorkLimits
from .asr import OfflineAsrAdapter
from .audio import FFmpegAudioExtractor
from .contract_stub import ContractStubAdapter
from .control_client import ControlLeaseClient
from .node_execution import NodeExecutionRouter
from .ppt_runtime import PptRuntimeCoordinator
from .ppt_slice import (
    PptSliceAdapter,
    PptSliceManifestValidator,
    PptSliceTerminalHandler,
)
from .ppt_text import OcrAdapter, PptTextPipeline


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
    visual_event_consumer: Any
    topic_manager: Any
    operator_http_client: Any | None = None


ResourceFactory = Callable[[OrchestratorSettings], OrchestratorResources]


class OrchestratorRuntime:
    REQUIRED_LOOPS = (
        "outbox_publisher",
        "course_consumer",
        "node_executor",
        "visual_dispatcher",
        "visual_event_consumer",
        "ppt_reconcile",
    )

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
        self._visual_event_consumer_started = False
        self._app: FastAPI | None = None
        self._ppt_coordinator: PptRuntimeCoordinator | None = None

    def attach(self, app: FastAPI) -> None:
        self._app = app
        app.state.orchestrator_runtime = self
        app.state.runtime_loops_started = self._started
        app.state.runtime_tasks = dict(self.tasks)
        app.state.course_repository = (
            self.resources.repository if self.resources is not None else None
        )
        app.state.ppt_terminal_handler = self._ppt_coordinator

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
        operator_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                max(
                    settings.asr.request_timeout_seconds,
                    settings.ppt.processing_timeout_seconds,
                    settings.ppt.ocr_request_timeout_seconds,
                )
            )
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
        visual_event_consumer = AioKafkaConsumerAdapter(
            topics=[settings.kafka.visual_event_topic],
            bootstrap_servers=settings.kafka.bootstrap_servers,
            group_id=settings.kafka.visual_event_consumer_group,
            client_id=f"{settings.kafka.client_id}-visual-events",
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
            visual_event_consumer=visual_event_consumer,
            topic_manager=topic_manager,
            operator_http_client=operator_http_client,
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
            await self.resources.visual_event_consumer.start()
            self._visual_event_consumer_started = True
            await self._start_loops()
            self._started = True
            self.attach(app)
        except BaseException:
            await self.stop(app)
            raise

    async def _start_loops(self) -> None:
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
        workspace_cleaner = (
            TerminalWorkspaceCleaner(
                repository,
                course_root=self.settings.storage.course_root,
                result_root=self.settings.storage.result_root,
            )
            if self.settings.storage.cleanup_terminal_workspace
            else None
        )
        operator_http_client = (
            self.resources.operator_http_client or self.resources.http_client
        )
        media_downloader = MediaDownloader(
            course_root=self.settings.storage.course_root,
            http_client=operator_http_client,
            inspector=FFprobeMediaInspector(),
            max_bytes=self.settings.storage.max_video_bytes,
        )
        ocr_pipeline = PptTextPipeline(
            repository,
            lease_client,
            OcrAdapter(
                operator_http_client,
                transport_max_attempts=(
                    self.settings.ppt.ocr_transport_max_attempts
                ),
                transport_retry_delay_seconds=(
                    self.settings.ppt.ocr_transport_retry_delay_seconds
                ),
            ),
            PptWorkLimits(
                batch_size=self.settings.ppt.ocr_batch_size,
                max_concurrency=self.settings.ppt.ocr_max_concurrency,
            ),
            lease_ttl_seconds=self.settings.control.default_lease_ttl_seconds,
            ocr_hard_timeout_seconds=self.settings.ppt.ocr_request_timeout_seconds,
        )
        terminal_handler = PptSliceTerminalHandler(
            repository=repository,
            validator=PptSliceManifestValidator(
                result_root=self.settings.storage.result_root,
                max_manifest_bytes=self.settings.ppt.max_manifest_bytes,
            ),
        )
        ppt_coordinator = PptRuntimeCoordinator(
            repository=repository,
            terminal_handler=terminal_handler,
            lease_client=lease_client,
            lease_ttl_seconds=self.settings.ppt.lease_ttl_seconds,
            lease_renew_interval_seconds=self.settings.ppt.lease_renew_interval_seconds,
            reconcile_interval_seconds=self.settings.ppt.reconcile_interval_seconds,
            workspace_cleaner=workspace_cleaner,
        )
        self._ppt_coordinator = ppt_coordinator
        await ppt_coordinator.recover()
        adapter = NodeExecutionRouter(
            repository,
            ocr_pipeline=ocr_pipeline,
            fallback=ContractStubAdapter(operator_http_client),
            media_downloader=media_downloader,
            audio_extractor=FFmpegAudioExtractor(
                course_root=self.settings.storage.course_root,
            ),
            asr_adapter=OfflineAsrAdapter(operator_http_client),
            ppt_slice_adapter=PptSliceAdapter(operator_http_client),
            ppt_callback_base_url=self.settings.ppt.callback_base_url,
            ppt_terminal_callback_path=self.settings.ppt.terminal_callback_path,
            ppt_slice_threshold=self.settings.ppt.slice_threshold,
        )
        executor = NodeExecutor(
            repository,
            dispatcher,
            adapter,
            worker_id=self.settings.worker.worker_id or f"orchestrator-{uuid4().hex[:12]}",
            concurrency=self.settings.worker.node_concurrency,
            operator_hard_timeout_seconds=max(
                self.settings.asr.request_timeout_seconds,
                self.settings.ppt.processing_timeout_seconds,
                self.settings.ppt.ocr_request_timeout_seconds,
            ),
            async_node_coordinator=ppt_coordinator,
            workspace_cleaner=workspace_cleaner,
        )
        visual_coordinator = VisualNodeCoordinator(
            repository,
            media_downloader,
            VisualCommandPublisher(
                self.resources.producer,
                topic=self.settings.kafka.visual_command_topic,
            ),
            worker_id=(
                self.settings.worker.worker_id
                or f"orchestrator-visual-{uuid4().hex[:12]}"
            ),
            workspace_cleaner=workspace_cleaner,
        )
        await visual_coordinator.recover()
        visual_event_loop = VisualEventConsumerLoop(
            self.resources.visual_event_consumer,
            VisualEventProcessor(
                repository,
                workspace_cleaner=workspace_cleaner,
            ),
            poll_timeout_seconds=self.settings.kafka.poll_timeout_seconds,
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
            "visual_dispatcher": asyncio.create_task(
                self._run_visual_dispatcher(visual_coordinator),
                name="orchestrator-visual-dispatcher",
            ),
            "visual_event_consumer": asyncio.create_task(
                visual_event_loop.run(self.stop_event),
                name="orchestrator-visual-event-consumer",
            ),
            "ppt_reconcile": asyncio.create_task(
                ppt_coordinator.run(self.stop_event),
                name="orchestrator-ppt-reconcile",
            ),
        }
        for name, task in self.tasks.items():
            task.add_done_callback(partial(self._record_loop_exit, name))

    async def _run_executor(self, executor: NodeExecutor) -> None:
        while not self.stop_event.is_set():
            try:
                executed = await executor.run_once()
            except (httpx.NetworkError, httpx.TimeoutException):
                executed = 0
            if executed > 0:
                continue
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=self.settings.worker.claim_poll_interval_seconds,
                )
            except TimeoutError:
                continue

    async def _run_visual_dispatcher(
        self,
        coordinator: VisualNodeCoordinator,
    ) -> None:
        while not self.stop_event.is_set():
            try:
                executed = await coordinator.run_once()
            except (httpx.NetworkError, httpx.TimeoutException, OSError, RuntimeError):
                executed = 0
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
        if self._ppt_coordinator is not None:
            await self._ppt_coordinator.shutdown()
        if self.resources is not None:
            try:
                try:
                    if self._visual_event_consumer_started:
                        await self.resources.visual_event_consumer.stop()
                finally:
                    if self._consumer_started:
                        await self.resources.consumer.stop()
            finally:
                try:
                    if self._producer_started:
                        await self.resources.producer.stop()
                finally:
                    try:
                        if (
                            self.resources.operator_http_client is not None
                            and self.resources.operator_http_client
                            is not self.resources.http_client
                        ):
                            await self.resources.operator_http_client.aclose()
                        await self.resources.http_client.aclose()
                    finally:
                        self.resources.engine.dispose()
        self.tasks = {}
        self.resources = None
        self._producer_started = False
        self._consumer_started = False
        self._visual_event_consumer_started = False
        self._started = False
        self._ppt_coordinator = None
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
