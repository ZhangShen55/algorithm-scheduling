from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol

from packages.platform_common.workspace import task_workspace
from packages.platform_contracts.status import NodeStatus

TERMINAL_STATUSES = {
    NodeStatus.COMPLETED,
    NodeStatus.FAILED,
    NodeStatus.CANCELLED,
}


class CleanupTaskRecord(Protocol):
    id: int
    status: NodeStatus


class CleanupNodeRecord(Protocol):
    status: NodeStatus
    artifact_path: str | None


class CleanupRepository(Protocol):
    def list_task_types(self, task_id: str) -> list[CleanupTaskRecord]: ...

    def list_nodes(self, course_task_type_id: int) -> list[CleanupNodeRecord]: ...


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
        if not task_types or any(
            task_type.status not in TERMINAL_STATUSES for task_type in task_types
        ):
            return False

        result_workspace = task_workspace(self._result_root, task_id).resolve()
        for task_type in task_types:
            nodes = self._repository.list_nodes(task_type.id)
            if not nodes or any(node.status not in TERMINAL_STATUSES for node in nodes):
                return False
            for node in nodes:
                if node.artifact_path is None:
                    continue
                artifact = Path(node.artifact_path).resolve()
                if not artifact.is_relative_to(result_workspace) or not artifact.exists():
                    return False

        course_workspace = task_workspace(self._course_root, task_id)
        if not course_workspace.exists():
            return True
        if course_workspace.is_symlink() or not course_workspace.is_dir():
            raise WorkspaceCleanupError(f"课程临时工作区不是安全目录: {course_workspace}")
        try:
            shutil.rmtree(course_workspace)
        except OSError as exc:
            raise WorkspaceCleanupError(f"删除课程临时工作区失败: {course_workspace}") from exc
        return True
