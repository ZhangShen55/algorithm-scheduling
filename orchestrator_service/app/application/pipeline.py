from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from packages.platform_common.repository import NodeRecord, NodeWrite
from packages.platform_contracts.status import NodeStatus, Priority, TaskType


class PipelineRepository(Protocol):
    def initialize_pipeline(
        self,
        task_id: str,
        task_type: TaskType,
        nodes: list[NodeWrite],
    ) -> list[NodeRecord]: ...


@dataclass(frozen=True, slots=True)
class CourseTaskCommand:
    event_id: UUID
    submission_id: str
    task_id: str
    task_type: TaskType
    priority: Priority

    @classmethod
    def from_bytes(cls, value: bytes) -> CourseTaskCommand:
        decoded: dict[str, Any] = json.loads(value)
        if decoded.get("event_type") != "COURSE_TASK_REQUESTED":
            raise ValueError(f"不支持的事件类型: {decoded.get('event_type')}")
        payload = decoded.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("课程任务事件缺少 payload")
        return cls(
            event_id=UUID(str(decoded["event_id"])),
            submission_id=str(payload["submission_id"]),
            task_id=str(payload["task_id"]),
            task_type=TaskType(payload["task_type"]),
            priority=Priority(payload.get("priority", Priority.NORMAL.value)),
        )


def pipeline_nodes(task_type: TaskType, priority: Priority) -> list[NodeWrite]:
    if task_type is TaskType.PPT:
        return [
            NodeWrite(
                "PPT_SLICE",
                NodeStatus.PENDING,
                priority,
                "等待 PPT 切片处理",
                "ppt_slice",
            ),
            NodeWrite(
                "PPT_OCR",
                NodeStatus.WAITING_PREREQUISITE,
                priority,
                "等待 PPT 切片完成",
                "ocr",
                ("PPT_SLICE",),
            ),
            NodeWrite(
                "PPT_KEYWORDS",
                NodeStatus.WAITING_PREREQUISITE,
                priority,
                "等待 OCR 完成",
                "extract_keywords",
                ("PPT_OCR",),
            ),
        ]
    if task_type is TaskType.ASR:
        return [
            NodeWrite(
                "ASR_TRANSCRIPTION",
                NodeStatus.PENDING,
                priority,
                "等待离线语音转写",
                "asr_offline",
            ),
            NodeWrite(
                "COURSE_OVERVIEW",
                NodeStatus.WAITING_PREREQUISITE,
                priority,
                "等待语音转写完成",
                "course_overviews",
                ("ASR_TRANSCRIPTION",),
            ),
        ]
    if task_type is TaskType.TEACHER_BEHAVIOR:
        return [
            NodeWrite(
                "TEACHER_BEHAVIOR_ANALYSIS",
                NodeStatus.PENDING,
                priority,
                "等待教师行为视觉分析",
            )
        ]
    return [
        NodeWrite(
            "STUDENT_BEHAVIOR_ANALYSIS",
            NodeStatus.PENDING,
            priority,
            "等待学生行为视觉分析",
        )
    ]


class PipelineInitializer:
    def __init__(self, repository: PipelineRepository) -> None:
        self._repository = repository

    async def handle(self, value: bytes) -> list[NodeRecord]:
        command = CourseTaskCommand.from_bytes(value)
        return await asyncio.to_thread(
            self._repository.initialize_pipeline,
            command.task_id,
            command.task_type,
            pipeline_nodes(command.task_type, command.priority),
        )
