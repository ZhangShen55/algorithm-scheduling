"""Conservatively complete only visually static missed-detection audits."""
from __future__ import annotations

import argparse
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import cv2

from app.core.config import settings
from app.services.dynamic_detection import ActivityAnalyzer


def apply_static_stability_assist(queue: dict, *, reviewed_at: str | None = None) -> dict:
    updated = copy.deepcopy(queue)
    analyzer = ActivityAnalyzer(
        pixel_difference_threshold=settings.DYNAMIC_PIXEL_DIFFERENCE_THRESHOLD,
        changed_pixel_ratio_threshold=settings.DYNAMIC_CHANGED_PIXEL_RATIO,
        grid_rows=settings.DYNAMIC_GRID_ROWS,
        grid_columns=settings.DYNAMIC_GRID_COLUMNS,
        active_grid_ratio_threshold=settings.DYNAMIC_ACTIVE_GRID_RATIO,
    )
    effective_reviewed_at = reviewed_at or datetime.now(timezone.utc).isoformat()
    for candidate in updated.get("candidates", []):
        if (
            candidate.get("candidate_kind") != "AUDIT"
            or candidate.get("review_status") == "COMPLETED"
        ):
            continue
        frames = []
        missing_paths = []
        for value in candidate.get("evidence_frames", []):
            image = cv2.imread(str(value))
            if image is None:
                missing_paths.append(str(value))
            else:
                frames.append(image)
        if missing_paths or len(frames) < 2:
            candidate["review_assist"] = {
                "classification": "INSUFFICIENT_EVIDENCE",
                "missing_paths": missing_paths,
                "active_transition_count": None,
                "transitions": [],
            }
            continue

        transitions = []
        for index, (previous, current) in enumerate(zip(frames, frames[1:]), start=1):
            observation = analyzer.analyze(index, previous, current)
            transitions.append(
                {
                    "index": index,
                    "changed_pixel_ratio": observation.changed_pixel_ratio,
                    "active_grid_ratio": observation.active_grid_ratio,
                    "is_active": observation.is_active,
                }
            )
        active_count = sum(item["is_active"] for item in transitions)
        candidate["review_assist"] = {
            "classification": "VISUALLY_STATIC" if active_count == 0 else "VISUAL_CHANGE_REQUIRES_REVIEW",
            "active_transition_count": active_count,
            "transitions": transitions,
        }
        if active_count == 0:
            candidate["label"] = "FALSE_POSITIVE"
            candidate["review_status"] = "COMPLETED"
            candidate["reviewer"] = "harness-static-stability-v1"
            candidate["reviewed_at"] = effective_reviewed_at
            candidate["notes"] = (
                "静态证据复核：相邻证据帧均未达到大范围活动阈值，未发现持续动态漏报。"
            )
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
    parser = argparse.ArgumentParser(description="保守标记静态漏报审计证据")
    parser.add_argument("--review-queue", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    queue = json.loads(args.review_queue.read_text(encoding="utf-8"))
    updated = apply_static_stability_assist(queue)
    _write_json(args.output, updated)
    completed = sum(
        item.get("candidate_kind") == "AUDIT"
        and item.get("review_status") == "COMPLETED"
        for item in updated.get("candidates", [])
    )
    pending = sum(
        item.get("candidate_kind") == "AUDIT"
        and item.get("review_status") != "COMPLETED"
        for item in updated.get("candidates", [])
    )
    print(json.dumps({"completed_audits": completed, "pending_audits": pending}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
