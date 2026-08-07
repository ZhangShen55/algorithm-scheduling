import asyncio
from pathlib import Path

import httpx
import pytest

from scripts import load_test


def test_load_test_script_exists():
    project_root = Path(__file__).resolve().parents[2]

    assert (project_root / "scripts" / "load_test.py").is_file()


def test_parse_args_supports_fixed_request_mode(tmp_path):
    image = tmp_path / "test.jpg"
    image.write_bytes(b"image")

    args = load_test.parse_args(
        [
            "--ip",
            "10.80.5.130",
            "--port",
            "8866",
            "--image",
            str(image),
            "--concurrency",
            "8",
            "--requests",
            "120",
            "--output",
            str(tmp_path / "report.json"),
        ]
    )

    assert args.ip == "10.80.5.130"
    assert args.port == 8866
    assert args.image == image
    assert args.concurrency == 8
    assert args.requests == 120
    assert args.duration is None


def test_parse_args_rejects_requests_and_duration_together(tmp_path):
    image = tmp_path / "test.jpg"
    image.write_bytes(b"image")

    with pytest.raises(SystemExit):
        load_test.parse_args(
            [
                "--image",
                str(image),
                "--requests",
                "10",
                "--duration",
                "5",
            ]
        )


def test_run_load_test_respects_request_count_and_concurrency(tmp_path):
    active = 0
    max_active = 0

    async def request_once(request_id):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.001)
        active -= 1
        if request_id == 3:
            return load_test.RequestResult(False, 2.0, "OCR 接口返回错误：busy")
        return load_test.RequestResult(True, 1.0)

    config = load_test.LoadTestConfig(
        ip="127.0.0.1",
        port=8866,
        image_path=tmp_path / "test.jpg",
        concurrency=3,
        total_requests=8,
        duration=None,
        timeout=10.0,
        warmup=0,
        enable_formula=False,
    )

    report = asyncio.run(load_test.run_load_test(config, request_once=request_once))

    assert report.completed_requests == 8
    assert report.successful_requests == 7
    assert report.failed_requests == 1
    assert report.errors == {"OCR 接口返回错误：busy": 1}
    assert max_active == 3


def test_run_load_test_supports_duration_mode(tmp_path):
    async def request_once(request_id):
        await asyncio.sleep(0.001)
        return load_test.RequestResult(True, 1.0)

    config = load_test.LoadTestConfig(
        ip="127.0.0.1",
        port=8866,
        image_path=tmp_path / "test.jpg",
        concurrency=2,
        total_requests=None,
        duration=0.01,
        timeout=10.0,
        warmup=0,
        enable_formula=False,
    )

    report = asyncio.run(load_test.run_load_test(config, request_once=request_once))

    assert report.completed_requests > 0
    assert report.successful_requests == report.completed_requests


def test_send_prediction_validates_ocr_response():
    async def run_request():
        async def handler(request):
            payload = __import__("json").loads(request.content)
            assert request.url.path == "/ocr/prediction"
            assert payload == {
                "key": ["load-test-5"],
                "value": ["aW1hZ2U="],
                "enable_formula": True,
            }
            return httpx.Response(
                200,
                json={
                    "err_no": 0,
                    "err_msg": "",
                    "key": ["load-test-5"],
                    "value": ["[]"],
                    "formula_results": [],
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://127.0.0.1:8866",
        ) as client:
            return await load_test.send_prediction(
                client,
                image_base64="aW1hZ2U=",
                request_id=5,
                enable_formula=True,
            )

    result = asyncio.run(run_request())

    assert result.success is True
    assert result.error is None
    assert result.latency_ms >= 0


def test_calculate_latency_statistics():
    statistics = load_test.calculate_latency_statistics([10.0, 20.0, 30.0, 40.0])

    assert statistics == {
        "min": 10.0,
        "average": 25.0,
        "p50": 25.0,
        "p90": 37.0,
        "p95": 38.5,
        "p99": 39.7,
        "max": 40.0,
    }
