from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
RUNNER = PLATFORM_ROOT / "deploy/scripts/run_milestone_2b_8a3.py"


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("milestone_2b_run_8a3", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_script_survives_child_process_consuming_stdin(tmp_path: Path) -> None:
    runner = _load_runner()
    runtime = """
python3 -c 'import sys; sys.stdin.read()'
printf 'CODEX_RUNTIME_CONTINUED\\n'
"""

    completed = runner.execute_runtime(runtime, cwd=tmp_path, capture_output=True)

    assert completed.returncode == 0
    assert completed.stdout.endswith("CODEX_RUNTIME_CONTINUED\n")
