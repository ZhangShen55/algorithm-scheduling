from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any, Protocol
from uuid import uuid4

import httpx
from aiokafka.errors import (  # type: ignore[import-untyped]
    KafkaConnectionError,
    KafkaTimeoutError,
    LeaderNotAvailableError,
    NodeNotReadyError,
    RequestTimedOutError,
    StaleMetadata,
)
from fastapi import FastAPI
from packages.platform_common.kafka import (
    AioKafkaConsumerAdapter,
    AioKafkaProducerAdapter,
    KafkaMessage,
    KafkaTopicManager,
)
from packages.platform_common.lease_resilience import LeaseRenewalPolicy
from packages.platform_common.media import FFprobeMediaInspector, MediaDownloader
from packages.platform_common.repository import (
    CourseRepository,
    PostgresRetryPolicy,
    TransientInfrastructureError,
)
from sqlalchemy import Engine, create_engine

from ..application.dispatcher import LeaseAwareDispatcher
from ..application.executor import NodeExecutor
from ..application.lifecycle import TerminalWorkspaceCleaner
from ..application.outbox import OutboxPublisher
from ..application.pipeline import PipelineInitializer
from ..application.recovery import StaleNodeRecovery
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

logger = logging.getLogger(__name__)

TRANSIENT_LOOP_ERRORS = (
    TransientInfrastructureError,
    httpx.NetworkError,
    httpx.TimeoutException,
    KafkaConnectionError,
    KafkaTimeoutError,
    LeaderNotAvailableError,
    NodeNotReadyError,
    RequestTimedOutError,
    StaleMetadata,
)


