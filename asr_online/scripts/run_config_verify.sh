#!/bin/bash
set -e

project_root=$(cd "$(dirname "$0")/.." && pwd)
start_script="$project_root/docker/start.sh"

if grep -Eiq 'nginx|instance_count|--workers[[:space:]]+[^1]' "$start_script"; then
    echo "[ERROR] 实时 ASR 启动脚本不符合一端点一实例约定"
    exit 1
fi

if ! grep -q '\${PORT:-8084}' "$start_script"; then
    echo "[ERROR] 实时 ASR 默认端口应为 8084"
    exit 1
fi

echo "[OK] 实时 ASR 使用单 Uvicorn、workers=1，多实例由多容器注册实现"
