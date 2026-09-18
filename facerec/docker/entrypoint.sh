#!/usr/bin/env bash
set -euo pipefail

PROCESS_NAME="${GPU_PROCESS_NAME-facerec}"
PORT="${PORT:-8000}"
WORKERS="${UVICORN_WORKERS:-1}"
[[ "$WORKERS" == "1" ]] || { echo "[ERROR] GPU operator requires exactly one Uvicorn worker" >&2; exit 1; }
if [[ ! "$PROCESS_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
  echo "[ERROR] GPU process name contains unsafe characters" >&2
  exit 1
fi

PYTHON_EXECUTABLE="$(command -v python3)"
[[ -x "$PYTHON_EXECUTABLE" && -f "$PYTHON_EXECUTABLE" ]] || {
  echo "[ERROR] python3 does not resolve to an executable file" >&2
  exit 1
}
ENTRYPOINT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export FACEREC_SPAWN_EXECUTABLE="$ENTRYPOINT_DIR/$(basename -- "${BASH_SOURCE[0]}")"

# multiprocessing spawn 会用该入口重新执行 Python；统一 argv[0]，避免 NVML 暴露解释器绝对路径。
if (( $# > 0 )); then
  exec -a "$PROCESS_NAME" "$PYTHON_EXECUTABLE" "$@"
fi

exec -a "$PROCESS_NAME" "$PYTHON_EXECUTABLE" -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers 1
