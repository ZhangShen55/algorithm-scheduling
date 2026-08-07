#!/usr/bin/env bash
set -euo pipefail

APP_MODULE="${APP_MODULE:-app.main:app}"
HOST="${UVICORN_HOST:-0.0.0.0}"
PORT="${UVICORN_PORT:-8000}"
WORKERS="${UVICORN_WORKERS:-2}"
RELOAD="${UVICORN_RELOAD:-0}"
LOG_LEVEL="${UVICORN_LOG_LEVEL:-info}"
EXTRA="${UVICORN_EXTRA:-}"

if [[ "${RELOAD}" == "1" ]]; then
  exec uvicorn "${APP_MODULE}" \
    --host "${HOST}" \
    --port "${PORT}" \
    --reload \
    --log-level "${LOG_LEVEL}" \
    ${EXTRA}
else
  exec uvicorn "${APP_MODULE}" \
    --host "${HOST}" \
    --port "${PORT}" \
    --workers "${WORKERS}" \
    --log-level "${LOG_LEVEL}" \
    --proxy-headers --forwarded-allow-ips="*" \
    ${EXTRA}
fi
