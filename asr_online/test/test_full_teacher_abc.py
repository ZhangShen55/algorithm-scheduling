"""Run the complete teacher audio against v1.0.1, v1.0.2 and v1.0.3."""

import argparse
import asyncio
import difflib
import json
import os
import string
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import websockets
from websockets.exceptions import ConnectionClosed


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAV = ROOT / "test" / "教师1_16k.wav"
CHUNK_SAMPLES = 960 * 8
SAMPLE_WIDTH = 2
ROUTES = {
    "A": "v1.0.1",
    "B": "v1.0.2",
    "C": "v1.0.3",
}
PUNCTUATION = set(string.punctuation + "，。！？；：、“”‘’（）【】《》…—")


@dataclass
class RouteState:
    label: str
    version: str
    url: str
    output_path: Path
    monitor_path: Path
    responses: int = 0
    finished: int = 0
    sent_chunks: int = 0
    audio_chunks_sent: int = 0
    tail_chunks_sent: int = 0
    total_chunks: int = 0
    last_ed: float | None = None
    last_response_at: float | None = None
    first_response_at: float | None = None
    max_response_gap: float = 0.0
    finished_delays: list[float] = field(default_factory=list)
    finished_waits: list[float] = field(default_factory=list)
    last_response: dict | None = None
    failure: str | None = None
    stalled: bool = False
    sender_done_at: float | None = None
    ended_at: float | None = None
    resource_samples: int = 0
    resource_sample_failures: int = 0
    max_rss_kib: int = 0
    max_gpu_memory_mib: int = 0
    max_gpu_utilization_percent: int = 0
    last_resource: dict | None = None
    connection_close: dict | None = None
    started_at: float = field(default_factory=time.monotonic)


def iter_pcm_chunks(
    wav_path: Path,
    start_seconds: float = 0.0,
    max_seconds: float | None = None,
):
    with wave.open(str(wav_path), "rb") as wav:
        if (
            wav.getnchannels() != 1
            or wav.getsampwidth() != SAMPLE_WIDTH
            or wav.getframerate() != 16000
        ):
            raise ValueError("测试音频必须是 16 kHz、单声道、16-bit WAV")
        wav.setpos(min(wav.getnframes(), int(start_seconds * wav.getframerate())))
        remaining = None if max_seconds is None else int(max_seconds * wav.getframerate())
        while remaining is None or remaining > 0:
            frames = CHUNK_SAMPLES if remaining is None else min(CHUNK_SAMPLES, remaining)
            chunk = wav.readframes(frames)
            if not chunk:
                return
            if remaining is not None:
                remaining -= len(chunk) // SAMPLE_WIDTH
            if len(chunk) < CHUNK_SAMPLES * SAMPLE_WIDTH:
                chunk += b"\x00" * (CHUNK_SAMPLES * SAMPLE_WIDTH - len(chunk))
            yield chunk


