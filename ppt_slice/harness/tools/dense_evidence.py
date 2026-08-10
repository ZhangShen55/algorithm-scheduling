"""Generate dense static evidence for explicitly selected ambiguous candidates."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Callable

from harness.tools.evidence import _contact_sheet, extract_frame


def dense_evidence_timestamps(
    start_ms: int,
    end_ms: int,
    *,
    step_ms: int = 1000,
    context_ms: int = 2000,
    duration_ms: int | None = None,
    max_frames: int = 120,
) -> list[int]:
    if start_ms >= end_ms:
        raise ValueError("候选区间必须满足 start_ms < end_ms")
    if step_ms <= 0 or max_frames < 2:
        raise ValueError("step_ms 必须大于 0 且 max_frames 至少为 2")
    first = max(0, int(start_ms) - max(0, int(context_ms)))
    last = int(end_ms) + max(0, int(context_ms))
    if duration_ms is not None:
        last = min(last, max(0, int(duration_ms) - 1000))
    values = list(range(first, last + 1, step_ms))
    if not values or values[-1] != last:
        values.append(last)
    if len(values) <= max_frames:
        return values
    indexes = [round(index * (len(values) - 1) / (max_frames - 1)) for index in range(max_frames)]
    return [values[index] for index in dict.fromkeys(indexes)]


def _write_json(path: Path, payload: dict) -> None:
    partial = path.with_name(f"{path.name}.part")
    try:
        partial.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def generate_dense_candidate_evidence(
    queue: dict,
    candidate_numbers: list[int],
    artifact_root: Path,
    *,
    extractor: Callable[[str, int, Path], None] = extract_frame,
    step_ms: int = 1000,
    context_ms: int = 2000,
    max_frames: int = 120,
    frames_per_page: int = 12,
) -> dict:
    if frames_per_page <= 0:
        raise ValueError("frames_per_page 必须大于 0")
    candidates = queue.get("candidates", [])
    artifact_root = Path(artifact_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    results = []
    for candidate_number in dict.fromkeys(candidate_numbers):
        if candidate_number <= 0 or candidate_number > len(candidates):
            raise ValueError(f"候选编号越界: {candidate_number}")
        candidate = candidates[candidate_number - 1]
        timestamps = dense_evidence_timestamps(
            int(candidate["start_ms"]),
            int(candidate["end_ms"]),
            step_ms=step_ms,
            context_ms=context_ms,
            duration_ms=candidate.get("duration_ms"),
            max_frames=max_frames,
        )
        candidate_root = artifact_root / f"candidate-{candidate_number:04d}"
        frame_root = candidate_root / "frames"
        frame_root.mkdir(parents=True, exist_ok=True)
        image_paths = []
        for timestamp_ms in timestamps:
            image_path = frame_root / f"evidence-{timestamp_ms:010d}.jpg"
            if not image_path.is_file():
                extractor(candidate["url"], timestamp_ms, image_path)
            image_paths.append(image_path)

        page_paths = []
        for page_number, offset in enumerate(range(0, len(image_paths), frames_per_page), start=1):
            page_images = image_paths[offset:offset + frames_per_page]
            page_timestamps = timestamps[offset:offset + frames_per_page]
            page_path = candidate_root / f"dense-contact-sheet-{page_number:03d}.jpg"
            _contact_sheet(page_images, page_timestamps, page_path)
            page_paths.append(str(page_path))
        results.append(
            {
                "candidate_number": candidate_number,
                "course_name": candidate.get("course_name", ""),
                "candidate_kind": candidate.get("candidate_kind", ""),
                "start_ms": int(candidate["start_ms"]),
                "end_ms": int(candidate["end_ms"]),
                "timestamps_ms": timestamps,
                "frames": [str(path) for path in image_paths],
                "pages": page_paths,
            }
        )
    result = {
        "schema_version": 1,
        "run_id": queue.get("run_id"),
        "candidate_count": len(results),
        "mp4_persisted": False,
        "candidates": results,
    }
    _write_json(artifact_root / "dense-evidence-index.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="为指定候选生成密集静态复核证据")
    parser.add_argument("--review-queue", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--candidate-number", type=int, action="append", required=True)
    parser.add_argument("--step-ms", type=int, default=1000)
    parser.add_argument("--context-ms", type=int, default=2000)
    parser.add_argument("--max-frames", type=int, default=120)
    parser.add_argument("--frames-per-page", type=int, default=12)
    args = parser.parse_args()
    queue = json.loads(args.review_queue.read_text(encoding="utf-8"))
    result = generate_dense_candidate_evidence(
        queue,
        args.candidate_number,
        args.artifact_root,
        step_ms=args.step_ms,
        context_ms=args.context_ms,
        max_frames=args.max_frames,
        frames_per_page=args.frames_per_page,
    )
    print(
        json.dumps(
            {
                "candidate_count": result["candidate_count"],
                "mp4_persisted": False,
                "output": str(args.artifact_root / "dense-evidence-index.json"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
