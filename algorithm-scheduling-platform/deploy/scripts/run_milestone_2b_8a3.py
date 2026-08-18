#!/usr/bin/env python3

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "harness/scenarios/milestone-2b-deploy.md"


def bash_blocks(markdown: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)\n```", markdown, re.DOTALL)


def section(document: str, heading: str, next_heading: str) -> str:
    return document.split(heading, 1)[1].split(next_heading, 1)[0]


def execute_runtime(
    runtime: str,
    *,
    cwd: Path,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    # Keep the script out of stdin because deployment subprocesses may consume it.
    return subprocess.run(
        ["bash", "-c", runtime],
        cwd=cwd,
        text=True,
        capture_output=capture_output,
    )


def main() -> int:
    document = SCENARIO.read_text(encoding="utf-8")
    prelude = document.split("## 阶段 1：服务器预检、快照和暂停", 1)[0]
    prelude_blocks = bash_blocks(prelude)
    selected_prelude = [
        block
        for block in prelude_blocks
        if block.startswith("set -euo pipefail")
        or "prepare-report-directory" in block
        or "HARNESS_RUNTIME_EVIDENCE" in block
    ]
    if len(selected_prelude) != 3:
        raise RuntimeError("无法唯一解析发布变量、报告初始化和 Harness Python 块")
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
    if not (len(stage1) == len(stage2) == len(stage3) == 1 and len(stage6) >= 2):
        raise RuntimeError("canonical 阶段代码块数量不符合预期")
    runtime = "\n\n".join(
        [
            *selected_prelude,
            stage1[0],
            stage2[0],
            stage3[0],
            """
set +e
. deploy/scripts/milestone-2b-stage45.sh
stage45_status="$STAGE45_FAILURES"
.venv/bin/python scripts/run_milestone_2b_case_batch.py \
  --catalog deploy/milestone-2b-case-catalog.yaml \
  --release-root "$RELEASE_ROOT" \
  --phase deployment \
  --concurrency 1 \
  --delegated-lock-holder-pid "$OPERATOR_LIFECYCLE_LOCK_PID" \
  --delegated-lock-path "$OPERATOR_LIFECYCLE_LOCK_PATH" \
  --require-cleanup \
  --require-all-selected
deployment_status=$?
set -e
""",
            stage6[1],
            """
printf 'CODEX_8A3_TERMINAL stage45_failures=%s deployment_status=%s\n' \
  "$stage45_status" "$deployment_status"
test "$stage45_status" = 0
test "$deployment_status" = 0
""",
        ]
    )
    completed = execute_runtime(runtime, cwd=ROOT)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
