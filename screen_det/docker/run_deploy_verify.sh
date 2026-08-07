#!/usr/bin/env bash
# 本地部署验收：拉起 8880 → 跑 test/ 用例 → 关闭服务
# 用法: conda activate screen_det && bash docker/run_deploy_verify.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BASE_URL="${BASE_URL:-http://127.0.0.1:8880}"
REPORT_DIR="${REPORT_DIR:-$ROOT/test/reports/$(date +%Y%m%d_%H%M%S)}"
PYTHON="${PYTHON:-python}"
PORT=8880
PID_FILE="/tmp/tilt-api-${PORT}.pid"
LOG_FILE="/tmp/tilt-api-${PORT}.log"

mkdir -p "$REPORT_DIR"

pass=0
fail=0
skip=0

log() { echo "[$(date +%H:%M:%S)] $*"; }

record() {
  local status="$1" name="$2" detail="${3:-}"
  case "$status" in
    PASS) pass=$((pass + 1)); log "✓ PASS  $name ${detail:+- $detail}" ;;
    FAIL) fail=$((fail + 1)); log "✗ FAIL  $name ${detail:+- $detail}" ;;
    SKIP) skip=$((skip + 1)); log "- SKIP  $name ${detail:+- $detail}" ;;
  esac
  printf '{"status":"%s","name":"%s","detail":"%s"}\n' "$status" "$name" "$detail" >> "$REPORT_DIR/summary.jsonl"
}

stop_server() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
      log "Stopping server pid=$pid"
      kill "$pid" 2>/dev/null || true
      for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
      done
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
  fi
}

start_server() {
  stop_server
  log "Starting uvicorn on :$PORT ..."
  nohup "$PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --workers 1 \
    >"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  for i in $(seq 1 90); do
    if curl -fsS "$BASE_URL/health" >/dev/null 2>&1; then
      log "Server ready (${i}s)"
      return 0
    fi
    if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      log "Server exited early:"
      tail -40 "$LOG_FILE" || true
      return 1
    fi
    sleep 1
  done
  tail -40 "$LOG_FILE" || true
  return 1
}

trap stop_server EXIT

log "Report: $REPORT_DIR"
log "Python: $($PYTHON --version 2>&1)"

# --- 静态检查 ---
for f in config.toml requirements.txt docker/Dockerfile docker/start.sh model/screen.pt model/occlusion.pt app/main.py; do
  [[ -e "$ROOT/$f" ]] && record PASS "static:$f" "exists" || record FAIL "static:$f" "missing"
done

# --- 启动 ---
if start_server; then
  record PASS "server:start" "port $PORT"
else
  record FAIL "server:start" "$LOG_FILE"
  exit 1
fi

curl -fsS "$BASE_URL/health" | tee "$REPORT_DIR/01_health.json" >/dev/null \
  && record PASS "api:health" || record FAIL "api:health"
curl -fsS "$BASE_URL/config" | tee "$REPORT_DIR/02_config.json" >/dev/null \
  && record PASS "api:config" || record FAIL "api:config"

# --- HTTP 批量测试 ---
while IFS=$'\t' read -r status name detail; do
  record "$status" "$name" "$detail"
done < <("$PYTHON" "$ROOT/docker/deploy_verify_http.py" "$BASE_URL" "$REPORT_DIR" --summary-only)

curl -fsS -X POST "$BASE_URL/config/reload" | tee "$REPORT_DIR/03_reload.json" >/dev/null \
  && record PASS "api:config_reload" || record FAIL "api:config_reload"

if command -v docker >/dev/null 2>&1; then
  log "Docker build (online mode, may take several minutes)..."
  if docker build -f "$ROOT/docker/Dockerfile" -t tilt-api-verify:local "$ROOT" \
    >"$REPORT_DIR/docker_build.log" 2>&1; then
    record PASS "docker:build" "tilt-api-verify:local"
  else
    record FAIL "docker:build" "see docker_build.log"
  fi
else
  record SKIP "docker:build" "docker not installed"
fi

stop_server
trap - EXIT

log "========================================"
log "PASS=$pass  FAIL=$fail  SKIP=$skip"
log "Report: $REPORT_DIR"
[[ "$fail" -eq 0 ]]
