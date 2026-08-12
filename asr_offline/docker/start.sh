#!/usr/bin/env bash
set -euo pipefail

PROCESS_NAME="${GPU_PROCESS_NAME:-asr_offline}"
PORT="${PORT:-8083}"
WORKERS="${UVICORN_WORKERS:-1}"
[[ "$WORKERS" == "1" ]] || { echo "[ERROR] GPU operator requires exactly one Uvicorn worker" >&2; exit 1; }

export CONFIG_PATH="${CONFIG_PATH:-/config.toml}"
export CONDA_ENV_NAME="${CONDA_ENV_NAME:-asr}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export KMP_DUPLICATE_LIB_OK="${KMP_DUPLICATE_LIB_OK:-TRUE}"
export CUDA_MODULE_LOADING="${CUDA_MODULE_LOADING:-LAZY}"
export PYTHONFAULTHANDLER="${PYTHONFAULTHANDLER:-1}"
export MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"

if [ ! -f "$CONFIG_PATH" ]; then
    echo "[ERROR] 配置文件不存在：$CONFIG_PATH"
    exit 1
fi

source /opt/conda/bin/activate "$CONDA_ENV_NAME"

echo "[INFO] 启动单实例离线 ASR，端口: $PORT"
exec -a "$PROCESS_NAME" python -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --workers 1
