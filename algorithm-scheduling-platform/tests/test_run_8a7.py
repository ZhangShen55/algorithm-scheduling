from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

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
        manual_review_timeout_seconds=7200.0,
        manual_review_poll_interval_seconds=2.0,
        media_preflight_attempts=3,
        media_preflight_timeout_seconds=30.0,
        media_preflight_retry_interval_seconds=2.0,
    )


def test_8a7_runtime_orders_all_strict_gates_before_restore(tmp_path: Path) -> None:
    runtime = runner.build_runtime(_arguments(tmp_path))

    expected = (
        "run-milestone-2b-clean-clone-gate",
        "verify-operator-config-authority",
        "deploy/scripts/build-images",
        "milestone-2b-stage45.sh",
        "Orchestrator 在部署用例前未就绪",
        "--phase deployment",
        "preflight-course-media",
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
        runtime.rindex(value)
        if value == "restore-existing-containers"
        else runtime.index(value)
        for value in expected
    ]
    assert offsets == sorted(offsets)
    preflight = 'preflight runtime --git-sha "$EXPECTED_GIT_SHA"'
    assert runtime.index(preflight) < runtime.index("--phase deployment")
    assert runtime.index("--phase deployment") < runtime.rindex(preflight)
    assert runtime.rindex(preflight) < runtime.index("--phase offline")
    assert runtime.index("--phase deployment") < runtime.index(
        "preflight-course-media"
    )
    assert runtime.index("preflight-course-media") < runtime.index("--phase offline")


def test_8a7_runtime_uses_current_seven_operator_authority(tmp_path: Path) -> None:
    runtime = runner.build_runtime(_arguments(tmp_path))
    contract = runner._load_current_scenario()
    totals = runner.CURRENT_TOPOLOGY.totals

    assert runner.SCENARIO.name == "milestone-2b-seven-operator-release.md"
    assert totals == {
        "operator_types": 7,
        "instances": 21,
        "gpu_instances": 18,
        "cpu_instances": 3,
        "config_authority_processes": 14,
        "operator_smoke_types": 7,
    }
    assert "CODEX_CURRENT_TOPOLOGY" in runtime
    assert "必须精确包含 21 个 service" in runtime
    assert "= 21" in runtime
    assert all(
        marker not in runtime for marker in contract["forbidden_runtime_markers"]
    )


def test_8a7_stabilizes_orchestrator_before_deployment_cases(tmp_path: Path) -> None:
    runtime = runner.build_runtime(_arguments(tmp_path))

    readiness = "http://127.0.0.1:18101/ops/readiness"
    restart = (
        "docker compose -f deploy/docker-compose.platform.yml restart "
        "orchestrator-service"
    )
    wait = (
        "docker compose -f deploy/docker-compose.platform.yml up -d "
        "\\\n    --wait --wait-timeout 300 orchestrator-service"
    )
    preflight = 'deploy/scripts/preflight runtime --git-sha "$EXPECTED_GIT_SHA"'

    assert runtime.count(readiness) == 2
    assert runtime.count(restart) == 1
    assert runtime.count(wait) == 1
    assert runtime.count(preflight) >= 3
    assert "restart control-service" not in runtime
    assert "restart vision-orchestrator-service" not in runtime
    assert "restart online-gateway-service" not in runtime
    assert "restart postgresql" not in runtime
    assert "restart redis" not in runtime
    assert "restart kafka" not in runtime
    assert "restart mongodb" not in runtime
    assert runtime.index("milestone-2b-stage45.sh") < runtime.index(readiness)
    assert runtime.index(readiness) < runtime.index(restart)
    assert runtime.index(restart) < runtime.index(wait)
    assert runtime.index(preflight) < runtime.index("--phase deployment")


def test_8a7_restabilizes_only_offline_runtimes_after_deployment_cases(
    tmp_path: Path,
) -> None:
    runtime = runner.build_runtime(_arguments(tmp_path))

    deployment = runtime.index("--phase deployment")
    offline = runtime.index("--phase offline")
    gate = runtime[deployment:offline]

    assert "http://127.0.0.1:18101/ops/readiness" in gate
    assert "http://127.0.0.1:18102/ready" in gate
    assert "orchestrator-service|http://127.0.0.1:18101/ops/readiness" in gate
    assert (
        "vision-orchestrator-service|http://127.0.0.1:18102/ready" in gate
    )
    assert "docker compose -f deploy/docker-compose.platform.yml restart" in gate
    assert '"$runtime_service"' in gate
    assert '--wait --wait-timeout 300 "$runtime_service"' in gate
    assert gate.count('curl --fail --silent --show-error --max-time 10') == 2
    assert gate.count('preflight runtime --git-sha "$EXPECTED_GIT_SHA"') == 1
    assert "restart control-service" not in gate
    assert "restart online-gateway-service" not in gate
    assert "restart postgresql" not in gate
    assert "restart redis" not in gate
    assert "restart kafka" not in gate
    assert "restart mongodb" not in gate


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
    _, _, _, stage3, _ = runner._current_stage_blocks()
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


