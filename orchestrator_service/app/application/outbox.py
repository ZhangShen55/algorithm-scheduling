from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from packages.platform_common.metrics import PlatformMetrics
from packages.platform_common.repository import OutboxRecord


class OutboxRepository(Protocol):
    def claim_outbox_events(self, batch_size: int) -> list[OutboxRecord]: ...

    def mark_outbox_published(self, event_id: UUID, claim_token: UUID) -> None: ...

    def mark_outbox_failed(self, event_id: UUID, claim_token: UUID, error: str) -> None: ...


class AsyncKafkaProducer(Protocol):
    async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> object: ...


@dataclass(slots=True)
class OutboxPublisherMetrics:
    claimed_total: int = 0
    published_total: int = 0
    failed_total: int = 0
    last_error_type: str | None = None


class OutboxPublisher:
    def __init__(
        self,
        repository: OutboxRepository,
        producer: AsyncKafkaProducer,
        *,
        topic: str,
        batch_size: int = 20,
        poll_interval_seconds: float = 1.0,
        platform_metrics: PlatformMetrics | None = None,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("Outbox 批次大小必须大于 0")
        if poll_interval_seconds <= 0:
            raise ValueError("Outbox 轮询间隔必须大于 0")
        self._repository = repository
        self._producer = producer
        self._topic = topic
        self._batch_size = batch_size
        self._poll_interval_seconds = poll_interval_seconds
        self._platform_metrics = platform_metrics
        self.metrics = OutboxPublisherMetrics()

    async def publish_once(self) -> int:
        events = await asyncio.to_thread(
            self._repository.claim_outbox_events,
            self._batch_size,
        )
        self.metrics.claimed_total += len(events)
        published = 0
        for event in events:
            envelope = {
                "event_id": str(event.event_id),
                "aggregate_type": event.aggregate_type,
                "aggregate_id": event.aggregate_id,
                "event_type": event.event_type,
                "payload": event.payload,
            }
            try:
                await self._producer.send_and_wait(
                    self._topic,
                    json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode(),
                    str(event.event_id).encode(),
                )
            except Exception as exc:
                self.metrics.failed_total += 1
                self.metrics.last_error_type = type(exc).__name__
                if self._platform_metrics is not None:
                    self._platform_metrics.record_outbox_publish("failed")
                await asyncio.to_thread(
                    self._repository.mark_outbox_failed,
                    event.event_id,
                    event.claim_token,
                    str(exc),
                )
                continue

            await asyncio.to_thread(
                self._repository.mark_outbox_published,
                event.event_id,
                event.claim_token,
            )
            self.metrics.published_total += 1
            if self._platform_metrics is not None:
                self._platform_metrics.record_outbox_publish("published")
            published += 1
        return published

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self.publish_once()
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                continue
