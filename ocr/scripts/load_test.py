from __future__ import annotations

import argparse
import asyncio
import base64
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time

import httpx


@dataclass(frozen=True)
class LoadTestConfig:
    ip: str
    port: int
    image_path: Path
    concurrency: int
    total_requests: int | None
    duration: float | None
    timeout: float
    warmup: int
    enable_formula: bool
    scheme: str = "http"
    output_path: Path | None = None

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.ip}:{self.port}"


@dataclass(frozen=True)
class RequestResult:
    success: bool
    latency_ms: float
    error: str | None = None


@dataclass(frozen=True)
class LoadTestReport:
    started_at: str
    base_url: str
    image_path: str
    mode: str
    concurrency: int
    elapsed_seconds: float
    completed_requests: int
    successful_requests: int
    failed_requests: int
    success_rate_percent: float
    requests_per_second: float
    latency_ms: dict[str, float]
    errors: dict[str, int]

    def to_dict(self) -> dict:
        return asdict(self)


RequestCallable = Callable[[int], Awaitable[RequestResult]]


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    position = (len(sorted_values) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def calculate_latency_statistics(latencies: list[float]) -> dict[str, float]:
    values = sorted(latencies)
    if not values:
        return {
            "min": 0.0,
            "average": 0.0,
            "p50": 0.0,
            "p90": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "max": 0.0,
        }
    statistics = {
        "min": values[0],
        "average": sum(values) / len(values),
        "p50": _percentile(values, 0.50),
        "p90": _percentile(values, 0.90),
        "p95": _percentile(values, 0.95),
        "p99": _percentile(values, 0.99),
        "max": values[-1],
    }
    return {name: round(value, 3) for name, value in statistics.items()}


async def send_prediction(
    client: httpx.AsyncClient,
    image_base64: str,
    request_id: int,
    enable_formula: bool,
) -> RequestResult:
    image_id = f"load-test-{request_id}"
    request_payload = {"key": [image_id], "value": [image_base64]}
    if enable_formula:
        request_payload["enable_formula"] = True

    started = time.perf_counter()
    try:
        response = await client.post("/ocr/prediction", json=request_payload)
        response.raise_for_status()
        payload = response.json()
        if payload.get("err_no") != 0:
            message = payload.get("err_msg") or "未知错误"
            raise RuntimeError(f"OCR 接口返回错误：{message}")
        success = True
        error_message = None
    except httpx.TimeoutException:
        success = False
        error_message = "请求超时"
    except httpx.HTTPStatusError as error:
        success = False
        error_message = f"HTTP {error.response.status_code}"
    except (httpx.HTTPError, RuntimeError, ValueError) as error:
        success = False
        error_message = str(error) or error.__class__.__name__

    latency_ms = (time.perf_counter() - started) * 1000
    return RequestResult(success, latency_ms, error_message)


async def _execute_workers(
    config: LoadTestConfig,
    request_once: RequestCallable,
) -> tuple[list[RequestResult], float, str]:
    next_request_id = 0
    started = time.perf_counter()
    deadline = started + config.duration if config.duration is not None else None
    results: list[RequestResult] = []

    async def worker() -> None:
        nonlocal next_request_id
        while True:
            if config.total_requests is not None:
                if next_request_id >= config.total_requests:
                    return
            elif deadline is not None and time.perf_counter() >= deadline:
                return

            request_id = next_request_id
            next_request_id += 1
            try:
                result = await request_once(request_id)
            except Exception as error:
                result = RequestResult(
                    False,
                    0.0,
                    f"压测客户端异常：{error or error.__class__.__name__}",
                )
            results.append(result)

    await asyncio.gather(*(worker() for _ in range(config.concurrency)))
    elapsed = time.perf_counter() - started
    if config.total_requests is not None:
        mode = f"requests:{config.total_requests}"
    else:
        mode = f"duration:{config.duration}s"
    return results, elapsed, mode


async def run_load_test(
    config: LoadTestConfig,
    request_once: RequestCallable | None = None,
) -> LoadTestReport:
    if (config.total_requests is None) == (config.duration is None):
        raise ValueError("total_requests 和 duration 必须且只能设置一个")
    if config.concurrency < 1:
        raise ValueError("并发量必须大于 0")

    if request_once is None:
        if not config.image_path.is_file():
            raise FileNotFoundError(f"测试图片不存在：{config.image_path}")
        image_base64 = base64.b64encode(config.image_path.read_bytes()).decode("ascii")
        limits = httpx.Limits(
            max_connections=config.concurrency,
            max_keepalive_connections=config.concurrency,
        )
        async with httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout,
            limits=limits,
        ) as client:

            async def http_request(request_id: int) -> RequestResult:
                return await send_prediction(
                    client,
                    image_base64,
                    request_id,
                    config.enable_formula,
                )

            for warmup_id in range(config.warmup):
                await http_request(-(warmup_id + 1))
            results, elapsed, mode = await _execute_workers(config, http_request)
    else:
        for warmup_id in range(config.warmup):
            await request_once(-(warmup_id + 1))
        results, elapsed, mode = await _execute_workers(config, request_once)

    successful = sum(result.success for result in results)
    completed = len(results)
    failed = completed - successful
    errors = Counter(result.error for result in results if result.error)
    latencies = [result.latency_ms for result in results]
    return LoadTestReport(
        started_at=datetime.now(timezone.utc).isoformat(),
        base_url=config.base_url,
        image_path=str(config.image_path),
        mode=mode,
        concurrency=config.concurrency,
        elapsed_seconds=round(elapsed, 3),
        completed_requests=completed,
        successful_requests=successful,
        failed_requests=failed,
        success_rate_percent=round(successful / completed * 100, 3)
        if completed
        else 0.0,
        requests_per_second=round(completed / elapsed, 3) if elapsed else 0.0,
        latency_ms=calculate_latency_statistics(latencies),
        errors=dict(errors),
    )


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("必须是大于 0 的整数")
    return value


