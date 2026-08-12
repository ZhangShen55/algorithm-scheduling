#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

SCRIPT_ROOT = Path(__file__).resolve().parent
PLATFORM_ROOT = SCRIPT_ROOT.parents[1]
COMPOSE_PATH = PLATFORM_ROOT / "deploy" / "docker-compose.operators.yml"
TAG_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40}")
OPERATOR_CONTRACTS = {
    "asr-offline": ("asr_offline", {"asr_offline"}),
    "asr-online": ("asr_online", {"asr_online"}),
    "ocr": ("ocr", {"ocr"}),
    "vbas": ("vbas", {"student_behavior", "teacher_behavior"}),
    "facerec": ("facerec", {"recognize"}),
    "screen-det": ("screen_det", {"detect_all"}),
    "ppt-slice": ("ppt_slice", {"ppt_slice"}),
    "text-analysis": ("text_analysis", {"course_overviews", "extract_keywords"}),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证里程碑 2B 的 24 个算子实例")
    parser.add_argument("--control-url", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--reports-root", type=Path, required=True)
    parser.add_argument("--expected-compose", type=Path, default=COMPOSE_PATH)
    parser.add_argument("--timeout-seconds", type=float, default=180)
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--request-timeout-seconds", type=float, default=5)
    return parser.parse_args()


def safe_component(value: str, pattern: re.Pattern[str], name: str) -> str:
    if value in {".", ".."} or pattern.fullmatch(value) is None:
        raise ValueError(f"{name} 不是安全的单路径段")
    return value


def ensure_private_dir(path: Path) -> Path:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            metadata = os.lstat(current)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(f"报告路径组件不安全: {current}")
    os.chmod(absolute, 0o700)
    return absolute


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_private_dir(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        content = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        descriptor = -1
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def load_expected(path: Path) -> dict[str, dict[str, Any]]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    services = document.get("services")
    if not isinstance(services, dict) or len(services) != 24:
        raise ValueError("Compose 权威清单必须精确包含 24 个实例")
    expected: dict[str, dict[str, Any]] = {}
    for instance_id, service in services.items():
        matches = [prefix for prefix in OPERATOR_CONTRACTS if instance_id.startswith(prefix + "-")]
        if len(matches) != 1:
            raise ValueError(f"无法确定实例的算子合同: {instance_id}")
        prefix = matches[0]
        code, capabilities = OPERATOR_CONTRACTS[prefix]
        environment = service.get("environment", {})
        if environment.get("PLATFORM_INSTANCE_ID") != instance_id:
            raise ValueError(f"Compose 实例 ID 不一致: {instance_id}")
        gpu_index = environment.get("PLATFORM_GPU_ID")
        expected[instance_id] = {
            "operator_code": code,
            "capabilities": capabilities,
            "gpu": str(gpu_index) if gpu_index is not None else None,
        }
    return expected


def get_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise ValueError(f"HTTP {response.status}")
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ConnectionError(f"无法连接 control-service 运维接口: {exc}") from exc


def validate_instances(
    rows: Any, expected: dict[str, dict[str, Any]]
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if not isinstance(rows, list):
        return ["运维列表响应不是数组"], {}
    ids = [row.get("instance_id") for row in rows if isinstance(row, dict)]
    counts = Counter(ids)
    duplicate = sorted(str(value) for value, count in counts.items() if count > 1)
    observed = {str(row.get("instance_id")): row for row in rows if isinstance(row, dict)}
    missing = sorted(set(expected) - set(observed))
    extra = sorted(set(observed) - set(expected))
    issues: list[str] = []
    if missing:
        issues.append("缺失实例: " + ", ".join(missing))
    if extra:
        issues.append("多余实例: " + ", ".join(extra))
    if duplicate:
        issues.append("重复实例: " + ", ".join(duplicate))
    for instance_id in sorted(set(expected) & set(observed)):
        row = observed[instance_id]
        contract = expected[instance_id]
        if row.get("operator_code") != contract["operator_code"]:
            issues.append(f"{instance_id} operator_code 不匹配")
        if set(row.get("capabilities") or []) != contract["capabilities"]:
            issues.append(f"{instance_id} capability 不匹配")
        if row.get("lifecycle") != "ONLINE":
            issues.append(f"{instance_id} 生命周期不是 ONLINE")
        if row.get("model_ready") is not True:
            issues.append(f"{instance_id} model_ready 不是 true")
        capacity = row.get("declared_capacity")
        inflight = row.get("inflight")
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
            issues.append(f"{instance_id} 声明容量必须大于 0")
        if (
            not isinstance(inflight, int)
            or isinstance(inflight, bool)
            or inflight < 0
            or isinstance(capacity, int)
            and inflight > capacity
        ):
            issues.append(f"{instance_id} inflight 与容量状态不合理")
        if not row.get("last_heartbeat_at"):
            issues.append(f"{instance_id} 没有最近心跳时间")
        raw_labels = row.get("labels")
        labels: dict[str, Any] = raw_labels if isinstance(raw_labels, dict) else {}
        if contract["gpu"] is None:
            if labels.get("gpu") not in {None, ""}:
                issues.append(f"{instance_id} CPU 实例不应声明 GPU 标签")
        elif labels.get("gpu") != contract["gpu"]:
            issues.append(f"{instance_id} GPU 标签错误")
    return issues, observed


def heartbeat_issues(
    base_url: str,
    observed: dict[str, dict[str, Any]],
    request_timeout: float,
    deadline: float,
) -> list[str]:
    issues = []
    for instance_id in sorted(observed):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            issues.append("注册验证全局超时，心跳审计未完成")
            break
        quoted = urllib.parse.quote(instance_id, safe="")
        events = get_json(
            f"{base_url}/ops/operator-instances/{quoted}/events?limit=100",
            min(request_timeout, remaining),
        )
        if not isinstance(events, list) or not any(
            isinstance(event, dict)
            and event.get("event_type") == "HEARTBEAT_SUMMARY"
            and (event.get("event_payload") or {}).get("model_ready") is True
            for event in events
        ):
            issues.append(f"{instance_id} 尚无首次心跳就绪审计")
    return issues


def main() -> int:
    args = parse_args()
    started_at = datetime.now(UTC).isoformat()
    output: Path | None = None
    last_issues: list[str] = []
    observed_count = 0
    try:
        tag = safe_component(args.release_tag, TAG_PATTERN, "release tag")
        sha = safe_component(args.git_sha.lower(), SHA_PATTERN, "Git SHA")
        if args.timeout_seconds <= 0 or args.poll_seconds <= 0 or args.request_timeout_seconds <= 0:
            raise ValueError("轮询与请求超时必须大于 0")
        parsed = urllib.parse.urlsplit(args.control_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("control URL 必须是 HTTP(S) URL")
        base_url = args.control_url.rstrip("/")
        expected = load_expected(args.expected_compose)
        output = (
            args.reports_root
            / "milestone-2b"
            / "releases"
            / tag
            / sha
            / "registration"
            / "operator-registration.json"
        )
        deadline = time.monotonic() + args.timeout_seconds
        while True:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("注册验证全局超时")
                rows = get_json(
                    f"{base_url}/ops/operator-instances",
                    min(args.request_timeout_seconds, remaining),
                )
                observed_count = len(rows) if isinstance(rows, list) else 0
                last_issues, observed = validate_instances(rows, expected)
                if last_issues:
                    break
                last_issues.extend(
                    heartbeat_issues(
                        base_url,
                        observed,
                        args.request_timeout_seconds,
                        deadline,
                    )
                )
            except (ConnectionError, ValueError, TimeoutError) as exc:
                if time.monotonic() >= deadline:
                    last_issues = [str(exc), "注册验证全局超时"]
                else:
                    last_issues = [str(exc)]
            if not last_issues or time.monotonic() >= deadline:
                break
            time.sleep(min(args.poll_seconds, max(0, deadline - time.monotonic())))
        status = "通过" if not last_issues else "失败"
        report = {
            "schema_version": 1,
            "status": status,
            "release_tag": tag,
            "git_sha": sha,
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "control_endpoint": (
                f"{parsed.scheme}://{parsed.hostname}:"
                f"{parsed.port or (443 if parsed.scheme == 'https' else 80)}"
            ),
            "summary": {
                "expected": len(expected),
                "observed": observed_count,
                "valid": len(expected) if not last_issues else 0,
            },
            "issues": last_issues,
        }
        atomic_json(output, report)
        if last_issues:
            print("注册验证失败: " + "; ".join(last_issues), file=sys.stderr)
            return 1
        print(str(output))
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI must persist a failure report when possible
        if output is not None:
            atomic_json(
                output,
                {
                    "schema_version": 1,
                    "status": "失败",
                    "release_tag": args.release_tag,
                    "git_sha": args.git_sha,
                    "started_at": started_at,
                    "finished_at": datetime.now(UTC).isoformat(),
                    "summary": {"expected": 24, "observed": observed_count, "valid": 0},
                    "issues": [str(exc)],
                },
            )
        print(f"注册验证失败: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
