from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol

import httpx
from fastapi import FastAPI
from sqlalchemy import Engine, create_engine

from packages.platform_common.kafka import (
    AioKafkaConsumerAdapter,
    AioKafkaProducerAdapter,
    KafkaMessage,
    KafkaTopicManager,
)
from packages.platform_common.lease_resilience import LeaseRenewalPolicy
from packages.platform_common.repository import CourseRepository
from packages.platform_contracts.vision import (
    LegacyVisualCommandError,
    VisualAnalysisCommand,
)

from ..application.analyzer import CourseVisualAnalyzer
from ..application.events import VisualCommandProcessor
from ..core.config import VisionSettings
from ..domain.evidence import VisionEvidenceConfig, VisionEvidencePublisher
from .capacity import CapacityLeaseHttpClient, CapacityUnavailableError
from .media import FFmpegFrameExtractor
from .metrics import VisionPipelineMetrics
from .vbas import (
    ControlVbasOfflineCapacitySource,
    VbasBatchClient,
    VbasBatchConfig,
    VbasOfflineCapacityGate,
)


class VisualConsumer(Protocol):
    async def poll(self, *, timeout_seconds: float) -> list[KafkaMessage]: ...

    async def commit(self, message: KafkaMessage) -> None: ...

    async def keepalive(self) -> None: ...

    def set_partitions_revoked_callback(self, callback: Callable[..., Any]) -> None: ...


class CommandHandler(Protocol):
    async def handle(self, value: bytes) -> None: ...


@dataclass(slots=True)
class _PartitionCommitState:
    next_offset: int
    messages: dict[int, KafkaMessage]
    completed_offsets: set[int]


