from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from aiokafka.errors import TopicAlreadyExistsError
from aiokafka.structs import TopicPartition

from packages.platform_common.kafka import (
    REQUIRED_TOPICS,
    AioKafkaConsumerAdapter,
    AioKafkaProducerAdapter,
    KafkaMessage,
    KafkaTopicManager,
)


class FakeProducer:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.messages: list[tuple[str, bytes, bytes]] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> object:
        self.messages.append((topic, value, key))
        return object()


@dataclass(frozen=True)
class FakeRecord:
    topic: str
    partition: int
    offset: int
    key: bytes | None
    value: bytes
    timestamp: int


class FakeConsumer:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.commits: list[dict[TopicPartition, Any]] = []
        self.partition = TopicPartition("algorithm.course.commands", 0)
        self.paused: list[TopicPartition] = []
        self.resumed: list[TopicPartition] = []
        self.subscribed_topics: list[str] = []
        self.rebalance_listener: object | None = None

    def subscribe(self, *, topics: list[str], listener: object) -> None:
        self.subscribed_topics = list(topics)
        self.rebalance_listener = listener

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def getmany(
        self,
        *,
        timeout_ms: int,
        max_records: int,
    ) -> dict[TopicPartition, list[FakeRecord]]:
        if timeout_ms == 0:
            assert max_records == 1
            return {}
        assert timeout_ms == 250
        assert max_records == 5
        return {
            self.partition: [
                FakeRecord(
                    topic=self.partition.topic,
                    partition=self.partition.partition,
                    offset=3,
                    key=b"event-1",
                    value=b'{"event_id":"event-1"}',
                    timestamp=1_750_000_000_000,
                )
            ]
        }

    async def commit(self, offsets: dict[TopicPartition, Any]) -> None:
        self.commits.append(offsets)

    async def end_offsets(
        self,
        partitions: list[TopicPartition],
    ) -> dict[TopicPartition, int]:
        return {partition: 9 for partition in partitions}

    async def position(self, partition: TopicPartition) -> int:
        assert partition == self.partition
        return 4

    def assignment(self) -> set[TopicPartition]:
        return {self.partition}

    def pause(self, *partitions: TopicPartition) -> None:
        self.paused.extend(partitions)

    def resume(self, *partitions: TopicPartition) -> None:
        self.resumed.extend(partitions)


class FakeAdmin:
    def __init__(
        self,
        existing_topics: set[str] | None = None,
        *,
        create_error: Exception | None = None,
    ) -> None:
        self.existing_topics = existing_topics or set()
        self.create_error = create_error
        self.started = False
        self.closed = False
        self.created: list[str] = []

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def list_topics(self) -> set[str]:
        return set(self.existing_topics)

    async def create_topics(self, topics: list[Any]) -> None:
        if self.create_error is not None:
            raise self.create_error
        self.created.extend(topic.name for topic in topics)


@pytest.mark.asyncio
async def test_producer_starts_confirms_send_and_stops() -> None:
    producer = FakeProducer()
    adapter = AioKafkaProducerAdapter(
        bootstrap_servers=["127.0.0.1:9092"],
        client_id="test-producer",
        producer_factory=lambda **_: producer,
    )

    await adapter.start()
    await adapter.send_and_wait("algorithm.course.commands", b"value", b"key")
    await adapter.stop()

    assert producer.started is True
    assert producer.messages == [("algorithm.course.commands", b"value", b"key")]
    assert producer.stopped is True


@pytest.mark.asyncio
async def test_consumer_polls_normalized_messages_and_commits_next_offset() -> None:
    consumer = FakeConsumer()
    adapter = AioKafkaConsumerAdapter(
        topics=["algorithm.course.commands"],
        bootstrap_servers=["127.0.0.1:9092"],
        group_id="test-consumer",
        client_id="test-consumer",
        max_poll_records=5,
        consumer_factory=lambda *_, **__: consumer,
    )

    await adapter.start()
    messages = await adapter.poll(timeout_seconds=0.25)
    await adapter.keepalive()
    await adapter.commit(messages[0])
    lag = await adapter.lag()
    await adapter.stop()

    assert messages == [
        KafkaMessage(
            topic="algorithm.course.commands",
            partition=0,
            offset=3,
            key=b"event-1",
            value=b'{"event_id":"event-1"}',
            timestamp_ms=1_750_000_000_000,
        )
    ]
    committed = consumer.commits[0][consumer.partition]
    assert committed.offset == 4
    assert lag == {"algorithm.course.commands:0": 5}
    assert consumer.started is True
    assert consumer.stopped is True
    assert consumer.subscribed_topics == ["algorithm.course.commands"]
    assert consumer.paused == [consumer.partition]
    assert consumer.resumed == [consumer.partition]


@pytest.mark.asyncio
async def test_topic_manager_creates_only_missing_required_topics() -> None:
    admin = FakeAdmin(existing_topics={"algorithm.course.commands"})
    manager = KafkaTopicManager(
        bootstrap_servers=["127.0.0.1:9092"],
        client_id="test-admin",
        admin_factory=lambda **_: admin,
    )

    created = await manager.ensure_topics()

    assert REQUIRED_TOPICS == (
        "algorithm.course.commands",
        "algorithm.visual.commands",
        "algorithm.visual.events",
    )
    assert set(created) == {
        "algorithm.visual.commands",
        "algorithm.visual.events",
    }
    assert set(admin.created) == set(created)
    assert admin.started is True
    assert admin.closed is True


@pytest.mark.asyncio
async def test_topic_manager_treats_concurrent_topic_creation_as_idempotent() -> None:
    admin = FakeAdmin(create_error=TopicAlreadyExistsError("并发创建"))
    manager = KafkaTopicManager(
        bootstrap_servers=["127.0.0.1:9092"],
        client_id="test-admin-race",
        topics=("algorithm.course.commands",),
        admin_factory=lambda **_: admin,
    )

    created = await manager.ensure_topics()

    assert created == ("algorithm.course.commands",)
    assert admin.closed is True


@pytest.mark.asyncio
async def test_topic_manager_rejects_missing_topics_without_creating_them() -> None:
    admin = FakeAdmin(existing_topics={"algorithm.course.commands"})
    manager = KafkaTopicManager(
        bootstrap_servers=["127.0.0.1:9092"],
        client_id="test-admin-validation",
        admin_factory=lambda **_: admin,
    )

    with pytest.raises(RuntimeError, match="required Kafka topics are missing"):
        await manager.validate_topics()

    assert admin.created == []
    assert admin.started is True
    assert admin.closed is True
