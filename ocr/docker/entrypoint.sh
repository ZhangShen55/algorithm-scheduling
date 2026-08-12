#!/usr/bin/env bash
set -euo pipefail

PROCESS_NAME="${GPU_PROCESS_NAME:-ocr}"
PORT="${PORT:-8866}"
WORKERS="${UVICORN_WORKERS:-1}"
[[ "$WORKERS" == "1" ]] || { echo "[ERROR] GPU operator requires exactly one Uvicorn worker" >&2; exit 1; }

exec -a "$PROCESS_NAME" python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers 1
