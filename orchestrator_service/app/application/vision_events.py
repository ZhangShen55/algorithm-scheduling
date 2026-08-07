from typing import Protocol

from packages.platform_contracts.vision import VisualAnalysisCommand


class AsyncKafkaProducer(Protocol):
    async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> object: ...


class VisualCommandPublisher:
    def __init__(self, producer: AsyncKafkaProducer, *, topic: str) -> None:
        self._producer = producer
        self._topic = topic

    async def publish(self, command: VisualAnalysisCommand) -> None:
        key = f"{command.task_id}:{command.task_type.value}".encode()
        await self._producer.send_and_wait(
            self._topic,
            command.to_bytes(),
            key,
        )
