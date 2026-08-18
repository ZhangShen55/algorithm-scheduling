#!/usr/bin/env bash

# This file is sourced by the canonical milestone 2B controller. The controller owns
# RELEASE_TAG, EXPECTED_GIT_SHA, REPORT_ROOT, RELEASE_ROOT and DEPLOY_PYTHON.

FIXTURE_ROOT=/root/workspace/.algorithm-scheduling-fixtures/v1.0_260812
FIXTURE_MANIFEST="$FIXTURE_ROOT/manifest.json"
INSTANCE_ENDPOINTS="$FIXTURE_ROOT/endpoints.json"
FULL_ENDPOINTS="$FIXTURE_ROOT/endpoints-full.json"
FIXTURE_TARGET_ROOT=/data/course/_harness/fixtures
RESULT_ROOT=/data/result
CONTROL_URL=http://127.0.0.1:18100
STAGE45_FAILURES=0

record_stage45_failure() {
  local message="$1"
  STAGE45_FAILURES=$((STAGE45_FAILURES + 1))
  printf 'CODEX_STAGE45_FAILURE %s\n' "$message" >&2
}

resolve_operator_container_id_stage45() {
  local service_name="$1"
  local -a container_ids=()
  local container_id actual_id compose_project compose_service
  mapfile -t container_ids < <(
    docker compose -f deploy/docker-compose.operators.yml \
      ps --all --no-trunc -q "$service_name"
  )
  if [[ "${#container_ids[@]}" -ne 1 ]]; then
    printf '权威 Compose 中 %s 必须精确对应一个容器\n' "$service_name" >&2
    return 1
  fi
  container_id="${container_ids[0]}"
  actual_id="$(docker inspect -f '{{.Id}}' "$container_id")" || return 1
  compose_project="$(
    docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' \
      "$container_id"
  )" || return 1
  compose_service="$(
    docker inspect -f '{{ index .Config.Labels "com.docker.compose.service" }}' \
      "$container_id"
  )" || return 1
  if [[ ! "$container_id" =~ ^[0-9a-f]{64}$ || "$actual_id" != "$container_id" || \
        "$compose_project" != "algorithm-operators" || \
        "$compose_service" != "$service_name" ]]; then
    printf '算子 Compose 容器身份不匹配: %s\n' "$service_name" >&2
    return 1
  fi
  printf '%s\n' "$container_id"
}

wait_container_healthy_stage45() {
  local container_id="$1"
  local timeout_seconds="${2:-900}"
  local deadline status running
  deadline=$(( $(date +%s) + timeout_seconds ))
  while (( $(date +%s) < deadline )); do
    running="$(docker inspect -f '{{.State.Running}}' "$container_id" 2>/dev/null)" || {
      sleep 2
      continue
    }
    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
      "$container_id" 2>/dev/null)" || {
      sleep 2
      continue
    }
    if [[ "$running" == "true" && "$status" == "healthy" ]]; then
      return 0
    fi
    if [[ "$status" == "unhealthy" ]]; then
      printf '容器进入 unhealthy: %s\n' "$container_id" >&2
      return 1
    fi
    sleep 2
  done
  printf '等待容器 healthy 超时: %s\n' "$container_id" >&2
  return 1
}

