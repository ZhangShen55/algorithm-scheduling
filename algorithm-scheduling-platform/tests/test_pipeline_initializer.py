import json

import pytest

from packages.platform_common.repository import NodeRecord, NodeWrite
from packages.platform_contracts.status import TaskType
from services.orchestrator_service.pipeline import PipelineInitializer


class RecordingPipelineRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, TaskType, list[NodeWrite]]] = []

    def initialize_pipeline(
        self,
        task_id: str,
        task_type: TaskType,
        nodes: list[NodeWrite],
    ) -> list[NodeRecord]:
        self.calls.append((task_id, task_type, nodes))
        return []


def command_bytes(task_type: str) -> bytes:
    return json.dumps(
        {
            "event_id": "11111111-1111-1111-1111-111111111111",
            "aggregate_type": "COURSE_TASK_TYPE",
            "aggregate_id": f"course-001:{task_type}",
            "event_type": "COURSE_TASK_REQUESTED",
            "payload": {
                "task_id": "course-001",
                "task_type": task_type,
                "priority": "NORMAL",
                "submission_id": "submission-001",
            },
        }
    ).encode()


@pytest.mark.asyncio
async def test_ppt_command_initializes_only_ppt_pipeline_nodes() -> None:
    repository = RecordingPipelineRepository()
    initializer = PipelineInitializer(repository)

    await initializer.handle(command_bytes("PPT"))

    task_id, task_type, nodes = repository.calls[0]
    assert task_id == "course-001"
    assert task_type is TaskType.PPT
    assert [node.node_code for node in nodes] == ["PPT_SLICE", "PPT_OCR", "PPT_KEYWORDS"]


@pytest.mark.asyncio
async def test_asr_command_does_not_initialize_visual_or_ppt_nodes() -> None:
    repository = RecordingPipelineRepository()
    initializer = PipelineInitializer(repository)

    await initializer.handle(command_bytes("ASR"))

    _, _, nodes = repository.calls[0]
    assert [node.node_code for node in nodes] == ["ASR_TRANSCRIPTION", "COURSE_OVERVIEW"]


@pytest.mark.asyncio
async def test_invalid_event_type_is_rejected_before_database_write() -> None:
    repository = RecordingPipelineRepository()
    initializer = PipelineInitializer(repository)
    event = json.loads(command_bytes("PPT"))
    event["event_type"] = "UNSUPPORTED_EVENT"

    with pytest.raises(ValueError, match="不支持的事件类型"):
        await initializer.handle(json.dumps(event).encode())

    assert repository.calls == []
