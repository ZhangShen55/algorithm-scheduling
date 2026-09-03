from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from packages.platform_contracts.status import NodeStatus, TaskType

from orchestrator_service.app.application.lifecycle import (
    TerminalWorkspaceCleaner,
    WorkspaceCleanupError,
)


@dataclass(frozen=True)
class TaskRecord:
    id: int
    status: NodeStatus
    submission_id: str = "submission-001"
    task_type: TaskType = TaskType.PPT


@dataclass(frozen=True)
class NodeRecord:
    status: NodeStatus
    artifact_path: str | None = None
    node_code: str = "PPT_SLICE"


class Repository:
    def __init__(
        self,
        tasks: list[TaskRecord],
        nodes: dict[int, list[NodeRecord]],
    ) -> None:
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


def _submission_file(tmp_path: Path, relative_path: str) -> Path:
    target = (
        tmp_path
        / "course/course-001/submissions/submission-001"
        / relative_path
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"temporary")
    return target


def test_releases_each_full_submission_input_after_its_consumers_finish(
    tmp_path: Path,
) -> None:
    slides = _submission_file(tmp_path, "media/slides.mp4")
    teacher = _submission_file(tmp_path, "media/teacher.mp4")
    student = _submission_file(tmp_path, "media/student.mp4")
    audio = _submission_file(tmp_path, "audio/teacher.wav")
    teacher_frames = tmp_path / "course/course-001/vision/t/frame.jpg"
    student_frames = tmp_path / "course/course-001/vision/s/frame.jpg"
    teacher_frames.parent.mkdir(parents=True)
    student_frames.parent.mkdir(parents=True)
    teacher_frames.write_bytes(b"frame")
    student_frames.write_bytes(b"frame")
    tasks = [
        TaskRecord(1, NodeStatus.RUNNING, task_type=TaskType.PPT),
        TaskRecord(2, NodeStatus.COMPLETED, task_type=TaskType.ASR),
        TaskRecord(3, NodeStatus.RUNNING, task_type=TaskType.TEACHER_BEHAVIOR),
        TaskRecord(4, NodeStatus.FAILED, task_type=TaskType.STUDENT_BEHAVIOR),
    ]
    cleaner = _cleaner(
        tmp_path,
        tasks,
        {
            1: [
                NodeRecord(NodeStatus.COMPLETED, node_code="PPT_SLICE"),
                NodeRecord(NodeStatus.RUNNING, node_code="PPT_OCR"),
            ],
            2: [NodeRecord(NodeStatus.COMPLETED, node_code="ASR_TRANSCRIPTION")],
            3: [NodeRecord(NodeStatus.RUNNING, node_code="TEACHER_BEHAVIOR_ANALYSIS")],
            4: [NodeRecord(NodeStatus.FAILED, node_code="STUDENT_BEHAVIOR_ANALYSIS")],
        },
    )

    assert cleaner.cleanup_if_terminal("course-001") is False
    assert not slides.exists()
    assert not audio.exists()
    assert not student.exists()
    assert not student_frames.parent.exists()
    assert teacher.exists()
    assert teacher_frames.exists()


def test_teacher_video_released_when_shared_consumers_have_mixed_terminal_results(
    tmp_path: Path,
) -> None:
    teacher = _submission_file(tmp_path, "media/teacher.mp4")
    keep = tmp_path / "course/course-001/keep.txt"
    keep.write_bytes(b"keep course non-terminal")
    cleaner = _cleaner(
        tmp_path,
        [
            TaskRecord(1, NodeStatus.COMPLETED, task_type=TaskType.ASR),
            TaskRecord(2, NodeStatus.FAILED, task_type=TaskType.TEACHER_BEHAVIOR),
            TaskRecord(3, NodeStatus.RUNNING, task_type=TaskType.PPT),
        ],
        {
            1: [NodeRecord(NodeStatus.COMPLETED, node_code="ASR_TRANSCRIPTION")],
            2: [NodeRecord(NodeStatus.FAILED, node_code="TEACHER_BEHAVIOR_ANALYSIS")],
            3: [NodeRecord(NodeStatus.RUNNING, node_code="PPT_SLICE")],
        },
    )

    assert cleaner.cleanup_if_terminal("course-001") is False
    assert not teacher.exists()
    assert keep.exists()


def test_failed_ppt_slice_cleans_workspace_without_waiting_for_blocked_ocr(
    tmp_path: Path,
) -> None:
    slides = _submission_file(tmp_path, "media/slides.mp4")
    cleaner = _cleaner(
        tmp_path,
        [TaskRecord(1, NodeStatus.FAILED, task_type=TaskType.PPT)],
        {
            1: [
                NodeRecord(NodeStatus.FAILED, node_code="PPT_SLICE"),
                NodeRecord(NodeStatus.WAITING_PREREQUISITE, node_code="PPT_OCR"),
            ]
        },
    )

    assert cleaner.cleanup_if_terminal("course-001") is True
    assert not slides.exists()
    assert not (tmp_path / "course/course-001").exists()


def test_periodic_reconcile_repairs_missed_terminal_cleanup(tmp_path: Path) -> None:
    _submission_file(tmp_path, "media/student.mp4")
    repository = Repository(
        [TaskRecord(1, NodeStatus.FAILED, task_type=TaskType.STUDENT_BEHAVIOR)],
        {1: [NodeRecord(NodeStatus.FAILED, node_code="STUDENT_BEHAVIOR_ANALYSIS")]},
    )
    cleaner = TerminalWorkspaceCleaner(
        repository,
        course_root=tmp_path / "course",
        result_root=tmp_path / "result",
    )

    assert cleaner.reconcile_existing_workspaces() == (1, 1)
    assert not (tmp_path / "course/course-001").exists()
