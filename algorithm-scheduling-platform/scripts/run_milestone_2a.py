from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = PROJECT_ROOT / "deploy" / "docker-compose.infrastructure.yml"
REPORT_ROOT = PROJECT_ROOT / "harness" / "reports" / "milestone-2a"


def main() -> int:
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "up",
            "-d",
            "--wait",
            "postgres",
            "redis",
            "kafka",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["MILESTONE_2A_REPORT_DIR"] = str(REPORT_ROOT)
    completed = subprocess.run(
        [
            str(PROJECT_ROOT / ".venv" / "bin" / "python"),
            "-m",
            "pytest",
            "-q",
            "-rs",
            "tests/integration/test_kafka_runtime.py",
            "tests/integration/test_milestone_2a_runtime.py",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
