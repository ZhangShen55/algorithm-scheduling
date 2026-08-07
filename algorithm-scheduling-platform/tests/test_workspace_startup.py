from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from packages.platform_common.application import create_service_app
from packages.platform_common.config import PlatformSettings
from packages.platform_common.workspace import (
    WorkspaceError,
    ensure_workspace_roots,
    task_workspace,
)


def test_startup_creates_and_checks_shared_workspace_roots(tmp_path: Path) -> None:
    settings = PlatformSettings(
        service_name="test-service",
        course_root=tmp_path / "course",
        result_root=tmp_path / "result",
    )
    app = create_service_app(settings)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"service": "test-service", "status": "ok"}
    assert settings.course_root.is_dir()
    assert settings.result_root.is_dir()


def test_workspace_check_rejects_a_file_where_directory_is_required(tmp_path: Path) -> None:
    invalid_root = tmp_path / "course"
    invalid_root.write_text("not-a-directory", encoding="utf-8")
    settings = PlatformSettings(course_root=invalid_root, result_root=tmp_path / "result")

    with pytest.raises(WorkspaceError, match="不是目录"):
        ensure_workspace_roots(settings)


def test_task_workspace_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="task_id"):
        task_workspace(tmp_path, "../another-course")