class VisualCommandConsumerLoop:
    def __init__(
        self,
        consumer: VisualConsumer,
        processor: CommandHandler,
        *,
        poll_timeout_seconds: float,
        retry_delay_seconds: float = 1.0,
        concurrency: int = 1,
        metrics: VisionPipelineMetrics | None = None,
    ) -> None:
        if retry_delay_seconds <= 0:
            raise ValueError("视觉命令重试间隔必须大于 0")
        if concurrency <= 0:
            raise ValueError("视觉课程并发上限必须大于 0")
        self._consumer = consumer
        self._processor = processor
        self._poll_timeout_seconds = poll_timeout_seconds
        self._retry_delay_seconds = retry_delay_seconds
        self._concurrency = concurrency
        self._metrics = metrics
        self._pending: deque[KafkaMessage] = deque()
        self._in_flight: dict[asyncio.Task[bool], KafkaMessage] = {}
        self._partitions: dict[tuple[str, int], _PartitionCommitState] = {}
        self._handled_total = 0
        self._peak_pending = 0
        self._peak_in_flight = 0
        set_rebalance_callback = getattr(
            self._consumer,
            "set_partitions_revoked_callback",
            None,
        )
        if callable(set_rebalance_callback):
            set_rebalance_callback(self._on_partitions_revoked)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def in_flight_count(self) -> int:
        return len(self._in_flight)

    @property
    def peak_pending_count(self) -> int:
        return self._peak_pending

    @property
    def peak_in_flight_count(self) -> int:
        return self._peak_in_flight

    @staticmethod
    def _partition_key(message: KafkaMessage) -> tuple[str, int]:
        return message.topic, message.partition

    def _register_messages(self, messages: list[KafkaMessage]) -> None:
        by_partition: dict[tuple[str, int], list[KafkaMessage]] = {}
        for message in messages:
            by_partition.setdefault(self._partition_key(message), []).append(message)

        for partition_key, partition_messages in by_partition.items():
            ordered = sorted(partition_messages, key=lambda item: item.offset)
            state = self._partitions.get(partition_key)
            if state is None:
                state = _PartitionCommitState(
                    next_offset=ordered[0].offset,
                    messages={},
                    completed_offsets=set(),
                )
                self._partitions[partition_key] = state
            for message in ordered:
                if message.offset < state.next_offset or message.offset in state.messages:
                    continue
                state.messages[message.offset] = message
                try:
                    VisualAnalysisCommand.from_bytes(message.value)
                except (LegacyVisualCommandError, ValueError):
                    # 旧代次或非法信封无法通过重试恢复，仍受连续 offset 水位约束。
                    state.completed_offsets.add(message.offset)
                    continue
                self._pending.append(message)
        self._peak_pending = max(self._peak_pending, len(self._pending))
        self._record_metrics()

    async def _commit_contiguous(self) -> None:
        for state in self._partitions.values():
            while state.next_offset in state.completed_offsets:
                message = state.messages[state.next_offset]
                await self._consumer.commit(message)
                state.completed_offsets.remove(state.next_offset)
                del state.messages[state.next_offset]
                state.next_offset += 1

    async def _process(
        self,
        message: KafkaMessage,
        stop_event: asyncio.Event | None,
    ) -> bool:
        while True:
            try:
                await self._processor.handle(message.value)
                return True
            except CapacityUnavailableError:
                if stop_event is None:
                    raise
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=self._retry_delay_seconds
                    )
                except TimeoutError:
                    continue
                return False

    def _fill_available_slots(self, stop_event: asyncio.Event | None) -> None:
        if stop_event is not None and stop_event.is_set():
            return
        while self._pending and len(self._in_flight) < self._concurrency:
            message = self._pending.popleft()
            task = asyncio.create_task(
                self._process(message, stop_event),
                name=(
                    f"vision-command-{message.topic}-{message.partition}-"
                    f"{message.offset}"
                ),
            )
            self._in_flight[task] = message
        self._peak_in_flight = max(self._peak_in_flight, len(self._in_flight))
        self._record_metrics()

    async def _reap_completed(
        self,
        *,
        wait: bool,
        timeout_seconds: float | None = None,
    ) -> int:
        if not self._in_flight:
            return 0
        if wait:
            done, _ = await asyncio.wait(
                self._in_flight,
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
        else:
            done = {task for task in self._in_flight if task.done()}
        handled = 0
        failure: BaseException | None = None
        for task in done:
            message = self._in_flight.pop(task, None)
            if message is None:
                continue
            try:
                succeeded = task.result()
            except BaseException as exc:  # noqa: BLE001 - 基础设施错误交给运行时门禁
                failure = failure or exc
                continue
            if not succeeded:
                continue
            state = self._partitions[self._partition_key(message)]
            state.completed_offsets.add(message.offset)
            handled += 1
        self._handled_total += handled
        self._record_metrics()
        await self._commit_contiguous()
        if failure is not None:
            raise failure
        return handled

    async def _cancel_in_flight(self) -> None:
        tasks = tuple(self._in_flight)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._in_flight.clear()
        self._record_metrics()

    async def _on_partitions_revoked(self, partitions: set[Any]) -> None:
        revoked = {
            (str(partition.topic), int(partition.partition))
            for partition in partitions
        }
        tasks = [
            task
            for task, message in self._in_flight.items()
            if self._partition_key(message) in revoked
        ]
        for task in tasks:
            self._in_flight.pop(task, None)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._pending = deque(
            message
            for message in self._pending
            if self._partition_key(message) not in revoked
        )
        for partition_key in revoked:
            self._partitions.pop(partition_key, None)
        self._record_metrics()

    def _record_metrics(self) -> None:
        if self._metrics is not None:
            self._metrics.set_command_counts(
                pending=len(self._pending),
                in_flight=len(self._in_flight),
                limit=self._concurrency,
            )

    async def run_once(self, *, stop_event: asyncio.Event | None = None) -> int:
        if self._pending or self._in_flight:
            raise RuntimeError("视觉命令单轮入口不能与未收敛任务重入")
        handled_before = self._handled_total
        messages = await self._consumer.poll(timeout_seconds=self._poll_timeout_seconds)
        if not messages:
            return 0
        self._register_messages(messages)
        await self._commit_contiguous()
        self._fill_available_slots(stop_event)
        try:
            while self._pending or self._in_flight:
                if self._in_flight:
                    handled = await self._reap_completed(
                        wait=True,
                        timeout_seconds=self._poll_timeout_seconds,
                    )
                    if handled == 0:
                        keepalive = getattr(self._consumer, "keepalive", None)
                        if callable(keepalive):
                            await keepalive()
                self._fill_available_slots(stop_event)
                if stop_event is not None and stop_event.is_set() and self._pending:
                    break
        finally:
            if self._in_flight:
                await self._cancel_in_flight()
        return self._handled_total - handled_before

    async def run(self, stop_event: asyncio.Event) -> None:
        try:
            while True:
                await self._reap_completed(wait=False)
                self._fill_available_slots(stop_event)

                if stop_event.is_set():
                    if not self._in_flight:
                        return
                    await self._reap_completed(wait=True)
                    continue

                if not self._pending and len(self._in_flight) < self._concurrency:
                    messages = await self._consumer.poll(
                        timeout_seconds=self._poll_timeout_seconds
                    )
                    if messages:
                        self._register_messages(messages)
                        await self._commit_contiguous()
                        self._fill_available_slots(stop_event)
                        continue

                if self._in_flight:
                    handled = await self._reap_completed(
                        wait=True,
                        timeout_seconds=self._poll_timeout_seconds,
                    )
                    if handled == 0:
                        keepalive = getattr(self._consumer, "keepalive", None)
                        if callable(keepalive):
                            await keepalive()
        finally:
            await self._cancel_in_flight()


@dataclass(slots=True)
class VisionResources:
    engine: Any
    repository: Any
    http_client: Any
    producer: Any
    consumer: Any
    topic_manager: Any


ResourceFactory = Callable[[VisionSettings], VisionResources]


class VisionOrchestratorRuntime:
    REQUIRED_LOOPS = ("visual_command_consumer",)

    def __init__(
        self,
        settings: VisionSettings,
        *,
        resource_factory: ResourceFactory | None = None,
        analyzer_factory: Callable[[VisionResources], CourseVisualAnalyzer]
        | None = None,
    ) -> None:
        self.settings = settings
        self._resource_factory = resource_factory or self._build_resources
        self._analyzer_factory = analyzer_factory or self._build_analyzer
        self.resources: VisionResources | None = None
        self.stop_event = asyncio.Event()
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.loop_errors: dict[str, str] = {}
        self._started = False
        self._producer_started = False
        self._consumer_started = False
        self._app: FastAPI | None = None
        self._pipeline_metrics: VisionPipelineMetrics | None = None

    def attach(self, app: FastAPI) -> None:
        self._app = app
        app.state.vision_runtime = self
        app.state.runtime_loops_started = self._started
        app.state.runtime_tasks = dict(self.tasks)
        app.state.course_repository = (
            self.resources.repository if self.resources is not None else None
        )

    def _build_resources(self, settings: VisionSettings) -> VisionResources:
        postgres = settings.postgres
        engine: Engine = create_engine(
            postgres.dsn,
            pool_size=postgres.pool_size,
            max_overflow=postgres.max_overflow,
            pool_timeout=postgres.pool_timeout_seconds,
            pool_pre_ping=True,
        )
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                max(
                    settings.control.timeout_seconds,
                    settings.vbas.request_timeout_seconds,
                )
            )
        )
        bootstrap_servers = _bootstrap_servers(settings.kafka.bootstrap_servers)
        producer = AioKafkaProducerAdapter(
            bootstrap_servers=bootstrap_servers,
            client_id=f"{settings.kafka.client_id}-producer",
        )
        consumer = AioKafkaConsumerAdapter(
            topics=[settings.kafka.command_topic],
            bootstrap_servers=bootstrap_servers,
            group_id=settings.kafka.consumer_group,
            client_id=f"{settings.kafka.client_id}-consumer",
            max_poll_records=settings.kafka.max_poll_records,
            auto_offset_reset=settings.kafka.auto_offset_reset,
        )
        topic_manager = KafkaTopicManager(
            bootstrap_servers=bootstrap_servers,
            client_id=f"{settings.kafka.client_id}-topic-admin",
            topics=(settings.kafka.command_topic, settings.kafka.event_topic),
            partitions=settings.kafka.topic_partitions,
            replication_factor=settings.kafka.topic_replication_factor,
        )
        return VisionResources(
            engine=engine,
            repository=CourseRepository(engine),
            http_client=http_client,
            producer=producer,
            consumer=consumer,
            topic_manager=topic_manager,
        )

    def _build_analyzer(self, resources: VisionResources) -> CourseVisualAnalyzer:
        settings = self.settings
        lease_client = CapacityLeaseHttpClient(
            resources.http_client,
            control_service_url=settings.control.base_url,
            metrics=(
                self._app.state.platform_metrics
                if self._app is not None
                else None
            ),
            renewal_policy=LeaseRenewalPolicy(
                max_attempts=settings.lease_renewal.max_attempts,
                base_delay_seconds=settings.lease_renewal.base_delay_seconds,
                max_delay_seconds=settings.lease_renewal.max_delay_seconds,
                safety_margin_seconds=settings.lease_renewal.safety_margin_seconds,
            ),
            acquire_wait_timeout_seconds=settings.lease_renewal.acquire_wait_timeout_seconds,
            acquire_retry_interval_seconds=settings.lease_renewal.acquire_retry_interval_seconds,
        )
        capacity_source = ControlVbasOfflineCapacitySource(
            resources.http_client,
            control_service_url=settings.control.base_url,
            refresh_seconds=settings.vbas.capacity_snapshot_refresh_seconds,
            request_timeout_seconds=settings.control.timeout_seconds,
        )
        capacity_gate = VbasOfflineCapacityGate(
            capacity_source,
            wait_timeout_seconds=settings.lease_renewal.acquire_wait_timeout_seconds,
            retry_interval_seconds=settings.lease_renewal.acquire_retry_interval_seconds,
            shutdown_event=self.stop_event,
        )
        vbas = VbasBatchClient(
            resources.http_client,
            lease_client,
            config=VbasBatchConfig(
                batch_size=min(settings.scan.batch_size, settings.vbas.max_batch_size),
                lease_ttl_seconds=settings.vbas.lease_ttl_seconds,
                request_timeout_seconds=settings.vbas.request_timeout_seconds,
                capacity_retry_delay_seconds=settings.worker.poll_interval_seconds,
                transient_max_attempts=settings.vbas.transient_max_attempts,
                transient_retry_base_delay_seconds=(
                    settings.vbas.transient_retry_base_delay_seconds
                ),
                transient_retry_max_delay_seconds=(
                    settings.vbas.transient_retry_max_delay_seconds
                ),
            ),
            capacity_gate=capacity_gate,
            shutdown_event=self.stop_event,
        )
        extractor = FFmpegFrameExtractor(
            course_root=settings.storage.course_root,
            ffmpeg_binary=settings.media.ffmpeg_binary,
            ffprobe_binary=settings.media.ffprobe_binary,
            command_timeout_seconds=settings.media.command_timeout_seconds,
            max_concurrent_processes=settings.media.max_concurrent_processes,
            batch_extraction_enabled=settings.media.batch_extraction_enabled,
            metrics=self._pipeline_metrics,
        )
        evidence = VisionEvidencePublisher(
            result_root=settings.storage.result_root,
            config=VisionEvidenceConfig(
                max_per_category=settings.evidence.max_per_category,
                max_total=settings.evidence.max_total,
                same_category_min_interval_seconds=(
                    settings.evidence.same_category_min_interval_seconds
                ),
            ),
        )
        return CourseVisualAnalyzer(
            resources.repository,
            extractor,
            vbas,
            evidence,
            settings=settings,
            metrics=self._pipeline_metrics,
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
            if (
                self._pipeline_metrics is None
                and self._app is not None
                and hasattr(self._app.state, "platform_metrics")
            ):
                self._pipeline_metrics = VisionPipelineMetrics(
                    self._app.state.platform_metrics.registry
                )
            analyzer = self._analyzer_factory(self.resources)
            processor = VisualCommandProcessor(
                analyzer,
                self.resources.repository,
                self.resources.producer,
                event_topic=self.settings.kafka.event_topic,
            )
            loop = VisualCommandConsumerLoop(
                self.resources.consumer,
                processor,
                poll_timeout_seconds=self.settings.kafka.poll_timeout_seconds,
                retry_delay_seconds=self.settings.worker.poll_interval_seconds,
                concurrency=self.settings.worker.concurrency,
                metrics=self._pipeline_metrics,
            )
            self.tasks = {
                "visual_command_consumer": asyncio.create_task(
                    loop.run(self.stop_event),
                    name="vision-orchestrator-command-consumer",
                )
            }
            for name, task in self.tasks.items():
                task.add_done_callback(partial(self._record_loop_exit, name))
            self._started = True
            self.attach(app)
        except BaseException:
            await self.stop(app)
            raise

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
                "detail": "vision orchestrator 运行时尚未启动",
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
        for name, check, required in dependency_checks:
            checks[name] = check
            if not required:
                check["ready"] = True
        ready = all(bool(check["ready"]) for check in checks.values())
        return {"status": "ready" if ready else "not_ready", "checks": checks}

    async def _check_postgres(self) -> tuple[str, dict[str, object], bool]:
        resources = self.resources
        assert resources is not None
        check = await self._dependency_check(
            lambda: asyncio.to_thread(resources.repository.count_courses),
            "PostgreSQL 连接正常",
        )
        return "postgresql", check, self.settings.readiness.require_postgres

    async def _check_kafka(self) -> tuple[str, dict[str, object], bool]:
        assert self.resources is not None
        check = await self._dependency_check(
            self.resources.consumer.lag,
            "Kafka Consumer 连接正常",
        )
        return "kafka", check, self.settings.readiness.require_kafka

    async def _check_control(self) -> tuple[str, dict[str, object], bool]:
        resources = self.resources
        assert resources is not None

        async def probe() -> None:
            response = await resources.http_client.get(
                f"{self.settings.control.base_url.rstrip('/')}/health"
            )
            response.raise_for_status()

        check = await self._dependency_check(probe, "control-service 连接正常")
        return "control_service", check, self.settings.readiness.require_control

    async def _dependency_check(
        self,
        operation: Callable[[], Any],
        success_detail: str,
    ) -> dict[str, object]:
        try:
            result = operation()
            if hasattr(result, "__await__"):
                await asyncio.wait_for(
                    result,
                    timeout=self.settings.readiness.dependency_timeout_seconds,
                )
        except Exception as exc:  # noqa: BLE001 - readiness must report arbitrary dependency failures
            return {"ready": False, "detail": f"依赖检查失败: {exc}"}
        return {"ready": True, "detail": success_detail}

    @asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncIterator[None]:
        await self.start(app)
        try:
            yield
        finally:
            await self.stop(app)


def _bootstrap_servers(value: str) -> list[str]:
    servers = [item.strip() for item in value.split(",") if item.strip()]
    if not servers:
        raise ValueError("Kafka bootstrap_servers 不能为空")
    return servers
