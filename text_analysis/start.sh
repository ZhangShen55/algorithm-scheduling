#!/usr/bin/env bash
set -euo pipefail

APP_MODULE="${APP_MODULE:-app.main:app}"
HOST="${UVICORN_HOST:-0.0.0.0}"
PORT="${UVICORN_PORT:-8000}"
WORKERS="${UVICORN_WORKERS:-1}"
RELOAD="${UVICORN_RELOAD:-0}"
LOG_LEVEL="${UVICORN_LOG_LEVEL:-info}"
EXTRA="${UVICORN_EXTRA:-}"

[[ "$WORKERS" == "1" ]] || { echo "[ERROR] Text Analysis requires exactly one Uvicorn process" >&2; exit 1; }
[[ "$RELOAD" == "0" ]] || { echo "[ERROR] Text Analysis requires exactly one Uvicorn process; reload is disabled" >&2; exit 1; }

EXTRA_ARGS=()
if [[ -n "$EXTRA" ]]; then
  read -r -a EXTRA_ARGS <<< "$EXTRA"
fi
for argument in "${EXTRA_ARGS[@]}"; do
  case "$argument" in
    --workers|--workers=*|--reload|--reload=*)
      echo "[ERROR] Text Analysis requires exactly one Uvicorn process" >&2
      exit 1
      ;;
  esac
done

exec uvicorn "${APP_MODULE}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --workers 1 \
  --log-level "${LOG_LEVEL}" \
  --proxy-headers --forwarded-allow-ips="*" \
  "${EXTRA_ARGS[@]}"
