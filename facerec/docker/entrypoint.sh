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
NAMED_PYTHON_DIR="/run/operator-python"
NAMED_PYTHON="$NAMED_PYTHON_DIR/$PROCESS_NAME"
[[ -x "$PYTHON_EXECUTABLE" && -f "$PYTHON_EXECUTABLE" ]] || {
  echo "[ERROR] python3 does not resolve to an executable file" >&2
  exit 1
}
install -d -m 0755 "$NAMED_PYTHON_DIR"
if [[ ( -e "$NAMED_PYTHON" || -L "$NAMED_PYTHON" ) && ! -L "$NAMED_PYTHON" ]]; then
  echo "[ERROR] named Python path already exists and is not a symbolic link" >&2
  exit 1
fi
ln -sfnT "$PYTHON_EXECUTABLE" "$NAMED_PYTHON"
[[ "$(readlink -f "$NAMED_PYTHON")" == "$(readlink -f "$PYTHON_EXECUTABLE")" ]] || {
  echo "[ERROR] named Python link does not resolve to python3" >&2
  exit 1
}
export PATH="$NAMED_PYTHON_DIR:$PATH"

exec "$PROCESS_NAME" -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers 1
