#!/bin/bash
set -e

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

echo "[INFO] 启动单实例实时 ASR，端口: ${PORT:-8084}"
exec python -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8084}" \
    --workers 1
