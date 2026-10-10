"""Run the v1.0.2 VAD configuration comparison on one GPU host."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from test_full_teacher_abc import (
    RouteState,
    make_comparison,
    run_route,
    timeline_metrics,
    wav_info,
)


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FIELDS = {"key", "text", "finished", "bg", "ed"}
EXPERIMENTS = (
    ("vad1000_no_merge", 1000, 1),
    ("vad1200_no_merge", 1200, 1),
    ("vad1000_merge720", 1000, 720),
)


def replace_config_value(config: str, name: str, value: int) -> str:
    pattern = rf"(?m)^{re.escape(name)}\s*=\s*\d+\s*$"
    updated, count = re.subn(pattern, f"{name} = {value}", config)
    if count != 1:
        raise ValueError(f"配置项 {name} 应恰好出现一次，实际为 {count}")
    return updated


def port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def status_payload(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if response.status != 200:
                return None
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def gpu_snapshot() -> dict:
    command = [
        "nvidia-smi",
        "--query-gpu=index,memory.used,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return {
        "returncode": result.returncode,
        "output": result.stdout.strip(),
        "error": result.stderr.strip(),
    }


def wait_for_service(
    process: subprocess.Popen,
    status_url: str,
    minimum_wait_seconds: float,
    startup_timeout_seconds: float,
) -> dict:
    started_at = time.monotonic()
    ready_at = None
    payload = None
    while time.monotonic() - started_at < startup_timeout_seconds:
        if process.poll() is not None:
            raise RuntimeError(f"服务启动失败，退出码 {process.returncode}")
        current = status_payload(status_url)
        if current is not None and ready_at is None:
            ready_at = time.monotonic()
            payload = current
        elapsed = time.monotonic() - started_at
        if ready_at is not None and elapsed >= minimum_wait_seconds:
            return {
                "ready_after_seconds": round(ready_at - started_at, 3),
                "test_started_after_seconds": round(elapsed, 3),
                "status": payload,
            }
        time.sleep(2)
    raise TimeoutError(f"服务在 {startup_timeout_seconds}s 内未就绪")


def stop_service(process: subprocess.Popen) -> dict:
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=90)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=30)
    deadline = time.monotonic() + 30
    while port_is_open("127.0.0.1", 8084) and time.monotonic() < deadline:
        time.sleep(1)
    return {
        "returncode": process.returncode,
        "port_8084_released": not port_is_open("127.0.0.1", 8084),
        "gpu_after_stop": gpu_snapshot(),
    }


def load_records(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def build_summary(
    name: str,
    vad_ms: int,
    merge_ms: int,
    state: RouteState,
    info: dict,
    output_path: Path,
    startup: dict,
    shutdown: dict,
    gpu_before_test: dict,
) -> dict:
    records = load_records(output_path)
    finished = [record for record in records if record.get("finished") is True]
    durations = [float(record["ed"]) - float(record["bg"]) for record in finished]
    protocol_anomalies = [
        index for index, record in enumerate(records) if set(record) != REQUIRED_FIELDS
    ]
    nonempty_keys = {
        str(record["key"]) for record in records if str(record.get("text", "")).strip()
    }
    finished_keys = {
        str(record["key"]) for record in finished if str(record.get("text", "")).strip()
    }
    unfinished_nonempty_keys = sorted(nonempty_keys - finished_keys)
    zero_duration = [
        index for index, duration in enumerate(durations) if duration <= 0
    ]
    finished_text_lengths = [len(str(record.get("text", ""))) for record in finished]
    elapsed = (state.ended_at or time.monotonic()) - state.started_at
    return {
        "name": name,
        "route": "/v1.0.2/seacraft_asr_online",
        "config": {
            "vad_max_end_silence_ms": vad_ms,
            "vad_merge_max_gap_ms": merge_ms,
            "merge_mode": "disabled_approximately_with_1ms" if merge_ms == 1 else "enabled",
        },
        "audio": info,
        "responses": len(records),
        "finished": len(finished),
        "correction_count": len(finished),
        "short_finished": {
            "lte_0_5_seconds": sum(duration <= 0.5 for duration in durations),
            "lte_1_0_seconds": sum(duration <= 1.0 for duration in durations),
        },
        "finished_latency_seconds": {
            "definition": "wall clock from WebSocket connection to finished response",
            "first": round(state.finished_delays[0], 3) if state.finished_delays else None,
            "average": (
                round(sum(state.finished_delays) / len(state.finished_delays), 3)
                if state.finished_delays
                else None
            ),
            "max": round(max(state.finished_delays), 3) if state.finished_delays else None,
        },
        "correction_wait_seconds": {
            "definition": "wall-clock gap from the preceding response to a finished response",
            "average": (
                round(sum(state.finished_waits) / len(state.finished_waits), 3)
                if state.finished_waits
                else None
            ),
            "max": round(max(state.finished_waits), 3) if state.finished_waits else None,
        },
        "text": {
            "finished_characters": sum(finished_text_lengths),
            "average_finished_characters": (
                round(sum(finished_text_lengths) / len(finished_text_lengths), 3)
                if finished_text_lengths
                else 0
            ),
        },
        "timeline": {
            **timeline_metrics(records, info["duration_seconds"]),
            "zero_duration_finished": zero_duration,
        },
        "protocol_field_anomalies": protocol_anomalies,
        "unfinished_nonempty_keys": unfinished_nonempty_keys,
        "sent_chunks": state.sent_chunks,
        "audio_chunks_sent": state.audio_chunks_sent,
        "tail_chunks_sent": state.tail_chunks_sent,
        "elapsed_seconds": round(elapsed, 3),
        "max_response_gap_seconds": round(state.max_response_gap, 3),
        "complete_audio_sent": state.audio_chunks_sent == info["total_chunks"],
        "stalled": state.stalled,
        "failure": state.failure,
        "connection_close": state.connection_close,
        "startup": startup,
        "gpu_before_test": gpu_before_test,
        "shutdown": shutdown,
        "output": str(output_path),
        "monitor": str(state.monitor_path),
    }


async def run_experiment(
    args: argparse.Namespace,
    name: str,
    vad_ms: int,
    merge_ms: int,
    info: dict,
) -> dict:
    output_path = args.output_dir / f"{name}.jsonl"
    state = RouteState(
        label=name,
        version="v1.0.2",
        url="ws://127.0.0.1:8084/v1.0.2/seacraft_asr_online",
        output_path=output_path,
        monitor_path=args.output_dir / f"{name}.monitor.jsonl",
        total_chunks=info["total_chunks"],
    )
    await run_route(
        state=state,
        wav_path=args.wav,
        tail_chunks=args.tail_chunks,
        drain_seconds=args.drain_seconds,
        no_response_timeout=args.no_response_timeout,
        max_runtime=args.max_runtime,
        start_seconds=args.start_seconds,
        max_seconds=args.max_seconds,
        max_inflight=args.max_inflight,
        response_paced=False,
        response_window=0,
        send_interval=args.send_interval,
        remote_host=None,
        remote_pid_file="",
        resource_interval=15,
        open_timeout=120,
    )
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", type=Path, default=ROOT / "test" / "教师1_16k.wav")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "test" / "vad_three_way_comparison")
    parser.add_argument("--start-seconds", type=float, default=98)
    parser.add_argument("--max-seconds", type=float, default=54)
    parser.add_argument("--minimum-startup-wait", type=float, default=300)
    parser.add_argument("--startup-timeout", type=float, default=900)
    parser.add_argument("--send-interval", type=float, default=0.4)
    parser.add_argument("--max-inflight", type=int, default=250)
    parser.add_argument("--tail-chunks", type=int, default=15)
    parser.add_argument("--drain-seconds", type=float, default=15)
    parser.add_argument("--no-response-timeout", type=float, default=120)
    parser.add_argument("--max-runtime", type=float, default=1800)
    args = parser.parse_args()

    if port_is_open("127.0.0.1", 8084):
        parser.error("8084 已被占用，拒绝启动对照实验")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    base_config = (ROOT / "config.toml").read_text(encoding="utf-8")
    info = wav_info(args.wav, args.start_seconds, args.max_seconds)
    summaries = []

    for name, vad_ms, merge_ms in EXPERIMENTS:
        config = replace_config_value(base_config, "vad_max_end_silence_ms", vad_ms)
        config = replace_config_value(config, "vad_merge_max_gap_ms", merge_ms)
        config_path = Path("/tmp") / f"asr_online_{name}.toml"
        config_path.write_text(config, encoding="utf-8")
        log_path = args.output_dir / f"{name}.service.log"
        print(f"[{name}] starting with VAD={vad_ms}ms merge={merge_ms}ms", flush=True)
        with log_path.open("w", encoding="utf-8") as log_file:
            env = os.environ.copy()
            env["CONFIG_PATH"] = str(config_path)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8084",
                    "--workers",
                    "1",
                    "--ws-ping-interval",
                    "20",
                    "--ws-ping-timeout",
                    "120",
                ],
                cwd=ROOT,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            startup = {}
            shutdown = {}
            try:
                startup = wait_for_service(
                    process,
                    "http://127.0.0.1:8084/get_status",
                    args.minimum_startup_wait,
                    args.startup_timeout,
                )
                gpu_before_test = gpu_snapshot()
                state = asyncio.run(run_experiment(args, name, vad_ms, merge_ms, info))
            finally:
                shutdown = stop_service(process)

        summary = build_summary(
            name,
            vad_ms,
            merge_ms,
            state,
            info,
            state.output_path,
            startup,
            shutdown,
            gpu_before_test,
        )
        summary_path = args.output_dir / f"{name}.summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        summaries.append(summary)
        print(
            f"[{name}] responses={summary['responses']} finished={summary['finished']} "
            f"short<=0.5s={summary['short_finished']['lte_0_5_seconds']} "
            f"failure={summary['failure']}",
            flush=True,
        )

    comparisons = []
    for left_index, right_index in ((0, 1), (0, 2), (1, 2)):
        left = summaries[left_index]
        right = summaries[right_index]
        output_path = args.output_dir / f"{left['name']}_vs_{right['name']}.jsonl"
        comparisons.append(
            make_comparison(
                left["name"],
                Path(left["output"]),
                right["name"],
                Path(right["output"]),
                output_path,
                info["duration_seconds"],
            )
        )

    run_complete = all(
        summary["complete_audio_sent"]
        and not summary["stalled"]
        and summary["failure"] is None
        and not summary["protocol_field_anomalies"]
        and summary["shutdown"]["port_8084_released"]
        for summary in summaries
    )
    all_nonempty_segments_finished = all(
        not summary["unfinished_nonempty_keys"] for summary in summaries
    )
    report = {
        "generated_at": time.time(),
        "host": socket.gethostname(),
        "python": sys.executable,
        "wav": str(args.wav),
        "audio_range_seconds": [args.start_seconds, args.start_seconds + args.max_seconds],
        "send_interval_seconds": args.send_interval,
        "minimum_startup_wait_seconds": args.minimum_startup_wait,
        "baseline_merge_note": "1ms approximates disabled merge because the config requires a positive integer",
        "complete": run_complete,
        "all_nonempty_segments_finished": all_nonempty_segments_finished,
        "experiments": summaries,
        "comparisons": comparisons,
        "formal_config_unchanged": (ROOT / "config.toml").read_text(encoding="utf-8") == base_config,
        "port_8084_released": not port_is_open("127.0.0.1", 8084),
        "gpu_after_all": gpu_snapshot(),
    }
    (args.output_dir / "comparison_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if run_complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
