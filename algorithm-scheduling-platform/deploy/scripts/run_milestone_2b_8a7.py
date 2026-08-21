#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import shlex
from collections.abc import Sequence
from pathlib import Path

from deploy.scripts.operator_topology import CURRENT_TOPOLOGY, DEFAULT_TOPOLOGY_PATH
from deploy.scripts.run_milestone_2b_8a3 import (
    HISTORICAL_SCENARIO,
    ROOT,
    bash_blocks,
    execute_runtime,
    section,
)

SCENARIO = ROOT / "harness/scenarios/milestone-2b-seven-operator-release.md"
_CURRENT_SCENARIO_FIELDS = {
    "schema_version",
    "topology_path",
    "lifecycle_scaffold",
    "forbidden_runtime_markers",
}


def _quoted(value: object) -> str:
    return shlex.quote(str(value))


def _campaign_command(args: argparse.Namespace, phase: str) -> str:
    parts = [
        "deploy/scripts/run-milestone-2b-business-campaign",
        "--phase",
        phase,
        "--release-root",
        '"$RELEASE_ROOT"',
        "--manual-review-json",
        _quoted(args.manual_review_json),
        "--course-timeout-seconds",
        str(args.course_timeout_seconds),
        "--online-timeout-seconds",
        str(args.online_timeout_seconds),
        "--manual-review-timeout-seconds",
        str(args.manual_review_timeout_seconds),
        "--manual-review-poll-interval-seconds",
        str(args.manual_review_poll_interval_seconds),
    ]
    if phase == "offline":
        parts.extend(
            [
                "--teacher-video-url",
                _quoted(args.teacher_video_url),
                "--student-video-url",
                _quoted(args.student_video_url),
                "--slides-video-url",
                _quoted(args.slides_video_url),
            ]
        )
    return " \\\n  ".join(parts)


