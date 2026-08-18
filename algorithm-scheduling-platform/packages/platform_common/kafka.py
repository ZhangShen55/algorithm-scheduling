from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer  # type: ignore[import-untyped]
from aiokafka.admin import AIOKafkaAdminClient, NewTopic  # type: ignore[import-untyped]
from aiokafka.errors import TopicAlreadyExistsError  # type: ignore[import-untyped]
from aiokafka.structs import OffsetAndMetadata, TopicPartition  # type: ignore[import-untyped]

REQUIRED_TOPICS = (
    "algorithm.course.commands",
    "algorithm.visual.commands",
    "algorithm.visual.events",
)


@dataclass(frozen=True, slots=True)
class KafkaMessage:
    topic: str
    partition: int
    offset: int
    key: bytes | None
    value: bytes
    timestamp_ms: int | None


class AioKafkaProducerAdapter:
    def __init__(
        self,
        *,
        bootstrap_servers: list[str],
        client_id: str,
        acks: str = "all",
        producer_factory: Callable[..., Any] = AIOKafkaProducer,
    ) -> None:
        self._producer = producer_factory(
            bootstrap_servers=bootstrap_servers,
            client_id=client_id,
            acks=acks,
        )

    async def start(self) -> None:
        await self._producer.start()

    async def stop(self) -> None:
        await self._producer.stop()

    async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> object:
        return await self._producer.send_and_wait(topic, value=value, key=key)


class AioKafkaConsumerAdapter:
    def __init__(
        self,
        *,
        topics: list[str],
        bootstrap_servers: list[str],
        group_id: str,
        client_id: str,
        max_poll_records: int,
        auto_offset_reset: str = "earliest",
        consumer_factory: Callable[..., Any] = AIOKafkaConsumer,
    ) -> None:
        self._max_poll_records = max_poll_records
        self._consumer = consumer_factory(
            *topics,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            client_id=client_id,
            enable_auto_commit=False,
            auto_offset_reset=auto_offset_reset,
            max_poll_records=max_poll_records,
        )

    async def start(self) -> None:
        await self._consumer.start()

    async def stop(self) -> None:
        await self._consumer.stop()

    async def poll(self, *, timeout_seconds: float) -> list[KafkaMessage]:
        batches = await self._consumer.getmany(
            timeout_ms=max(1, int(timeout_seconds * 1000)),
            max_records=self._max_poll_records,
        )
        messages: list[KafkaMessage] = []
        for records in batches.values():
            messages.extend(
                KafkaMessage(
                    topic=record.topic,
                    partition=record.partition,
                    offset=record.offset,
                    key=record.key,
                    value=record.value,
                    timestamp_ms=record.timestamp,
                )
                for record in records
            )
        return messages

    async def commit(self, message: KafkaMessage) -> None:
        partition = TopicPartition(message.topic, message.partition)
        await self._consumer.commit(
            {partition: OffsetAndMetadata(message.offset + 1, "")}
        )

    async def lag(self) -> dict[str, int]:
        partitions = list(self._consumer.assignment())
        if not partitions:
            return {}
        end_offsets = await self._consumer.end_offsets(partitions)
        lag: dict[str, int] = {}
        for partition in partitions:
            position = await self._consumer.position(partition)
            lag[f"{partition.topic}:{partition.partition}"] = max(
                0,
                int(end_offsets[partition]) - int(position),
            )
        return lag


class KafkaTopicManager:
    def __init__(
        self,
        *,
        bootstrap_servers: list[str],
        client_id: str,
        topics: tuple[str, ...] = REQUIRED_TOPICS,
        partitions: int = 1,
        replication_factor: int = 1,
        admin_factory: Callable[..., Any] = AIOKafkaAdminClient,
    ) -> None:
        self._topics = topics
        self._partitions = partitions
        self._replication_factor = replication_factor
        self._admin = admin_factory(
            bootstrap_servers=bootstrap_servers,
            client_id=client_id,
        )

    async def ensure_topics(self) -> tuple[str, ...]:
        await self._admin.start()
        try:
            existing = await self._admin.list_topics()
            missing = tuple(topic for topic in self._topics if topic not in existing)
            if missing:
                try:
                    await self._admin.create_topics(
                        [
                            NewTopic(
                                name=topic,
                                num_partitions=self._partitions,
                                replication_factor=self._replication_factor,
                            )
                            for topic in missing
                        ]
                    )
                except TopicAlreadyExistsError:
                    pass
            return missing
        finally:
            await self._admin.close()

    async def validate_topics(self) -> None:
        await self._admin.start()
        try:
            existing = await self._admin.list_topics()
            missing = tuple(topic for topic in self._topics if topic not in existing)
            if missing:
                raise RuntimeError(
                    "required Kafka topics are missing: " + ", ".join(missing)
                )
        finally:
            await self._admin.close()
