from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from orchestrator_service.app.application.lifecycle import (
    TerminalWorkspaceCleaner,
    WorkspaceCleanupError,
)
from packages.platform_contracts.status import NodeStatus


@dataclass(frozen=True)
class TaskRecord:
    id: int
    status: NodeStatus


@dataclass(frozen=True)
class NodeRecord:
    status: NodeStatus
    artifact_path: str | None = None


class Repository:
    def __init__(self, tasks: list[TaskRecord], nodes: dict[int, list[NodeRecord]]) -> None:
        self.tasks = tasks
        self.nodes = nodes

    def list_task_types(self, task_id: str) -> list[TaskRecord]:
        assert task_id == "course-001"
        return self.tasks

    def list_nodes(self, course_task_type_id: int) -> list[NodeRecord]:
        return self.nodes[course_task_type_id]


def _cleaner(
    tmp_path: Path,
    tasks: list[TaskRecord],
    nodes: dict[int, list[NodeRecord]],
) -> TerminalWorkspaceCleaner:
    return TerminalWorkspaceCleaner(
        Repository(tasks, nodes),
        course_root=tmp_path / "course",
        result_root=tmp_path / "result",
    )


def test_terminal_workspace_cleanup_removes_course_and_keeps_result(
    tmp_path: Path,
) -> None:
    course_workspace = tmp_path / "course" / "course-001"
    result_workspace = tmp_path / "result" / "course-001"
    course_workspace.mkdir(parents=True)
    (course_workspace / "teacher.wav").write_bytes(b"audio")
    result_workspace.mkdir(parents=True)
    artifact = result_workspace / "ppt" / "slide-001.png"
    artifact.parent.mkdir()
    artifact.write_bytes(b"image")
    cleaner = _cleaner(
        tmp_path,
        [TaskRecord(1, NodeStatus.COMPLETED)],
        {1: [NodeRecord(NodeStatus.COMPLETED, str(artifact))]},
    )

    assert cleaner.cleanup_if_terminal("course-001") is True
    assert not course_workspace.exists()
    assert artifact.read_bytes() == b"image"


def test_non_terminal_task_preserves_course_workspace(tmp_path: Path) -> None:
    course_workspace = tmp_path / "course" / "course-001"
    course_workspace.mkdir(parents=True)
    cleaner = _cleaner(
        tmp_path,
        [TaskRecord(1, NodeStatus.RUNNING)],
        {1: [NodeRecord(NodeStatus.RUNNING)]},
    )

    assert cleaner.cleanup_if_terminal("course-001") is False
    assert course_workspace.is_dir()


def test_artifact_outside_result_workspace_prevents_cleanup(tmp_path: Path) -> None:
    course_workspace = tmp_path / "course" / "course-001"
    course_workspace.mkdir(parents=True)
    outside_artifact = tmp_path / "outside.png"
    outside_artifact.write_bytes(b"image")
    cleaner = _cleaner(
        tmp_path,
        [TaskRecord(1, NodeStatus.COMPLETED)],
        {1: [NodeRecord(NodeStatus.COMPLETED, str(outside_artifact))]},
    )

    assert cleaner.cleanup_if_terminal("course-001") is False
    assert course_workspace.is_dir()


def test_symlink_course_workspace_is_rejected(tmp_path: Path) -> None:
    course_root = tmp_path / "course"
    course_root.mkdir()
    outside = tmp_path / "outside-course"
    outside.mkdir()
    (course_root / "course-001").symlink_to(outside, target_is_directory=True)
    cleaner = _cleaner(
        tmp_path,
        [TaskRecord(1, NodeStatus.COMPLETED)],
        {1: [NodeRecord(NodeStatus.COMPLETED)]},
    )

    with pytest.raises(WorkspaceCleanupError, match="不是安全目录"):
        cleaner.cleanup_if_terminal("course-001")
    assert outside.is_dir()
