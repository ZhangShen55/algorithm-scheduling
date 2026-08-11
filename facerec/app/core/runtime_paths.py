from pathlib import Path


def ensure_runtime_directories(project_root: Path) -> None:
    for directory_name in ("media", "logs"):
        (project_root / directory_name).mkdir(parents=True, exist_ok=True)
