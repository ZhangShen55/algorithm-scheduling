from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from io import StringIO
from pathlib import Path

import pytest

from deploy.scripts import preflight_course_media as media_preflight


def _expected_digests() -> dict[str, str]:
    return {
        role: hashlib.sha256(f"http://media.test/{role}.mp4".encode()).hexdigest()
        for role in media_preflight.MEDIA_ROLES
    }


def _arguments(tmp_path: Path, *, attempts: int = 2) -> argparse.Namespace:
    return argparse.Namespace(
        release_root=tmp_path / "v1.0_260821" / ("a" * 40),
        teacher_video_url="http://media.test/teacher.mp4",
        student_video_url="http://media.test/student.mp4",
        slides_video_url="http://media.test/slides.mp4",
        attempts=attempts,
        request_timeout_seconds=10.0,
        retry_interval_seconds=1.0,
    )


def _probe_document(*, attempts: int, passed: bool) -> dict[str, object]:
    results = []
    for attempt in range(1, attempts + 1):
        items = []
        for role in media_preflight.MEDIA_ROLES:
            item_passed = passed or role != "slides"
            items.append(
                {
                    "role": role,
                    "url_sha256": _expected_digests()[role],
                    "status_code": 206 if item_passed else 404,
                    "declared_length": 1024 if item_passed else 0,
                    "first_chunk_bytes": 1024 if item_passed else 0,
                    "passed": item_passed,
                    "error_type": None if item_passed else "invalid_response",
                }
            )
        results.append({"attempt": attempt, "results": items})
    return {
        "schema_version": 1,
        "probe_location": "orchestrator-service",
        "status": "passed" if passed else "failed",
        "attempts": results,
    }


def test_container_command_runs_all_three_gets_inside_orchestrator(
    tmp_path: Path,
) -> None:
    args = _arguments(tmp_path)

    command = media_preflight.build_container_command()
    request = json.loads(media_preflight.build_container_input(args))

    assert command[:3] == ["docker", "compose", "--project-directory"]
    assert command[command.index("exec") + 1 : command.index("exec") + 3] == [
        "-T",
        "orchestrator-service",
    ]
    assert request["sources"] == [
        {"role": "teacher", "url": args.teacher_video_url},
        {"role": "student", "url": args.student_video_url},
        {"role": "slides", "url": args.slides_video_url},
    ]
    assert request["attempts"] == args.attempts
    assert all(args.teacher_video_url not in item for item in command)
    assert all(args.student_video_url not in item for item in command)
    assert all(args.slides_video_url not in item for item in command)
    assert "json.load(sys.stdin)" in command[-1]
    assert "client.stream" in command[-1]
    assert '"GET"' in command[-1]
    assert "aiter_raw" in command[-1]
    assert "content-length" in command[-1]


def test_probe_document_requires_every_role_in_every_round_to_pass() -> None:
    passed = _probe_document(attempts=3, passed=True)
    assert (
        media_preflight.validate_probe_document(
            passed,
            expected_attempts=3,
            expected_url_digests=_expected_digests(),
        )["status"]
        == "passed"
    )

    failed = _probe_document(attempts=3, passed=False)
    assert (
        media_preflight.validate_probe_document(
            failed,
            expected_attempts=3,
            expected_url_digests=_expected_digests(),
        )["status"]
        == "failed"
    )


def test_probe_document_rejects_inconsistent_aggregate_status() -> None:
    document = _probe_document(attempts=1, passed=False)
    document["status"] = "passed"

    with pytest.raises(RuntimeError, match="aggregate status"):
        media_preflight.validate_probe_document(
            document,
            expected_attempts=1,
            expected_url_digests=_expected_digests(),
        )


def test_probe_document_rejects_digest_not_bound_to_input() -> None:
    document = _probe_document(attempts=2, passed=True)
    document["attempts"][1]["results"][0]["url_sha256"] = "f" * 64

    with pytest.raises(RuntimeError, match="does not match input"):
        media_preflight.validate_probe_document(
            document,
            expected_attempts=2,
            expected_url_digests=_expected_digests(),
        )


@pytest.mark.parametrize(("passed", "returncode"), ((True, 0), (False, 1)))
def test_run_publishes_write_once_evidence_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    passed: bool,
    returncode: int,
) -> None:
    args = _arguments(tmp_path)
    args.release_root.mkdir(parents=True)
    probe = _probe_document(attempts=args.attempts, passed=passed)
    completed = subprocess.CompletedProcess(
        args=["docker"],
        returncode=returncode,
        stdout=json.dumps(probe),
        stderr="",
    )
    monkeypatch.setattr(media_preflight.subprocess, "run", lambda *a, **k: completed)

    status = media_preflight.run(args)

    assert status == returncode
    evidence = json.loads(
        (args.release_root / "preflight/course-media.json").read_text(encoding="utf-8")
    )
    assert evidence["git_sha"] == "a" * 40
    assert evidence["probe_location"] == "orchestrator-service"
    assert evidence["status"] == ("passed" if passed else "failed")
    assert evidence["configured_attempts"] == args.attempts
    assert evidence["failure_type"] == (None if passed else "media_probe_failed")


