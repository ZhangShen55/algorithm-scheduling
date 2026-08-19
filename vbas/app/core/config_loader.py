import json
import os
from pathlib import Path
from typing import Any, Dict

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_config_path(config_path: str | Path | None = None) -> Path:
    selected = Path(
        config_path
        if config_path is not None
        else os.getenv("CONFIG_PATH", PROJECT_ROOT / "config.toml")
    ).expanduser()
    if not selected.is_absolute():
        selected = PROJECT_ROOT / selected
    return selected.resolve()


def load_config(config_path: str | Path) -> Dict[str, Any]:
    resolved_path = resolve_config_path(config_path)
    lower_path = str(resolved_path).lower()
    _, ext = os.path.splitext(lower_path)
    with resolved_path.open("rb") as config_file:
        if ext == ".toml" or lower_path.endswith(".toml.example"):
            return tomllib.load(config_file)
        if ext == ".json" or lower_path.endswith(".json.example"):
            return json.load(config_file)

    raise ValueError(f"Unsupported config file format: {config_path}")