write_gpu_trigger_stage45() {
  local output="$1"
  local operator_code="$2"
  local instance_id="$3"
  local run_id="$4"
  "$DEPLOY_PYTHON" - \
    "$output" "$operator_code" "$instance_id" "$run_id" \
    "$RELEASE_TAG" "$EXPECTED_GIT_SHA" "$REPORT_ROOT" \
    "$FIXTURE_MANIFEST" "$FIXTURE_ROOT" "$FIXTURE_TARGET_ROOT" \
    "$RESULT_ROOT" "$INSTANCE_ENDPOINTS" <<'PY'
import json
import os
import sys

(
    output,
    operator_code,
    instance_id,
    run_id,
    release_tag,
    git_sha,
    reports_root,
    fixture_manifest,
    fixture_root,
    fixture_target_root,
    result_root,
    endpoints,
) = sys.argv[1:]
argv = [
    "/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/scripts/run-operator-smoke",
    "--release-tag", release_tag,
    "--git-sha", git_sha,
    "--reports-root", reports_root,
    "--fixture-manifest", fixture_manifest,
    "--external-fixture-root", fixture_root,
    "--fixture-target-root", fixture_target_root,
    "--result-root", result_root,
    "--endpoints-json", endpoints,
    "--operator", operator_code,
    "--instance", instance_id,
    "--run-id", run_id,
    "--repeat", "1",
    "--hold-seconds", "30",
    "--timeout-seconds", "1200",
]
descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
try:
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as stream:
        json.dump(argv, stream, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
finally:
    os.close(descriptor)
PY
}

stop_verify_restart_gpu_stage45() (
  local container_id="$1"
  local instance_id="$2"
  local operator_code="$3"
  local physical_gpu="$4"
  local recovery_status=0
  local restart_complete=0
  local stop_attempted=0

  recover_stopped_gpu_stage45() {
    if ((stop_attempted == 0 || restart_complete == 1)); then
      return 0
    fi
    printf 'GPU 恢复兜底正在重启实例: %s\n' "$instance_id" >&2
    if docker restart "$container_id" >/dev/null 2>&1; then
      wait_container_healthy_stage45 "$container_id" 900 || true
    fi
  }

  trap recover_stopped_gpu_stage45 EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM

  stop_attempted=1
  if docker stop --time 60 "$container_id"; then
    :
  else
    recovery_status=$?
  fi
  if deploy/scripts/verify-gpu-instance \
    --container "$container_id" --instance-id "$instance_id" \
    --physical-gpu "$physical_gpu" --process-name "$operator_code" \
    --assert-stopped \
    --evidence "$RELEASE_ROOT/gpu-instances/${instance_id}.json" \
    --stop-timeout 60 \
    --output "$RELEASE_ROOT/recovery/${instance_id}-stopped.json"; then
    :
  else
    recovery_status=$?
  fi
  if docker restart "$container_id"; then
    if wait_container_healthy_stage45 "$container_id" 900; then
      if deploy/scripts/activate-operator-instances \
        --control-url "$CONTROL_URL" \
        --instance "$instance_id" --timeout-seconds 300; then
        restart_complete=1
      else
        recovery_status=$?
      fi
    else
      recovery_status=$?
    fi
  else
    recovery_status=$?
  fi

  if ((restart_complete == 1)); then
    trap - EXIT HUP INT TERM
  fi
  if ((recovery_status != 0 || restart_complete != 1)); then
    return 1
  fi
  return 0
)

verify_one_gpu_instance_stage45() {
  local service_name="$1"
  local operator_code="$2"
  local physical_gpu="$3"
  local instance_id="$service_name"
  local container_id trigger_file run_id
  local running_status=0 recovery_status=0 registration_status=0

  printf 'CODEX_STAGE4_GPU_START %s gpu=%s\n' "$instance_id" "$physical_gpu"
  container_id="$(resolve_operator_container_id_stage45 "$service_name")" || return 1
  if ! wait_container_healthy_stage45 "$container_id" 900; then
    return 1
  fi
  run_id="gpu-activity-${instance_id}"
  trigger_file="$(mktemp "/root/workspace/.${instance_id}.trigger.XXXXXX.json")" || return 1
  rm -f -- "$trigger_file"
  if ! write_gpu_trigger_stage45 "$trigger_file" "$operator_code" "$instance_id" "$run_id"; then
    rm -f -- "$trigger_file"
    return 1
  fi

  if deploy/scripts/verify-gpu-instance \
    --container "$container_id" --instance-id "$instance_id" \
    --physical-gpu "$physical_gpu" --process-name "$operator_code" \
    --trigger-file "$trigger_file" --sample-window 60 \
    --trigger-timeout 3600 \
    --output "$RELEASE_ROOT/gpu-instances/${instance_id}.json"; then
    running_status=0
  else
    running_status=$?
  fi
  rm -f -- "$trigger_file"

  if stop_verify_restart_gpu_stage45 \
    "$container_id" "$instance_id" "$operator_code" "$physical_gpu"; then
    :
  else
    recovery_status=$?
  fi
  if deploy/scripts/verify-operator-registration \
    --control-url "$CONTROL_URL" \
    --release-tag "$RELEASE_TAG" --git-sha "$EXPECTED_GIT_SHA" \
    --reports-root "$REPORT_ROOT" --timeout-seconds 300 \
    --instance "$instance_id"; then
    :
  else
    registration_status=$?
  fi

  if ((running_status != 0 || recovery_status != 0 || registration_status != 0)); then
    printf 'GPU 实例验证未全部通过: %s running=%s recovery=%s registration=%s\n' \
      "$instance_id" "$running_status" "$recovery_status" \
      "$registration_status" >&2
    return 1
  fi
  printf 'CODEX_STAGE4_GPU_PASS %s gpu=%s\n' "$instance_id" "$physical_gpu"
  return 0
}

run_cpu_instance_smoke_stage45() {
  local operator_code="$1"
  local instance_id="$2"
  printf 'CODEX_STAGE5_CPU_SMOKE_START %s\n' "$instance_id"
  deploy/scripts/run-operator-smoke \
    --release-tag "$RELEASE_TAG" --git-sha "$EXPECTED_GIT_SHA" \
    --reports-root "$REPORT_ROOT" \
    --fixture-manifest "$FIXTURE_MANIFEST" \
    --external-fixture-root "$FIXTURE_ROOT" \
    --fixture-target-root "$FIXTURE_TARGET_ROOT" \
    --result-root "$RESULT_ROOT" \
    --callback-listen-host "$ALGORITHM_PLATFORM_GATEWAY" \
    --callback-advertise-base-url "http://${ALGORITHM_PLATFORM_GATEWAY}:19090" \
    --endpoints-json "$INSTANCE_ENDPOINTS" \
    --operator "$operator_code" --instance "$instance_id" \
    --run-id "cpu-smoke-${instance_id}" --timeout-seconds 3600
}

GPU_MATRIX=(
  'asr-offline-gpu0|asr_offline|0'
  'asr-offline-gpu1|asr_offline|1'
  'asr-offline-gpu2|asr_offline|2'
  'asr-online-gpu0|asr_online|0'
  'asr-online-gpu1|asr_online|1'
  'asr-online-gpu2|asr_online|2'
  'ocr-gpu0|ocr|0'
  'ocr-gpu1|ocr|1'
  'ocr-gpu2|ocr|2'
  'vbas-gpu0|vbas|0'
  'vbas-gpu1|vbas|1'
  'vbas-gpu2|vbas|2'
  'facerec-gpu0|facerec|0'
  'facerec-gpu1|facerec|1'
  'facerec-gpu2|facerec|2'
  'screen-det-gpu0|screen_det|0'
  'screen-det-gpu1|screen_det|1'
  'screen-det-gpu2|screen_det|2'
)

for gpu_case in "${GPU_MATRIX[@]}"; do
  IFS='|' read -r service_name operator_code physical_gpu <<<"$gpu_case"
  if ! verify_one_gpu_instance_stage45 \
    "$service_name" "$operator_code" "$physical_gpu"; then
    record_stage45_failure "GPU:${service_name}"
  fi
done

if ! deploy/scripts/preflight operators --full --git-sha "$EXPECTED_GIT_SHA" \
  --control-url "$CONTROL_URL" \
  --release-tag "$RELEASE_TAG" --reports-root "$REPORT_ROOT"; then
  record_stage45_failure 'full-operator-preflight'
fi

ALGORITHM_PLATFORM_GATEWAY=''
if gateway_value="$(
  docker network inspect algorithm-platform \
    --format '{{(index .IPAM.Config 0).Gateway}}'
)"; then
  ALGORITHM_PLATFORM_GATEWAY="$gateway_value"