def test_run_records_exit_status_that_disagrees_with_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _arguments(tmp_path)
    args.release_root.mkdir(parents=True)
    completed = subprocess.CompletedProcess(
        args=["docker"],
        returncode=1,
        stdout=json.dumps(_probe_document(attempts=args.attempts, passed=True)),
        stderr="",
    )
    monkeypatch.setattr(media_preflight.subprocess, "run", lambda *a, **k: completed)

    assert media_preflight.run(args) == 1

    evidence = json.loads(
        (args.release_root / "preflight/course-media.json").read_text(encoding="utf-8")
    )
    assert evidence["status"] == "failed"
    assert evidence["failure_type"] == "probe_exit_status_mismatch"
    assert evidence["attempts"] == _probe_document(
        attempts=args.attempts, passed=True
    )["attempts"]


@pytest.mark.parametrize(
    ("completed", "expected_failure"),
    (
        (
            subprocess.CompletedProcess(
                args=["docker"], returncode=1, stdout="", stderr="container missing"
            ),
            "container_probe_unavailable",
        ),
        (
            subprocess.CompletedProcess(
                args=["docker"], returncode=0, stdout="not-json", stderr=""
            ),
            "invalid_probe_output",
        ),
    ),
)
def test_run_records_failed_evidence_for_unusable_probe_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    completed: subprocess.CompletedProcess[str],
    expected_failure: str,
) -> None:
    args = _arguments(tmp_path)
    args.release_root.mkdir(parents=True)
    monkeypatch.setattr(media_preflight.subprocess, "run", lambda *a, **k: completed)

    assert media_preflight.run(args) == 1

    evidence = json.loads(
        (args.release_root / "preflight/course-media.json").read_text(encoding="utf-8")
    )
    assert evidence["status"] == "failed"
    assert evidence["failure_type"] == expected_failure
    assert evidence["attempts"] == []
    assert "container missing" not in json.dumps(evidence)
    assert args.teacher_video_url not in json.dumps(evidence)


@pytest.mark.parametrize(
    ("error", "expected_failure"),
    (
        (subprocess.TimeoutExpired(cmd=["docker"], timeout=1), "container_probe_timeout"),
        (OSError("docker unavailable"), "container_probe_start_failed"),
    ),
)
def test_run_records_failed_evidence_when_probe_cannot_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    expected_failure: str,
) -> None:
    args = _arguments(tmp_path)
    args.release_root.mkdir(parents=True)

    def fail(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise error

    monkeypatch.setattr(media_preflight.subprocess, "run", fail)

    assert media_preflight.run(args) == 1
    evidence = json.loads(
        (args.release_root / "preflight/course-media.json").read_text(encoding="utf-8")
    )
    assert evidence["failure_type"] == expected_failure
    assert evidence["attempts"] == []


def test_media_json_stdin_resolves_urls_without_cli_arguments(tmp_path: Path) -> None:
    args = media_preflight.parse_args(
        [
            "--release-root",
            str(tmp_path / "v1.0_260821" / ("c" * 40)),
            "--media-json-stdin",
        ]
    )
    media_preflight.resolve_media_urls(
        args,
        StringIO(
            json.dumps(
                {
                    "teacher_video_url": "http://media.test/teacher.mp4",
                    "student_video_url": "http://media.test/student.mp4",
                    "slides_video_url": "http://media.test/slides.mp4",
                }
            )
        ),
    )

    assert args.teacher_video_url == "http://media.test/teacher.mp4"
    assert args.student_video_url == "http://media.test/student.mp4"
    assert args.slides_video_url == "http://media.test/slides.mp4"


def test_cli_rejects_non_http_media_and_non_positive_attempts(tmp_path: Path) -> None:
    release_root = tmp_path / "v1.0_260821" / ("b" * 40)
    common = [
        "--release-root",
        str(release_root),
        "--teacher-video-url",
        "http://media.test/teacher.mp4",
        "--student-video-url",
        "http://media.test/student.mp4",
        "--slides-video-url",
        "http://media.test/slides.mp4",
    ]
    with pytest.raises(SystemExit):
        media_preflight.parse_args([*common, "--attempts", "0"])
    invalid = common.copy()
    invalid[invalid.index("http://media.test/teacher.mp4")] = "/local/teacher.mp4"
    with pytest.raises(SystemExit):
        media_preflight.parse_args(invalid)
