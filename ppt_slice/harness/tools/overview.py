"""Build paged static overviews for visual review of contact sheets."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import cv2
import numpy as np


STATIC_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def _time_text(milliseconds: int) -> str:
    total_seconds = milliseconds / 1000
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


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


def build_overview_pages(
    queue: dict,
    output_dir: Path,
    *,
    candidate_kind: str | None = None,
    pending_only: bool = False,
    columns: int = 4,
    rows: int = 3,
    page_extension: str = ".jpg",
) -> dict:
    if columns <= 0 or rows <= 0:
        raise ValueError("columns 和 rows 必须大于 0")
    normalized_extension = page_extension.lower()
    if normalized_extension not in STATIC_EXTENSIONS:
        raise ValueError("复核总览只能保存为静态 JPEG 或 PNG")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    items = []
    missing = []
    for candidate_index, candidate in enumerate(queue.get("candidates", [])):
        if candidate_kind and candidate.get("candidate_kind") != candidate_kind:
            continue
        if pending_only and candidate.get("review_status") == "COMPLETED":
            continue
        evidence_path = Path(candidate.get("evidence_path", ""))
        if evidence_path.suffix.lower() not in STATIC_EXTENSIONS or not evidence_path.is_file():
            missing.append(candidate_index)
            continue
        items.append((candidate_index, candidate, evidence_path))

    tile_width = 720
    image_height = 270
    header_height = 64
    tile_height = image_height + header_height
    items_per_page = columns * rows
    pages = []
    index_items = []
    for page_number, offset in enumerate(range(0, len(items), items_per_page), start=1):
        page_items = items[offset:offset + items_per_page]
        canvas = np.full(
            (rows * tile_height, columns * tile_width, 3),
            245,
            dtype=np.uint8,
        )
        page_entries = []
        for slot, (candidate_index, candidate, evidence_path) in enumerate(page_items):
            row, column = divmod(slot, columns)
            x = column * tile_width
            y = row * tile_height
            evidence = cv2.imread(str(evidence_path))
            if evidence is None:
                missing.append(candidate_index)
                continue
            evidence = cv2.resize(
                evidence,
                (tile_width, image_height),
                interpolation=cv2.INTER_AREA,
            )
            canvas[y + header_height:y + tile_height, x:x + tile_width] = evidence
            candidate_number = candidate_index + 1
            title = (
                f"#{candidate_number:04d} {candidate.get('candidate_kind', '')} "
                f"{candidate.get('source', '')}"
            )
            interval = (
                f"{_time_text(int(candidate['start_ms']))} - "
                f"{_time_text(int(candidate['end_ms']))}"
            )
            cv2.putText(
                canvas,
                title[:88],
                (x + 8, y + 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (15, 15, 15),
                1,
                cv2.LINE_AA,
            )
            cv2.putText(
                canvas,
                interval,
                (x + 8, y + 51),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (15, 15, 15),
                1,
                cv2.LINE_AA,
            )
            entry = {
                "candidate_index": candidate_index,
                "candidate_number": candidate_number,
                "course_name": candidate.get("course_name", ""),
                "candidate_kind": candidate.get("candidate_kind", ""),
                "source": candidate.get("source", ""),
                "start_ms": int(candidate["start_ms"]),
                "end_ms": int(candidate["end_ms"]),
                "evidence_path": str(evidence_path),
                "page_number": page_number,
                "page_slot": slot + 1,
            }
            page_entries.append(entry)
            index_items.append(entry)

        page_path = output_dir / f"overview-{page_number:04d}{normalized_extension}"
        partial = page_path.with_name(f"{page_path.stem}.part{page_path.suffix}")
        try:
            if not cv2.imwrite(str(partial), canvas):
                raise RuntimeError(f"无法写入复核总览: {page_path}")
            os.replace(partial, page_path)
        finally:
            partial.unlink(missing_ok=True)
        pages.append(
            {
                "page_number": page_number,
                "path": str(page_path),
                "candidate_numbers": [item["candidate_number"] for item in page_entries],
            }
        )

    result = {
        "schema_version": 1,
        "run_id": queue.get("run_id"),
        "candidate_kind": candidate_kind or "ALL",
        "pending_only": pending_only,
        "item_count": len(index_items),
        "page_count": len(pages),
        "missing_candidate_indexes": sorted(set(missing)),
        "pages": pages,
        "items": index_items,
    }
    _write_json(output_dir / "overview-index.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="从静态联系表生成分页视觉复核总览")
    parser.add_argument("--review-queue", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--kind", choices=["DETECTION", "AUDIT"])
    parser.add_argument("--pending-only", action="store_true")
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--rows", type=int, default=3)
    args = parser.parse_args()
    queue = json.loads(args.review_queue.read_text(encoding="utf-8"))
    result = build_overview_pages(
        queue,
        args.output_dir,
        candidate_kind=args.kind,
        pending_only=args.pending_only,
        columns=args.columns,
        rows=args.rows,
    )
    print(
        json.dumps(
            {
                "item_count": result["item_count"],
                "page_count": result["page_count"],
                "missing_count": len(result["missing_candidate_indexes"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