def _is_transient_loop_error(exc: BaseException) -> bool:
    if isinstance(exc, TRANSIENT_LOOP_ERRORS):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {
        429,
        500,
        502,
        503,
        504,
    }


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
        self.loop_states: dict[str, dict[str, object]] = {}
        self.fatal_exit_requested = False
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
            repository=CourseRepository(
                engine,
                postgres_retry=PostgresRetryPolicy(
                    max_attempts=settings.postgres_retry.max_attempts,
                    base_delay_seconds=settings.postgres_retry.base_delay_seconds,
                    max_delay_seconds=settings.postgres_retry.max_delay_seconds,
                    jitter_ratio=settings.postgres_retry.jitter_ratio,
                ),
            ),
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
        self.loop_states = {
            name: {
                "state": "starting",
                "transient_retries": 0,
                "recoveries": 0,
                "last_transient_error": None,
            }
            for name in self.REQUIRED_LOOPS
        }
        self.fatal_exit_requested = False
        self.resources = self._resource_factory(self.settings)
        retry_observer = getattr(
            app.state.platform_metrics,
            "record_postgres_transaction_event",
            None,
        )
        set_retry_observer = getattr(
            self.resources.repository,
            "set_postgres_retry_observer",
            None,
        )
        if callable(set_retry_observer):
            set_retry_observer(retry_observer)
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
            renewal_policy=LeaseRenewalPolicy(
                max_attempts=self.settings.lease_renewal.max_attempts,
                base_delay_seconds=self.settings.lease_renewal.base_delay_seconds,
                max_delay_seconds=self.settings.lease_renewal.max_delay_seconds,
                safety_margin_seconds=(
                    self.settings.lease_renewal.safety_margin_seconds
                ),
            ),
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
            renewal_policy=LeaseRenewalPolicy(
                max_attempts=self.settings.lease_renewal.max_attempts,
                base_delay_seconds=self.settings.lease_renewal.base_delay_seconds,
                max_delay_seconds=self.settings.lease_renewal.max_delay_seconds,
                safety_margin_seconds=(
                    self.settings.lease_renewal.safety_margin_seconds
                ),
            ),
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
            ppt_slice_adapter=PptSliceAdapter(
                operator_http_client,
                transport_max_attempts=(
                    self.settings.ppt.submit_transport_max_attempts
                ),
                transport_retry_delay_seconds=(
                    self.settings.ppt.submit_transport_retry_delay_seconds
                ),
            ),
            ppt_callback_base_url=self.settings.ppt.callback_base_url,
            ppt_terminal_callback_path=self.settings.ppt.terminal_callback_path,
            ppt_slice_threshold=self.settings.ppt.slice_threshold,
        )
        worker_id = (
            self.settings.worker.worker_id
            or f"orchestrator-{uuid4().hex[:12]}"
        )
        executor = NodeExecutor(
            repository,
            dispatcher,
            adapter,
            worker_id=worker_id,
            concurrency=self.settings.worker.node_concurrency,
            operator_hard_timeout_seconds=max(
                self.settings.asr.request_timeout_seconds,
                self.settings.ppt.processing_timeout_seconds,
                self.settings.ppt.ocr_request_timeout_seconds,
            ),
            async_node_coordinator=ppt_coordinator,
            workspace_cleaner=workspace_cleaner,
        )
        stale_node_recovery = StaleNodeRecovery(
            repository,
            lease_client,
            timeout_seconds=self.settings.worker.stale_node_recovery_seconds,
            current_worker_id=worker_id,
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
        runners: dict[str, Callable[[], Awaitable[None]]] = {
            "outbox_publisher": partial(outbox.run, self.stop_event),
            "course_consumer": partial(consumer_loop.run, self.stop_event),
            "node_executor": partial(
                self._run_executor,
                executor,
                stale_node_recovery,
            ),
            "visual_dispatcher": partial(
                self._run_visual_dispatcher,
                visual_coordinator,
            ),
            "visual_event_consumer": partial(
                visual_event_loop.run,
                self.stop_event,
            ),
            "ppt_reconcile": partial(ppt_coordinator.run, self.stop_event),
        }
        self.tasks = {
            name: asyncio.create_task(
                self._supervise_loop(name, runner),
                name=f"orchestrator-{name.replace('_', '-')}",
            )
            for name, runner in runners.items()
        }
        for name, task in self.tasks.items():
            task.add_done_callback(partial(self._record_loop_exit, name))

    async def _run_executor(
        self,
        executor: NodeExecutor,
        recovery: StaleNodeRecovery | None = None,
    ) -> None:
        consecutive_errors = 0
        next_recovery_at = 0.0
        while not self.stop_event.is_set():
            try:
                loop_time = asyncio.get_running_loop().time()
                if recovery is not None and loop_time >= next_recovery_at:
                    await recovery.recover_once()
                    next_recovery_at = (
                        loop_time
                        + self.settings.worker.recovery_scan_interval_seconds
                    )
                executed = await executor.run_once()
            except Exception as exc:
                if not _is_transient_loop_error(exc):
                    raise
                consecutive_errors += 1
                self._record_transient_loop_error("node_executor", exc)
                executed = 0
                await self._wait_for_retry(consecutive_errors)
                continue
            if consecutive_errors:
                self._record_loop_recovery("node_executor")
                consecutive_errors = 0
            if executed > 0:
                continue
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=self.settings.worker.claim_poll_interval_seconds,
                )
            except TimeoutError:
                continue

    async def _supervise_loop(
        self,
        name: str,
        runner: Callable[[], Awaitable[None]],
    ) -> None:
        consecutive_errors = 0
        recovering = False
        while not self.stop_event.is_set():
            if recovering:
                self._record_loop_recovery(name)
                recovering = False
            else:
                self.loop_states.setdefault(name, {})["state"] = "running"
            try:
                await runner()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not _is_transient_loop_error(exc):
                    raise
                consecutive_errors += 1
                self._record_transient_loop_error(name, exc)
                await self._wait_for_retry(consecutive_errors)
                recovering = not self.stop_event.is_set()
                continue
            if self.stop_event.is_set():
                return
            raise RuntimeError(f"关键后台循环意外退出: {name}")

    async def _wait_for_retry(self, consecutive_errors: int) -> None:
        delay = min(
            self.settings.worker.transient_error_max_delay_seconds,
            self.settings.worker.transient_error_base_delay_seconds
            * (2 ** max(0, consecutive_errors - 1)),
        )
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=delay)
        except TimeoutError:
            return

    def _record_transient_loop_error(self, name: str, exc: BaseException) -> None:
        state = self.loop_states.setdefault(name, {})
        state["state"] = "degraded"
        retry_count = state.get("transient_retries", 0)
        state["transient_retries"] = (
            retry_count + 1 if isinstance(retry_count, int) else 1
        )
        state["last_transient_error"] = type(exc).__name__
        logger.warning(
            "Orchestrator 关键循环遇到可恢复基础设施错误",
            extra={
                "loop_name": name,
                "exception_type": type(exc).__name__,
                "outcome": "retry",
            },
        )

    def _record_loop_recovery(self, name: str) -> None:
        state = self.loop_states.setdefault(name, {})
        state["state"] = "running"
        recovery_count = state.get("recoveries", 0)
        state["recoveries"] = (
            recovery_count + 1 if isinstance(recovery_count, int) else 1
        )
        state["last_transient_error"] = None
        state["last_recovered_at"] = datetime.now(UTC).isoformat()

    async def _run_visual_dispatcher(
        self,
        coordinator: VisualNodeCoordinator,
    ) -> None:
        while not self.stop_event.is_set():
            executed = await coordinator.run_once()
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
            self.loop_states.setdefault(name, {})["state"] = "fatal"
            self.stop_event.set()
            self._request_fatal_exit(name, error)
        elif not self.stop_event.is_set():
            self.loop_errors[name] = "必需后台循环意外退出"
            self.loop_states.setdefault(name, {})["state"] = "fatal"
            self.stop_event.set()
            self._request_fatal_exit(name, RuntimeError("必需后台循环意外退出"))

    def _request_fatal_exit(self, name: str, error: BaseException) -> None:
        self.fatal_exit_requested = True
        logger.critical(
            "Orchestrator 关键循环发生不可恢复错误，申请退出主进程",
            extra={
                "loop_name": name,
                "exception_type": type(error).__name__,
                "outcome": "fatal_exit",
            },
        )
        # 生产服务必须退出并交给 Docker 重启；测试/开发环境只记录退出意图。
        if self._started and self.settings.service.environment == "production":
            os.kill(os.getpid(), signal.SIGTERM)

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
                "state": (
                    "fatal"
                    if error is not None
                    else self.loop_states.get(name, {}).get(
                        "state",
                        "running" if ready else "stopped",
                    )
                ),
                "last_transient_error": self.loop_states.get(name, {}).get(
                    "last_transient_error"
                ),
                "transient_retries": self.loop_states.get(name, {}).get(
                    "transient_retries",
                    0,
                ),
                "recoveries": self.loop_states.get(name, {}).get("recoveries", 0),
                "last_recovered_at": self.loop_states.get(name, {}).get(
                    "last_recovered_at"
                ),
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
