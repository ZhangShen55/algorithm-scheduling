#!/usr/bin/env python3
"""批量评估 YOLO-seg 镜头遮挡模型。

输出：
- summary JSON：总图数、任意 mask 数、不同面积阈值命中数、topN
- CSV 明细：每张图最大置信度、mask 数、mask 并集面积占比
- 可选 overlay：将预测 mask 叠加到原图，便于人工复核
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.image_preprocess import clamp01


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def image_files(folder: Path) -> list[Path]:
    return sorted(path for path in folder.rglob("*") if path.suffix.lower() in IMAGE_SUFFIXES)


def as_numpy(values) -> np.ndarray:
    if values is None:
        return np.array([], dtype=np.float32)
    if hasattr(values, "detach"):
        values = values.detach()
    if hasattr(values, "cpu"):
        values = values.cpu()
    if hasattr(values, "numpy"):
        values = values.numpy()
    return np.asarray(values, dtype=np.float32)


def parse_result(result, threshold: float) -> tuple[int, float, float, np.ndarray | None]:
    if result.masks is None or result.boxes is None or len(result.boxes) == 0:
        return 0, 0.0, 0.0, None
    masks = as_numpy(result.masks.data)
    confs = as_numpy(result.boxes.conf).reshape(-1)
    if masks.ndim == 2:
        masks = masks[None, :, :]
    if masks.ndim != 3:
        return 0, 0.0, 0.0, None

    union = None
    max_conf = 0.0
    used = 0
    for mask, conf in zip(masks, confs):
        conf = float(conf)
        if conf < threshold:
            continue
        mask_bool = mask > 0.5
        union = mask_bool if union is None else (union | mask_bool)
        max_conf = max(max_conf, conf)
        used += 1

    if union is None:
        return 0, 0.0, 0.0, None
    area_ratio = clamp01(float(union.sum()) / float(union.size))
    return used, max_conf, area_ratio, union


def write_overlay(image_path: Path, union_mask: np.ndarray, output_path: Path) -> None:
    bgr = cv2.imread(str(image_path))
    if bgr is None:
        return
    if union_mask.shape[:2] != bgr.shape[:2]:
        union_mask = cv2.resize(
            union_mask.astype(np.uint8),
            (bgr.shape[1], bgr.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        ).astype(bool)
    overlay = bgr.copy()
    overlay[union_mask] = (0, 0, 255)
    blended = cv2.addWeighted(bgr, 0.65, overlay, 0.35, 0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), blended)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate YOLO-seg occlusion model on image folder")
    parser.add_argument("--images", required=True, type=Path, help="图片目录")
    parser.add_argument("--weights", default=ROOT / "model/occlusion.pt", type=Path, help="YOLO-seg 权重")
    parser.add_argument("--output-dir", required=True, type=Path, help="输出目录")
    parser.add_argument("--imgsz", default=960, type=int)
    parser.add_argument(
        "--threshold",
        "--conf",
        dest="threshold",
        default=0.25,
        type=float,
        help="YOLO 置信度阈值",
    )
    parser.add_argument("--area-ratio", default=0.2, type=float, help="遮挡面积判定阈值")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch", default=16, type=int)
    parser.add_argument("--top-n", default=20, type=int)
    parser.add_argument("--save-overlays", action="store_true")
    args = parser.parse_args()

    from ultralytics import YOLO

    images = image_files(args.images)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(args.weights))
    thresholds = sorted({0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, args.area_ratio})

    rows: list[dict] = []
    start = time.time()
    for chunk_start in range(0, len(images), max(1, args.batch)):
        chunk = images[chunk_start : chunk_start + max(1, args.batch)]
        results = model.predict(
            source=[str(path) for path in chunk],
            imgsz=args.imgsz,
            conf=args.threshold,
            device=args.device,
            stream=False,
            verbose=False,
            retina_masks=True,
        )
        for image_path, result in zip(chunk, results):
            mask_count, max_conf, area_ratio, union = parse_result(result, args.threshold)
            is_occluded = area_ratio >= args.area_ratio
            row = {
                "file": image_path.name,
                "path": str(image_path),
                "mask_count": mask_count,
                "max_conf": round(max_conf, 6),
                "occlusion_area_ratio": round(area_ratio, 6),
                "is_occluded": is_occluded,
            }
            rows.append(row)
            if args.save_overlays and union is not None:
                write_overlay(image_path, union, args.output_dir / "overlays" / image_path.name)
        print(f"processed {min(chunk_start + len(chunk), len(images))}/{len(images)}", flush=True)

    rows.sort(key=lambda item: item["occlusion_area_ratio"], reverse=True)
    csv_path = args.output_dir / "details.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "file",
                "path",
                "mask_count",
                "max_conf",
                "occlusion_area_ratio",
                "is_occluded",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    top_rows = rows[: args.top_n]
    top_csv_path = args.output_dir / "top.csv"
    with top_csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "file",
                "path",
                "mask_count",
                "max_conf",
                "occlusion_area_ratio",
                "is_occluded",
            ],
        )
        writer.writeheader()
        writer.writerows(top_rows)
    top_json_path = args.output_dir / "top.json"
    top_json_path.write_text(json.dumps(top_rows, ensure_ascii=False, indent=2), encoding="utf-8")

    counts = {str(t): sum(1 for row in rows if row["occlusion_area_ratio"] > t) for t in thresholds}
    summary = {
        "images": len(images),
        "weights": str(args.weights),
        "imgsz": args.imgsz,
        "threshold": args.threshold,
        "area_ratio": args.area_ratio,
        "device": args.device,
        "elapsed_sec": round(time.time() - start, 3),
        "any_prediction_count": sum(1 for row in rows if row["mask_count"] > 0),
        "occluded_count": sum(1 for row in rows if row["is_occluded"]),
        "counts_by_area_ratio_gt": counts,
        "top": top_rows,
        "top_csv": str(top_csv_path),
        "top_json": str(top_json_path),
    }
    json_path = args.output_dir / "summary.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"csv={csv_path}")
    print(f"json={json_path}")
    print(f"top_csv={top_csv_path}")
    print(f"top_json={top_json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
