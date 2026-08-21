#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit

from scripts.aggregate_milestone_2b_cases import publish_json_once
from scripts.milestone_2b_case_runners.evidence import release_identity

PLATFORM_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = PLATFORM_ROOT / "deploy/docker-compose.platform.yml"
SERVICE_NAME = "orchestrator-service"
MEDIA_ROLES = ("teacher", "student", "slides")

CONTAINER_PROBE = r"""
import asyncio
import hashlib
import json
import sys

import httpx

request = json.load(sys.stdin)
sources = request["sources"]
attempt_count = request["attempts"]
request_timeout = request["request_timeout_seconds"]
retry_interval = request["retry_interval_seconds"]


async def probe_one(client, role, url):
    result = {
        "role": role,
        "url_sha256": hashlib.sha256(url.encode("utf-8")).hexdigest(),
        "status_code": None,
        "declared_length": None,
        "first_chunk_bytes": 0,
        "passed": False,
        "error_type": None,
    }
    try:
        async with client.stream(
            "GET",
            url,
            headers={
                "Accept-Encoding": "identity",
                "Range": "bytes=0-1048575",
                "User-Agent": "algorithm-scheduling-course-media-preflight/1",
            },
        ) as response:
            result["status_code"] = response.status_code
            raw_length = response.headers.get("content-length")
            try:
                declared_length = int(raw_length) if raw_length is not None else None
            except ValueError:
                declared_length = None
            result["declared_length"] = declared_length
            iterator = response.aiter_raw()
            try:
                first_chunk = await anext(iterator)
            except StopAsyncIteration:
                first_chunk = b""
            result["first_chunk_bytes"] = len(first_chunk)
            result["passed"] = (
                response.status_code in {200, 206}
                and declared_length is not None
                and declared_length > 0
                and len(first_chunk) > 0
            )
            if not result["passed"]:
                result["error_type"] = "invalid_response"
    except Exception as exc:
        result["error_type"] = type(exc).__name__
    return result


async def main():
    timeout = httpx.Timeout(request_timeout)
    attempts = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        for attempt in range(1, attempt_count + 1):
            results = await asyncio.gather(
                *(probe_one(client, item["role"], item["url"]) for item in sources)
            )
            attempts.append({"attempt": attempt, "results": results})
            if attempt < attempt_count:
                await asyncio.sleep(retry_interval)
    passed = all(
        result["passed"]
        for attempt in attempts
        for result in attempt["results"]
    )
    print(
        json.dumps(
            {
                "schema_version": 1,
                "probe_location": "orchestrator-service",
                "status": "passed" if passed else "failed",
                "attempts": attempts,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )
    return 0 if passed else 1


raise SystemExit(asyncio.run(main()))
"""


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return parsed


def _http_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise argparse.ArgumentTypeError("must be an absolute HTTP(S) URL")
    return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--teacher-video-url", type=_http_url)
    parser.add_argument("--student-video-url", type=_http_url)
    parser.add_argument("--slides-video-url", type=_http_url)
    parser.add_argument("--media-json-stdin", action="store_true")
    parser.add_argument("--attempts", type=_positive_int, default=3)
    parser.add_argument("--request-timeout-seconds", type=_positive_float, default=30.0)
    parser.add_argument("--retry-interval-seconds", type=_positive_float, default=2.0)
    args = parser.parse_args(argv)
    args.release_root = args.release_root.expanduser().resolve()
    return args


def _parse_media_stdin(stream: TextIO) -> dict[str, str]:
    try:
        value = json.load(stream)
    except (json.JSONDecodeError, OSError) as exc:
        raise argparse.ArgumentTypeError("stdin must contain one valid JSON object") from exc
    if type(value) is not dict or set(value) != {
        "teacher_video_url",
        "student_video_url",
        "slides_video_url",
    }:
        raise argparse.ArgumentTypeError(
            "stdin JSON must contain exactly teacher/student/slides video URLs"
        )
    parsed: dict[str, str] = {}
    for field in ("teacher_video_url", "student_video_url", "slides_video_url"):
        raw = value[field]
        if type(raw) is not str:
            raise argparse.ArgumentTypeError(f"{field} must be a string")
        parsed[field] = _http_url(raw)
    return parsed


