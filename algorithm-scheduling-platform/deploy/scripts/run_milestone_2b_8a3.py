#!/usr/bin/env python3

from __future__ import annotations

import os
import re
import signal
import subprocess
from pathlib import Path
from types import FrameType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "harness/scenarios/milestone-2b-deploy.md"


def bash_blocks(markdown: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)\n```", markdown, re.DOTALL)


def section(document: str, heading: str, next_heading: str) -> str:
    return document.split(heading, 1)[1].split(next_heading, 1)[0]


def _terminate_runtime_children(root_pid: int) -> None:
    """Stop runtime work while preserving the release lock holder for EXIT cleanup."""
    completed = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,command="],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return

    children: dict[int, list[int]] = {}
    commands: dict[int, str] = {}
    for line in completed.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) < 2:
            continue
        try:
            pid = int(fields[0])
            parent_pid = int(fields[1])
        except ValueError:
            continue
        children.setdefault(parent_pid, []).append(pid)
        commands[pid] = fields[2] if len(fields) == 3 else ""

    descendants: list[int] = []
    pending = list(children.get(root_pid, ()))
    while pending:
        pid = pending.pop()
        descendants.append(pid)
        pending.extend(children.get(pid, ()))

    protected = {
        pid
        for pid in descendants
        if "operator_lifecycle.py" in commands.get(pid, "")
        and "hold-lock" in commands.get(pid, "")
    }
    pending = list(protected)
    while pending:
        pid = pending.pop()
        for child_pid in children.get(pid, ()):
            if child_pid not in protected:
                protected.add(child_pid)
                pending.append(child_pid)

    for pid in reversed(descendants):
        if pid in protected:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError:
            continue


def execute_runtime(
    runtime: str,
    *,
    cwd: Path,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    signal_traps = "\n".join(
        (
            "trap 'exit 129' HUP",
            "trap 'exit 130' INT",
            "trap 'exit 143' TERM",
        )
    )
    # Keep the script out of stdin because deployment subprocesses may consume it.
    process = subprocess.Popen(
        ["bash", "-c", f"{signal_traps}\n{runtime}"],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        # Do not let a terminal signal kill the release lock holder before Bash can
        # run its EXIT recovery trap. The controller translates HUP/INT/TERM to TERM
        # for the outer Bash and waits for recovery to finish.
        start_new_session=True,
    )
    forwarded_signal: int | None = None
    previous_handlers: dict[signal.Signals, Any] = {}

    def terminate_outer_shell(signum: int, _frame: FrameType | None) -> None:
        nonlocal forwarded_signal
        if forwarded_signal is not None:
            return
        forwarded_signal = signum
        if process.poll() is None:
            _terminate_runtime_children(process.pid)
            # Bash normally exits immediately on SIGTERM without running an EXIT
            # trap. The explicit shell traps above translate the forwarded signal
            # into `exit`, so the canonical recovery trap finishes before this
            # controller returns.
            try:
                os.kill(process.pid, signum)
            except ProcessLookupError:
                pass

    try:
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, terminate_outer_shell)
        stdout, stderr = process.communicate()
    except KeyboardInterrupt:
        # Keep the same safe recovery behavior if signal delivery races with
        # handler installation.
        terminate_outer_shell(signal.SIGINT, None)
        stdout, stderr = process.communicate()
    finally:
        for registered_signal, handler in previous_handlers.items():
            signal.signal(registered_signal, handler)

    return subprocess.CompletedProcess(
        process.args,
        process.returncode,
        stdout,
        stderr,
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
.venv/bin/python -m scripts.run_milestone_2b_case_batch \
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