def _non_negative_int(raw: str) -> int:
    value = int(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("必须是非负整数")
    return value


def _positive_float(raw: str) -> float:
    value = float(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("必须是大于 0 的数字")
    return value


def _port(raw: str) -> int:
    value = int(raw)
    if not 1 <= value <= 65535:
        raise argparse.ArgumentTypeError("端口必须在 1 到 65535 之间")
    return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="并发压测 OCR prediction 接口")
    parser.add_argument("--ip", default="10.80.5.128", help="OCR 服务 IP")
    parser.add_argument("--port", type=_port, default=8866, help="OCR 服务端口")
    parser.add_argument(
        "--scheme",
        choices=("http", "https"),
        default="http",
        help="请求协议",
    )
    parser.add_argument("--image", type=Path,default="/Users/zhangshen/Documents/data/OCR测试数据/ppt课件截图_Formula.png", required=True, help="测试图片路径")
    parser.add_argument(
        "--concurrency",
        type=_positive_int,
        default=10,
        help="并发请求数，默认 20",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--requests", type=_positive_int, help="固定请求总数")
    mode.add_argument("--duration", type=_positive_float, help="固定压测秒数")
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=120.0,
        help="单请求超时秒数，默认 120",
    )
    parser.add_argument(
        "--warmup",
        type=_non_negative_int,
        default=1,
        help="正式统计前的预热请求数，默认 1",
    )
    parser.add_argument(
        "--enable-formula",
        action="store_true",
        help="请求公式识别",
    )
    parser.add_argument("--output", type=Path, help="JSON 报告输出路径")
    args = parser.parse_args(argv)
    if args.requests is None and args.duration is None:
        args.requests = 100
    return args


def _print_report(report: LoadTestReport) -> None:
    latency = report.latency_ms
    print("\nOCR 压测结果")
    print(f"目标地址: {report.base_url}/ocr/prediction")
    print(f"压测模式: {report.mode}")
    print(f"并发量: {report.concurrency}")
    print(f"实际耗时: {report.elapsed_seconds:.3f} 秒")
    print(f"完成请求: {report.completed_requests}")
    print(f"成功/失败: {report.successful_requests}/{report.failed_requests}")
    print(f"成功率: {report.success_rate_percent:.3f}%")
    print(f"QPS: {report.requests_per_second:.3f}")
    print(
        "耗时(ms): "
        f"min={latency['min']:.3f} avg={latency['average']:.3f} "
        f"p50={latency['p50']:.3f} p90={latency['p90']:.3f} "
        f"p95={latency['p95']:.3f} p99={latency['p99']:.3f} "
        f"max={latency['max']:.3f}"
    )
    if report.errors:
        print("失败原因:")
        for message, count in sorted(report.errors.items()):
            print(f"  {count} x {message}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = LoadTestConfig(
        ip=args.ip,
        port=args.port,
        image_path=args.image,
        concurrency=args.concurrency,
        total_requests=args.requests,
        duration=args.duration,
        timeout=args.timeout,
        warmup=args.warmup,
        enable_formula=args.enable_formula,
        scheme=args.scheme,
        output_path=args.output,
    )
    try:
        report = asyncio.run(run_load_test(config))
    except (FileNotFoundError, OSError, ValueError) as error:
        print(f"压测启动失败：{error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("压测已中止", file=sys.stderr)
        return 130

    _print_report(report)
    if config.output_path is not None:
        config.output_path.parent.mkdir(parents=True, exist_ok=True)
        config.output_path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"JSON 报告: {config.output_path}")
    return 0 if report.failed_requests == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
