"""Extract sparse static evidence directly from remote videos."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import cv2
import numpy as np


def evidence_timestamps(
    start_ms: int,
    end_ms: int,
    *,
    context_ms: int = 5000,
    duration_ms: int | None = None,
) -> list[int]:
    values = [
        max(0, int(start_ms) - context_ms),
        int(start_ms),
        int(start_ms + (end_ms - start_ms) // 2),
        max(int(start_ms), int(end_ms) - 1),
        int(end_ms) + context_ms,
    ]
    if duration_ms is not None:
        # Container duration can extend beyond the final decodable frame.
        last_valid_ms = max(0, int(duration_ms) - 1000)
        values = [min(value, last_valid_ms) for value in values]
    return list(dict.fromkeys(values))


def extract_frame(url: str, timestamp_ms: int, destination: Path) -> None:
    destination = Path(destination)
    if destination.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise ValueError("证据只能保存为静态图片")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.stem}.part{destination.suffix}")
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-ss",
        f"{timestamp_ms / 1000:.3f}",
        "-i",
        url,
        "-frames:v",
        "1",
        "-vf",
        "scale=640:-2",
        "-q:v",
        "3",
        str(partial),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=60)
        if not partial.is_file():
            raise RuntimeError(f"远程视频在 {timestamp_ms}ms 未输出证据帧")
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def _contact_sheet(image_paths: list[Path], timestamps_ms: list[int], destination: Path) -> None:
    tiles = []
    for image_path, timestamp_ms in zip(image_paths, timestamps_ms):
        image = cv2.imread(str(image_path))
        if image is None:
            raise RuntimeError(f"无法读取证据图片: {image_path}")
        image = cv2.resize(image, (480, 270), interpolation=cv2.INTER_AREA)
        cv2.rectangle(image, (0, 0), (190, 30), (0, 0, 0), -1)
        cv2.putText(
            image,
            f"{timestamp_ms / 1000:.3f}s",
            (8, 21),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        tiles.append(image)
    columns = min(3, len(tiles))
    rows = math.ceil(len(tiles) / columns)
    blank = np.zeros_like(tiles[0])
    while len(tiles) < rows * columns:
        tiles.append(blank.copy())
    row_images = [np.hstack(tiles[index:index + columns]) for index in range(0, len(tiles), columns)]
    sheet = np.vstack(row_images)
    if not cv2.imwrite(str(destination), sheet):
        raise RuntimeError(f"联系表写入失败: {destination}")


def generate_segment_evidence(
    url: str,
    segments: list[dict],
    artifact_root: Path,
    *,
    extractor: Callable[[str, int, Path], None] = extract_frame,
    duration_ms: int | None = None,
) -> list[dict]:
    artifact_root = Path(artifact_root)
    results = []
    for index, segment in enumerate(segments, start=1):
        start_ms = int(segment["start_ms"])
        end_ms = int(segment["end_ms"])
        segment_dir = artifact_root / f"segment-{index:04d}-{start_ms}-{end_ms}"
        segment_dir.mkdir(parents=True, exist_ok=True)
        timestamps = evidence_timestamps(start_ms, end_ms, duration_ms=duration_ms)
        image_paths = []
        for frame_index, timestamp_ms in enumerate(timestamps, start=1):
            image_path = segment_dir / f"evidence-{frame_index:02d}-{timestamp_ms}.jpg"
            extractor(url, timestamp_ms, image_path)
            image_paths.append(image_path)
        contact_sheet = segment_dir / "contact-sheet.jpg"
        _contact_sheet(image_paths, timestamps, contact_sheet)
        results.append(
            {
                "segment_index": index,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "timestamps_ms": timestamps,
                "image_paths": [str(path) for path in image_paths],
                "contact_sheet_path": str(contact_sheet),
            }
        )
    return results


def generate_review_queue_evidence(
    queue: dict,
    artifact_root: Path,
    *,
    extractor: Callable[[str, int, Path], None] = extract_frame,
    max_workers: int = 4,
    max_retries: int = 1,
    checkpoint: Callable[[dict], None] | None = None,
) -> dict:
    updated = json.loads(json.dumps(queue, ensure_ascii=False))
    pending = []
    for index, candidate in enumerate(updated.get("candidates", [])):
        existing = candidate.get("evidence_path")
        if existing and Path(existing).is_file():
            candidate["evidence_status"] = "COMPLETED"
            continue
        identity = (
            f"{candidate['url']}|{candidate['candidate_kind']}|"
            f"{candidate['start_ms']}|{candidate['end_ms']}"
        )
        candidate_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        segment_dir = (
            Path(artifact_root)
            / candidate_id
            / f"segment-0001-{candidate['start_ms']}-{candidate['end_ms']}"
        )
        timestamps = evidence_timestamps(
            int(candidate["start_ms"]),
            int(candidate["end_ms"]),
            duration_ms=candidate.get("duration_ms"),
        )
        image_paths = [
            segment_dir / f"evidence-{frame_index:02d}-{timestamp_ms}.jpg"
            for frame_index, timestamp_ms in enumerate(timestamps, start=1)
        ]
        contact_sheet = segment_dir / "contact-sheet.jpg"
        if contact_sheet.is_file() and all(path.is_file() for path in image_paths):
            candidate["evidence_path"] = str(contact_sheet)
            candidate["evidence_frames"] = [str(path) for path in image_paths]
            candidate["evidence_status"] = "COMPLETED"
            continue
        pending.append((index, candidate))

    def generate(entry):
        index, candidate = entry
        identity = (
            f"{candidate['url']}|{candidate['candidate_kind']}|"
            f"{candidate['start_ms']}|{candidate['end_ms']}"
        )
        candidate_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        last_error = None
        for attempt in range(max(0, max_retries) + 1):
            try:
                evidence = generate_segment_evidence(
                    candidate["url"],
                    [{"start_ms": candidate["start_ms"], "end_ms": candidate["end_ms"]}],
                    Path(artifact_root) / candidate_id,
                    extractor=extractor,
                    duration_ms=candidate.get("duration_ms"),
                )
                return index, evidence[0], attempt + 1, None
            except Exception as exc:
                last_error = exc
        return index, None, max(0, max_retries) + 1, str(last_error)

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = [executor.submit(generate, entry) for entry in pending]
        for future in as_completed(futures):
            index, evidence, attempt_count, error = future.result()
            candidate = updated["candidates"][index]
            candidate["evidence_attempt_count"] = attempt_count
            if evidence is None:
                candidate["evidence_status"] = "FAILED"
                candidate["evidence_error"] = error
            else:
                candidate["evidence_path"] = evidence["contact_sheet_path"]
                candidate["evidence_frames"] = evidence["image_paths"]
                candidate["evidence_status"] = "COMPLETED"
                candidate.pop("evidence_error", None)
            if checkpoint is not None:
                checkpoint(updated)
    return updated


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.part")
    try:
        partial.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="从远程 URL 提取静态区间证据，不保存视频")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--detection", type=Path)
    source.add_argument("--review-queue", type=Path)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--max-retries", type=int, default=1)
    args = parser.parse_args()

    if args.review_queue:
        queue = json.loads(args.review_queue.read_text(encoding="utf-8"))
        updated = generate_review_queue_evidence(
            queue,
            args.artifact_root,
            max_workers=args.max_workers,
            max_retries=args.max_retries,
            checkpoint=lambda payload: _write_json(args.output, payload),
        )
        evidence = updated
        segment_count = len(updated.get("candidates", []))
    else:
        detection = json.loads(args.detection.read_text(encoding="utf-8"))
        evidence = generate_segment_evidence(
            detection["url"],
            detection.get("dynamic_segments", []),
            args.artifact_root,
        )
        segment_count = len(evidence)
    _write_json(args.output, evidence)
    print(json.dumps({"segment_count": segment_count, "mp4_persisted": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
