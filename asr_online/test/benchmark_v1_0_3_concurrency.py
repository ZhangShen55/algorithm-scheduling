import argparse
import asyncio
import json
import math
import os
import re
import signal
import subprocess
import time
import tomli
import urllib.request
import wave
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import websockets
from websockets.exceptions import ConnectionClosed


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAV = PROJECT_ROOT / "test" / "教师1_16k.wav"
DEFAULT_URL = "ws://127.0.0.1:8084/v1.0.3/seacraft_asr_online"
CHUNK_SECONDS = 0.48
CHUNK_SAMPLES = int(16000 * CHUNK_SECONDS)
EXPECTED_FIELDS = {"key", "text", "finished", "bg", "ed"}


def percentile(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))
    return round(ordered[index], 6)


def distribution(values):
    return {
        "count": len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": round(max(values), 6) if values else None,
    }


def read_chunks(path: Path, duration_seconds: float | None, start_seconds: float = 0):
    chunks = []
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
            raise ValueError("测试音频必须是 16 kHz、单声道、16-bit WAV")
        if start_seconds < 0:
            raise ValueError("start_seconds 不能为负数")
        wav.setpos(min(wav.getnframes(), int(start_seconds * 16000)))
        remaining = None
        if duration_seconds is not None and duration_seconds > 0:
            remaining = int(duration_seconds * 16000)
        while remaining is None or remaining > 0:
            count = CHUNK_SAMPLES if remaining is None else min(CHUNK_SAMPLES, remaining)
            data = wav.readframes(count)
            if not data:
                break
            actual_samples = len(data) // 2
            if remaining is not None:
                remaining -= actual_samples
            if actual_samples < CHUNK_SAMPLES:
                data += b"\x00" * ((CHUNK_SAMPLES - actual_samples) * 2)
            chunks.append(data)
    if not chunks:
        raise ValueError("测试音频没有可发送数据")
    return chunks


@dataclass
class ClientState:
    client_id: int
    sent_chunks: int = 0
    responses: int = 0
    finished: int = 0
    first_send_at: float | None = None
    last_send_at: float | None = None
    first_response_at: float | None = None
    last_response_at: float | None = None
    send_times: list[float] = field(default_factory=list)
    partial_latencies: list[float] = field(default_factory=list)
    finished_latencies: list[float] = field(default_factory=list)
    protocol_errors: list[str] = field(default_factory=list)
    timestamp_backwards: int = 0
    timestamp_overlaps: int = 0
    duplicate_finished: int = 0
    close_code: int | None = None
    failure: str | None = None
    last_response_ed: float = 0.0
    max_audio_backlog_seconds: float = 0.0
    backlog_over_limit_since: float | None = None
    finished_keys: set[str] = field(default_factory=set)
    previous_finished_bg: float | None = None
    previous_finished_ed: float | None = None

    def observe_backlog(self, now: float) -> bool:
        backlog = max(0.0, self.sent_chunks * CHUNK_SECONDS - self.last_response_ed)
        self.max_audio_backlog_seconds = max(self.max_audio_backlog_seconds, backlog)
        if backlog <= 60:
            self.backlog_over_limit_since = None
            return False
        self.backlog_over_limit_since = self.backlog_over_limit_since or now
        return now - self.backlog_over_limit_since >= 30


class JsonlWriter:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("w", encoding="utf-8")
        self._lock = asyncio.Lock()

    async def write(self, payload):
        async with self._lock:
            self._file.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._file.flush()

    def close(self):
        self._file.close()


def response_latency(state: ClientState, ed: float, received_at: float):
    if not state.send_times:
        return None
    index = max(0, math.ceil(max(0.0, ed) / CHUNK_SECONDS) - 1)
    index = min(index, len(state.send_times) - 1)
    return max(0.0, received_at - state.send_times[index])


