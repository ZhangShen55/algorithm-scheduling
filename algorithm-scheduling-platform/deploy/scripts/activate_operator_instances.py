#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from verify_operator_registration import COMPOSE_PATH, load_expected, select_expected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="显式恢复权威 Compose 中所选算子实例的 ONLINE 生命周期",
        allow_abbrev=False,
    )
    parser.add_argument("--control-url", required=True)
    parser.add_argument(
        "--management-token",
        default=os.getenv("OPERATOR_REGISTRY_TOKEN", ""),
    )
    parser.add_argument("--expected-compose", type=Path, default=COMPOSE_PATH)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--profile", action="append", default=[])
    selection.add_argument("--instance", action="append", default=[])
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--request-timeout-seconds", type=float, default=5)
    return parser.parse_args()


def activate_instance(
    *,
    base_url: str,
    management_token: str,
    instance_id: str,
    request_timeout_seconds: float,
) -> None:
    body = json.dumps(
        {"instance_id": instance_id, "lifecycle": "ONLINE"},
        separators=(",", ":"),
    ).encode()
    request = urllib.request.Request(
        f"{base_url}/api/operator-instances/lifecycle",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Operator-Registry-Token": management_token,
        },
    )
    with urllib.request.urlopen(request, timeout=request_timeout_seconds) as response:
        payload: Any = json.load(response)
    if (
        response.status != 200
        or not isinstance(payload, dict)
        or payload.get("instance_id") != instance_id
        or payload.get("lifecycle") != "ONLINE"
    ):
        raise ValueError(f"{instance_id} 生命周期恢复响应不符合合同")


def main() -> int:
    args = parse_args()
    try:
        if not args.management_token.strip():
            raise ValueError("算子注册管理令牌不能为空")
        if (
            args.timeout_seconds <= 0
            or args.poll_seconds <= 0
            or args.request_timeout_seconds <= 0
        ):
            raise ValueError("轮询与请求超时必须大于 0")
        parsed = urllib.parse.urlsplit(args.control_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("control URL 必须是 HTTP(S) URL")
        base_url = args.control_url.rstrip("/")
        authoritative = load_expected(args.expected_compose)
        selected, _, _ = select_expected(
            authoritative,
            profiles=args.profile,
            instances=args.instance,
        )
        pending = set(selected)
        deadline = time.monotonic() + args.timeout_seconds
        last_errors: dict[str, str] = {}
        while pending and time.monotonic() < deadline:
            for instance_id in sorted(pending):
                try:
                    activate_instance(
                        base_url=base_url,
                        management_token=args.management_token,
                        instance_id=instance_id,
                        request_timeout_seconds=min(
                            args.request_timeout_seconds,
                            max(0.1, deadline - time.monotonic()),
                        ),
                    )
                except urllib.error.HTTPError as exc:
                    if exc.code in {401, 403}:
                        raise ValueError(
                            f"{instance_id} 生命周期恢复鉴权失败: HTTP {exc.code}"
                        ) from exc
                    last_errors[instance_id] = f"HTTP {exc.code}"
                except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                    last_errors[instance_id] = str(exc)
                else:
                    pending.remove(instance_id)
                    last_errors.pop(instance_id, None)
                    print(f"operator lifecycle: ONLINE: {instance_id}")
            if pending:
                time.sleep(min(args.poll_seconds, max(0, deadline - time.monotonic())))
        if pending:
            details = "; ".join(
                f"{instance_id}: {last_errors.get(instance_id, '尚未注册')}"
                for instance_id in sorted(pending)
            )
            raise TimeoutError(f"算子生命周期恢复超时: {details}")
        return 0
    except (ValueError, TimeoutError, OSError) as exc:
        print(f"operator lifecycle: FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