def wav_info(wav_path: Path, start_seconds: float = 0.0, max_seconds: float | None = None):
    with wave.open(str(wav_path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != SAMPLE_WIDTH or wav.getframerate() != 16000:
            raise ValueError("测试音频必须是 16 kHz、单声道、16-bit WAV")
        start_frame = min(wav.getnframes(), int(start_seconds * wav.getframerate()))
        frames = wav.getnframes() - start_frame
        if max_seconds is not None:
            frames = min(frames, int(max_seconds * wav.getframerate()))
        return {
            "duration_seconds": round(frames / wav.getframerate(), 3),
            "frames": frames,
            "total_chunks": (frames + CHUNK_SAMPLES - 1) // CHUNK_SAMPLES,
        }


def append_monitor(path: Path, payload: dict):
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(payload, ensure_ascii=False) + "\n")


async def run_route(
    state: RouteState,
    wav_path: Path,
    tail_chunks: int,
    drain_seconds: float,
    no_response_timeout: float,
    max_runtime: float,
    start_seconds: float,
    max_seconds: float | None,
    max_inflight: int,
    response_paced: bool,
    response_window: int,
    send_interval: float,
    remote_host: str | None,
    remote_pid_file: str,
    resource_interval: float,
    open_timeout: float,
):
    state.started_at = time.monotonic()
    state.monitor_path.parent.mkdir(parents=True, exist_ok=True)
    state.output_path.parent.mkdir(parents=True, exist_ok=True)
    state.output_path.write_text("", encoding="utf-8")
    state.monitor_path.write_text("", encoding="utf-8")
    response_file = state.output_path.open("a", encoding="utf-8")
    receiver = None
    watchdog = None
    resource_sampler = None
    done = asyncio.Event()
    response_progress = asyncio.Event()
    pending_chunks = 0

    if response_paced:
        response_window = 1

    async def receive_responses(websocket):
        nonlocal pending_chunks
        try:
            while True:
                raw = await websocket.recv()
                received_at = time.monotonic()
                response = json.loads(raw)
                if not isinstance(response, dict):
                    raise ValueError("响应不是 JSON object")
                required = {"key", "text", "finished", "bg", "ed"}
                if set(response) != required:
                    raise ValueError(f"响应字段不兼容: {sorted(response)}")
                response_file.write(json.dumps(response, ensure_ascii=False) + "\n")
                response_file.flush()
                state.responses += 1
                state.finished += int(response["finished"] is True)
                state.last_ed = response["ed"]
                state.last_response = response
                if state.first_response_at is None:
                    state.first_response_at = received_at
                response_gap = None
                if state.last_response_at is not None:
                    response_gap = received_at - state.last_response_at
                    state.max_response_gap = max(
                        state.max_response_gap,
                        response_gap,
                    )
                state.last_response_at = received_at
                if pending_chunks > 0:
                    pending_chunks -= 1
                if response["finished"] is True:
                    state.finished_delays.append(received_at - state.started_at)
                    if response_gap is not None:
                        state.finished_waits.append(response_gap)
                response_progress.set()
                append_monitor(
                    state.monitor_path,
                    {
                        "event": "response",
                        "at": time.time(),
                        "response_index": state.responses,
                        "finished": response["finished"],
                        "bg": response["bg"],
                        "ed": response["ed"],
                        "key": response["key"],
                    },
                )
        except ConnectionClosed as exc:
            state.connection_close = {
                "code": exc.code,
                "reason": exc.reason,
            }
            normal_after_complete_send = (
                state.sender_done_at is not None
                and state.tail_chunks_sent >= tail_chunks
                and exc.code in {1000, 1001}
            )
            if not done.is_set() and state.failure is None and not normal_after_complete_send:
                state.failure = "websocket_closed_before_drain"
        except Exception as exc:  # preserve route-local failure and finish others
            state.failure = f"receiver: {type(exc).__name__}: {exc}"

    async def wait_for_new_response(previous_count: int):
        while state.responses <= previous_count:
            response_progress.clear()
            if state.responses > previous_count:
                return
            try:
                await asyncio.wait_for(
                    response_progress.wait(),
                    timeout=no_response_timeout,
                )
            except asyncio.TimeoutError:
                state.stalled = True
                state.failure = (
                    f"no_response_for:{no_response_timeout}s;"
                    f"last_sent_chunk:{state.sent_chunks};"
                    f"last_response:{state.last_response}"
                )
                raise

    async def watch_progress():
        while not done.is_set():
            await asyncio.sleep(5)
            now = time.monotonic()
            if now - state.started_at > max_runtime:
                state.failure = f"max_runtime_exceeded:{max_runtime}"
                state.stalled = True
                return
            if state.sent_chunks:
                gap = now - (state.last_response_at or state.started_at)
                if gap > no_response_timeout:
                    state.failure = f"no_response_for:{round(gap, 1)}s"
                    state.stalled = True
                    return
            append_monitor(
                state.monitor_path,
                {
                    "event": "progress",
                    "at": time.time(),
                    "sent_chunks": state.sent_chunks,
                    "total_chunks": state.total_chunks,
                    "responses": state.responses,
                    "finished": state.finished,
                    "last_ed": state.last_ed,
                    "last_response_gap": (
                        None
                        if state.last_response_at is None
                        else round(now - state.last_response_at, 3)
                    ),
                },
            )

    async def sample_remote_resources():
        if not remote_host:
            return
        command = (
            f"pid=$(cat {remote_pid_file} 2>/dev/null || true); "
            "alive=0; [ -n \"$pid\" ] && kill -0 \"$pid\" 2>/dev/null && alive=1; "
            "rss=$(ps -o rss= -p \"$pid\" 2>/dev/null | tr -d ' '); "
            "port=$(ss -ltn 'sport = :8084' 2>/dev/null | awk 'NR>1 {n++} END {print n+0}'); "
            "gpu=$(nvidia-smi -i 0 --query-gpu=memory.used,utilization.gpu "
            "--format=csv,noheader,nounits 2>/dev/null | head -n 1 | tr -d ' '); "
            "load=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null); "
            "available=$(awk '/MemAvailable/ {print $2}' /proc/meminfo 2>/dev/null); "
            "printf '%s|%s|%s|%s|%s|%s|%s\\n' "
            "\"$pid\" \"$alive\" \"$rss\" \"$port\" \"$gpu\" \"$load\" \"$available\""
        )
        while not done.is_set():
            payload = {"event": "resource", "at": time.time()}
            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=5",
                    remote_host,
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
                if process.returncode != 0:
                    raise RuntimeError(stderr.decode("utf-8", errors="replace").strip())
                values = stdout.decode("utf-8", errors="replace").strip().split("|")
                if len(values) != 7:
                    raise ValueError(f"unexpected resource sample: {values}")
                gpu_values = values[4].split(",") if values[4] else []
                payload.update(
                    {
                        "pid": int(values[0]) if values[0] else None,
                        "process_alive": values[1] == "1",
                        "rss_kib": int(values[2]) if values[2] else None,
                        "port_8084_listening": int(values[3]) > 0,
                        "gpu_0_memory_mib": int(gpu_values[0]) if gpu_values else None,
                        "gpu_0_utilization_percent": (
                            int(gpu_values[1]) if len(gpu_values) > 1 else None
                        ),
                        "load_1m": float(values[5]) if values[5] else None,
                        "memory_available_kib": int(values[6]) if values[6] else None,
                    }
                )
                state.resource_samples += 1
                state.last_resource = payload
                state.max_rss_kib = max(state.max_rss_kib, payload["rss_kib"] or 0)
                state.max_gpu_memory_mib = max(
                    state.max_gpu_memory_mib,
                    payload["gpu_0_memory_mib"] or 0,
                )
                state.max_gpu_utilization_percent = max(
                    state.max_gpu_utilization_percent,
                    payload["gpu_0_utilization_percent"] or 0,
                )
            except Exception as exc:
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()
                state.resource_sample_failures += 1
                payload["error"] = f"{type(exc).__name__}: {exc}"
            append_monitor(state.monitor_path, payload)
            try:
                await asyncio.wait_for(done.wait(), timeout=resource_interval)
            except asyncio.TimeoutError:
                pass

    try:
        async with websockets.connect(
            state.url,
            max_size=None,
            open_timeout=open_timeout,
            ping_interval=None,
        ) as websocket:
            receiver = asyncio.create_task(receive_responses(websocket))
            watchdog = asyncio.create_task(watch_progress())
            resource_sampler = asyncio.create_task(sample_remote_resources())
            for chunk in iter_pcm_chunks(wav_path, start_seconds, max_seconds):
                if state.failure:
                    raise RuntimeError(state.failure)
                if response_window > 0:
                    while pending_chunks >= response_window:
                        await wait_for_new_response(state.responses)
                else:
                    while (
                        state.audio_chunks_sent * (CHUNK_SAMPLES / 16000)
                        - float(state.last_ed or 0.0)
                        >= max_inflight * (CHUNK_SAMPLES / 16000)
                    ):
                        response_progress.clear()
                        if (
                            state.audio_chunks_sent * (CHUNK_SAMPLES / 16000)
                            - float(state.last_ed or 0.0)
                            < max_inflight * (CHUNK_SAMPLES / 16000)
                        ):
                            break
                        await wait_for_new_response(state.responses)
                if response_window > 0:
                    pending_chunks += 1
                try:
                    await asyncio.wait_for(websocket.send(chunk), timeout=60)
                except Exception:
                    if response_window > 0:
                        pending_chunks -= 1
                    raise
                state.sent_chunks += 1
                state.audio_chunks_sent += 1
                if send_interval > 0:
                    await asyncio.sleep(send_interval)
            state.sender_done_at = time.monotonic()
            append_monitor(
                state.monitor_path,
                {
                    "event": "sender_done",
                    "at": time.time(),
                    "sent_chunks": state.sent_chunks,
                },
            )
            for _ in range(max(1, tail_chunks)):
                if response_window > 0:
                    while pending_chunks >= response_window:
                        await wait_for_new_response(state.responses)
                    pending_chunks += 1
                try:
                    await asyncio.wait_for(
                        websocket.send(b"\x00" * CHUNK_SAMPLES * SAMPLE_WIDTH),
                        timeout=60,
                    )
                except Exception:
                    if response_window > 0:
                        pending_chunks -= 1
                    raise
                state.sent_chunks += 1
                state.tail_chunks_sent += 1

            while response_window > 0 and pending_chunks > 0:
                await wait_for_new_response(state.responses)

            while True:
                if state.failure:
                    break
                quiet_for = (
                    float("inf")
                    if state.last_response_at is None
                    else time.monotonic() - state.last_response_at
                )
                if quiet_for >= drain_seconds:
                    break
                await asyncio.sleep(0.5)
            done.set()
            await websocket.close()
    except Exception as exc:
        if state.failure is None:
            state.failure = f"session: {type(exc).__name__}: {exc}"
    finally:
        done.set()
        state.ended_at = time.monotonic()
        if watchdog is not None:
            watchdog.cancel()
        if receiver is not None:
            receiver.cancel()
        for task in (watchdog, receiver, resource_sampler):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, ConnectionClosed):
                    pass
        response_file.close()