async def run_client(
    state: ClientState,
    url: str,
    chunks: list[bytes],
    point_started_at: float,
    stagger_seconds: float,
    tail_chunks: int,
    drain_seconds: float,
    stop_event: asyncio.Event,
    writer: JsonlWriter,
):
    audio_done = asyncio.Event()
    try:
        await asyncio.sleep(max(0.0, point_started_at + stagger_seconds - time.monotonic()))
        async with websockets.connect(url, max_size=None, open_timeout=10) as websocket:
            async def receiver():
                while not stop_event.is_set():
                    try:
                        raw = await asyncio.wait_for(websocket.recv(), timeout=1)
                    except asyncio.TimeoutError:
                        now = time.monotonic()
                        if state.first_send_at is not None and state.first_response_at is None:
                            if now - state.first_send_at > 10:
                                state.failure = "first_response_timeout"
                                stop_event.set()
                                return
                        elif (
                            not audio_done.is_set()
                            and state.last_response_at is not None
                            and now - state.last_response_at > 15
                        ):
                            state.failure = "active_response_stalled"
                            stop_event.set()
                            return
                        if state.observe_backlog(now):
                            state.failure = "audio_backlog_over_60s_for_30s"
                            stop_event.set()
                            return
                        if (
                            audio_done.is_set()
                            and state.last_response_at is not None
                            and now - state.last_response_at >= drain_seconds
                        ):
                            return
                        continue
                    except ConnectionClosed as exc:
                        state.close_code = exc.code
                        if not audio_done.is_set():
                            state.failure = f"connection_closed:{exc.code}"
                            stop_event.set()
                        return

                    received_at = time.monotonic()
                    state.responses += 1
                    state.first_response_at = state.first_response_at or received_at
                    state.last_response_at = received_at
                    try:
                        response = json.loads(raw)
                    except json.JSONDecodeError:
                        state.protocol_errors.append("invalid_json")
                        continue
                    if set(response) != EXPECTED_FIELDS:
                        state.protocol_errors.append("response_fields")
                    response_ed = float(response.get("ed", 0))
                    advances_partial_timeline = (
                        response.get("finished") is not True
                        and response_ed > state.last_response_ed
                    )
                    if response_ed < state.last_response_ed:
                        state.timestamp_backwards += 1
                    latency = response_latency(state, response_ed, received_at)
                    state.last_response_ed = max(
                        state.last_response_ed, response_ed
                    )
                    if state.observe_backlog(received_at):
                        state.failure = "audio_backlog_over_60s_for_30s"
                        stop_event.set()
                    if response.get("finished") is True:
                        state.finished += 1
                        if latency is not None:
                            state.finished_latencies.append(latency)
                        key = str(response.get("key", ""))
                        if key in state.finished_keys:
                            state.duplicate_finished += 1
                        state.finished_keys.add(key)
                        bg = float(response.get("bg", 0))
                        ed = float(response.get("ed", 0))
                        if state.previous_finished_bg is not None and bg < state.previous_finished_bg:
                            state.timestamp_backwards += 1
                        if state.previous_finished_ed is not None and bg < state.previous_finished_ed:
                            state.timestamp_overlaps += 1
                        state.previous_finished_bg = bg
                        state.previous_finished_ed = ed
                    elif advances_partial_timeline and latency is not None:
                        state.partial_latencies.append(latency)
                    await writer.write(
                        {
                            "type": "response",
                            "client_id": state.client_id,
                            "received_monotonic": received_at,
                            "latency_seconds": latency,
                            "response": response,
                        }
                    )

            receiver_task = asyncio.create_task(receiver())
            stream_started_at = time.monotonic()
            for index, chunk in enumerate(chunks):
                if stop_event.is_set():
                    break
                scheduled_at = stream_started_at + index * CHUNK_SECONDS
                await asyncio.sleep(max(0.0, scheduled_at - time.monotonic()))
                await websocket.send(chunk)
                sent_at = time.monotonic()
                state.first_send_at = state.first_send_at or sent_at
                state.last_send_at = sent_at
                state.send_times.append(sent_at)
                state.sent_chunks += 1
            if not stop_event.is_set():
                silence = b"\x00" * (CHUNK_SAMPLES * 2)
                for index in range(tail_chunks):
                    await asyncio.sleep(CHUNK_SECONDS)
                    await websocket.send(silence)
                    state.send_times.append(time.monotonic())
            audio_done.set()
            try:
                await asyncio.wait_for(receiver_task, timeout=drain_seconds + 2)
            except asyncio.TimeoutError:
                receiver_task.cancel()
                await asyncio.gather(receiver_task, return_exceptions=True)
            await websocket.close()
    except Exception as exc:
        state.failure = f"{type(exc).__name__}: {exc}"
        stop_event.set()
    finally:
        audio_done.set()


