#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml  # type: ignore[import-untyped]

SCRIPT_ROOT = Path(__file__).resolve().parent
if TYPE_CHECKING:
    from deploy.scripts.deployment_contracts import validate_operator_toml_contract
else:
    if str(SCRIPT_ROOT) not in sys.path:
        sys.path.insert(0, str(SCRIPT_ROOT))
    from deployment_contracts import validate_operator_toml_contract
    from operator_topology import CURRENT_TOPOLOGY

PLATFORM_ROOT = SCRIPT_ROOT.parents[1]
COMPOSE_PATH = PLATFORM_ROOT / "deploy" / "docker-compose.operators.yml"
TAG_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40}")
if TYPE_CHECKING:
    from deploy.scripts.operator_topology import CURRENT_TOPOLOGY

OPERATOR_CONTRACTS = {
    entry.service_prefix: (entry.operator_code, set(entry.capabilities))
    for entry in CURRENT_TOPOLOGY.operators
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="验证里程碑 2B 的 21 个算子实例",
        allow_abbrev=False,
    )
    parser.add_argument("--control-url", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--reports-root", type=Path, required=True)
    parser.add_argument("--expected-compose", type=Path, default=COMPOSE_PATH)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--profile", action="append", default=[])
    selection.add_argument("--instance", action="append", default=[])
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
    content = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor: int | None = None
    published = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        try:
            os.link(temporary, path, follow_symlinks=False)
            published = True
        except FileExistsError:
            try:
                existing_descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            except OSError as error:
                raise ValueError(f"注册报告已存在且不可安全读取: {path}") from error
            try:
                if not stat.S_ISREG(os.fstat(existing_descriptor).st_mode):
                    raise ValueError(f"注册报告已存在且不是普通文件: {path}")
                chunks: list[bytes] = []
                while chunk := os.read(existing_descriptor, 65536):
                    chunks.append(chunk)
            finally:
                os.close(existing_descriptor)
            if b"".join(chunks) != content:
                raise ValueError(
                    f"注册报告已存在不同内容，write-once 拒绝覆盖: {path}"
                ) from None
            return
        directory_fd = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW,
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        if published:
            directory_fd = os.open(
                path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW,
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)


def load_expected(path: Path) -> dict[str, dict[str, Any]]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    services = document.get("services")
    if not isinstance(services, dict) or len(services) != CURRENT_TOPOLOGY.totals["instances"]:
        raise ValueError("Compose 权威清单必须精确包含 21 个实例")
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
        service_url = environment.get("PLATFORM_SERVICE_URL")
        if (
            not isinstance(service_url, str)
            or not service_url
            or any(character.isspace() for character in service_url)
        ):
            raise ValueError(f"Compose service URL 无效: {instance_id}")
        try:
            parsed_service_url = urllib.parse.urlsplit(service_url)
            service_port = parsed_service_url.port
        except ValueError as exc:
            raise ValueError(f"Compose service URL 无效: {instance_id}") from exc
        if (
            parsed_service_url.scheme not in {"http", "https"}
            or not parsed_service_url.hostname
            or service_port is None
            or service_port <= 0
        ):
            raise ValueError(f"Compose service URL 无效: {instance_id}")
        volumes = service.get("volumes")
        if not isinstance(volumes, list):
            raise ValueError(f"Compose 配置挂载无效: {instance_id}")
        config_path = environment.get("CONFIG_PATH")
        config_mount = next(
            (
                mount
                for mount in volumes
                if isinstance(mount, dict) and mount.get("target") == config_path
            ),
            None,
        )
        if config_mount is None:
            raise ValueError(f"Compose 配置挂载无效: {instance_id}")
        config_source = Path(str(config_mount.get("source", "")))
        if not config_source.is_absolute():
            config_source = path.resolve().parent / config_source
        config_source = config_source.resolve()
        validate_operator_toml_contract(instance_id, config_source)
        config = tomllib.loads(config_source.read_text(encoding="utf-8"))
        declared_capacity = config["platform"]["max_concurrent_requests"]
        gpu_index = environment.get("PLATFORM_GPU_ID")
        profiles = service.get("profiles")
        if (
            not isinstance(profiles, list)
            or not profiles
            or any(not isinstance(profile, str) or not profile for profile in profiles)
        ):
            raise ValueError(f"Compose profile 无效: {instance_id}")
        expected[instance_id] = {
            "operator_code": code,
            "capabilities": capabilities,
            "service_url": service_url,
            "declared_capacity": declared_capacity,
            "gpu": str(gpu_index) if gpu_index is not None else None,
            "profiles": set(profiles),
        }
    return expected


def select_expected(
    expected: dict[str, dict[str, Any]],
    *,
    profiles: list[str],
    instances: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], str]:
    if profiles:
        values = sorted({safe_component(value, TAG_PATTERN, "profile") for value in profiles})
        known = {profile for contract in expected.values() for profile in contract["profiles"]}
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(f"未知 Compose profile: {unknown}")
        selected = {
            instance_id: contract
            for instance_id, contract in expected.items()
            if contract["profiles"] & set(values)
        }
        suffix = "profiles-" + "-".join(values) if len(values) > 1 else f"profile-{values[0]}"
        return selected, {"mode": "profile", "values": values}, suffix
    if instances:
        values = [safe_component(value, TAG_PATTERN, "instance ID") for value in instances]
        if len(values) != len(set(values)):
            raise ValueError("--instance 不能重复")
        unknown = sorted(set(values) - set(expected))
        if unknown:
            raise ValueError(f"实例不在 Compose 权威清单: {unknown}")
        selected = {instance_id: expected[instance_id] for instance_id in sorted(values)}
        suffix = (
            f"instance-{values[0]}"
            if len(values) == 1
            else "instances-" + hashlib.sha256("\n".join(sorted(values)).encode()).hexdigest()[:12]
        )
        return selected, {"mode": "instance", "values": sorted(values)}, suffix
    return expected, {"mode": "full", "values": []}, "full"


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
    rows: Any,
    expected: dict[str, dict[str, Any]],
    *,
    strict_observed: bool = True,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if not isinstance(rows, list):
        return ["运维列表响应不是数组"], {}
    ids = [
        row.get("instance_id")
        for row in rows
        if isinstance(row, dict) and row.get("instance_id") in expected
    ]
    counts = Counter(ids)
    duplicate = sorted(str(value) for value, count in counts.items() if count > 1)
    observed = {
        str(row.get("instance_id")): row
        for row in rows
        if isinstance(row, dict) and row.get("instance_id") in expected
    }
    missing = sorted(set(expected) - set(observed))
    all_observed = {
        str(row.get("instance_id")) for row in rows if isinstance(row, dict)
    }
    extra = sorted(all_observed - set(expected)) if strict_observed else []
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
        if row.get("service_url") != contract["service_url"]:
            issues.append(f"{instance_id} service_url 不匹配")
        if row.get("lifecycle") != "ONLINE":
            issues.append(f"{instance_id} 生命周期不是 ONLINE")
        if row.get("model_ready") is not True:
            issues.append(f"{instance_id} model_ready 不是 true")
        capacity = row.get("declared_capacity")
        inflight = row.get("inflight")
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
            issues.append(f"{instance_id} 声明容量必须大于 0")
        elif capacity != contract["declared_capacity"]:
            issues.append(f"{instance_id} 声明容量不匹配")
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


