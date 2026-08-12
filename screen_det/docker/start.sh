#!/usr/bin/env bash
set -euo pipefail

PROCESS_NAME="${GPU_PROCESS_NAME:-screen_det}"
PORT="${PORT:-8880}"
WORKERS="${UVICORN_WORKERS:-1}"
[[ "$WORKERS" == "1" ]] || { echo "[ERROR] GPU operator requires exactly one Uvicorn worker" >&2; exit 1; }

cd /app

if [[ ! -f config.toml ]]; then
  echo "ERROR: config.toml not found. Mount with: -v /path/to/config.toml:/app/config.toml:ro" >&2
  exit 1
fi

# 将第三方库缓存写入 logs 挂载目录，避免 /tmp 无限增长
export YOLO_CONFIG_DIR="${YOLO_CONFIG_DIR:-/app/logs/.ultralytics}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-/app/logs/.matplotlib}"
mkdir -p logs "$YOLO_CONFIG_DIR" "$MPLCONFIGDIR"

exec -a "$PROCESS_NAME" python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers 1
