from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from packages.platform_contracts.status import Priority, TaskType

JsonObject = dict[str, Any]


class VisualEventType(StrEnum):
    PROGRESS = "VISUAL_ANALYSIS_PROGRESS"
    COMPLETED = "VISUAL_ANALYSIS_COMPLETED"


@dataclass(frozen=True, slots=True)
class VisualAnalysisCommand:
    command_id: UUID
    task_id: str
    task_type: TaskType
    node_id: int
    submission_id: str
    local_video_path: str
    priority: Priority
    strategy: JsonObject = field(default_factory=dict)
    student_count: int | None = None
    front_points: list[JsonObject] | None = None
    back_point: list[JsonObject] | None = None

    def __post_init__(self) -> None:
        if self.task_type not in {
            TaskType.TEACHER_BEHAVIOR,
            TaskType.STUDENT_BEHAVIOR,
        }:
            raise ValueError(f"视觉命令不支持任务类型: {self.task_type.value}")
        if not self.task_id or not self.submission_id:
            raise ValueError("视觉命令 task_id 和 submission_id 不能为空")
        if self.node_id <= 0:
            raise ValueError("视觉命令 node_id 必须大于 0")
        if not Path(self.local_video_path).is_absolute():
            raise ValueError("local_video_path 必须是绝对本地路径")
        if self.task_type is TaskType.STUDENT_BEHAVIOR and self.student_count is None:
            raise ValueError("学生行为视觉命令缺少 student_count")

    def to_bytes(self) -> bytes:
        payload: JsonObject = {
            "task_id": self.task_id,
            "task_type": self.task_type.value,
            "node_id": self.node_id,
            "submission_id": self.submission_id,
            "local_video_path": self.local_video_path,
            "priority": self.priority.value,
            "strategy": self.strategy,
        }
        if self.student_count is not None:
            payload["student_count"] = self.student_count
        if self.front_points is not None:
            payload["front_points"] = self.front_points
        if self.back_point is not None:
            payload["back_point"] = self.back_point
        return json.dumps(
            {
                "event_id": str(self.command_id),
                "event_type": "VISUAL_ANALYSIS_REQUESTED",
                "payload": payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()

    @classmethod
    def from_bytes(cls, value: bytes) -> VisualAnalysisCommand:
        try:
            envelope = json.loads(value)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("视觉命令不是有效 JSON") from exc
        if not isinstance(envelope, dict):
            raise ValueError("视觉命令必须是 JSON 对象")
        if envelope.get("event_type") != "VISUAL_ANALYSIS_REQUESTED":
            raise ValueError(f"不支持的视觉命令类型: {envelope.get('event_type')}")
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("视觉命令缺少 payload")
        forbidden_fields = {"image", "image_base64", "frame_bytes", "video_bytes"}
        included_forbidden = sorted(forbidden_fields.intersection(payload))
        if included_forbidden:
            raise ValueError(
                f"视觉 Kafka 命令不得携带媒体字段: {', '.join(included_forbidden)}"
            )
        try:
            return cls(
                command_id=UUID(str(envelope["event_id"])),
                task_id=str(payload["task_id"]),
                task_type=TaskType(payload["task_type"]),
                node_id=int(payload["node_id"]),
                submission_id=str(payload["submission_id"]),
                local_video_path=str(payload["local_video_path"]),
                priority=Priority(payload.get("priority", Priority.NORMAL.value)),
                strategy=_json_object(payload.get("strategy", {}), "strategy"),
                student_count=_optional_int(payload.get("student_count")),
                front_points=_optional_regions(payload.get("front_points"), "front_points"),
                back_point=_optional_regions(payload.get("back_point"), "back_point"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("local_video_path"):
                raise
            raise ValueError(f"视觉命令字段不合法: {exc}") from exc


@dataclass(frozen=True, slots=True)
class VisualAnalysisEvent:
    event_id: UUID
    command_id: UUID
    event_type: VisualEventType
    task_id: str
    task_type: TaskType
    node_id: int
    progress: int
    stage: str
    reason: str

    @classmethod
    def create(
        cls,
        command: VisualAnalysisCommand,
        *,
        event_type: VisualEventType,
        progress: int,
        stage: str,
        reason: str,
    ) -> VisualAnalysisEvent:
        if not 0 <= progress <= 100:
            raise ValueError("视觉分析进度必须在 0 到 100 之间")
        return cls(
            event_id=uuid4(),
            command_id=command.command_id,
            event_type=event_type,
            task_id=command.task_id,
            task_type=command.task_type,
            node_id=command.node_id,
            progress=progress,
            stage=stage,
            reason=reason,
        )

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "event_id": str(self.event_id),
                "event_type": self.event_type.value,
                "payload": {
                    "command_id": str(self.command_id),
                    "task_id": self.task_id,
                    "task_type": self.task_type.value,
                    "node_id": self.node_id,
                    "progress": self.progress,
                    "stage": self.stage,
                    "reason": self.reason,
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()

    @classmethod
    def from_bytes(cls, value: bytes) -> VisualAnalysisEvent:
        try:
            envelope = json.loads(value)
            payload = envelope["payload"]
            return cls(
                event_id=UUID(str(envelope["event_id"])),
                command_id=UUID(str(payload["command_id"])),
                event_type=VisualEventType(envelope["event_type"]),
                task_id=str(payload["task_id"]),
                task_type=TaskType(payload["task_type"]),
                node_id=int(payload["node_id"]),
                progress=int(payload["progress"]),
                stage=str(payload["stage"]),
                reason=str(payload["reason"]),
            )
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"视觉分析事件不合法: {exc}") from exc


def _json_object(value: Any, field_name: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是对象")
    return value


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("student_count 必须是整数")
    return int(value)


def _optional_regions(value: Any, field_name: str) -> list[JsonObject] | None:
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{field_name} 必须是对象列表")
    return value
