from datetime import UTC, datetime
from uuid import UUID

import pytest

from packages.platform_common.repository import OutboxRecord
from services.orchestrator_service.outbox import OutboxPublisher


class FakeOutboxRepository:
    def __init__(self, events: list[OutboxRecord]) -> None:
        self.events = events
        self.published: list[tuple[UUID, UUID]] = []
        self.failed: list[tuple[UUID, UUID, str]] = []

    def claim_outbox_events(self, batch_size: int) -> list[OutboxRecord]:
        return self.events[:batch_size]

    def mark_outbox_published(self, event_id: UUID, claim_token: UUID) -> None:
        self.published.append((event_id, claim_token))

    def mark_outbox_failed(self, event_id: UUID, claim_token: UUID, error: str) -> None:
        self.failed.append((event_id, claim_token, error))


class FakeProducer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[tuple[str, bytes, bytes]] = []

    async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> None:
        self.messages.append((topic, value, key))
        if self.fail:
            raise RuntimeError("Kafka 暂时不可用")


def outbox_record() -> OutboxRecord:
    return OutboxRecord(
        event_id=UUID("11111111-1111-1111-1111-111111111111"),
        aggregate_type="COURSE_TASK_TYPE",
        aggregate_id="course-001:PPT",
        event_type="COURSE_TASK_REQUESTED",
        payload={"task_id": "course-001", "task_type": "PPT", "priority": "NORMAL"},
        claim_token=UUID("22222222-2222-2222-2222-222222222222"),
        claimed_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_publisher_marks_event_only_after_kafka_confirmation() -> None:
    event = outbox_record()
    repository = FakeOutboxRepository([event])
    producer = FakeProducer()
    publisher = OutboxPublisher(repository, producer, topic="course-task-commands")

    published_count = await publisher.publish_once()

    assert published_count == 1
    assert producer.messages[0][2] == str(event.event_id).encode()
    assert repository.published == [(event.event_id, event.claim_token)]
    assert repository.failed == []
    assert publisher.metrics.published_total == 1


@pytest.mark.asyncio
async def test_publisher_keeps_failed_event_pending_and_records_metric() -> None:
    event = outbox_record()
    repository = FakeOutboxRepository([event])
    producer = FakeProducer(fail=True)
    publisher = OutboxPublisher(repository, producer, topic="course-task-commands")

    published_count = await publisher.publish_once()

    assert published_count == 0
    assert repository.published == []
    assert repository.failed[0][:2] == (event.event_id, event.claim_token)
    assert "Kafka 暂时不可用" in repository.failed[0][2]
    assert publisher.metrics.failed_total == 1
