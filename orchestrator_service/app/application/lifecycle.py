from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Protocol

from packages.platform_common.workspace import task_workspace
from packages.platform_contracts.status import NodeStatus, TaskType

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {
    NodeStatus.COMPLETED,
    NodeStatus.FAILED,
    NodeStatus.CANCELLED,
}


class CleanupTaskRecord(Protocol):
    id: int
    status: NodeStatus
    submission_id: str
    task_type: TaskType


class CleanupNodeRecord(Protocol):
    node_code: str
    status: NodeStatus
    artifact_path: str | None


class CleanupRepository(Protocol):
    def list_task_types(self, task_id: str) -> list[CleanupTaskRecord]: ...

    def list_nodes(self, course_task_type_id: int) -> list[CleanupNodeRecord]: ...


class WorkspaceCleaner(Protocol):
    def cleanup_if_terminal(self, task_id: str) -> bool: ...


class WorkspaceCleanupError(RuntimeError):
    pass


class TerminalWorkspaceCleaner:
    def __init__(
        self,
        repository: CleanupRepository,
        *,
        course_root: Path,
        result_root: Path,
    ) -> None:
        self._repository = repository
        self._course_root = course_root
        self._result_root = result_root

    def cleanup_if_terminal(self, task_id: str) -> bool:
        task_types = self._repository.list_task_types(task_id)
        if not task_types:
            return False

        nodes_by_task = {
            task_type.id: self._repository.list_nodes(task_type.id)
            for task_type in task_types
        }
        self._cleanup_released_inputs(task_id, task_types, nodes_by_task)
        if any(
            task_type.status not in TERMINAL_STATUSES for task_type in task_types
        ):
            return False

        result_workspace = task_workspace(self._result_root, task_id).resolve()
        for task_type in task_types:
            for node in nodes_by_task[task_type.id]:
                if node.artifact_path is None:
                    continue
                if node.status is not NodeStatus.COMPLETED:
                    continue
                artifact = Path(node.artifact_path).resolve()
                if (
                    not artifact.is_relative_to(result_workspace)
                    or not artifact.exists()
                ):
                    return False

        course_workspace = task_workspace(self._course_root, task_id)
        if not course_workspace.exists():
            return True
        if course_workspace.is_symlink() or not course_workspace.is_dir():
            raise WorkspaceCleanupError(
                f"课程临时工作区不是安全目录: {course_workspace}"
            )
        try:
            shutil.rmtree(course_workspace)
        except OSError as exc:
            raise WorkspaceCleanupError(
                f"删除课程临时工作区失败: {course_workspace}"
            ) from exc
        return True

    def reconcile_existing_workspaces(self) -> tuple[int, int]:
        if not self._course_root.is_dir():
            return 0, 0
        inspected = 0
        cleaned = 0
        for workspace in self._course_root.iterdir():
            if not workspace.is_dir() or workspace.is_symlink():
                continue
            inspected += 1
            try:
                cleaned += int(self.cleanup_if_terminal(workspace.name))
            except Exception as exc:  # noqa: BLE001 - 单个目录不得阻断其他课程修复
                logger.warning(
                    "课程临时工作区周期清理失败",
                    extra={"task_id": workspace.name, "reason": str(exc)},
                )
        return inspected, cleaned

    def _cleanup_released_inputs(
        self,
        task_id: str,
        task_types: list[CleanupTaskRecord],
        nodes_by_task: dict[int, list[CleanupNodeRecord]],
    ) -> None:
        course_workspace = task_workspace(self._course_root, task_id)
        if not course_workspace.exists():
            return
        if course_workspace.is_symlink() or not course_workspace.is_dir():
            raise WorkspaceCleanupError(
                f"课程临时工作区不是安全目录: {course_workspace}"
            )

        tasks_by_submission: dict[str, list[CleanupTaskRecord]] = {}
        for task_type in task_types:
            if not task_type.submission_id:
                continue
            tasks_by_submission.setdefault(task_type.submission_id, []).append(
                task_type
            )

        for submission_id, submission_tasks in tasks_by_submission.items():
            submission_workspace = task_workspace(
                course_workspace / "submissions",
                submission_id,
            )
            if not submission_workspace.exists():
                continue
            self._validate_child_directory(course_workspace, submission_workspace)

            ppt = self._task(submission_tasks, TaskType.PPT)
            if ppt is not None and self._node_is_terminal(
                nodes_by_task[ppt.id],
                "PPT_SLICE",
            ):
                self._unlink_file(
                    course_workspace,
                    submission_workspace / "media/slides.mp4",
                )

            asr = self._task(submission_tasks, TaskType.ASR)
            if asr is not None and asr.status in TERMINAL_STATUSES:
                self._unlink_file(
                    course_workspace,
                    submission_workspace / "audio/teacher.wav",
                )

            student = self._task(submission_tasks, TaskType.STUDENT_BEHAVIOR)
            if student is not None and student.status in TERMINAL_STATUSES:
                self._unlink_file(
                    course_workspace,
                    submission_workspace / "media/student.mp4",
                )

            teacher_consumers = [
                task_type
                for task_type in submission_tasks
                if task_type.task_type in {TaskType.ASR, TaskType.TEACHER_BEHAVIOR}
            ]
            if teacher_consumers and all(
                task_type.status in TERMINAL_STATUSES
                for task_type in teacher_consumers
            ):
                self._unlink_file(
                    course_workspace,
                    submission_workspace / "media/teacher.mp4",
                )

        teacher = self._task(task_types, TaskType.TEACHER_BEHAVIOR)
        if teacher is not None and teacher.status in TERMINAL_STATUSES:
            self._remove_tree(course_workspace, course_workspace / "vision/t")
        student = self._task(task_types, TaskType.STUDENT_BEHAVIOR)
        if student is not None and student.status in TERMINAL_STATUSES:
            self._remove_tree(course_workspace, course_workspace / "vision/s")

    @staticmethod
    def _task(
        task_types: list[CleanupTaskRecord],
        expected: TaskType,
    ) -> CleanupTaskRecord | None:
        return next(
            (task_type for task_type in task_types if task_type.task_type is expected),
            None,
        )

    @staticmethod
    def _node_is_terminal(
        nodes: list[CleanupNodeRecord],
        node_code: str,
    ) -> bool:
        return any(
            node.node_code == node_code and node.status in TERMINAL_STATUSES
            for node in nodes
        )

    @staticmethod
    def _validate_child_directory(course_workspace: Path, directory: Path) -> None:
        if directory.is_symlink() or not directory.resolve().is_relative_to(
            course_workspace.resolve()
        ):
            raise WorkspaceCleanupError(f"课程临时子目录不安全: {directory}")

    def _unlink_file(self, course_workspace: Path, target: Path) -> None:
        if not target.exists() and not target.is_symlink():
            return
        self._validate_child_directory(course_workspace, target.parent)
        try:
            target.unlink()
            self._prune_empty_parents(target.parent, course_workspace)
        except OSError as exc:
            raise WorkspaceCleanupError(f"删除课程临时文件失败: {target}") from exc

    def _remove_tree(self, course_workspace: Path, target: Path) -> None:
        if not target.exists():
            return
        self._validate_child_directory(course_workspace, target)
        try:
            shutil.rmtree(target)
            self._prune_empty_parents(target.parent, course_workspace)
        except OSError as exc:
            raise WorkspaceCleanupError(f"删除课程临时目录失败: {target}") from exc

    @staticmethod
    def _prune_empty_parents(directory: Path, course_workspace: Path) -> None:
        while directory != course_workspace:
            try:
                directory.rmdir()
            except OSError:
                return
            directory = directory.parent
