#!/usr/bin/env python3
"""容量恢复 Campaign 的只读运行时采样器。"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VBAS_INSTANCES = ("vbas-gpu0", "vbas-gpu1", "vbas-gpu2")
TARGET_CONTAINERS = (
    "algorithm-scheduling-platform-control-service-1",
    "algorithm-scheduling-platform-orchestrator-service-1",
    "algorithm-scheduling-platform-vision-orchestrator-service-1",
    "algorithm-scheduling-platform-online-gateway-service-1",
    "algorithm-operators-vbas-gpu0-1",
    "algorithm-operators-vbas-gpu1-1",
    "algorithm-operators-vbas-gpu2-1",
)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _get_json(url: str) -> object:
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            return json.loads(response.read())
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return {"probe_error": type(exc).__name__}


def _command(*command: str) -> dict[str, object]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"probe_error": type(exc).__name__}
    return {
        "returncode": completed.returncode,
        "lines": [line for line in completed.stdout.splitlines() if line],
    }


def _sample(control_origin: str) -> dict[str, Any]:
    leases = {
        instance: _get_json(
            f"{control_origin}/ops/operator-instances/{instance}/active-leases"
        )
        for instance in VBAS_INSTANCES
    }
    return {
        "recorded_at": _now(),
        "queues": _get_json(f"{control_origin}/ops/queues"),
        "operator_instances": _get_json(f"{control_origin}/ops/operator-instances"),
        "active_leases": leases,
        "gpu": _command(
            "nvidia-smi",
            "--query-gpu=index,uuid,memory.used,utilization.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ),
        "gpu_processes": _command(
            "nvidia-smi",
            "--query-compute-apps=gpu_uuid,pid,used_memory,process_name",
            "--format=csv,noheader,nounits",
        ),
        "containers": _command(
            "docker",
            "inspect",
            "--format",
            "{{.Name}} {{.Id}} {{.Image}} {{.State.Status}} {{.RestartCount}}",
            *TARGET_CONTAINERS,
        ),
        "disk": _command("df", "-B1", "/data"),
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--stop-file", required=True, type=Path)
    parser.add_argument("--control-origin", default="http://127.0.0.1:18100")
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    if arguments.interval_seconds <= 0:
        raise ValueError("interval-seconds 必须为正数")
    if arguments.output.exists() or arguments.output.is_symlink():
        raise FileExistsError(f"证据文件已存在，禁止覆盖: {arguments.output}")
    arguments.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    arguments.stop_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stopped = False

    def stop(*_: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    descriptor = os.open(
        arguments.output,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8", buffering=1) as stream:
        while not stopped and not arguments.stop_file.exists():
            started = time.monotonic()
            stream.write(json.dumps(_sample(arguments.control_origin), ensure_ascii=False) + "\n")
            remaining = arguments.interval_seconds - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