def registration_evidence_instance(row: dict[str, Any]) -> dict[str, Any]:
    raw_labels = row.get("labels")
    labels = raw_labels if isinstance(raw_labels, dict) else {}
    return {
        "instance_id": row.get("instance_id"),
        "operator_code": row.get("operator_code"),
        "capabilities": sorted(str(item) for item in (row.get("capabilities") or [])),
        "service_url": row.get("service_url"),
        "declared_capacity": row.get("declared_capacity"),
        "labels": {"gpu": labels.get("gpu")},
        "lifecycle": row.get("lifecycle"),
        "inflight": row.get("inflight"),
        "model_ready": row.get("model_ready"),
        "last_heartbeat_at": row.get("last_heartbeat_at"),
    }


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
    base_report: dict[str, Any] | None = None
    last_issues: list[str] = []
    last_specific_issues: list[str] = []
    observed_count = 0
    expected_count = CURRENT_TOPOLOGY.totals["instances"]
    observed: dict[str, dict[str, Any]] = {}
    selection = {"mode": "full", "values": []}
    try:
        tag = safe_component(args.release_tag, TAG_PATTERN, "release tag")
        sha = safe_component(args.git_sha.lower(), SHA_PATTERN, "Git SHA")
        if args.timeout_seconds <= 0 or args.poll_seconds <= 0 or args.request_timeout_seconds <= 0:
            raise ValueError("轮询与请求超时必须大于 0")
        parsed = urllib.parse.urlsplit(args.control_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("control URL 必须是 HTTP(S) URL")
        base_url = args.control_url.rstrip("/")
        authoritative = load_expected(args.expected_compose)
        expected, selection, output_suffix = select_expected(
            authoritative,
            profiles=args.profile,
            instances=args.instance,
        )
        expected_count = len(expected)
        output = (
            args.reports_root
            / "milestone-2b"
            / "releases"
            / tag
            / sha
            / "registration"
            / (
                "operator-registration.json"
                if selection["mode"] == "full"
                else f"operator-registration-{output_suffix}.json"
            )
        )
        base_report = {
            "schema_version": 1,
            "evidence_type": "operator_registration",
            "mock": False,
            "target": "operator-registry",
            "release_tag": tag,
            "git_sha": sha,
            "started_at": started_at,
        }
        deadline = time.monotonic() + args.timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_issues = list(last_specific_issues or last_issues)
                if "注册验证全局超时" not in last_issues:
                    last_issues.append("注册验证全局超时")
                break
            try:
                rows = get_json(
                    f"{base_url}/ops/operator-instances",
                    min(args.request_timeout_seconds, remaining),
                )
                observed_count = (
                    sum(
                        1
                        for row in rows
                        if isinstance(row, dict) and row.get("instance_id") in expected
                    )
                    if isinstance(rows, list)
                    else 0
                )
                last_issues, observed = validate_instances(
                    rows,
                    expected,
                    strict_observed=selection["mode"] == "full",
                )
                if isinstance(rows, list):
                    rogue = sorted(
                        {
                            str(row.get("instance_id"))
                            for row in rows
                            if isinstance(row, dict)
                            and row.get("instance_id") not in authoritative
                        }
                    )
                    if rogue:
                        last_issues.append("实例不在 Compose 权威清单: " + ", ".join(rogue))
                if not last_issues:
                    last_issues.extend(
                        heartbeat_issues(
                            base_url,
                            observed,
                            args.request_timeout_seconds,
                            deadline,
                        )
                    )
                if last_issues and not any("全局超时" in issue for issue in last_issues):
                    last_specific_issues = list(last_issues)
            except (ConnectionError, ValueError, TimeoutError) as exc:
                last_issues = [str(exc)]
                last_specific_issues = list(last_issues)
            if not last_issues:
                break
            time.sleep(min(args.poll_seconds, max(0, deadline - time.monotonic())))
        status = "通过" if not last_issues else "失败"
        validated_instances = (
            [
                registration_evidence_instance(observed[instance_id])
                for instance_id in sorted(expected)
            ]
            if not last_issues
            else []
        )
        report = {
            **base_report,
            "status": status,
            "finished_at": datetime.now(UTC).isoformat(),
            "control_endpoint": (
                f"{parsed.scheme}://{parsed.hostname}:"
                f"{parsed.port or (443 if parsed.scheme == 'https' else 80)}"
            ),
            "selection": selection,
            "summary": {
                "expected": len(expected),
                "observed": observed_count,
                "valid": len(expected) if not last_issues else 0,
            },
            "validated_instances": validated_instances,
            "issues": last_issues,
        }
        atomic_json(output, report)
        if last_issues:
            print("注册验证失败: " + "; ".join(last_issues), file=sys.stderr)
            return 1
        print(str(output))
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI must persist a failure report when possible
        if output is not None and base_report is not None:
            atomic_json(
                output,
                {
                    **base_report,
                    "status": "失败",
                    "finished_at": datetime.now(UTC).isoformat(),
                    "selection": selection,
                    "summary": {
                        "expected": expected_count,
                        "observed": observed_count,
                        "valid": 0,
                    },
                    "validated_instances": [],
                    "issues": [str(exc)],
                },
            )
        print(f"注册验证失败: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
