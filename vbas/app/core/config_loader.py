import json
import os
from typing import Any, Dict

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib


def load_config(config_path: str) -> Dict[str, Any]:
    lower_path = config_path.lower()
    _, ext = os.path.splitext(lower_path)
    with open(config_path, "rb") as config_file:
        if ext == ".toml" or lower_path.endswith(".toml.example"):
            return tomllib.load(config_file)
        if ext == ".json" or lower_path.endswith(".json.example"):
            return json.load(config_file)

    raise ValueError(f"Unsupported config file format: {config_path}")
