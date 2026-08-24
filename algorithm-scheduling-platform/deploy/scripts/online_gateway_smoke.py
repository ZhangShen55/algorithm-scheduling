from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

BODY_MAX_BYTES = 75_497_472
DECODED_MAX_BYTES = 52_428_800
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
TAG_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


class SmokeError(RuntimeError):
    pass


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as error:
        status = error.code
        raw = error.read()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SmokeError("Online Gateway returned invalid JSON") from error
    if not isinstance(parsed, dict):
        raise SmokeError("Online Gateway response must be an object")
    return status, parsed


def _post_declared_oversize(
    url: str, declared_size: int, timeout: float
) -> tuple[int, dict[str, Any]]:
    """只发送超限声明头，验证网关在读取大请求体前拒绝。"""

    parsed = urlsplit(url)
    hostname = parsed.hostname
    if hostname is None:
        raise SmokeError("gateway URL is missing a hostname")
    connection_type = (
        http.client.HTTPSConnection
        if parsed.scheme == "https"
        else http.client.HTTPConnection
    )
    connection = connection_type(hostname, parsed.port, timeout=timeout)
    try:
        path = parsed.path or "/"
        connection.putrequest("POST", path)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(declared_size))
        connection.endheaders()
        response = connection.getresponse()
        status = response.status
        raw = response.read()
    finally:
        connection.close()
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SmokeError("Online Gateway returned invalid JSON") from error
    if not isinstance(body, dict):
        raise SmokeError("Online Gateway response must be an object")
    return status, body


def _validate_gateway_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SmokeError("gateway URL must be an HTTP(S) origin without credentials")
    return value.rstrip("/") + "/api/online/ocr/recognize"


def _expect_business_code(
    case_id: str,
    url: str,
    payload: dict[str, Any],
    expected_code: int,
    timeout: float,
) -> dict[str, Any]:
    http_status, response = _post_json(url, payload, timeout)
    actual_code = response.get("code")
    if http_status != 200 or actual_code != expected_code:
        raise SmokeError(
            f"{case_id} expected HTTP 200/code {expected_code}, "
            f"got HTTP {http_status}/code {actual_code!r}"
        )
    if expected_code == 0:
        data = response.get("data")
        if not isinstance(data, dict) or data.get("err_no") != 0:
            raise SmokeError(f"{case_id} OCR response data is invalid")
    return {"case_id": case_id, "http_status": http_status, "business_code": actual_code}


def _expect_declared_body_limit(url: str, timeout: float) -> dict[str, Any]:
    case_id = "ONLINE-OCR-003"
    http_status, response = _post_declared_oversize(url, BODY_MAX_BYTES + 1, timeout)
    actual_code = response.get("code")
    if http_status != 200 or actual_code != 40001:
        raise SmokeError(
            f"{case_id} expected HTTP 200/code 40001, "
            f"got HTTP {http_status}/code {actual_code!r}"
        )
    return {"case_id": case_id, "http_status": http_status, "business_code": actual_code}


def run_smoke(image_bytes: bytes, gateway_url: str, timeout: float) -> list[dict[str, Any]]:
    if not image_bytes or len(image_bytes) > 5 * 1024 * 1024:
        raise SmokeError("real OCR smoke image must be between 1 byte and 5 MiB")
    endpoint = _validate_gateway_url(gateway_url)
    results = [
        _expect_business_code(
            "ONLINE-OCR-001",
            endpoint,
            {
                "image_id": "online-ocr-smoke",
                "image": base64.b64encode(image_bytes).decode(),
            },
            0,
            timeout,
        )
    ]
    decoded_oversize = base64.b64encode(b"\0" * (DECODED_MAX_BYTES + 1)).decode()
    results.append(
        _expect_business_code(
            "ONLINE-OCR-002",
            endpoint,
            {"image_id": "decoded-limit", "image": decoded_oversize},
            40001,
            timeout,
        )
    )
    results.append(_expect_declared_body_limit(endpoint, timeout))
    return results


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise SmokeError(f"smoke evidence already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
        view = memoryview(data)
        while view:
            view = view[os.write(descriptor, view) :]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
    except BaseException:
        temporary.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 Online Gateway 单图 OCR 与大小边界 Smoke")
    parser.add_argument("--gateway-url", default="http://127.0.0.1:18103")
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--reports-root", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    output: Path | None = None
    try:
        if TAG_PATTERN.fullmatch(arguments.release_tag) is None:
            raise SmokeError("release tag is invalid")
        sha = arguments.git_sha.lower()
        if SHA_PATTERN.fullmatch(sha) is None:
            raise SmokeError("Git SHA must be a full lowercase revision")
        if not 0 < arguments.timeout_seconds <= 600:
            raise SmokeError("timeout must be between 0 and 600 seconds")
        if arguments.image.is_symlink() or not arguments.image.is_file():
            raise SmokeError("real OCR smoke image is missing or unsafe")
        output = (
            arguments.reports_root
            / "milestone-2b"
            / "releases"
            / arguments.release_tag
            / sha
            / "online"
            / "online-ocr.json"
        )
        results = run_smoke(
            arguments.image.read_bytes(),
            arguments.gateway_url,
            arguments.timeout_seconds,
        )
        _atomic_json(
            output,
            {
                "schema_version": 1,
                "evidence_type": "online_gateway_ocr_smoke",
                "status": "PASS",
                "mock": False,
                "release_tag": arguments.release_tag,
                "git_sha": sha,
                "created_at": datetime.now(UTC).isoformat(),
                "limits": {
                    "body_max_bytes": BODY_MAX_BYTES,
                    "base64_max_decoded_bytes": DECODED_MAX_BYTES,
                },
                "cases": results,
            },
        )
    except (OSError, SmokeError) as error:
        print(f"online gateway smoke failed: {error}", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