def resolve_media_urls(args: argparse.Namespace, stream: TextIO) -> None:
    direct = {
        "teacher_video_url": args.teacher_video_url,
        "student_video_url": args.student_video_url,
        "slides_video_url": args.slides_video_url,
    }
    if args.media_json_stdin:
        if any(value is not None for value in direct.values()):
            raise argparse.ArgumentTypeError(
                "--media-json-stdin cannot be combined with URL arguments"
            )
        direct = _parse_media_stdin(stream)
    elif any(value is None for value in direct.values()):
        raise argparse.ArgumentTypeError(
            "all three URL arguments are required without --media-json-stdin"
        )
    for field, value in direct.items():
        setattr(args, field, value)


def build_container_command() -> list[str]:
    return [
        "docker",
        "compose",
        "--project-directory",
        str(COMPOSE_FILE.parent),
        "-f",
        str(COMPOSE_FILE),
        "exec",
        "-T",
        SERVICE_NAME,
        "python",
        "-c",
        CONTAINER_PROBE,
    ]


def build_container_input(args: argparse.Namespace) -> str:
    return json.dumps(
        {
            "sources": [
                {"role": "teacher", "url": args.teacher_video_url},
                {"role": "student", "url": args.student_video_url},
                {"role": "slides", "url": args.slides_video_url},
            ],
            "attempts": args.attempts,
            "request_timeout_seconds": args.request_timeout_seconds,
            "retry_interval_seconds": args.retry_interval_seconds,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _require_int(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise RuntimeError(f"invalid container probe field: {label}")
    return value


def validate_probe_document(
    value: object,
    *,
    expected_attempts: int,
    expected_url_digests: dict[str, str],
) -> dict[str, Any]:
    if type(value) is not dict:
        raise RuntimeError("container probe did not return an object")
    document = dict(value)
    if set(document) != {"schema_version", "probe_location", "status", "attempts"}:
        raise RuntimeError("container probe returned unexpected fields")
    if document["schema_version"] != 1 or document["probe_location"] != SERVICE_NAME:
        raise RuntimeError("container probe identity is invalid")
    if document["status"] not in {"passed", "failed"}:
        raise RuntimeError("container probe status is invalid")
    attempts = document["attempts"]
    if type(attempts) is not list or len(attempts) != expected_attempts:
        raise RuntimeError("container probe attempt count is invalid")

    all_passed = True
    for expected_number, attempt in enumerate(attempts, start=1):
        if type(attempt) is not dict or set(attempt) != {"attempt", "results"}:
            raise RuntimeError("container probe attempt shape is invalid")
        if attempt["attempt"] != expected_number:
            raise RuntimeError("container probe attempt order is invalid")
        results = attempt["results"]
        if type(results) is not list or len(results) != len(MEDIA_ROLES):
            raise RuntimeError("container probe result count is invalid")
        if [item.get("role") for item in results if type(item) is dict] != list(MEDIA_ROLES):
            raise RuntimeError("container probe media roles are invalid")
        for result in results:
            if type(result) is not dict or set(result) != {
                "role",
                "url_sha256",
                "status_code",
                "declared_length",
                "first_chunk_bytes",
                "passed",
                "error_type",
            }:
                raise RuntimeError("container probe result shape is invalid")
            digest = result["url_sha256"]
            if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise RuntimeError("container probe URL digest is invalid")
            if digest != expected_url_digests[result["role"]]:
                raise RuntimeError("container probe URL digest does not match input")
            if result["status_code"] is not None:
                status_code = _require_int(result["status_code"], "status_code", minimum=100)
                if status_code > 599:
                    raise RuntimeError("invalid container probe field: status_code")
            if result["declared_length"] is not None:
                _require_int(result["declared_length"], "declared_length", minimum=0)
            _require_int(result["first_chunk_bytes"], "first_chunk_bytes", minimum=0)
            if type(result["passed"]) is not bool:
                raise RuntimeError("container probe passed flag is invalid")
            if result["error_type"] is not None and type(result["error_type"]) is not str:
                raise RuntimeError("container probe error type is invalid")
            result_passed = (
                result["status_code"] in {200, 206}
                and type(result["declared_length"]) is int
                and result["declared_length"] > 0
                and result["first_chunk_bytes"] > 0
                and result["error_type"] is None
            )
            if result["passed"] is not result_passed:
                raise RuntimeError("container probe result status is inconsistent")
            all_passed = all_passed and result["passed"]
    expected_status = "passed" if all_passed else "failed"
    if document["status"] != expected_status:
        raise RuntimeError("container probe aggregate status is inconsistent")
    return document


def _parse_probe_stdout(
    stdout: str,
    *,
    expected_attempts: int,
    expected_url_digests: dict[str, str],
) -> dict[str, Any]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RuntimeError("container probe must emit exactly one JSON line")
    try:
        value = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise RuntimeError("container probe emitted invalid JSON") from exc
    return validate_probe_document(
        value,
        expected_attempts=expected_attempts,
        expected_url_digests=expected_url_digests,
    )


def run(args: argparse.Namespace) -> int:
    release_tag, git_sha = release_identity(args.release_root)
    expected_url_digests = {
        role: hashlib.sha256(url.encode("utf-8")).hexdigest()
        for role, url in (
            ("teacher", args.teacher_video_url),
            ("student", args.student_video_url),
            ("slides", args.slides_video_url),
        )
    }
    timeout_seconds = (
        args.attempts * (args.request_timeout_seconds + args.retry_interval_seconds) + 60.0
    )
    probe: dict[str, Any] | None = None
    failure_type: str | None = None
    try:
        completed = subprocess.run(
            build_container_command(),
            cwd=PLATFORM_ROOT,
            input=build_container_input(args),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        failure_type = "container_probe_timeout"
    except OSError:
        failure_type = "container_probe_start_failed"
    else:
        try:
            probe = _parse_probe_stdout(
                completed.stdout,
                expected_attempts=args.attempts,
                expected_url_digests=expected_url_digests,
            )
        except RuntimeError:
            failure_type = (
                "container_probe_unavailable"
                if completed.returncode != 0 and not completed.stdout.strip()
                else "invalid_probe_output"
            )
        else:
            returned_success = completed.returncode == 0
            reported_success = probe["status"] == "passed"
            if returned_success != reported_success:
                failure_type = "probe_exit_status_mismatch"
            elif not returned_success:
                failure_type = "media_probe_failed"
    passed = failure_type is None and probe is not None and probe["status"] == "passed"
    document = {
        "schema_version": 1,
        "evidence_type": "course_media_preflight",
        "release_tag": release_tag,
        "git_sha": git_sha,
        "recorded_at": datetime.now(UTC).isoformat(),
        "probe_location": SERVICE_NAME,
        "configured_attempts": args.attempts,
        "status": "passed" if passed else "failed",
        "attempts": [] if probe is None else probe["attempts"],
        "failure_type": failure_type,
    }
    publish_json_once(
        release_root=args.release_root,
        relative_path=Path("preflight/course-media.json"),
        document=document,
    )
    if not passed:
        print(
            f"preflight-course-media: FAIL: {failure_type or 'unknown_probe_failure'}",
            flush=True,
        )
        return 1
    print(
        f"preflight-course-media: OK: {args.attempts} rounds from {SERVICE_NAME}",
        flush=True,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        resolve_media_urls(args, sys.stdin)
    except argparse.ArgumentTypeError as exc:
        raise SystemExit(f"preflight-course-media: {exc}") from exc
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
