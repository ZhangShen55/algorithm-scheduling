import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_SCRIPT = PROJECT_ROOT / "docker/start.sh"


def _write_config(root: Path, *, host: str, port: int, workers: int) -> None:
    (root / "config.toml").write_text(
        "\n".join(
            (
                "[server]",
                f'host = "{host}"',
                f"port = {port}",
                f"workers = {workers}",
            )
        ),
        encoding="utf-8",
    )


def _install_python_stub(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture_path = tmp_path / "uvicorn-args"
    stub = bin_dir / "python"
    stub.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-" ]]; then
  exec "$REAL_PYTHON" "$@"
fi
printf '%s\\n' "$@" > "$CAPTURE_PATH"
""",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bin_dir, capture_path


def _run_entrypoint(tmp_path: Path, **environment: str) -> subprocess.CompletedProcess[str]:
    bin_dir, capture_path = _install_python_stub(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "REAL_PYTHON": sys.executable,
        "CAPTURE_PATH": str(capture_path),
        "SCREEN_DET_ROOT": str(tmp_path),
        **environment,
    }
    return subprocess.run(
        ["bash", str(START_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )


def _read_application_server_config(
    config_path: str,
    *,
    cwd: Path,
) -> dict[str, object]:
    env = {
        **os.environ,
        "CONFIG_PATH": config_path,
        "PYTHONPATH": str(PROJECT_ROOT),
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; "
                "from app.core.config import get_settings; "
                "server = get_settings().server; "
                "print(json.dumps({"
                "'host': server.host, 'port': server.port, 'workers': server.workers"
                "}))"
            ),
        ],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_entrypoint_uses_server_toml_values(tmp_path: Path) -> None:
    _write_config(tmp_path, host="127.0.0.2", port=9123, workers=1)

    completed = _run_entrypoint(tmp_path)

    assert completed.returncode == 0, completed.stderr
    args = (tmp_path / "uvicorn-args").read_text(encoding="utf-8").splitlines()
    assert args[args.index("--host") + 1] == "127.0.0.2"
    assert args[args.index("--port") + 1] == "9123"
    assert args[args.index("--workers") + 1] == "1"


def test_config_path_override_is_shared_by_entrypoint_and_application(
    tmp_path: Path,
) -> None:
    _write_config(tmp_path, host="127.0.0.9", port=9456, workers=1)
    config_path = tmp_path / "config.toml"

    completed = _run_entrypoint(tmp_path, CONFIG_PATH=str(config_path))

    assert completed.returncode == 0, completed.stderr
    args = (tmp_path / "uvicorn-args").read_text(encoding="utf-8").splitlines()
    assert args[args.index("--host") + 1] == "127.0.0.9"
    assert args[args.index("--port") + 1] == "9456"
    assert _read_application_server_config(str(config_path), cwd=PROJECT_ROOT) == {
        "host": "127.0.0.9",
        "port": 9456,
        "workers": 1,
    }


def test_application_resolves_relative_config_path_from_project_root(
    tmp_path: Path,
) -> None:
    _write_config(tmp_path, host="127.0.0.8", port=9567, workers=1)
    relative_path = os.path.relpath(tmp_path / "config.toml", PROJECT_ROOT)

    assert _read_application_server_config(relative_path, cwd=tmp_path) == {
        "host": "127.0.0.8",
        "port": 9567,
        "workers": 1,
    }


def test_entrypoint_environment_overrides_server_toml(tmp_path: Path) -> None:
    _write_config(tmp_path, host="127.0.0.2", port=9123, workers=2)

    completed = _run_entrypoint(
        tmp_path,
        UVICORN_HOST="0.0.0.0",
        UVICORN_PORT="9234",
        UVICORN_WORKERS="1",
    )

    assert completed.returncode == 0, completed.stderr
    args = (tmp_path / "uvicorn-args").read_text(encoding="utf-8").splitlines()
    assert args[args.index("--host") + 1] == "0.0.0.0"
    assert args[args.index("--port") + 1] == "9234"


def test_entrypoint_preserves_port_environment_compatibility(tmp_path: Path) -> None:
    _write_config(tmp_path, host="127.0.0.2", port=9123, workers=1)

    completed = _run_entrypoint(tmp_path, PORT="9345")

    assert completed.returncode == 0, completed.stderr
    args = (tmp_path / "uvicorn-args").read_text(encoding="utf-8").splitlines()
    assert args[args.index("--port") + 1] == "9345"


def test_entrypoint_rejects_toml_multiple_workers_before_initialization(
    tmp_path: Path,
) -> None:
    _write_config(tmp_path, host="0.0.0.0", port=8880, workers=2)

    completed = _run_entrypoint(tmp_path)

    assert completed.returncode != 0
    assert "GPU operator requires exactly one Uvicorn worker" in completed.stderr
    assert not (tmp_path / "logs").exists()
