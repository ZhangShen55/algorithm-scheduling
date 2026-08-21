#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import shlex
from collections.abc import Sequence
from pathlib import Path

from deploy.scripts.run_milestone_2b_8a3 import (
    ROOT,
    SCENARIO,
    bash_blocks,
    execute_runtime,
    section,
)


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


def build_runtime(args: argparse.Namespace) -> str:
    document = SCENARIO.read_text(encoding="utf-8")
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
        raise RuntimeError("无法唯一解析里程碑 2B canonical 阶段")

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
    return "\n\n".join(
        [
            *selected_prelude,
            stage1[0],
            infrastructure_and_clean_clone,
            stage2[0],
            stage3[0],
            stage45_and_deployment,
            _media_preflight_command(args),
            business,
            stage6[2],
            image_cleanup,
            stage6[1],
            terminal,
        ]
    )


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
