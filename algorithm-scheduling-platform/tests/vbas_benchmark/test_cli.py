from __future__ import annotations

from scripts.vbas_benchmark.cli import build_parser


def test_vbas_cli_default_uses_long_window_sample_floor() -> None:
    args = build_parser().parse_args(
        [
            "vbas",
            "--campaign-id",
            "campaign-test",
            "--report-root",
            "/tmp/report",
            "--case-id",
            "student-c1",
            "--fixture-root",
            "/tmp/fixture",
            "--fixture-root-in-container",
            "/tmp/fixture",
            "--manifest",
            "/tmp/fixture/manifest.json",
            "--target",
            "vbas-gpu0,http://127.0.0.1:18981,0,vbas-gpu0",
            "--mode",
            "student",
            "--concurrency",
            "1",
            "--effective-config-path",
            "/tmp/vbas.toml",
        ]
    )

    assert args.min_steady_batches == 100


def test_full_chain_report_cli_requires_capacity_and_sources() -> None:
    args = build_parser().parse_args(
        [
            "full-chain-report",
            "--vision-log",
            "/tmp/vision.log",
            "--gpu-csv",
            "/tmp/gpu.csv",
            "--capacity",
            "6",
            "--output",
            "/tmp/report.json",
        ]
    )

    assert args.command == "full-chain-report"
    assert args.capacity == 6