def finished_records(path: Path):
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if item.get("finished") is True:
            records.append(item)
    return records


def text_diff(left: str, right: str):
    matcher = difflib.SequenceMatcher(a=left, b=right)
    opcodes = []
    counts = {"equal": 0, "insert": 0, "delete": 0, "replace": 0}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        counts[tag] += max(i2 - i1, j2 - j1)
        if tag != "equal":
            opcodes.append(
                {
                    "operation": tag,
                    "left_start": i1,
                    "left_end": i2,
                    "right_start": j1,
                    "right_end": j2,
                    "left": left[i1:i2],
                    "right": right[j1:j2],
                }
            )
    return counts, opcodes


def punctuation_count(text: str):
    return sum(character in PUNCTUATION for character in text)


def covered_seconds(records):
    intervals = sorted(
        (float(item["bg"]), float(item["ed"]))
        for item in records
        if item.get("finished") is True and float(item["ed"]) > float(item["bg"])
    )
    if not intervals:
        return 0.0
    total = 0.0
    current_start, current_end = intervals[0]
    for start, end in intervals[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
            continue
        total += current_end - current_start
        current_start, current_end = start, end
    return total + current_end - current_start


def timeline_metrics(records, audio_duration=None):
    finished = [item for item in records if item.get("finished") is True]
    backwards = []
    overlaps = []
    holes = []
    previous_bg = None
    previous_ed = None
    for index, item in enumerate(finished):
        bg, ed = float(item["bg"]), float(item["ed"])
        if previous_bg is not None and bg < previous_bg:
            backwards.append(index)
        if previous_ed is not None:
            if bg < previous_ed:
                overlaps.append(index)
            if bg > previous_ed + 0.02:
                holes.append(index)
        previous_bg, previous_ed = bg, ed
    coverage = covered_seconds(finished)
    return {
        "finished_count": len(finished),
        "timestamp_backwards": backwards,
        "timestamp_overlaps": overlaps,
        "timestamp_holes": holes,
        "last_finished_ed": finished[-1]["ed"] if finished else None,
        "covered_seconds": round(coverage, 3),
        "audio_coverage_ratio": (
            round(min(coverage, audio_duration) / audio_duration, 6)
            if audio_duration
            else None
        ),
        "punctuation_count": sum(punctuation_count(item["text"]) for item in finished),
        "duplicate_finished_text": sum(
            finished[index]["text"] == finished[index - 1]["text"]
            and bool(finished[index]["text"])
            for index in range(1, len(finished))
        ),
    }


def interval_overlap(left_item, right_item):
    return max(
        0.0,
        min(float(left_item["ed"]), float(right_item["ed"]))
        - max(float(left_item["bg"]), float(right_item["bg"])),
    )


def match_by_time(left, right):
    available_right = set(range(len(right)))
    matches = []
    for left_index, left_item in enumerate(left):
        candidates = [
            (interval_overlap(left_item, right[right_index]), right_index)
            for right_index in available_right
        ]
        overlap, right_index = max(candidates, default=(0.0, None))
        if right_index is None or overlap <= 0:
            matches.append((left_index, None, 0.0))
            continue
        available_right.remove(right_index)
        matches.append((left_index, right_index, overlap))
    matches.extend((None, right_index, 0.0) for right_index in sorted(available_right))
    return matches


def make_comparison(
    left_label,
    left_path,
    right_label,
    right_path,
    output_path,
    audio_duration,
):
    left = finished_records(left_path)
    right = finished_records(right_path)
    rows = []
    for index, (left_index, right_index, overlap) in enumerate(match_by_time(left, right)):
        left_item = None if left_index is None else left[left_index]
        right_item = None if right_index is None else right[right_index]
        left_text = "" if left_item is None else left_item["text"]
        right_text = "" if right_item is None else right_item["text"]
        counts, changes = text_diff(left_text, right_text)
        punctuation_changes = sum(
            punctuation_count(change["left"]) + punctuation_count(change["right"])
            for change in changes
        )
        rows.append(
            {
                "index": index,
                "match_method": "time_overlap" if overlap > 0 else "unmatched",
                "overlap_seconds": round(overlap, 3),
                "left": left_label,
                "right": right_label,
                "left_index": left_index,
                "right_index": right_index,
                "left_key": None if left_item is None else left_item["key"],
                "right_key": None if right_item is None else right_item["key"],
                "left_bg": None if left_item is None else left_item["bg"],
                "left_ed": None if left_item is None else left_item["ed"],
                "right_bg": None if right_item is None else right_item["bg"],
                "right_ed": None if right_item is None else right_item["ed"],
                "text_equal": left_text == right_text,
                "text_diff_counts": counts,
                "correction_positions": changes,
                "left_punctuation_count": punctuation_count(left_text),
                "right_punctuation_count": punctuation_count(right_text),
                "punctuation_change_count": punctuation_changes,
            }
        )
    with output_path.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {
        "left": left_label,
        "right": right_label,
        "left_finished": len(left),
        "right_finished": len(right),
        "different_segments": sum(not row["text_equal"] for row in rows),
        "time_overlap_matches": sum(row["match_method"] == "time_overlap" for row in rows),
        "unmatched_left": sum(
            row["left_index"] is not None and row["right_index"] is None for row in rows
        ),
        "unmatched_right": sum(
            row["right_index"] is not None and row["left_index"] is None for row in rows
        ),
        "punctuation_change_count": sum(row["punctuation_change_count"] for row in rows),
        "left_timeline": timeline_metrics(left, audio_duration),
        "right_timeline": timeline_metrics(right, audio_duration),
        "comparison_file": str(output_path),
    }


async def main_async(args):
    info = wav_info(args.wav, args.start_seconds, args.max_seconds)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    states = {
        label: RouteState(
            label=label,
            version=version,
            url=f"{args.base_url.rstrip('/')}/v{version[1:]}/seacraft_asr_online",
            output_path=output_dir / f"{label}_{version.replace('.', '_')}_teacher1_full.jsonl",
            monitor_path=output_dir / f"{label}_monitor.jsonl",
            total_chunks=info["total_chunks"],
        )
        for label, version in ROUTES.items()
    }
    for label, state in states.items():
        print(f"[{label}] starting {state.version}: {state.url}", flush=True)
        await run_route(
            state,
            args.wav,
            args.tail_chunks,
            args.drain_seconds,
            args.no_response_timeout,
            args.max_runtime,
            args.start_seconds,
            args.max_seconds,
            args.max_inflight,
            args.response_paced,
            args.response_window,
            (
                args.v1_send_interval
                if label == "A"
                else args.corrected_send_interval
            ),
            args.remote_host,
            args.remote_pid_file,
            args.resource_interval,
            args.open_timeout,
        )
        print(
            f"[{label}] finished: audio={state.audio_chunks_sent}/{state.total_chunks}, "
            f"responses={state.responses}, finished={state.finished}, failure={state.failure}",
            flush=True,
        )

    summaries = {}
    for label, state in states.items():
        records = json.loads("[" + ",".join(
            line for line in state.output_path.read_text(encoding="utf-8").splitlines()
        ) + "]") if state.output_path.read_text(encoding="utf-8").strip() else []
        summary = {
            "label": label,
            "version": state.version,
            "url": state.url,
            "wav": str(args.wav),
            **info,
            "sent_chunks": state.sent_chunks,
            "audio_chunks_sent": state.audio_chunks_sent,
            "tail_chunks_sent": state.tail_chunks_sent,
            "responses": state.responses,
            "finished": state.finished,
            "last_ed": state.last_ed,
            "elapsed_seconds": round((state.ended_at or time.monotonic()) - state.started_at, 3),
            "max_response_gap_seconds": round(state.max_response_gap, 3),
            "finished_delay_seconds": {
                "first": round(state.finished_delays[0], 3) if state.finished_delays else None,
                "average": round(sum(state.finished_delays) / len(state.finished_delays), 3)
                if state.finished_delays else None,
                "max": round(max(state.finished_delays), 3) if state.finished_delays else None,
            },
            "max_correction_wait_seconds": (
                round(max(state.finished_waits), 3) if state.finished_waits else None
            ),
            "complete_audio_sent": state.audio_chunks_sent == info["total_chunks"],
            "drain_completed": state.failure is None and state.tail_chunks_sent == args.tail_chunks,
            "stalled": state.stalled,
            "failure": state.failure,
            "connection_close": state.connection_close,
            "last_response": state.last_response,
            "timeline": timeline_metrics(records, info["duration_seconds"]),
            "resources": {
                "samples": state.resource_samples,
                "sample_failures": state.resource_sample_failures,
                "max_rss_kib": state.max_rss_kib,
                "max_gpu_0_memory_mib": state.max_gpu_memory_mib,
                "max_gpu_0_utilization_percent": state.max_gpu_utilization_percent,
                "last": state.last_resource,
            },
            "output": str(state.output_path),
            "monitor": str(state.monitor_path),
        }
        (output_dir / f"{label}_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        summaries[label] = summary

    comparisons = []
    for left, right in (("A", "B"), ("A", "C"), ("B", "C")):
        path = output_dir / f"{left}{right}_compare.jsonl"
        comparisons.append(
            make_comparison(
                left,
                states[left].output_path,
                right,
                states[right].output_path,
                path,
                info["duration_seconds"],
            )
        )
    full = {
        "wav": str(args.wav),
        "base_url": args.base_url,
        "execution_mode": "sequential",
        "send_mode": (
            "response-paced"
            if args.response_paced
            else f"response-window:{args.response_window}"
            if args.response_window > 0
            else "timestamp-window"
        ),
        "send_intervals": {
            "A": args.v1_send_interval,
            "B": args.corrected_send_interval,
            "C": args.corrected_send_interval,
        },
        "execution_order": list(ROUTES),
        "complete": all(
            summary["complete_audio_sent"]
            and summary["drain_completed"]
            and summary["failure"] is None
            for summary in summaries.values()
        ),
        "generated_at": time.time(),
        "routes": summaries,
        "comparisons": comparisons,
        "watchdog": {
            "no_response_timeout_seconds": args.no_response_timeout,
            "max_runtime_seconds": args.max_runtime,
            "drain_seconds": args.drain_seconds,
        },
    }
    (output_dir / "full_test_summary.json").write_text(
        json.dumps(full, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(full, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", type=Path, default=Path(os.getenv("ASR_TEST_WAV", DEFAULT_WAV)))
    parser.add_argument("--base-url", default=os.getenv("ASR_TEST_BASE_URL", "ws://192.168.29.12:8084"))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "test")
    parser.add_argument("--tail-chunks", type=int, default=5)
    parser.add_argument("--start-seconds", type=float, default=0.0)
    parser.add_argument("--max-seconds", type=float, default=None)
    parser.add_argument("--max-inflight", type=int, default=4)
    parser.add_argument("--response-paced", action="store_true")
    parser.add_argument("--response-window", type=int, default=0)
    parser.add_argument("--v1-send-interval", type=float, default=0.0)
    parser.add_argument("--corrected-send-interval", type=float, default=0.0)
    parser.add_argument("--drain-seconds", type=float, default=30)
    parser.add_argument("--no-response-timeout", type=float, default=600)
    parser.add_argument("--max-runtime", type=float, default=4 * 60 * 60)
    parser.add_argument(
        "--remote-host",
        default=os.getenv("ASR_TEST_REMOTE_HOST", "root@192.168.29.12"),
    )
    parser.add_argument(
        "--remote-pid-file",
        default=os.getenv("ASR_TEST_REMOTE_PID_FILE", "/tmp/asr_online_vad_20261009.pid"),
    )
    parser.add_argument("--resource-interval", type=float, default=15.0)
    parser.add_argument("--open-timeout", type=float, default=120.0)
    args = parser.parse_args()
    if args.response_window < 0:
        parser.error("--response-window 不能为负数")
    if args.v1_send_interval < 0 or args.corrected_send_interval < 0:
        parser.error("发送间隔不能为负数")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