def _media_preflight_command(args: argparse.Namespace) -> str:
    media_document = json.dumps(
        {
            "teacher_video_url": args.teacher_video_url,
            "student_video_url": args.student_video_url,
            "slides_video_url": args.slides_video_url,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    parts = [
        "deploy/scripts/preflight-course-media",
        "--release-root",
        '"$RELEASE_ROOT"',
        "--media-json-stdin",
        "--attempts",
        str(args.media_preflight_attempts),
        "--request-timeout-seconds",
        str(args.media_preflight_timeout_seconds),
        "--retry-interval-seconds",
        str(args.media_preflight_retry_interval_seconds),
    ]
    return (
        " \\\n  ".join(parts)
        + " <<'CODEX_COURSE_MEDIA_JSON'\n"
        + media_document
        + "\nCODEX_COURSE_MEDIA_JSON"
    )


def _case_batch(phase: str) -> str:
    return f""".venv/bin/python -m scripts.run_milestone_2b_case_batch \\
  --catalog deploy/milestone-2b-case-catalog.yaml \\
  --release-root "$RELEASE_ROOT" \\
  --phase {phase} \\
  --concurrency 1 \\
  --delegated-lock-holder-pid "$OPERATOR_LIFECYCLE_LOCK_PID" \\
  --delegated-lock-path "$OPERATOR_LIFECYCLE_LOCK_PATH" \\
  --require-cleanup \\
  --require-all-selected"""


def _load_current_scenario() -> dict[str, object]:
    document = SCENARIO.read_text(encoding="utf-8")
    marker = "```json\n"
    if document.count(marker) != 1:
        raise RuntimeError("当前七算子 canonical 场景必须包含一个 JSON 合同")
    payload = document.split(marker, 1)[1].split("\n```", 1)[0]
    parsed = json.loads(payload)
    if type(parsed) is not dict or set(parsed) != _CURRENT_SCENARIO_FIELDS:
        raise RuntimeError("当前七算子 canonical 场景字段不完整")
    if parsed["schema_version"] != 1:
        raise RuntimeError("当前七算子 canonical 场景版本不受支持")
    topology_path = (ROOT / str(parsed["topology_path"])).resolve()
    scaffold_path = (ROOT / str(parsed["lifecycle_scaffold"])).resolve()
    if topology_path != DEFAULT_TOPOLOGY_PATH.resolve():
        raise RuntimeError("当前七算子 canonical 场景没有引用拓扑权威")
    if scaffold_path != HISTORICAL_SCENARIO.resolve():
        raise RuntimeError("当前七算子 canonical 场景的生命周期脚手架不受信任")
    markers = parsed["forbidden_runtime_markers"]
    if (
        type(markers) is not list
        or not markers
        or any(type(value) is not str or not value for value in markers)
        or len(markers) != len(set(markers))
    ):
        raise RuntimeError("当前七算子 canonical 场景的禁止标识无效")
    return parsed


def _topology_guard() -> str:
    totals = json.dumps(
        CURRENT_TOPOLOGY.totals,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f""".venv/bin/python - <<'PY'
import json

from deploy.scripts.operator_topology import CURRENT_TOPOLOGY

expected = json.loads({totals!r})
if CURRENT_TOPOLOGY.totals != expected:
    raise SystemExit(
        f"operator topology changed: {{CURRENT_TOPOLOGY.totals!r}} != {{expected!r}}"
    )
ordered = (
    "operator_types",
    "instances",
    "gpu_instances",
    "cpu_instances",
    "config_authority_processes",
    "operator_smoke_types",
)
print(
    "CODEX_CURRENT_TOPOLOGY "
    + " ".join(f"{{key}}={{expected[key]}}" for key in ordered)
)
PY"""


def _adapt_current_stage3(stage3: str) -> str:
    instance_count = CURRENT_TOPOLOGY.totals["instances"]
    legacy_service_gate = (
        'if [[ "$(wc -l <"$OPERATOR_SERVICE_ALLOWLIST_TMP" | '
        "tr -d ' ')\" != 24 ]]; then\n"
        '  echo "权威算子 Compose 必须精确包含 24 个 service" >&2\n'
        "  exit 1\n"
        "fi"
    )
    current_service_gate = (
        'if [[ "$(wc -l <"$OPERATOR_SERVICE_ALLOWLIST_TMP" | '
        f"tr -d ' ')\" != {instance_count} ]]; then\n"
        f'  echo "权威算子 Compose 必须精确包含 {instance_count} 个 service" >&2\n'
        "  exit 1\n"
        "fi"
    )
    legacy_ledger_gate = """test "$(wc -l <"$NEW_OPERATOR_IDS" | tr -d ' ')" = 24"""
    current_ledger_gate = (
        f"test \"$(wc -l <\"$NEW_OPERATOR_IDS\" | tr -d ' ')\" = {instance_count}"
    )
    legacy_inheritance = '''  validate_operator_ledger_file "$PREVIOUS_BASELINE_OPERATOR_IDS"
  validate_operator_ledger_file "$PREVIOUS_NEW_OPERATOR_IDS"

  INHERIT_CURRENT_TMP="$(
    mktemp "$LEDGER_DIR/.inherit-current-operator-container-ids.XXXXXX"
  )"
  OPERATOR_LEDGER_TEMPS+=("$INHERIT_CURRENT_TMP")
  INHERIT_NEW_TMP="$(
    mktemp "$LEDGER_DIR/.inherit-new-operator-container-ids.XXXXXX"
  )"
  OPERATOR_LEDGER_TEMPS+=("$INHERIT_NEW_TMP")
  snapshot_current_operator_ids "$INHERIT_CURRENT_TMP"
  if ! comm -23 "$INHERIT_CURRENT_TMP" "$PREVIOUS_BASELINE_OPERATOR_IDS" \\
    >"$INHERIT_NEW_TMP"; then
    echo "无法重算 current 与 previous baseline 的差集" >&2
    exit 1
  fi
  if ! cmp -s "$INHERIT_NEW_TMP" "$PREVIOUS_NEW_OPERATOR_IDS"; then
    echo "current - previous baseline 必须与 previous new ledger 精确一致" >&2
    exit 1
  fi

  BASELINE_TMP="$(mktemp "$LEDGER_DIR/.baseline-operator-container-ids.XXXXXX")"
  OPERATOR_LEDGER_TEMPS+=("$BASELINE_TMP")
  if ! cp -- "$PREVIOUS_BASELINE_OPERATOR_IDS" "$BASELINE_TMP"; then
    echo "无法继承 previous baseline 账本" >&2
    exit 1
  fi
  chmod 0600 "$BASELINE_TMP"
  if ! cmp -s "$BASELINE_TMP" "$PREVIOUS_BASELINE_OPERATOR_IDS"; then
    echo "previous baseline 继承内容不一致" >&2
    exit 1
  fi'''
    current_inheritance = '''  PROJECTED_PREVIOUS_BASELINE_TMP="$(
    mktemp "$LEDGER_DIR/.projected-previous-baseline-operator-container-ids.XXXXXX"
  )"
  OPERATOR_LEDGER_TEMPS+=("$PROJECTED_PREVIOUS_BASELINE_TMP")
  PROJECTED_PREVIOUS_NEW_TMP="$(
    mktemp "$LEDGER_DIR/.projected-previous-new-operator-container-ids.XXXXXX"
  )"
  OPERATOR_LEDGER_TEMPS+=("$PROJECTED_PREVIOUS_NEW_TMP")
  if ! "$DEPLOY_PYTHON" deploy/scripts/deployment_contracts.py \\
    project-inherited-operator-ledgers \\
    --allowlist "$OPERATOR_SERVICE_ALLOWLIST_TMP" \\
    --baseline "$PREVIOUS_BASELINE_OPERATOR_IDS" \\
    --new "$PREVIOUS_NEW_OPERATOR_IDS" \\
    --projected-baseline "$PROJECTED_PREVIOUS_BASELINE_TMP" \\
    --projected-new "$PROJECTED_PREVIOUS_NEW_TMP"; then
    echo "继承算子账本无法安全投影到当前拓扑" >&2
    exit 1
  fi
  validate_operator_ledger_file "$PROJECTED_PREVIOUS_BASELINE_TMP"
  validate_operator_ledger_file "$PROJECTED_PREVIOUS_NEW_TMP"

  INHERIT_CURRENT_TMP="$(
    mktemp "$LEDGER_DIR/.inherit-current-operator-container-ids.XXXXXX"
  )"
  OPERATOR_LEDGER_TEMPS+=("$INHERIT_CURRENT_TMP")
  INHERIT_NEW_TMP="$(
    mktemp "$LEDGER_DIR/.inherit-new-operator-container-ids.XXXXXX"
  )"
  OPERATOR_LEDGER_TEMPS+=("$INHERIT_NEW_TMP")
  snapshot_current_operator_ids "$INHERIT_CURRENT_TMP"
  if ! comm -23 "$INHERIT_CURRENT_TMP" "$PROJECTED_PREVIOUS_BASELINE_TMP" \\
    >"$INHERIT_NEW_TMP"; then
    echo "无法重算 current 与 projected previous baseline 的差集" >&2
    exit 1
  fi
  if ! cmp -s "$INHERIT_NEW_TMP" "$PROJECTED_PREVIOUS_NEW_TMP"; then
    printf '%s%s\n' \\
      "current - previous baseline（当前拓扑投影后）必须与 " \\
      "previous new ledger（当前拓扑投影后）精确一致" >&2
    exit 1
  fi

  BASELINE_TMP="$(mktemp "$LEDGER_DIR/.baseline-operator-container-ids.XXXXXX")"
  OPERATOR_LEDGER_TEMPS+=("$BASELINE_TMP")
  if ! cp -- "$PROJECTED_PREVIOUS_BASELINE_TMP" "$BASELINE_TMP"; then
    echo "无法继承 projected previous baseline 账本" >&2
    exit 1
  fi
  chmod 0600 "$BASELINE_TMP"
  if ! cmp -s "$BASELINE_TMP" "$PROJECTED_PREVIOUS_BASELINE_TMP"; then
    echo "projected previous baseline 继承内容不一致" >&2
    exit 1
  fi'''
    if (
        stage3.count(legacy_service_gate) != 1
        or stage3.count(legacy_ledger_gate) != 1
        or stage3.count(legacy_inheritance) != 1
    ):
        raise RuntimeError("历史生命周期脚手架中的实例数量门禁发生漂移")
    return (
        stage3.replace(legacy_service_gate, current_service_gate)
        .replace(legacy_ledger_gate, current_ledger_gate)
        .replace(legacy_inheritance, current_inheritance)
    )


def _current_stage_blocks() -> tuple[list[str], str, str, str, list[str]]:
    document = HISTORICAL_SCENARIO.read_text(encoding="utf-8")
    prelude = document.split("## 阶段 1：服务器预检、快照和暂停", 1)[0]
    selected_prelude = [
        block
        for block in bash_blocks(prelude)
        if block.startswith("set -euo pipefail")
        or "prepare-report-directory" in block
        or "HARNESS_RUNTIME_EVIDENCE" in block
    ]
    stage1 = bash_blocks(
        section(
            document,
            "## 阶段 1：服务器预检、快照和暂停",
            "## 阶段 2：基础设施、模型资产和八镜像",
        )
    )
    stage2 = bash_blocks(
        section(
            document,
            "## 阶段 2：基础设施、模型资产和八镜像",
            "## 阶段 3：平台和逐卡算子拓扑",
        )
    )
    stage3 = bash_blocks(
        section(
            document,
            "## 阶段 3：平台和逐卡算子拓扑",
            "## 阶段 4：GPU 真实性证据",
        )
    )
    stage6 = bash_blocks(
        section(
            document,
            "## 阶段 6：反例、压力、恢复和报告渲染",
            "## 2026-08-17 现场执行结果",
        )
    )
    if not (
        len(selected_prelude) == 3
        and len(stage1) == len(stage2) == len(stage3) == 1
        and len(stage6) == 3
    ):
        raise RuntimeError("无法唯一解析里程碑 2B canonical 生命周期脚手架")
    return selected_prelude, stage1[0], stage2[0], _adapt_current_stage3(stage3[0]), stage6


def build_runtime(args: argparse.Namespace) -> str:
    current_scenario = _load_current_scenario()
    selected_prelude, stage1, stage2, stage3, stage6 = _current_stage_blocks()

    infrastructure_and_clean_clone = """
docker compose -f deploy/docker-compose.infrastructure.yml up -d --wait --wait-timeout 300
deploy/scripts/run-milestone-2b-clean-clone-gate \\
  --release-root "$RELEASE_ROOT" \\
  --expected-git-sha "$EXPECTED_GIT_SHA"
deploy/scripts/verify-operator-config-authority \\
  --workspace-root .. \\
  --git-sha "$EXPECTED_GIT_SHA" \\
  --output "$RELEASE_ROOT/preflight/operator-config-authority.json"
"""
    stage45_and_deployment = f"""
set +e
. deploy/scripts/milestone-2b-stage45.sh
stage45_status="$STAGE45_FAILURES"
set -e
test "$stage45_status" = 0
if ! curl --fail --silent --show-error --max-time 10 \\
  http://127.0.0.1:18101/ops/readiness >/dev/null
then
  printf 'Orchestrator 在部署用例前未就绪，执行精确服务重启后重新验收\n' >&2
  docker compose -f deploy/docker-compose.platform.yml restart orchestrator-service
  docker compose -f deploy/docker-compose.platform.yml up -d \\
    --wait --wait-timeout 300 orchestrator-service
fi
deploy/scripts/preflight runtime --git-sha "$EXPECTED_GIT_SHA"
{stage6[0]}
for runtime_probe in \
  'orchestrator-service|http://127.0.0.1:18101/ops/readiness' \
  'vision-orchestrator-service|http://127.0.0.1:18102/ready'
do
  IFS='|' read -r runtime_service readiness_url <<<"$runtime_probe"
  if ! curl --fail --silent --show-error --max-time 10 \
    "$readiness_url" >/dev/null
  then
    printf '%s 在部署变异用例后未就绪，执行精确服务重启后重新验收\n' \
      "$runtime_service" >&2
    docker compose -f deploy/docker-compose.platform.yml restart \
      "$runtime_service"
    docker compose -f deploy/docker-compose.platform.yml up -d \
      --wait --wait-timeout 300 "$runtime_service"
  fi
  curl --fail --silent --show-error --max-time 10 \
    "$readiness_url" >/dev/null
done
deploy/scripts/preflight runtime --git-sha "$EXPECTED_GIT_SHA"
"""
    business = "\n\n".join(
        f"{_campaign_command(args, phase)}\n{_case_batch(phase)}"
        for phase in ("offline", "vision", "online", "final")
    )
    image_cleanup = """
deploy/scripts/release-image-cleanup cleanup \\
  --release-root "$RELEASE_ROOT" \\
  --deploy-root deploy \\
  --execute \\
  --lifecycle-lock-holder-pid "$OPERATOR_LIFECYCLE_LOCK_PID" \\
  --lifecycle-lock-path "$OPERATOR_LIFECYCLE_LOCK_PATH"
"""
    terminal = """
printf 'CODEX_8A7_TERMINAL stage45_failures=%s ' "$stage45_status"
printf 'overall_status=通过 cleanup=complete restore=complete\n'
test "$stage45_status" = 0
"""
    runtime = "\n\n".join(
        [
            *selected_prelude,
            _topology_guard(),
            stage1,
            infrastructure_and_clean_clone,
            stage2,
            stage3,
            stage45_and_deployment,
            _media_preflight_command(args),
            business,
            stage6[2],
            image_cleanup,
            stage6[1],
            terminal,
        ]
    )
    forbidden = current_scenario["forbidden_runtime_markers"]
    assert isinstance(forbidden, list)
    present = [marker for marker in forbidden if marker in runtime]
    if present:
        raise RuntimeError(f"当前七算子 runtime 包含退役命令: {present}")
    return runtime


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--teacher-video-url", required=True)
    parser.add_argument("--student-video-url", required=True)
    parser.add_argument("--slides-video-url", required=True)
    parser.add_argument("--manual-review-json", type=_path, required=True)
    parser.add_argument("--course-timeout-seconds", type=float, default=14400)
    parser.add_argument("--online-timeout-seconds", type=float, default=300)
    parser.add_argument("--manual-review-timeout-seconds", type=float, default=7200)
    parser.add_argument("--manual-review-poll-interval-seconds", type=float, default=2)
    parser.add_argument("--media-preflight-attempts", type=int, choices=(3,), default=3)
    parser.add_argument("--media-preflight-timeout-seconds", type=float, default=30)
    parser.add_argument("--media-preflight-retry-interval-seconds", type=float, default=2)
    args = parser.parse_args(argv)
    for field in (
        "course_timeout_seconds",
        "online_timeout_seconds",
        "manual_review_timeout_seconds",
        "manual_review_poll_interval_seconds",
        "media_preflight_timeout_seconds",
        "media_preflight_retry_interval_seconds",
    ):
        value = getattr(args, field)
        if not math.isfinite(value) or value <= 0:
            parser.error(f"{field} must be a finite positive number")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    completed = execute_runtime(build_runtime(args), cwd=ROOT)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
