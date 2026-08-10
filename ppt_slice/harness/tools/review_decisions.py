"""Apply explicit, auditable review decisions to a review queue."""
from __future__ import annotations

import argparse
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from harness.tools.review import REVIEW_LABELS


def apply_review_decisions(
    queue: dict,
    decisions: dict,
    *,
    reviewed_at: str | None = None,
) -> dict:
    updated = copy.deepcopy(queue)
    candidates = updated.get("candidates", [])
    reviewer = str(decisions.get("reviewer") or "").strip()
    if not reviewer:
        raise ValueError("reviewer 不能为空")
    effective_reviewed_at = reviewed_at or datetime.now(timezone.utc).isoformat()
    seen = set()
    for decision in decisions.get("decisions", []):
        candidate_number = int(decision["candidate_number"])
        if candidate_number in seen:
            raise ValueError(f"候选编号重复: {candidate_number}")
        seen.add(candidate_number)
        if candidate_number <= 0 or candidate_number > len(candidates):
            raise ValueError(f"候选编号越界: {candidate_number}")
        label = str(decision["label"])
        if label not in REVIEW_LABELS:
            raise ValueError(f"未知复核标签: {label}")
        candidate = candidates[candidate_number - 1]
        candidate["label"] = label
        candidate["review_status"] = "COMPLETED"
        candidate["reviewer"] = reviewer
        candidate["reviewed_at"] = effective_reviewed_at
        candidate["notes"] = str(decision.get("notes") or "")
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
    parser = argparse.ArgumentParser(description="应用独立的静态证据复核决定")
    parser.add_argument("--review-queue", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    queue = json.loads(args.review_queue.read_text(encoding="utf-8"))
    decisions = json.loads(args.decisions.read_text(encoding="utf-8"))
    updated = apply_review_decisions(queue, decisions)
    _write_json(args.output, updated)
    print(
        json.dumps(
            {"applied": len(decisions.get("decisions", [])), "output": str(args.output)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
