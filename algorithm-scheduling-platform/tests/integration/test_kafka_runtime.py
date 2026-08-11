from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from aiokafka.admin import AIOKafkaAdminClient

from packages.platform_common.kafka import (
    AioKafkaConsumerAdapter,
    AioKafkaProducerAdapter,
    KafkaMessage,
    KafkaTopicManager,
)

pytestmark = pytest.mark.integration

BOOTSTRAP_SERVERS = ["127.0.0.1:9092"]
PROJECT_ROOT = Path(__file__).resolve().parents[2]


async def _poll_one(
    consumer: AioKafkaConsumerAdapter,
    *,
    timeout_seconds: float = 10.0,
) -> KafkaMessage:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        messages = await consumer.poll(timeout_seconds=0.25)
        if messages:
            return messages[0]
    raise AssertionError("在截止时间内未收到 Kafka 消息")


def _delete_group_cli(group_id: str) -> None:
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(PROJECT_ROOT / "deploy" / "docker-compose.infrastructure.yml"),
            "exec",
            "-T",
            "kafka",
            "/opt/kafka/bin/kafka-consumer-groups.sh",
            "--bootstrap-server",
            "localhost:9092",
            "--delete",
            "--group",
            group_id,
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


async def _delete_kafka_resources(topic: str, *group_ids: str) -> None:
    admin = AIOKafkaAdminClient(
        bootstrap_servers=BOOTSTRAP_SERVERS,
        client_id=f"milestone-2a-cleanup-{uuid4().hex[:8]}",
    )
    await admin.start()
    try:
        for group_id in group_ids:
            if (
                re.fullmatch(
                    r"algorithm-test-(?:committed|redelivery)-[0-9a-f]{32}",
                    group_id,
                )
                is None
            ):
                raise AssertionError(f"拒绝删除非 Kafka 测试 Consumer Group: {group_id}")
        existing_groups = {str(row[0]) for row in await admin.list_consumer_groups()}
        for group_id in group_ids:
            if group_id in existing_groups:
                await asyncio.to_thread(_delete_group_cli, group_id)
        remaining_groups = {str(row[0]) for row in await admin.list_consumer_groups()}
        assert not (set(group_ids) & remaining_groups)
        await admin.delete_topics([topic])
    finally:
        await admin.close()


@pytest.mark.asyncio
async def test_real_kafka_publish_manual_commit_and_uncommitted_redelivery() -> None:
    suffix = uuid4().hex
    topic = f"algorithm.test.milestone2a.{suffix}"
    committed_group = f"algorithm-test-committed-{suffix}"
    redelivery_group = f"algorithm-test-redelivery-{suffix}"
    manager = KafkaTopicManager(
        bootstrap_servers=BOOTSTRAP_SERVERS,
        client_id=f"milestone-2a-admin-{suffix[:8]}",
        topics=(topic,),
    )
    producer = AioKafkaProducerAdapter(
        bootstrap_servers=BOOTSTRAP_SERVERS,
        client_id=f"milestone-2a-producer-{suffix[:8]}",
    )

    await manager.ensure_topics()
    await producer.start()
    try:
        committed_consumer = AioKafkaConsumerAdapter(
            topics=[topic],
            bootstrap_servers=BOOTSTRAP_SERVERS,
            group_id=committed_group,
            client_id=f"committed-first-{suffix[:8]}",
            max_poll_records=10,
        )
        await committed_consumer.start()
        try:
            await producer.send_and_wait(topic, b"first", b"event-1")
            first = await _poll_one(committed_consumer)
            assert first.value == b"first"
            await committed_consumer.commit(first)
        finally:
            await committed_consumer.stop()

        resumed_consumer = AioKafkaConsumerAdapter(
            topics=[topic],
            bootstrap_servers=BOOTSTRAP_SERVERS,
            group_id=committed_group,
            client_id=f"committed-second-{suffix[:8]}",
            max_poll_records=10,
        )
        await resumed_consumer.start()
        try:
            await producer.send_and_wait(topic, b"second", b"event-2")
            second = await _poll_one(resumed_consumer)
            assert second.value == b"second"
            assert second.offset == first.offset + 1
            await resumed_consumer.commit(second)
        finally:
            await resumed_consumer.stop()

        first_delivery = AioKafkaConsumerAdapter(
            topics=[topic],
            bootstrap_servers=BOOTSTRAP_SERVERS,
            group_id=redelivery_group,
            client_id=f"redelivery-first-{suffix[:8]}",
            max_poll_records=10,
        )
        await first_delivery.start()
        try:
            delivered = await _poll_one(first_delivery)
        finally:
            await first_delivery.stop()

        second_delivery = AioKafkaConsumerAdapter(
            topics=[topic],
            bootstrap_servers=BOOTSTRAP_SERVERS,
            group_id=redelivery_group,
            client_id=f"redelivery-second-{suffix[:8]}",
            max_poll_records=10,
        )
        await second_delivery.start()
        try:
            redelivered = await _poll_one(second_delivery)
            assert redelivered.offset == delivered.offset
            assert redelivered.value == delivered.value
        finally:
            await second_delivery.stop()
    finally:
        await producer.stop()
        await _delete_kafka_resources(topic, committed_group, redelivery_group)
