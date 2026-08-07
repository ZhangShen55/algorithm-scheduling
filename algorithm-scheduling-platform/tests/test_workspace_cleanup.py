from dataclasses import dataclass
from pathlib import Path

from orchestrator_service.app.application.lifecycle import TerminalWorkspaceCleaner

from packages.platform_contracts.status import NodeStatus


@dataclass
class TaskRecord:
    id: int
    status: NodeStatus


@dataclass
class NodeRecord:
    status: NodeStatus
    artifact_path: str | None = None


class CleanupRepository:
    def __init__(
        self,
        tasks: list[TaskRecord],
        nodes: dict[int, list[NodeRecord]],
    ) -> None:
        self.tasks = tasks
        self.nodes = nodes

    def list_task_types(self, task_id: str) -> list[TaskRecord]:
        del task_id
        return self.tasks

    def list_nodes(self, course_task_type_id: int) -> list[NodeRecord]:
        return self.nodes[course_task_type_id]


def test_terminal_cleanup_removes_only_course_workspace_after_artifacts_exist(
    tmp_path: Path,
) -> None:
    course_root = tmp_path / "course"
    result_root = tmp_path / "result"
    course_dir = course_root / "course-001"
    result_dir = result_root / "course-001" / "ppt" / "slices"
    other_course_dir = course_root / "course-002"
    course_dir.mkdir(parents=True)
    result_dir.mkdir(parents=True)
    other_course_dir.mkdir(parents=True)
    (course_dir / "teacher.mp4").write_bytes(b"video")
    (result_dir / "ppt-001.jpg").write_bytes(b"image")
    repository = CleanupRepository(
        tasks=[TaskRecord(1, NodeStatus.COMPLETED)],
        nodes={
            1: [
                NodeRecord(
                    NodeStatus.COMPLETED,
                    str(result_dir),
                )
            ]
        },
    )

    cleaned = TerminalWorkspaceCleaner(
        repository,
        course_root=course_root,
        result_root=result_root,
    ).cleanup_if_terminal("course-001")

    assert cleaned is True
    assert not course_dir.exists()
    assert result_dir.exists()
    assert other_course_dir.exists()


def test_cleanup_waits_for_all_requested_pipelines_and_durable_files(tmp_path: Path) -> None:
    course_root = tmp_path / "course"
    result_root = tmp_path / "result"
    course_dir = course_root / "course-001"
    course_dir.mkdir(parents=True)
    missing_artifact = result_root / "course-001" / "ppt" / "slices"
    running_repository = CleanupRepository(
        tasks=[
            TaskRecord(1, NodeStatus.COMPLETED),
            TaskRecord(2, NodeStatus.RUNNING),
        ],
        nodes={
            1: [NodeRecord(NodeStatus.COMPLETED)],
            2: [NodeRecord(NodeStatus.RUNNING)],
        },
    )
    missing_file_repository = CleanupRepository(
        tasks=[TaskRecord(1, NodeStatus.COMPLETED)],
        nodes={
            1: [
                NodeRecord(
                    NodeStatus.COMPLETED,
                    str(missing_artifact),
                )
            ]
        },
    )

    running_cleaned = TerminalWorkspaceCleaner(
        running_repository,
        course_root=course_root,
        result_root=result_root,
    ).cleanup_if_terminal("course-001")
    missing_file_cleaned = TerminalWorkspaceCleaner(
        missing_file_repository,
        course_root=course_root,
        result_root=result_root,
    ).cleanup_if_terminal("course-001")

    assert running_cleaned is False
    assert missing_file_cleaned is False
    assert course_dir.exists()
