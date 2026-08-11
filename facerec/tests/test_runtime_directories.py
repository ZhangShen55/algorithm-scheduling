import importlib
from pathlib import Path


def test_ensure_runtime_directories_creates_media_and_logs(tmp_path: Path) -> None:
    runtime_paths = importlib.import_module("app.core.runtime_paths")

    runtime_paths.ensure_runtime_directories(tmp_path)

    assert (tmp_path / "media").is_dir()
    assert (tmp_path / "logs").is_dir()
