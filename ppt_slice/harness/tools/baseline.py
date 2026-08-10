"""Measure the legacy slice behavior by streaming remote videos in memory."""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path
from typing import Callable

import av
import numpy as np

from app.services.image_compare import compare_images


class BaselineAccumulator:
    """Pure in-memory reproduction of the legacy stable-frame decision."""

    def __init__(
        self,
        *,
        contiguous_threshold: float = 0.99,
        saved_threshold: float = 0.98,
        comparator: Callable = compare_images,
    ) -> None:
        self.contiguous_threshold = contiguous_threshold
        self.saved_threshold = saved_threshold
        self.comparator = comparator
        self.last_frame = None
        self.saved_frame = None
        self.observation_count = 0
        self.slice_timestamps_ms: list[int] = []

    def observe(self, timestamp_ms: int, frame) -> None:
        if self.last_frame is not None:
            contiguous_similarity = self.comparator(self.last_frame, frame)
            if self.saved_frame is None and contiguous_similarity > self.contiguous_threshold:
                self.saved_frame = self.last_frame
                self.slice_timestamps_ms.append(int(timestamp_ms))
            elif contiguous_similarity > self.contiguous_threshold:
                saved_similarity = self.comparator(self.saved_frame, frame)
                if saved_similarity < self.saved_threshold:
                    self.saved_frame = frame
                    self.slice_timestamps_ms.append(int(timestamp_ms))
        self.last_frame = frame
        self.observation_count += 1


def summarize_intervals(timestamps_ms: list[int]) -> dict:
    intervals = np.diff(np.asarray(timestamps_ms, dtype=np.int64))
    if intervals.size == 0:
        return {"count": 0, "min_ms": None, "max_ms": None, "p50_ms": None, "p95_ms": None}
    return {
        "count": int(intervals.size),
        "min_ms": int(intervals.min()),
        "max_ms": int(intervals.max()),
        "p50_ms": int(np.percentile(intervals, 50)),
        "p95_ms": int(np.percentile(intervals, 95)),
    }


def summarize_slice_density(
    slice_timestamps_ms: list[int],
    *,
    duration_ms: int,
    window_ms: int = 60000,
    dense_slice_count: int = 5,
) -> dict:
    if duration_ms < 0 or window_ms <= 0 or dense_slice_count <= 0:
        raise ValueError("时长、窗口和密集切片阈值必须有效")
    window_count = math.ceil(duration_ms / window_ms) if duration_ms else 0
    counts = [0] * window_count
    for timestamp_ms in slice_timestamps_ms:
        if window_count == 0:
            break
        index = min(max(int(timestamp_ms), 0) // window_ms, window_count - 1)
        counts[index] += 1
    windows = [
        {
            "start_ms": index * window_ms,
            "end_ms": min((index + 1) * window_ms, duration_ms),
            "count": count,
        }
        for index, count in enumerate(counts)
    ]
    return {
        "average_slices_per_minute": (
            round(len(slice_timestamps_ms) / (duration_ms / 60000), 6)
            if duration_ms
            else 0.0
        ),
        "minute_slice_counts": windows,
        "dense_slice_windows": [
            window for window in windows if window["count"] >= dense_slice_count
        ],
    }


def scan_url(
    url: str,
    *,
    contiguous_threshold: float = 0.99,
    saved_threshold: float = 0.98,
) -> dict:
    started = time.monotonic()
    timestamps_ms: list[int] = []
    accumulator = BaselineAccumulator(
        contiguous_threshold=contiguous_threshold,
        saved_threshold=saved_threshold,
    )
    container = av.open(url, options={"rw_timeout": "60000000"})
    try:
        stream = container.streams.video[0]
        stream.codec_context.skip_frame = "NONKEY"
        fallback_ms = 0
        average_rate = float(stream.average_rate) if stream.average_rate else 30.0
        for frame in container.decode(stream):
            if frame.pts is not None and frame.time_base is not None:
                timestamp_ms = int(round(float(frame.pts * frame.time_base) * 1000))
            else:
                timestamp_ms = fallback_ms
            fallback_ms = timestamp_ms + int(round(1000 / max(average_rate, 1.0)))
            image = frame.to_ndarray(format="bgr24")
            timestamps_ms.append(timestamp_ms)
            accumulator.observe(timestamp_ms, image)
    finally:
        container.close()

    result = {
        "url": url,
        "status": "COMPLETED",
        "observation_count": accumulator.observation_count,
        "slice_count": len(accumulator.slice_timestamps_ms),
        "slice_timestamps_ms": accumulator.slice_timestamps_ms,
        "keyframe_intervals": summarize_intervals(timestamps_ms),
        "first_timestamp_ms": timestamps_ms[0] if timestamps_ms else None,
        "last_timestamp_ms": timestamps_ms[-1] if timestamps_ms else None,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "mp4_persisted": False,
    }
    duration_ms = max((result["last_timestamp_ms"] or 0) - (result["first_timestamp_ms"] or 0), 0)
    result.update(
        summarize_slice_density(
            accumulator.slice_timestamps_ms,
            duration_ms=duration_ms,
        )
    )
    return result


def _write_json(destination: Path, payload: dict) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")
    try:
        partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def scan_inventory(inventory_path: Path, output_path: Path, *, selected_split: str | None = None) -> dict:
    inventory = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
    if output_path.exists():
        result = json.loads(output_path.read_text(encoding="utf-8"))
    else:
        result = {
            "schema_version": 1,
            "run_id": inventory["run_id"],
            "inventory_fingerprint": inventory["inventory_fingerprint"],
            "baseline_algorithm": "legacy-keyframe-similarity",
            "items": [],
        }
    completed = {item["url"] for item in result["items"] if item.get("status") == "COMPLETED"}

    candidates = [
        item
        for item in inventory["items"]
        if item["probe_status"] == "COMPLETED"
        and (selected_split is None or item["split"] == selected_split)
    ]
    for index, item in enumerate(candidates, start=1):
        if item["url"] in completed:
            continue
        print(f"[{index}/{len(candidates)}] baseline {item['course_name']}", flush=True)
        try:
            scanned = scan_url(item["url"])
        except Exception as exc:
            scanned = {
                "url": item["url"],
                "status": "FAILED",
                "error_reason": str(exc),
                "mp4_persisted": False,
            }
        scanned["course_name"] = item["course_name"]
        scanned["split"] = item["split"]
        scanned["resource_fingerprint"] = item["resource_fingerprint"]
        result["items"] = [existing for existing in result["items"] if existing["url"] != item["url"]]
        result["items"].append(scanned)
        _write_json(output_path, result)
    for item in result["items"]:
        if item.get("status") != "COMPLETED":
            continue
        duration_ms = max(
            int(item.get("last_timestamp_ms") or 0)
            - int(item.get("first_timestamp_ms") or 0),
            0,
        )
        item.update(
            summarize_slice_density(
                item.get("slice_timestamps_ms", []),
                duration_ms=duration_ms,
            )
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="流式统计旧 PPT 切片基线，不保存 MP4 或切片图片")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url")
    group.add_argument("--inventory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=["CALIBRATION", "HOLDOUT"])
    args = parser.parse_args()

    if args.url:
        result = scan_url(args.url)
    else:
        result = scan_inventory(args.inventory, args.output, selected_split=args.split)
    _write_json(args.output, result)
    print(json.dumps({"output": str(args.output), "mp4_persisted": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
