"""Prepare a stable review queue for detections and missed-detection audits."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from harness.tools.review import build_audit_windows
from harness.tools.corpus import canonical_url


def _key(item: dict) -> tuple:
    return (
        item["url"],
        item["candidate_kind"],
        int(item["start_ms"]),
        int(item["end_ms"]),
    )


def prepare_review_queue(
    inventory: dict,
    detections: dict,
    *,
    existing_reviews: list[dict] | None = None,
) -> dict:
    inventory_by_url = {
        canonical_url(item["url"], directory=False): item
        for item in inventory.get("items", [])
    }
    existing_by_key = {_key(item): item for item in (existing_reviews or [])}
    candidates = []
    audit_windows = []

    detection_items = detections.get("items")
    if detection_items is None and detections.get("url"):
        detection_items = [detections]
    for detection in detection_items or []:
        if detection.get("status") != "COMPLETED":
            continue
        url = canonical_url(detection["url"], directory=False)
        inventory_item = inventory_by_url.get(url, {})
        base = {
            "url": url,
            "course_name": inventory_item.get("course_name", detection.get("course_name", "")),
            "duration_ms": int(round(float(inventory_item.get("duration") or 0) * 1000)),
        }
        for segment in detection.get("dynamic_segments", []):
            candidate = {
                **base,
                "candidate_kind": "DETECTION",
                "source": "ALGORITHM",
                "start_ms": int(segment["start_ms"]),
                "end_ms": int(segment["end_ms"]),
                "confidence": segment.get("confidence"),
                "label": "",
                "review_status": "PENDING",
                "reviewer": "",
                "reviewed_at": "",
                "evidence_path": "",
                "notes": "",
            }
            candidate.update(existing_by_key.get(_key(candidate), {}))
            candidates.append(candidate)

        duration_ms = base["duration_ms"]
        for window in build_audit_windows(detection, duration_ms=duration_ms):
            candidate = {
                **base,
                "candidate_kind": "AUDIT",
                "source": window["source"],
                "start_ms": int(window["start_ms"]),
                "end_ms": int(window["end_ms"]),
                "confidence": "",
                "label": "",
                "review_status": "PENDING",
                "reviewer": "",
                "reviewed_at": "",
                "evidence_path": "",
                "notes": "",
            }
            candidate.update(existing_by_key.get(_key(candidate), {}))
            candidates.append(candidate)
            audit_windows.append(
                {
                    "url": url,
                    "source": window["source"],
                    "start_ms": int(window["start_ms"]),
                    "end_ms": int(window["end_ms"]),
                }
            )

    candidates.sort(
        key=lambda item: (
            0 if item["candidate_kind"] == "DETECTION" else 1,
            item["course_name"],
            item["start_ms"],
            item["source"],
        )
    )
    return {
        "schema_version": 1,
        "run_id": detections.get("run_id") or inventory.get("run_id"),
        "inventory_run_id": (
            detections.get("inventory_run_id") or inventory.get("run_id")
        ),
        "inventory_fingerprint": inventory.get("inventory_fingerprint"),
        "candidates": candidates,
        "audit_windows": audit_windows,
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.part")
    try:
        partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成检测候选和漏报检查复核队列")
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--detections", type=Path, required=True)
    parser.add_argument("--existing", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    detections = json.loads(args.detections.read_text(encoding="utf-8"))
    existing = None
    if args.existing and args.existing.exists():
        existing_payload = json.loads(args.existing.read_text(encoding="utf-8"))
        existing = existing_payload.get("candidates", existing_payload)
    queue = prepare_review_queue(inventory, detections, existing_reviews=existing)
    _write_json(args.output, queue)
    print(
        json.dumps(
            {
                "candidate_count": len(queue["candidates"]),
                "audit_count": len(queue["audit_windows"]),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
