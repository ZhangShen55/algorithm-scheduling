#!/usr/bin/env python3
"""对三条在线 VBas 路由执行真实图片恢复探测，不保存图片或推理正文。"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

ROUTES = {"person-count", "teacher", "student"}


def _payload(route: str, image: str, request_id: str) -> dict[str, Any]:
    if route == "person-count":
        return {
            "TaskID": request_id,
            "TaskType": 4,
            "ImageList": [{"ImageID": request_id, "Data": image}],
            "AnalysisRule": {
                "AlgParams": {
                    "ImageFormat": 0,
                    "ImageResolution": {"ImageWidth": 1920, "ImageHeight": 1080},
                    "PolygonList": [],
                }
            },
            "RunAlways": False,
        }
    return {
        "task_id": request_id,
        "batch_id": f"{request_id}-batch",
        "stream_type": route,
        "ImageList": [
            {
                "ImageId": request_id,
                "StoragePath": image,
                "frame_id": request_id,
                "frame_index": 0,
                "timestamp_seconds": 0.0,
            }
        ],
    }


def _result_code(route: str, body: object) -> str:
    if not isinstance(body, dict):
        return "响应不是对象"
    if route == "person-count":
        response = body.get("Response")
        if isinstance(response, dict):
            return str(response.get("ErrCode", "缺少 ErrCode"))
    status = body.get("StatusObject")
    if isinstance(status, dict):
        return str(status.get("StatusCode", "缺少 StatusCode"))
    code = body.get("code")
    return str(code if code is not None else "缺少业务状态")


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    image = base64.b64encode(args.image.read_bytes()).decode("ascii")
    semaphore = asyncio.Semaphore(args.concurrency)
    counters: Counter[str] = Counter()
    latencies: list[float] = []
    lock = asyncio.Lock()
    endpoint = f"{args.gateway_url.rstrip('/')}/online/vbas/{args.route}"
    timeout = httpx.Timeout(args.timeout, connect=min(args.timeout, 30.0))
    limits = httpx.Limits(
        max_connections=args.concurrency,
        max_keepalive_connections=args.concurrency,
    )
    started = time.perf_counter()

    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        async def invoke(index: int) -> None:
            request_id = f"{args.task_prefix}-{index + 1:04d}"
            request_started = time.perf_counter()
            key = "未执行"
            async with semaphore:
                try:
                    response = await client.post(
                        endpoint,
                        json=_payload(args.route, image, request_id),
                    )
                    try:
                        body: object = response.json()
                    except ValueError:
                        body = None
                    key = f"http:{response.status_code}/business:{_result_code(args.route, body)}"
                except httpx.TimeoutException as exc:
                    key = f"timeout:{type(exc).__name__}"
                except httpx.HTTPError as exc:
                    key = f"transport:{type(exc).__name__}"
                finally:
                    elapsed = time.perf_counter() - request_started
                    async with lock:
                        counters[key] += 1
                        latencies.append(elapsed)

        await asyncio.gather(*(invoke(index) for index in range(args.requests)))

    total_elapsed = time.perf_counter() - started
    success_key = "http:200/business:0"
    return {
        "route": args.route,
        "endpoint": endpoint,
        "concurrency": args.concurrency,
        "requests": args.requests,
        "success": counters[success_key],
        "failure": args.requests - counters[success_key],
        "classifications": dict(sorted(counters.items())),
        "elapsed_seconds": round(total_elapsed, 4),
        "throughput_requests_per_second": round(args.requests / total_elapsed, 4),
        "latency_seconds": {
            "min": round(min(latencies), 4),
            "mean": round(statistics.fmean(latencies), 4),
            "max": round(max(latencies), 4),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="在线 VBas 恢复探针")
    parser.add_argument("--gateway-url", default="http://127.0.0.1:18103")
    parser.add_argument("--route", required=True, choices=sorted(ROUTES))
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--requests", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=660.0)
    parser.add_argument("--task-prefix", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.concurrency <= 0 or args.requests <= 0 or args.timeout <= 0:
        parser.error("并发数、请求总数和超时必须大于 0")
    if args.output.exists():
        parser.error(f"输出文件已存在，拒绝覆盖: {args.output}")
    result = asyncio.run(_run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["failure"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
