"""Run dynamic detection directly against remote URLs without persisting video."""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import resource
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterable

import av

from app.core.config import settings
from app.models.task import FrameData
from app.services.slice_pipeline import SlicePipeline, SlicePipelineConfig
from harness.tools.baseline import summarize_intervals
from harness.tools.corpus import canonical_url


def _scan_process_entry(scanner, url, config, sender) -> None:
    try:
        sender.send(("COMPLETED", scanner(url, config)))
    except BaseException as exc:
        sender.send(("FAILED", f"{type(exc).__name__}: {exc}"))
    finally:
        sender.close()


def scan_with_hard_timeout(
    scanner,
    url: str,
    config: SlicePipelineConfig,
    *,
    timeout_seconds: float,
) -> dict:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_scan_process_entry,
        args=(scanner, url, config, sender),
        daemon=True,
    )
    process.start()
    sender.close()
    try:
        if not receiver.poll(timeout_seconds):
            process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            raise TimeoutError(f"视频流式扫描超过硬超时 {timeout_seconds}s")
        try:
            status, payload = receiver.recv()
        except EOFError as exc:
            raise RuntimeError(f"视频扫描子进程异常退出: exitcode={process.exitcode}") from exc
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        if status != "COMPLETED":
            raise RuntimeError(payload)
        return payload
    finally:
        receiver.close()


def _code_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).resolve().parents[2],
        ).stdout.strip()
    except Exception:
        return ""


