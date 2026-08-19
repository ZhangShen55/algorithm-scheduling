from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from deploy.scripts import run_milestone_2b_8a7 as runner


def _arguments(tmp_path: Path) -> argparse.Namespace:
    review = tmp_path / "reviews.json"
    review.write_text("{}\n", encoding="utf-8")
    return argparse.Namespace(
        teacher_video_url="http://media.test/teacher.mp4",
        student_video_url="http://media.test/student.mp4",
        slides_video_url="http://media.test/slides.mp4",
        manual_review_json=review,
        course_timeout_seconds=14400.0,
        online_timeout_seconds=300.0,
    )


def test_8a7_runtime_orders_all_strict_gates_before_restore(tmp_path: Path) -> None:
    runtime = runner.build_runtime(_arguments(tmp_path))

    expected = (
        "run-milestone-2b-clean-clone-gate",
        "verify-operator-config-authority",
        "deploy/scripts/build-images",
        "milestone-2b-stage45.sh",
        "--phase deployment",
        "--phase offline",
        "--phase vision",
        "--phase online",
        "--phase final",
        "aggregate_milestone_2b_cases.py",
        "render_milestone_2b_report.py",
        "release-image-cleanup cleanup",
        "restore-existing-containers",
        "CODEX_8A7_TERMINAL",
    )
    offsets = [
        runtime.rindex(value) if value == "restore-existing-containers" else runtime.index(value)
        for value in expected
    ]
    assert offsets == sorted(offsets)


def test_8a7_cleanup_is_exact_and_lifecycle_guarded(tmp_path: Path) -> None:
    runtime = runner.build_runtime(_arguments(tmp_path))

    assert "--lifecycle-lock-holder-pid" in runtime
    assert "--lifecycle-lock-path" in runtime
    assert "trap cleanup_milestone_2b_runtime EXIT" in runtime
    assert "restore_existing_business_after_failure" in runtime
    assert "trap cleanup_operator_lifecycle EXIT" in runtime
    assert "MILESTONE_2B_RESTORE_COMPLETED=1" in runtime
    assert "docker system prune" not in runtime
    assert "docker image rm -f" not in runtime
    assert "down -v" not in runtime


def test_stage3_early_failure_uses_outer_restore_before_operator_trap(
    tmp_path: Path,
) -> None:
    document = runner.SCENARIO.read_text(encoding="utf-8")
    stage3 = runner.bash_blocks(
        runner.section(
            document,
            "## 阶段 3：平台和逐卡算子拓扑",
            "## 阶段 4：GPU 真实性证据",
        )
    )[0]
    release_root = tmp_path / "reports/milestone-2b/releases/v1.0_260812" / ("a" * 40)
    ledger_dir = release_root / "container-maintenance"
    ledger_dir.mkdir(parents=True)
    (ledger_dir / "baseline-operator-container-ids.txt").write_text("", encoding="utf-8")
    (ledger_dir / "new-operator-container-ids.txt").write_text("", encoding="utf-8")
    restore_marker = tmp_path / "restored"
    script = f"""
set -euo pipefail
RELEASE_ROOT={runner._quoted(release_root)}
PREVIOUS_RELEASE_ROOT=
REPORT_ROOT={runner._quoted(tmp_path / 'reports')}
RELEASE_TAG=v1.0_260812
EXPECTED_GIT_SHA={'a' * 40}
DEPLOY_PYTHON=python3
PLATFORM_WAIT_TIMEOUT_SECONDS=180
OPERATOR_LIFECYCLE_LOCK_PID=1
OPERATOR_LIFECYCLE_LOCK_PATH={runner._quoted(tmp_path / 'lifecycle.lock')}
MILESTONE_2B_RESTORE_COMPLETED=0
operator_lifecycle_lock_is_held() {{ return 0; }}
restore_existing_business_after_failure() {{
  printf restored >{runner._quoted(restore_marker)}
  MILESTONE_2B_RESTORE_COMPLETED=1
}}
release_operator_lifecycle_lock() {{ return 0; }}
cleanup_milestone_2b_runtime() {{
  local original_status=$?
  trap - EXIT
  restore_existing_business_after_failure
  return "$original_status"
}}
trap cleanup_milestone_2b_runtime EXIT
mktemp() {{ return 73; }}
docker() {{ return 99; }}
{stage3}
"""

    completed = subprocess.run(
        ["bash", "-c", script],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert restore_marker.read_text(encoding="utf-8") == "restored"
    assert "command not found" not in completed.stderr


def test_8a7_campaign_requires_manual_review_evidence(tmp_path: Path) -> None:
    arguments = _arguments(tmp_path)
    runtime = runner.build_runtime(arguments)

    assert runtime.count("--manual-review-json") == 4
    assert str(arguments.manual_review_json) in runtime
