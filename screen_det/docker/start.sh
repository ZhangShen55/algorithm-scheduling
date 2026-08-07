#!/usr/bin/env bash
set -euo pipefail

cd /app

if [[ ! -f config.toml ]]; then
  echo "ERROR: config.toml not found. Mount with: -v /path/to/config.toml:/app/config.toml:ro" >&2
  exit 1
fi

mapfile -t _SERVER_CFG < <(python - <<'PY'
import toml

cfg = toml.load("config.toml")
server = cfg.get("server", {})
print(server.get("host", "0.0.0.0"))
print(int(server.get("port", 8880)))
print(int(server.get("workers", 1)))
PY
)

HOST="${_SERVER_CFG[0]:-0.0.0.0}"
PORT="${_SERVER_CFG[1]:-8880}"
WORKERS="${_SERVER_CFG[2]:-1}"

# 将第三方库缓存写入 logs 挂载目录，避免 /tmp 无限增长
export YOLO_CONFIG_DIR="${YOLO_CONFIG_DIR:-/app/logs/.ultralytics}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-/app/logs/.matplotlib}"
mkdir -p logs "$YOLO_CONFIG_DIR" "$MPLCONFIGDIR"

exec uvicorn app.main:app \
  --host "${HOST}" \
  --port "${PORT}" \
  --workers "${WORKERS}"