def _algorithm_source_fingerprint() -> str:
    project_root = Path(__file__).resolve().parents[2]
    relative_paths = [
        Path("app/core/config.py"),
        Path("app/services/dynamic_detection.py"),
        Path("app/services/slice_pipeline.py"),
        Path("app/services/video_processor.py"),
        Path("harness/tools/detect.py"),
    ]
    digest = hashlib.sha256()
    for relative_path in relative_paths:
        digest.update(str(relative_path).encode("utf-8"))
        digest.update(b"\0")
        digest.update((project_root / relative_path).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class MetadataWriter:
    """Collect output metadata while intentionally discarding image bytes."""

    def __init__(self) -> None:
        self.slices: list[dict] = []
        self.dynamic_segments: list[dict] = []

    def write_image(self, *, frame_seq: int, snap_time: int, frame) -> None:
        self.slices.append({"frame_seq": int(frame_seq), "snap_time": int(snap_time)})

    def set_dynamic_segments(self, segments) -> None:
        self.dynamic_segments = [dict(segment) for segment in segments]


def stream_keyframes(url: str):
    container = av.open(url, options={"rw_timeout": "60000000"})
    try:
        stream = container.streams.video[0]
        stream.codec_context.skip_frame = "NONKEY"
        average_rate = float(stream.average_rate) if stream.average_rate else 30.0
        fallback_ms = 0
        for frame in container.decode(stream):
            if frame.pts is not None and frame.time_base is not None:
                timestamp_ms = int(round(float(frame.pts * frame.time_base) * 1000))
            else:
                timestamp_ms = fallback_ms
            fallback_ms = timestamp_ms + int(round(1000 / max(average_rate, 1.0)))
            yield FrameData(frame.to_ndarray(format="bgr24"), timestamp_ms=timestamp_ms)
    finally:
        container.close()


def stream_sampled_frames(url: str, *, sample_interval_ms: int):
    """Decode reference frames and emit at most one observation per configured interval."""
    if sample_interval_ms <= 0:
        raise ValueError("sample_interval_ms 必须大于 0")
    container = av.open(url, options={"rw_timeout": "60000000"})
    try:
        stream = container.streams.video[0]
        stream.codec_context.skip_frame = "NONREF"
        average_rate = float(stream.average_rate) if stream.average_rate else 30.0
        fallback_ms = 0
        last_sampled_ms: int | None = None
        for frame in container.decode(stream):
            if frame.pts is not None and frame.time_base is not None:
                timestamp_ms = int(round(float(frame.pts * frame.time_base) * 1000))
            else:
                timestamp_ms = fallback_ms
            fallback_ms = timestamp_ms + int(round(1000 / max(average_rate, 1.0)))
            if (
                last_sampled_ms is not None
                and timestamp_ms - last_sampled_ms < sample_interval_ms
            ):
                continue
            last_sampled_ms = timestamp_ms
            yield FrameData(frame.to_ndarray(format="bgr24"), timestamp_ms=timestamp_ms)
    finally:
        container.close()


def detect_frames(frames: Iterable[FrameData], config: SlicePipelineConfig) -> dict:
    writer = MetadataWriter()
    activity_observations = []
    pipeline = SlicePipeline(writer, config, activity_sink=activity_observations.append)
    timestamps_ms: list[int] = []
    for frame_data in frames:
        timestamps_ms.append(frame_data.timestamp_ms)
        pipeline.observe(frame_data)
    pipeline.finish(timestamps_ms[-1] if timestamps_ms else 0)
    near_timestamps = []
    for observation in activity_observations:
        if observation.is_active:
            continue
        changed_proximity = (
            observation.changed_pixel_ratio / config.changed_pixel_ratio
            if config.changed_pixel_ratio
            else 1.0
        )
        grid_proximity = (
            observation.active_grid_ratio / config.active_grid_ratio
            if config.active_grid_ratio
            else 1.0
        )
        if min(changed_proximity, grid_proximity) >= 0.5:
            near_timestamps.append(observation.timestamp_ms)
    near_threshold_windows = []
    audit_step_ms = max(config.sample_interval_ms, 1000)
    for timestamp_ms in near_timestamps:
        if (
            near_threshold_windows
            and timestamp_ms - near_threshold_windows[-1]["end_ms"] <= audit_step_ms
        ):
            near_threshold_windows[-1]["end_ms"] = timestamp_ms + audit_step_ms
        else:
            near_threshold_windows.append(
                {
                    "start_ms": timestamp_ms,
                    "end_ms": timestamp_ms + audit_step_ms,
                }
            )
    return {
        "status": "COMPLETED",
        "observation_count": pipeline.observation_count,
        "dynamic_segments": writer.dynamic_segments,
        "dynamic_segment_count": len(writer.dynamic_segments),
        "slices": writer.slices,
        "slice_count": len(writer.slices),
        "suppressed_candidate_count": pipeline.suppressed_candidate_count,
        "keyframe_intervals": summarize_intervals(timestamps_ms),
        "near_threshold_windows": near_threshold_windows,
        "first_timestamp_ms": timestamps_ms[0] if timestamps_ms else None,
        "last_timestamp_ms": timestamps_ms[-1] if timestamps_ms else None,
        "mp4_persisted": False,
    }


def scan_url(url: str, config: SlicePipelineConfig) -> dict:
    started = time.monotonic()
    result = detect_frames(
        stream_sampled_frames(url, sample_interval_ms=config.sample_interval_ms),
        config,
    )
    result["url"] = canonical_url(url, directory=False)
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result["peak_memory_bytes"] = int(peak_rss if sys.platform == "darwin" else peak_rss * 1024)
    return result


def _write_json(destination: Path, payload: dict) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")
    try:
        partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def scan_inventory(
    inventory_path: Path,
    output_path: Path,
    config: SlicePipelineConfig,
    *,
    selected_split: str | None = None,
    scanner: Callable[[str, SlicePipelineConfig], dict] = scan_url,
    max_workers: int = 1,
    max_retries: int = 1,
    run_id: str | None = None,
    timeout_seconds: float = 600,
) -> dict:
    inventory = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
    effective_run_id = run_id or inventory["run_id"]
    config_payload = asdict(config)
    config_fingerprint = hashlib.sha256(
        json.dumps(config_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if output_path.exists():
        result = json.loads(output_path.read_text(encoding="utf-8"))
        if result.get("config_fingerprint") not in {None, config_fingerprint}:
            raise ValueError("已有检测检查点使用不同配置，请使用新的输出路径")
        if result.get("run_id") not in {None, effective_run_id}:
            raise ValueError("已有检测检查点使用不同 run_id，请使用新的输出路径")
        if result.get("inventory_run_id") not in {None, inventory["run_id"]}:
            raise ValueError("已有检测检查点属于不同 inventory，请使用新的输出路径")
        result["run_id"] = effective_run_id
        result["inventory_run_id"] = inventory["run_id"]
        result["algorithm_source_fingerprint"] = _algorithm_source_fingerprint()
    else:
        result = {
            "schema_version": 1,
            "run_id": effective_run_id,
            "inventory_run_id": inventory["run_id"],
            "inventory_fingerprint": inventory["inventory_fingerprint"],
            "algorithm_version": settings.APP_VERSION,
            "code_commit": _code_commit(),
            "algorithm_source_fingerprint": _algorithm_source_fingerprint(),
            "effective_config": config_payload,
            "config_fingerprint": config_fingerprint,
            "items": [],
        }
    completed = {
        (item["url"], item.get("resource_fingerprint"))
        for item in result["items"]
        if item.get("status") == "COMPLETED"
    }
    candidates = [
        item
        for item in inventory["items"]
        if item["probe_status"] == "COMPLETED"
        and (selected_split is None or item["split"] == selected_split)
    ]
    pending = [
        item
        for item in candidates
        if (item["url"], item["resource_fingerprint"]) not in completed
    ]

    def execute(item):
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                if scanner is scan_url:
                    detected = scan_with_hard_timeout(
                        scanner,
                        item["url"],
                        config,
                        timeout_seconds=timeout_seconds,
                    )
                else:
                    detected = scanner(item["url"], config)
                detected["attempt_count"] = attempt + 1
                return item, detected
            except Exception as exc:
                last_error = exc
        return item, {
            "url": item["url"],
            "status": "FAILED",
            "error_reason": str(last_error),
            "dynamic_segments": [],
            "slices": [],
            "mp4_persisted": False,
            "attempt_count": max_retries + 1,
        }

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        future_map = {}
        for index, item in enumerate(pending, start=1):
            print(f"[{index}/{len(pending)}] detect {item['course_name']}", flush=True)
            future_map[executor.submit(execute, item)] = item
        for future in as_completed(future_map):
            item, detected = future.result()
            detected["course_name"] = item["course_name"]
            detected["split"] = item["split"]
            detected["resource_fingerprint"] = item["resource_fingerprint"]
            result["items"] = [existing for existing in result["items"] if existing["url"] != item["url"]]
            result["items"].append(detected)
            _write_json(output_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="流式检测 P 视频动态区间，不保存 MP4")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url")
    group.add_argument("--inventory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=["CALIBRATION", "HOLDOUT"])
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--run-id")
    parser.add_argument("--timeout-seconds", type=float, default=600)
    args = parser.parse_args()

    config = SlicePipelineConfig.from_settings(settings)
    if args.url:
        result = scan_url(args.url, config)
        result["run_id"] = args.run_id or ""
        result["algorithm_version"] = settings.APP_VERSION
        result["code_commit"] = _code_commit()
        result["algorithm_source_fingerprint"] = _algorithm_source_fingerprint()
        result["effective_config"] = asdict(config)
        _write_json(args.output, result)
    else:
        result = scan_inventory(
            args.inventory,
            args.output,
            config,
            selected_split=args.split,
            max_workers=args.max_workers,
            max_retries=args.max_retries,
            run_id=args.run_id,
            timeout_seconds=args.timeout_seconds,
        )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "completed": sum(item.get("status") == "COMPLETED" for item in result.get("items", [])),
                "failed": sum(item.get("status") == "FAILED" for item in result.get("items", [])),
                "mp4_persisted": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