def process_group_stats(process_group: int | None):
    if process_group is None:
        return {"rss_kib": None, "cpu_percent": None, "pids": []}
    result = subprocess.run(
        ["ps", "-eo", "pid=,pgid=,rss=,pcpu="],
        text=True,
        capture_output=True,
        check=False,
    )
    rss = 0
    cpu = 0.0
    pids = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 4 or int(fields[1]) != process_group:
            continue
        pids.append(int(fields[0]))
        rss += int(fields[2])
        cpu += float(fields[3])
    return {"rss_kib": rss, "cpu_percent": round(cpu, 3), "pids": pids}


def listener_pids(port: int):
    result = subprocess.run(
        ["ss", "-ltnp", f"sport = :{port}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return set()
    return {int(value) for value in re.findall(r"pid=(\d+)", result.stdout)}


def gpu_stats():
    try:
        gpu = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return {"memory_mib": None, "utilization_percent": None}
    if gpu.returncode != 0 or not gpu.stdout.strip():
        return {"memory_mib": None, "utilization_percent": None}
    first = gpu.stdout.splitlines()[0].split(",")
    return {
        "memory_mib": int(first[0].strip()),
        "utilization_percent": int(first[1].strip()),
    }


async def monitor_resources(
    process,
    process_group,
    stop_event,
    samples,
    writer,
):
    while not stop_event.is_set():
        process_alive = process is None or process.poll() is None
        sample = {
            "type": "resource",
            "monotonic": time.monotonic(),
            "process_alive": process_alive,
            **process_group_stats(process_group),
            **gpu_stats(),
        }
        samples.append(sample)
        await writer.write(sample)
        if not process_alive:
            stop_event.set()
            return
        await asyncio.sleep(1)


def read_service_events(path: Path | None, offset: int):
    events = []
    raw_text = ""
    if path is None or not path.exists():
        return events, raw_text
    with path.open("r", encoding="utf-8", errors="replace") as source:
        source.seek(offset)
        raw_text = source.read()
    for line in raw_text.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        encoded = payload.get("event")
        if isinstance(encoded, str) and encoded.startswith("{"):
            try:
                telemetry = json.loads(encoded)
            except json.JSONDecodeError:
                telemetry = None
            if isinstance(telemetry, dict) and telemetry.get("name"):
                payload.update(telemetry)
                payload["event"] = telemetry["name"]
        events.append(payload)
    return events, raw_text


def summarize_service_events(events, raw_text, total_audio_seconds):
    by_event = {}
    for item in events:
        by_event.setdefault(item.get("event"), []).append(item)
    correction_visible = by_event.get("correction_visible", [])
    finished_visible = by_event.get("finished_visible", [])
    correction_completed = by_event.get("correction_completed", [])
    model_events = by_event.get("v3_model_inference", [])
    online = [item for item in model_events if item.get("model_name") == "online"]
    vad = [item for item in model_events if item.get("model_name") == "vad"]
    inference_seconds = sum(
        float(item.get("inference_ms", 0)) / 1000
        for item in model_events + correction_completed
    )
    queue_depths = [int(item["queue_depth"]) for item in events if "queue_depth" in item]
    inflight_counts = [int(item["inflight"]) for item in events if "inflight" in item]
    total_audio = max(total_audio_seconds, 0.001)
    return {
        "correction_visible": distribution(
            [float(item["visible_ms"]) / 1000 for item in correction_visible if "visible_ms" in item]
        ),
        "finished_visible": distribution(
            [float(item["visible_ms"]) / 1000 for item in finished_visible if "visible_ms" in item]
        ),
        "correction_queue": distribution(
            [float(item["queue_ms"]) / 1000 for item in correction_completed if "queue_ms" in item]
        ),
        "correction_inference": distribution(
            [float(item["inference_ms"]) / 1000 for item in correction_completed if "inference_ms" in item]
        ),
        "online_queue": distribution(
            [float(item["queue_ms"]) / 1000 for item in online if "queue_ms" in item]
        ),
        "vad_queue": distribution(
            [float(item["queue_ms"]) / 1000 for item in vad if "queue_ms" in item]
        ),
        "queue_depth_max": max(
            queue_depths, default=0
        ),
        "queue_depth_average": (
            round(sum(queue_depths) / len(queue_depths), 6) if queue_depths else 0.0
        ),
        "inflight_max": max(
            inflight_counts, default=0
        ),
        "inflight_average": (
            round(sum(inflight_counts) / len(inflight_counts), 6)
            if inflight_counts
            else 0.0
        ),
        "stale": len(by_event.get("correction_stale", [])),
        "degraded": len(by_event.get("correction_degraded", [])),
        "queue_full": len(by_event.get("correction_queue_full", [])),
        "final_timeout": len(by_event.get("correction_final_timeout", [])),
        "compute_rtf": round(inference_seconds / total_audio, 6),
        "cuda_oom": "out of memory" in raw_text.lower(),
    }


async def run_point(args, process=None, process_group=None, service_log=None):
    chunks = read_chunks(args.wav, args.duration_seconds, args.start_seconds)
    audio_seconds = len(chunks) * CHUNK_SECONDS
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_{args.run_label}" if getattr(args, "run_label", "") else ""
    stem = f"w{args.correction_workers}_c{args.concurrency}_{int(audio_seconds)}s{suffix}"
    raw_path = output_dir / f"{stem}.jsonl"
    summary_path = output_dir / f"{stem}.summary.json"
    writer = JsonlWriter(raw_path)
    stop_event = asyncio.Event()
    states = [ClientState(client_id=index) for index in range(args.concurrency)]
    resources = []
    service_offset = service_log.stat().st_size if service_log and service_log.exists() else 0
    started_at = time.monotonic()
    monitor = asyncio.create_task(
        monitor_resources(process, process_group, stop_event, resources, writer)
    )
    clients = [
        asyncio.create_task(
            run_client(
                state,
                args.url,
                chunks,
                started_at,
                index * CHUNK_SECONDS / max(1, args.concurrency),
                args.tail_chunks,
                args.drain_seconds,
                stop_event,
                writer,
            )
        )
        for index, state in enumerate(states)
    ]
    hard_timeout = audio_seconds + CHUNK_SECONDS + 90
    try:
        await asyncio.wait_for(asyncio.gather(*clients), timeout=hard_timeout)
    except asyncio.TimeoutError:
        stop_event.set()
        for task in clients:
            task.cancel()
        await asyncio.gather(*clients, return_exceptions=True)
        for state in states:
            state.failure = state.failure or "point_hard_timeout"
    finally:
        stop_event.set()
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
        writer.close()

    events, raw_service_text = read_service_events(service_log, service_offset)
    sent_audio_seconds = sum(state.sent_chunks for state in states) * CHUNK_SECONDS
    service = summarize_service_events(events, raw_service_text, sent_audio_seconds)
    partial_latencies = [value for state in states for value in state.partial_latencies]
    finished_end_to_end_latencies = [
        value for state in states for value in state.finished_latencies
    ]
    complete_clients = sum(state.sent_chunks == len(chunks) for state in states)
    failed_clients = sum(state.failure is not None for state in states)
    protocol_errors = sum(len(state.protocol_errors) for state in states)
    timestamp_errors = sum(
        state.timestamp_backwards + state.timestamp_overlaps + state.duplicate_finished
        for state in states
    )
    partial = distribution(partial_latencies)
    finished_end_to_end = distribution(finished_end_to_end_latencies)
    finished = (
        service["finished_visible"]
        if service["finished_visible"]["count"]
        else finished_end_to_end
    )
    correction = service["correction_visible"]
    realtime_failures = []
    if complete_clients != args.concurrency:
        realtime_failures.append("audio_incomplete")
    if failed_clients:
        realtime_failures.append("client_failure")
    if partial["p95"] is None or partial["p95"] > 1.0:
        realtime_failures.append("partial_p95")
    if correction["count"] and correction["p95"] > 1.0:
        realtime_failures.append("correction_p95")
    if finished["count"] and finished["p95"] > 1.5:
        realtime_failures.append("finished_p95")
    if service["compute_rtf"] > 1.0:
        realtime_failures.append("rtf")
    if protocol_errors or timestamp_errors:
        realtime_failures.append("protocol_or_timestamp")
    if service["cuda_oom"]:
        realtime_failures.append("cuda_oom")
    if service["queue_full"] or service["final_timeout"]:
        realtime_failures.append("queue_overload")

    summary = {
        "workers": args.correction_workers,
        "concurrency": args.concurrency,
        "audio_seconds_per_client": audio_seconds,
        "start_seconds": args.start_seconds,
        "expected_chunks_per_client": len(chunks),
        "complete_clients": complete_clients,
        "failed_clients": failed_clients,
        "responses": sum(state.responses for state in states),
        "finished": sum(state.finished for state in states),
        "partial_latency": partial,
        "finished_latency": finished,
        "finished_end_to_end_latency": finished_end_to_end,
        "service": service,
        "protocol_errors": protocol_errors,
        "timestamp_errors": timestamp_errors,
        "elapsed_seconds": round(time.monotonic() - started_at, 3),
        "resources": {
            "samples": len(resources),
            "max_rss_kib": max((item.get("rss_kib") or 0 for item in resources), default=0),
            "max_cpu_percent": max((item.get("cpu_percent") or 0 for item in resources), default=0),
            "max_gpu_memory_mib": max((item.get("memory_mib") or 0 for item in resources), default=0),
            "max_gpu_utilization_percent": max((item.get("utilization_percent") or 0 for item in resources), default=0),
        },
        "realtime_pass": not realtime_failures,
        "realtime_failures": realtime_failures,
        "clients": [
            {
                "client_id": state.client_id,
                "sent_chunks": state.sent_chunks,
                "responses": state.responses,
                "finished": state.finished,
                "failure": state.failure,
                "close_code": state.close_code,
                "max_audio_backlog_seconds": round(
                    state.max_audio_backlog_seconds, 3
                ),
            }
            for state in states
        ],
        "raw_jsonl": str(raw_path),
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def write_worker_config(template: Path, target: Path, workers: int):
    with template.open("rb") as source:
        tomli.load(source)
    lines = template.read_text(encoding="utf-8").splitlines()
    key = "sensevoice_correction_workers"
    matches = [index for index, line in enumerate(lines) if line.strip().startswith(f"{key} =")]
    if len(matches) != 1:
        raise ValueError(f"{template} 必须且只能包含一个 {key}")
    lines[matches[0]] = f"{key} = {workers}"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with target.open("rb") as generated:
        parsed = tomli.load(generated)
    if int(parsed[key]) != workers:
        raise ValueError("临时 worker 配置校验失败")


def start_service(args, workers, worker_dir):
    config_path = worker_dir / f"config.worker-{workers}.toml"
    write_worker_config(args.config, config_path, workers)
    service_log = worker_dir / f"service.worker-{workers}.log"
    output = service_log.open("w", encoding="utf-8")
    env = dict(os.environ)
    env.update(CONFIG_PATH=str(config_path), PYTHONUNBUFFERED="1")
    command = [
        args.conda,
        "run",
        "--no-capture-output",
        "-n",
        args.conda_env,
        "python",
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(args.port),
        "--workers",
        "1",
    ]
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        stdout=output,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return process, output, service_log


async def wait_ready(process, process_group, url, timeout):
    deadline = time.monotonic() + timeout
    status_url = url.replace("ws://", "http://").split("/v1.0.3", 1)[0] + "/get_status"
    port = urlparse(url).port or 80
    consecutive_ready = 0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False, f"service_exit:{process.returncode}"
        try:
            response = await asyncio.to_thread(urllib.request.urlopen, status_url, timeout=2)
            group_pids = set(process_group_stats(process_group)["pids"])
            owns_listener = bool(listener_pids(port) & group_pids)
            if response.status == 200 and owns_listener:
                consecutive_ready += 1
                if consecutive_ready >= 2:
                    return True, None
            else:
                consecutive_ready = 0
        except Exception:
            consecutive_ready = 0
        await asyncio.sleep(2)
    return False, "startup_timeout"


def stop_service(process, output):
    try:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
    finally:
        output.close()


def markdown_report(matrix):
    lines = [
        "# v1.0.3 渐进纠正并发报告",
        "",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "| correction worker | 并发 | 状态 | partial p95 | 纠正 p95 | finished p95 | RTF | GPU MiB | 失败原因 |",
        "| ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in matrix["points"]:
        if item.get("unavailable"):
            lines.append(
                f"| {item['workers']} | {item['concurrency']} | 不可用 | - | - | - | - | - | {item['reason']} |"
            )
            continue
        lines.append(
            "| {workers} | {concurrency} | {status} | {partial} | {correction} | "
            "{finished} | {rtf} | {gpu} | {failures} |".format(
                workers=item["workers"],
                concurrency=item["concurrency"],
                status="实时通过" if item["realtime_pass"] else "未通过",
                partial=item["partial_latency"]["p95"],
                correction=item["service"]["correction_visible"]["p95"],
                finished=item["finished_latency"]["p95"],
                rtf=item["service"]["compute_rtf"],
                gpu=item["resources"]["max_gpu_memory_mib"],
                failures=", ".join(item["realtime_failures"]) or "-",
            )
        )
    lines.extend(["", "## 容量结论", ""])
    for workers in matrix["workers"]:
        passed = [
            item["concurrency"]
            for item in matrix["points"]
            if item["workers"] == workers and item.get("realtime_pass")
        ]
        maximum = max(passed) if passed else 0
        lines.append(f"- correction worker={workers}：最大已验证实时并发 {maximum}。")
    if matrix.get("retests"):
        lines.extend(["", "## 边界复验", ""])
        for item in matrix["retests"]:
            lines.append(
                f"- worker={item['workers']}、并发={item['concurrency']}、"
                f"边界={item['boundary']}："
                f"{'实时通过' if item['realtime_pass'] else '未通过'}。"
            )
    lines.extend(
        [
            "",
            "## 判定口径",
            "",
            "所有客户端完整发送、无 stalled/OOM/协议时间轴异常、RTF 不大于 1，且 "
            "partial/中间纠正/finished p95 分别不超过 1.0/1.0/1.5 秒。",
            "",
        ]
    )
    return "\n".join(lines)


async def run_matrix(args):
    workers_values = [int(value) for value in args.workers.split(",")]
    concurrency_values = [int(value) for value in args.concurrencies.split(",")]
    matrix = {"workers": workers_values, "concurrencies": concurrency_values, "points": []}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for workers in workers_values:
        worker_dir = args.output_dir / f"worker-{workers}"
        worker_dir.mkdir(parents=True, exist_ok=True)
        process, output, service_log = start_service(args, workers, worker_dir)
        try:
            process_group = os.getpgid(process.pid)
            ready, reason = await wait_ready(
                process,
                process_group,
                args.url,
                args.startup_timeout,
            )
            if not ready:
                for concurrency in concurrency_values:
                    matrix["points"].append(
                        {
                            "workers": workers,
                            "concurrency": concurrency,
                            "unavailable": True,
                            "reason": reason,
                        }
                    )
                continue
            worker_points = []
            for concurrency in concurrency_values:
                point_args = argparse.Namespace(**vars(args))
                point_args.correction_workers = workers
                point_args.concurrency = concurrency
                point_args.output_dir = worker_dir
                summary = await run_point(
                    point_args,
                    process=process,
                    process_group=process_group,
                    service_log=service_log,
                )
                matrix["points"].append(summary)
                worker_points.append(summary)
                if process.poll() is not None:
                    remaining = concurrency_values[concurrency_values.index(concurrency) + 1 :]
                    for unavailable in remaining:
                        matrix["points"].append(
                            {
                                "workers": workers,
                                "concurrency": unavailable,
                                "unavailable": True,
                                "reason": f"service_exit:{process.returncode}",
                            }
                        )
                    break
            if args.retest_boundaries and process.poll() is None:
                passed = [
                    item["concurrency"]
                    for item in worker_points
                    if item.get("realtime_pass")
                ]
                maximum = max(passed) if passed else None
                failed = [
                    item["concurrency"]
                    for item in worker_points
                    if not item.get("realtime_pass")
                    and (maximum is None or item["concurrency"] > maximum)
                ]
                boundaries = []
                if maximum is not None:
                    boundaries.append(("maximum_pass", maximum))
                if failed:
                    boundaries.append(("first_failure", min(failed)))
                matrix.setdefault("retests", [])
                for boundary, concurrency in boundaries:
                    point_args = argparse.Namespace(**vars(args))
                    point_args.correction_workers = workers
                    point_args.concurrency = concurrency
                    point_args.output_dir = worker_dir
                    point_args.run_label = f"retest-{boundary}"
                    summary = await run_point(
                        point_args,
                        process=process,
                        process_group=process_group,
                        service_log=service_log,
                    )
                    matrix["retests"].append(
                        {"boundary": boundary, **summary}
                    )
        finally:
            stop_service(process, output)
        matrix_path = args.output_dir / "matrix-summary.json"
        matrix_path.write_text(
            json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(markdown_report(matrix), encoding="utf-8")
    return matrix


def common_arguments(parser):
    parser.add_argument("--wav", type=Path, default=DEFAULT_WAV)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=float, default=600)
    parser.add_argument("--start-seconds", type=float, default=0)
    parser.add_argument("--tail-chunks", type=int, default=5)
    parser.add_argument("--drain-seconds", type=float, default=30)


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    point = subparsers.add_parser("point")
    common_arguments(point)
    point.add_argument("--correction-workers", type=int, required=True)
    point.add_argument("--concurrency", type=int, required=True)
    point.add_argument("--run-label", default="")

    matrix = subparsers.add_parser("matrix")
    common_arguments(matrix)
    matrix.add_argument("--workers", default="1,2,4,6")
    matrix.add_argument("--concurrencies", default="1,2,4,8,16,32,64")
    matrix.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.toml")
    matrix.add_argument("--conda", default="conda")
    matrix.add_argument("--conda-env", default="asr")
    matrix.add_argument("--port", type=int, default=8084)
    matrix.add_argument("--startup-timeout", type=float, default=600)
    matrix.add_argument("--report", type=Path, required=True)
    matrix.add_argument(
        "--retest-boundaries",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    args = parser.parse_args()

    if args.command == "point":
        asyncio.run(run_point(args))
    else:
        asyncio.run(run_matrix(args))


if __name__ == "__main__":
    main()
