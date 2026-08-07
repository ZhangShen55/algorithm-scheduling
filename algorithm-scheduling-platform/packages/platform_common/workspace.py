import os
import re
from pathlib import Path

from packages.platform_common.config import PlatformSettings

_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class WorkspaceError(RuntimeError):
    """Raised when shared media roots are unsafe or unusable."""


def _ensure_directory(path: Path) -> None:
    if path.exists() and not path.is_dir():
        raise WorkspaceError(f"工作目录不是目录: {path}")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkspaceError(f"无法创建工作目录: {path}") from exc
    if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        raise WorkspaceError(f"工作目录不可读写: {path}")


def ensure_workspace_roots(settings: PlatformSettings) -> None:
    _ensure_directory(settings.course_root)
    _ensure_directory(settings.result_root)


def task_workspace(root: Path, task_id: str) -> Path:
    if not _TASK_ID_PATTERN.fullmatch(task_id) or task_id in {".", ".."}:
        raise ValueError("task_id 只能包含字母、数字、点、下划线和连字符")
    target = root / task_id
    if target.parent.resolve() != root.resolve():
        raise ValueError("task_id 不能跳出工作目录")
    return target
