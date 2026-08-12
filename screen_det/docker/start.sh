#!/usr/bin/env bash
set -euo pipefail

PROCESS_NAME="${GPU_PROCESS_NAME:-screen_det}"
APP_ROOT="${SCREEN_DET_ROOT:-/app}"
CONFIG_PATH="${CONFIG_PATH:-$APP_ROOT/config.toml}"
if [[ -n "${UVICORN_WORKERS:-}" && "$UVICORN_WORKERS" != "1" ]]; then
  echo "[ERROR] GPU operator requires exactly one Uvicorn worker" >&2
  exit 1
fi

cd "$APP_ROOT"

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "ERROR: config.toml not found: $CONFIG_PATH" >&2
  exit 1
fi

IFS=$'\t' read -r CONFIG_HOST CONFIG_PORT CONFIG_WORKERS < <(python - "$CONFIG_PATH" <<'PY'
import sys

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

with open(sys.argv[1], "rb") as config_file:
    server = tomllib.load(config_file).get("server", {})

print(
    server.get("host", "0.0.0.0"),
    int(server.get("port", 8880)),
    int(server.get("workers", 1)),
    sep="\t",
)
PY
)

HOST="${UVICORN_HOST:-$CONFIG_HOST}"
PORT="${UVICORN_PORT:-${PORT:-$CONFIG_PORT}}"
WORKERS="${UVICORN_WORKERS:-$CONFIG_WORKERS}"
[[ "$WORKERS" == "1" ]] || { echo "[ERROR] GPU operator requires exactly one Uvicorn worker" >&2; exit 1; }

# 将第三方库缓存写入 logs 挂载目录，避免 /tmp 无限增长
export YOLO_CONFIG_DIR="${YOLO_CONFIG_DIR:-$APP_ROOT/logs/.ultralytics}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-$APP_ROOT/logs/.matplotlib}"
mkdir -p logs "$YOLO_CONFIG_DIR" "$MPLCONFIGDIR"

exec -a "$PROCESS_NAME" python -m uvicorn app.main:app \
  --host "$HOST" \
  --port "$PORT" \
  --workers 1