def test_8a7_campaign_command_passes_only_declared_arguments(tmp_path: Path) -> None:
    arguments = _arguments(tmp_path)
    executable = tmp_path / "deploy/scripts/run-milestone-2b-business-campaign"
    executable.parent.mkdir(parents=True)
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import sys\n"
        "print(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)

    completed = subprocess.run(
        ["bash", "-c", runner._campaign_command(arguments, "offline")],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "RELEASE_ROOT": "$RELEASE_ROOT"},
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == [
        "--phase",
        "offline",
        "--release-root",
        "$RELEASE_ROOT",
        "--manual-review-json",
        str(arguments.manual_review_json),
        "--course-timeout-seconds",
        "14400.0",
        "--online-timeout-seconds",
        "300.0",
        "--manual-review-timeout-seconds",
        "7200.0",
        "--manual-review-poll-interval-seconds",
        "2.0",
        "--teacher-video-url",
        arguments.teacher_video_url,
        "--student-video-url",
        arguments.student_video_url,
        "--slides-video-url",
        arguments.slides_video_url,
    ]


def test_8a7_media_preflight_passes_only_declared_arguments(tmp_path: Path) -> None:
    arguments = _arguments(tmp_path)
    executable = tmp_path / "deploy/scripts/preflight-course-media"
    executable.parent.mkdir(parents=True)
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import sys\n"
        "print(json.dumps({'argv': sys.argv[1:], 'stdin': json.load(sys.stdin)}))\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)

    completed = subprocess.run(
        ["bash", "-c", runner._media_preflight_command(arguments)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "RELEASE_ROOT": "$RELEASE_ROOT"},
    )

    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)
    assert observed["argv"] == [
        "--release-root",
        "$RELEASE_ROOT",
        "--media-json-stdin",
        "--attempts",
        "3",
        "--request-timeout-seconds",
        "30.0",
        "--retry-interval-seconds",
        "2.0",
    ]
    assert observed["stdin"] == {
        "teacher_video_url": arguments.teacher_video_url,
        "student_video_url": arguments.student_video_url,
        "slides_video_url": arguments.slides_video_url,
    }
    assert arguments.teacher_video_url not in observed["argv"]
    assert arguments.student_video_url not in observed["argv"]
    assert arguments.slides_video_url not in observed["argv"]


def test_execute_runtime_waits_for_exit_recovery_after_sigint(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    recovered = tmp_path / "recovered"
    lock_held_during_recovery = tmp_path / "lock-held-during-recovery"
    runtime = (
        "set -euo pipefail\n"
        "python -c 'import time; time.sleep(60)' "
        "operator_lifecycle.py hold-lock &\n"
        "holder=$!\n"
        "trap 'kill -0 \"$holder\" && "
        f"printf held >{runner._quoted(lock_held_during_recovery)}; "
        "kill \"$holder\"; wait \"$holder\" 2>/dev/null || true; "
        f"printf recovered >{runner._quoted(recovered)}' EXIT\n"
        f"printf ready >{runner._quoted(ready)}\n"
        "sleep 60\n"
    )
    child_code = (
        "from pathlib import Path\n"
        "from deploy.scripts.run_milestone_2b_8a3 import execute_runtime\n"
        f"result = execute_runtime({runtime!r}, cwd=Path({str(runner.ROOT)!r}))\n"
        "raise SystemExit(result.returncode)\n"
    )
    worker = subprocess.Popen(
        [sys.executable, "-c", child_code],
        cwd=runner.ROOT,
        env={**os.environ, "PYTHONPATH": f"{runner.ROOT}:{runner.ROOT.parent}"},
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()

        worker.send_signal(signal.SIGINT)
        returncode = worker.wait(timeout=10)
    finally:
        if worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=10)

    assert returncode != 0
    assert lock_held_during_recovery.read_text(encoding="utf-8") == "held"
    assert recovered.read_text(encoding="utf-8") == "recovered"


def test_8a7_rejects_invalid_review_wait_before_building_runtime(tmp_path: Path) -> None:
    review = tmp_path / "reviews.json"
    review.write_text("{}\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        runner.parse_args(
            [
                "--teacher-video-url",
                "http://media.test/teacher.mp4",
                "--student-video-url",
                "http://media.test/student.mp4",
                "--slides-video-url",
                "http://media.test/slides.mp4",
                "--manual-review-json",
                str(review),
                "--manual-review-timeout-seconds",
                "0",
            ]
        )


@pytest.mark.parametrize("attempts", ("0", "2", "4"))
def test_8a7_requires_exactly_three_media_preflight_attempts(
    tmp_path: Path,
    attempts: str,
) -> None:
    review = tmp_path / "reviews.json"
    review.write_text("{}\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        runner.parse_args(
            [
                "--teacher-video-url",
                "http://media.test/teacher.mp4",
                "--student-video-url",
                "http://media.test/student.mp4",
                "--slides-video-url",
                "http://media.test/slides.mp4",
                "--manual-review-json",
                str(review),
                "--media-preflight-attempts",
                attempts,
            ]
        )
