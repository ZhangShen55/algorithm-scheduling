#!/usr/bin/env python3
"""在线人数识别压力测试。

示例：
    python tests/online_person_count_load.py --concurrency 100 --requests 5000
    python tests/online_person_count_load.py \
        --url http://192.168.29.11:18103 --concurrency 256 --requests 10240

脚本只输出请求状态、业务码和耗时统计，不保存 VBas 返回正文或图片 Base64。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = ROOT / "vbas" / "tests" / "teacher_person_count" / "frame_000068.jpg"
DEFAULT_URL = os.getenv("ONLINE_GATEWAY_URL", "http://127.0.0.1:18103")


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是正数")
    return parsed


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile)))
    return ordered[index]


def _load_image(path: Path) -> str:
    try:
        return base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError as exc:
        raise ValueError(f"无法读取图片: {path}") from exc


def _build_payload(image: str, request_id: str, width: int, height: int) -> dict[str, Any]:
    return {
        "TaskID": request_id,
        "TaskType": 4,
        "ImageList": [{"ImageID": request_id, "Data": image}],
        "AnalysisRule": {
            "AlgParams": {
                "ImageFormat": 0,
                "ImageResolution": {"ImageWidth": width, "ImageHeight": height},
                "PolygonList": [],
            }
        },
        "RunAlways": False,
    }


def _business_code(body: object) -> str:
    if not isinstance(body, dict):
        return "non_object"
    code = body.get("code")
    if isinstance(code, (int, str)):
        return str(code)
    response = body.get("Response")
    if isinstance(response, dict):
        err_code = response.get("ErrCode")
        if isinstance(err_code, (int, str)):
            return str(err_code)
    return "none"


def _is_success(http_status: int, body: object) -> bool:
    if not 200 <= http_status < 300 or not isinstance(body, dict):
        return False
    if body.get("code") not in (None, 0, "0"):
        return False
    response = body.get("Response")
    return not isinstance(response, dict) or response.get("ErrCode") in (None, 0, "0")


async def run_load(
    *,
    url: str,
    image: str,
    concurrency: int,
    total_requests: int,
    timeout_seconds: float,
    task_prefix: str,
    image_width: int,
    image_height: int,
) -> dict[str, Any]:
    endpoint = url.rstrip("/") + "/online/vbas/person-count"
    latencies: list[float] = []
    http_statuses: Counter[str] = Counter()
    business_codes: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    completed = 0
    started_at = time.perf_counter()
    lock = asyncio.Lock()
    limits = httpx.Limits(
        max_connections=max(concurrency, 10),
        max_keepalive_connections=max(concurrency, 10),
    )
    timeout = httpx.Timeout(
        connect=min(timeout_seconds, 30.0),
        read=timeout_seconds,
        write=timeout_seconds,
        pool=timeout_seconds,
    )

    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:

        async def worker(worker_index: int) -> None:
            nonlocal completed
            for request_index in range(worker_index, total_requests, concurrency):
                request_id = f"{task_prefix}-{request_index + 1:06d}"
                payload = _build_payload(image, request_id, image_width, image_height)
                request_started = time.perf_counter()
                try:
                    response = await client.post(endpoint, json=payload)
                    elapsed = time.perf_counter() - request_started
                    try:
                        body: object = response.json()
                    except ValueError:
                        body = None
                    async with lock:
                        latencies.append(elapsed)
                        http_statuses[str(response.status_code)] += 1
                        business_codes[_business_code(body)] += 1
                        if not _is_success(response.status_code, body):
                            errors["business_or_http_failure"] += 1
                except httpx.TimeoutException as exc:
                    async with lock:
                        errors[f"timeout:{type(exc).__name__}"] += 1
                except httpx.HTTPError as exc:
                    async with lock:
                        errors[f"transport:{type(exc).__name__}"] += 1
                finally:
                    async with lock:
                        completed += 1

        await asyncio.gather(*(worker(index) for index in range(concurrency)))

    elapsed_total = time.perf_counter() - started_at
    p50 = _percentile(latencies, 0.50)
    p95 = _percentile(latencies, 0.95)
    p99 = _percentile(latencies, 0.99)
    successes = sum(
        count
        for code, count in business_codes.items()
        if code in {"0", "none"}
    ) - errors.get("business_or_http_failure", 0)
    successes = max(0, min(total_requests - sum(errors.values()), successes))
    return {
        "url": endpoint,
        "concurrency": concurrency,
        "requests": total_requests,
        "completed": completed,
        "success": successes,
        "failure": total_requests - successes,
        "elapsed_seconds": round(elapsed_total, 4),
        "throughput_requests_per_second": round(total_requests / elapsed_total, 4)
        if elapsed_total
        else None,
        "latency_seconds": {
            "min": round(min(latencies), 4) if latencies else None,
            "mean": round(statistics.fmean(latencies), 4) if latencies else None,
            "p50": round(p50, 4) if p50 is not None else None,
            "p95": round(p95, 4) if p95 is not None else None,
            "p99": round(p99, 4) if p99 is not None else None,
            "max": round(max(latencies), 4) if latencies else None,
        },
        "http_statuses": dict(sorted(http_statuses.items())),
        "business_codes": dict(sorted(business_codes.items())),
        "errors": dict(sorted(errors.items())),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="在线 VBas 人数识别压力测试")
    parser.add_argument(
        "--url",
        "--gateway-url",
        dest="url",
        default=DEFAULT_URL,
        help="Online Gateway 根地址",
    )
    parser.add_argument(
        "--image",
        "--image-path",
        dest="image",
        type=Path,
        default=DEFAULT_IMAGE,
        help="本地测试图片路径",
    )
    parser.add_argument("--concurrency", type=_positive_int, default=1, help="并发请求数")
    parser.add_argument(
        "--requests",
        "--total-requests",
        dest="requests",
        type=_positive_int,
        default=1,
        help="请求总量",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=300.0,
        help="单请求读写超时时间（秒，默认 300）",
    )
    parser.add_argument(
        "--task-prefix",
        default="online-person-count-load",
        help="请求 TaskID 前缀",
    )
    parser.add_argument("--image-width", type=_positive_int, default=1920)
    parser.add_argument("--image-height", type=_positive_int, default=1080)
    parser.add_argument("--output", type=Path, help="可选：将汇总 JSON 写入文件")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        image = _load_image(args.image)
        result = asyncio.run(
            run_load(
                url=args.url,
                image=image,
                concurrency=args.concurrency,
                total_requests=args.requests,
                timeout_seconds=args.timeout,
                task_prefix=args.task_prefix,
                image_width=args.image_width,
                image_height=args.image_height,
            )
        )
    except (ValueError, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["failure"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