else
  record_stage45_failure '读取 algorithm-platform gateway 失败'
fi
if [[ -z "$ALGORITHM_PLATFORM_GATEWAY" || "$ALGORITHM_PLATFORM_GATEWAY" == '<no value>' ]]; then
  record_stage45_failure '无法解析 algorithm-platform gateway'
else
  CPU_MATRIX=(
    'ppt_slice|ppt-slice-cpu0'
    'ppt_slice|ppt-slice-cpu1'
    'ppt_slice|ppt-slice-cpu2'
    'text_analysis|text-analysis-cpu0'
    'text_analysis|text-analysis-cpu1'
    'text_analysis|text-analysis-cpu2'
  )
  for cpu_case in "${CPU_MATRIX[@]}"; do
    IFS='|' read -r operator_code instance_id <<<"$cpu_case"
    if run_cpu_instance_smoke_stage45 "$operator_code" "$instance_id"; then
      printf 'CODEX_STAGE5_CPU_SMOKE_PASS %s\n' "$instance_id"
    else
      record_stage45_failure "CPU-Smoke:${instance_id}"
    fi
  done
fi

FACEREC_IDS=()
for service_name in facerec-gpu0 facerec-gpu1 facerec-gpu2; do
  if container_id="$(resolve_operator_container_id_stage45 "$service_name")" && \
    wait_container_healthy_stage45 "$container_id" 900; then
    FACEREC_IDS+=("$service_name")
  else
    record_stage45_failure "FaceRec-ready:${service_name}"
  fi
done
if [[ "${#FACEREC_IDS[@]}" -eq 3 ]]; then
  if ! deploy/scripts/verify-operator-registration \
    --control-url "$CONTROL_URL" \
    --release-tag "$RELEASE_TAG" --git-sha "$EXPECTED_GIT_SHA" \
    --reports-root "$REPORT_ROOT" --timeout-seconds 300 \
    --instance facerec-gpu0 --instance facerec-gpu1 --instance facerec-gpu2; then
    record_stage45_failure 'FaceRec-three-instance-registration'
  fi
fi

if [[ -n "${ALGORITHM_PLATFORM_GATEWAY:-}" && \
      "$ALGORITHM_PLATFORM_GATEWAY" != '<no value>' ]]; then
  if ! deploy/scripts/run-operator-smoke \
    --release-tag "$RELEASE_TAG" --git-sha "$EXPECTED_GIT_SHA" \
    --reports-root "$REPORT_ROOT" \
    --fixture-manifest "$FIXTURE_MANIFEST" \
    --external-fixture-root "$FIXTURE_ROOT" \
    --fixture-target-root "$FIXTURE_TARGET_ROOT" \
    --result-root "$RESULT_ROOT" \
    --callback-listen-host "$ALGORITHM_PLATFORM_GATEWAY" \
    --callback-advertise-base-url "http://${ALGORITHM_PLATFORM_GATEWAY}:19090" \
    --endpoints-json "$FULL_ENDPOINTS" --timeout-seconds 3600; then
    record_stage45_failure 'full-eight-operator-smoke'
  fi
fi

printf 'CODEX_STAGE45_COMPLETE failures=%s\n' "$STAGE45_FAILURES"
true
