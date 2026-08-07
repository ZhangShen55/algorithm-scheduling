#!/usr/bin/env bash
set -euo pipefail

BOOTSTRAP_KEY="${TIAS_BOOTSTRAP_KEY_FILE:-/run/bootstrap-secrets/tias_model_key}"
RUNTIME_KEY="${TIAS_RUNTIME_KEY_FILE:-/dev/shm/tias_model_key}"

if [[ "${TIAS_REQUIRE_BOOTSTRAP_KEY:-1}" == "1" ]]; then
  if [[ ! -s "$BOOTSTRAP_KEY" ]]; then
    echo "[ERROR] 模型密钥源文件不存在或为空: $BOOTSTRAP_KEY" >&2
    exit 1
  fi

  mkdir -p "$(dirname "$RUNTIME_KEY")"
  cp "$BOOTSTRAP_KEY" "$RUNTIME_KEY"
  chmod 0400 "$RUNTIME_KEY"
  export TIAS_RUNTIME_KEY_ROOT="${TIAS_RUNTIME_KEY_ROOT:-$(dirname "$RUNTIME_KEY")}"
  echo "[INFO] 运行期模型密钥副本已生成: $RUNTIME_KEY"
fi

exec "$@"
