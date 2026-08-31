#!/usr/bin/env python3
"""Compare student.pt detections at imgsz 1920 and 1024."""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.settings import settings, yolo_student_model  # noqa: E402
from app.services.student_behavior_service import (  # noqa: E402
    STUDENT_BEHAVIOR_CLASSES,
    get_student_behavior_label_threshold,
    get_student_behavior_predict_conf,
)


CLASS_NAMES_CN = {
    "Using_phone": "使用手机",
    "Hand_raising": "举手",
    "Sleep": "睡觉",
    "standing": "站立",
    "Read_W": "阅读",
}

CLASS_COLORS = {
    "Using_phone": (230, 66, 66),
    "Hand_raising": (245, 156, 35),
    "Sleep": (117, 87, 232),
    "standing": (32, 132, 232),
    "Read_W": (34, 156, 94),
}

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def iter_images(image_dir: Path, limit: int) -> Iterable[Path]:
    paths = [
        path
        for path in sorted(image_dir.iterdir())
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    ]
    return paths[:limit]


def resolve_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def box_iou(box_a: List[float], box_b: List[float]) -> float:
    left = max(box_a[0], box_b[0])
    top = max(box_a[1], box_b[1])
    right = min(box_a[2], box_b[2])
    bottom = min(box_a[3], box_b[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    if intersection <= 0:
        return 0.0
    area_a = max(0.0, box_a[2] - box_a[0]) * max(0.0, box_a[3] - box_a[1])
    area_b = max(0.0, box_b[2] - box_b[0]) * max(0.0, box_b[3] - box_b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def predict(image: np.ndarray, imgsz: int) -> Tuple[List[dict], float]:
    start = time.perf_counter()
    prediction = yolo_student_model.predict(
        image,
        imgsz=imgsz,
        conf=get_student_behavior_predict_conf(),
        half=settings.Inference.StudentUseHalf,
        verbose=False,
    )[0]
    elapsed_ms = (time.perf_counter() - start) * 1000

    detections = []
    for row in prediction.boxes.data.tolist():
        *xyxy, confidence, class_id = row
        class_id = int(class_id)
        class_name = STUDENT_BEHAVIOR_CLASSES.get(class_id)
        if class_name is None:
            continue
        threshold = get_student_behavior_label_threshold(class_name)
        if float(confidence) < threshold:
            continue
        detections.append({
            "xyxy": [float(value) for value in xyxy],
            "confidence": float(confidence),
            "class_id": class_id,
            "class_name": class_name,
            "threshold": threshold,
            "status": "",
        })
    return detections, elapsed_ms


def match_detections(
        detections_1920: List[dict],
        detections_1024: List[dict],
        min_iou: float) -> Tuple[List[Tuple[int, int, float]], List[int], List[int]]:
    candidates = []
    for index_1920, det_1920 in enumerate(detections_1920):
        for index_1024, det_1024 in enumerate(detections_1024):
            if det_1920["class_name"] != det_1024["class_name"]:
                continue
            iou = box_iou(det_1920["xyxy"], det_1024["xyxy"])
            if iou >= min_iou:
                candidates.append((iou, index_1920, index_1024))

    matched_1920 = set()
    matched_1024 = set()
    matches = []
    for iou, index_1920, index_1024 in sorted(candidates, reverse=True):
        if index_1920 in matched_1920 or index_1024 in matched_1024:
            continue
        matched_1920.add(index_1920)
        matched_1024.add(index_1024)
        matches.append((index_1920, index_1024, iou))

    missing_at_1024 = [index for index in range(len(detections_1920)) if index not in matched_1920]
    only_at_1024 = [index for index in range(len(detections_1024)) if index not in matched_1024]
    return matches, missing_at_1024, only_at_1024


def annotate(image: np.ndarray, detections: List[dict], title: str) -> np.ndarray:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    canvas = Image.fromarray(rgb)
    draw = ImageDraw.Draw(canvas)
    font = resolve_font(max(18, int(min(image.shape[:2]) * 0.023)))
    title_font = resolve_font(max(24, int(min(image.shape[:2]) * 0.03)))
    draw.rectangle((0, 0, image.shape[1], 48), fill=(20, 20, 20))
    draw.text((12, 8), title, font=title_font, fill=(255, 255, 255))

    for detection in detections:
        x1, y1, x2, y2 = [int(round(value)) for value in detection["xyxy"]]
        class_name = detection["class_name"]
        status = detection.get("status", "")
        color = CLASS_COLORS.get(class_name, (255, 255, 255))
        width = 3
        suffix = ""
        if status == "missing_at_1024":
            color = (255, 0, 0)
            width = 6
            suffix = " 1024漏"
        elif status == "only_at_1024":
            color = (255, 210, 0)
            width = 6
            suffix = " 仅1024"
        text = f"{CLASS_NAMES_CN.get(class_name, class_name)} {detection['confidence']:.3f}{suffix}"

        draw.rectangle((x1, y1, x2, y2), outline=color, width=width)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        text_w = right - left
        text_h = bottom - top
        label_y = max(48, y1 - text_h - 8)
        draw.rectangle((x1, label_y, x1 + text_w + 10, label_y + text_h + 8), fill=color)
        draw.text((x1 + 5, label_y + 4), text, font=font, fill=(255, 255, 255))

    return cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)


def write_csv(path: Path, rows: List[dict]) -> None:
    fields = [
        "image",
        "status",
        "class_name",
        "class_name_cn",
        "conf_1920",
        "conf_1024",
        "iou",
        "box_1920",
        "box_1024",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run(args: argparse.Namespace) -> None:
    image_dir = args.image_dir.resolve()
    output_dir = args.output_dir.resolve()
    dir_1920 = output_dir / "imgsz_1920"
    dir_1024 = output_dir / "imgsz_1024"
    side_dir = output_dir / "side_by_side"
    for directory in (dir_1920, dir_1024, side_dir):
        directory.mkdir(parents=True, exist_ok=True)

    image_paths = list(iter_images(image_dir, args.limit))
    if not image_paths:
        raise SystemExit(f"No images found under {image_dir}")

    warmup = cv2.imread(str(image_paths[0]))
    predict(warmup, 1920)
    predict(warmup, 1024)

    csv_rows = []
    matched_counts = Counter()
    missing_counts = Counter()
    only_1024_counts = Counter()
    total_1920 = Counter()
    total_1024 = Counter()
    times_1920 = []
    times_1024 = []
    per_image = []

    for image_path in image_paths:
        image = cv2.imread(str(image_path))
        if image is None:
            continue
        detections_1920, elapsed_1920 = predict(image, 1920)
        detections_1024, elapsed_1024 = predict(image, 1024)
        matches, missing, only_1024 = match_detections(
            detections_1920,
            detections_1024,
            args.match_iou,
        )
        times_1920.append(elapsed_1920)
        times_1024.append(elapsed_1024)

        for detection in detections_1920:
            total_1920[detection["class_name"]] += 1
        for detection in detections_1024:
            total_1024[detection["class_name"]] += 1

        for index_1920, index_1024, iou in matches:
            det_1920 = detections_1920[index_1920]
            det_1024 = detections_1024[index_1024]
            class_name = det_1920["class_name"]
            matched_counts[class_name] += 1
            csv_rows.append({
                "image": image_path.name,
                "status": "matched",
                "class_name": class_name,
                "class_name_cn": CLASS_NAMES_CN[class_name],
                "conf_1920": f"{det_1920['confidence']:.6f}",
                "conf_1024": f"{det_1024['confidence']:.6f}",
                "iou": f"{iou:.6f}",
                "box_1920": det_1920["xyxy"],
                "box_1024": det_1024["xyxy"],
            })

        for index in missing:
            detection = detections_1920[index]
            detection["status"] = "missing_at_1024"
            class_name = detection["class_name"]
            missing_counts[class_name] += 1
            csv_rows.append({
                "image": image_path.name,
                "status": "missing_at_1024",
                "class_name": class_name,
                "class_name_cn": CLASS_NAMES_CN[class_name],
                "conf_1920": f"{detection['confidence']:.6f}",
                "conf_1024": "",
                "iou": "",
                "box_1920": detection["xyxy"],
                "box_1024": "",
            })

        for index in only_1024:
            detection = detections_1024[index]
            detection["status"] = "only_at_1024"
            class_name = detection["class_name"]
            only_1024_counts[class_name] += 1
            csv_rows.append({
                "image": image_path.name,
                "status": "only_at_1024",
                "class_name": class_name,
                "class_name_cn": CLASS_NAMES_CN[class_name],
                "conf_1920": "",
                "conf_1024": f"{detection['confidence']:.6f}",
                "iou": "",
                "box_1920": "",
                "box_1024": detection["xyxy"],
            })

        drawn_1920 = annotate(image, detections_1920, "imgsz=1920，红框表示在1024中未匹配")
        drawn_1024 = annotate(image, detections_1024, "imgsz=1024，黄框表示仅1024检出")
        cv2.imwrite(str(dir_1920 / image_path.name), drawn_1920)
        cv2.imwrite(str(dir_1024 / image_path.name), drawn_1024)
        cv2.imwrite(str(side_dir / image_path.name), cv2.hconcat([drawn_1920, drawn_1024]))
        per_image.append({
            "image": image_path.name,
            "count_1920": len(detections_1920),
            "count_1024": len(detections_1024),
            "matched": len(matches),
            "missing_at_1024": len(missing),
            "only_at_1024": len(only_1024),
        })

    write_csv(output_dir / "comparison.csv", csv_rows)

    lines = [
        "# student.pt 输入尺寸对比",
        "",
        f"- 图片数量: {len(image_paths)}",
        f"- 同类别框匹配 IoU: {args.match_iou}",
        f"- imgsz=1920 平均耗时: {sum(times_1920) / len(times_1920):.1f} ms",
        f"- imgsz=1024 平均耗时: {sum(times_1024) / len(times_1024):.1f} ms",
        f"- 加速比: {sum(times_1920) / sum(times_1024):.3f}x",
        "",
        "## 汇总",
        "",
        "| 标签 | 1920总数 | 1024总数 | 匹配 | 1024未匹配1920框 | 仅1024检出 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for class_name in STUDENT_BEHAVIOR_CLASSES.values():
        lines.append(
            f"| {CLASS_NAMES_CN[class_name]} | {total_1920[class_name]} | {total_1024[class_name]} | "
            f"{matched_counts[class_name]} | {missing_counts[class_name]} | {only_1024_counts[class_name]} |"
        )
    lines.extend([
        "",
        "## 每张图片",
        "",
        "| 图片 | 1920 | 1024 | 匹配 | 1024未匹配1920框 | 仅1024检出 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    for row in per_image:
        lines.append(
            f"| {row['image']} | {row['count_1920']} | {row['count_1024']} | {row['matched']} | "
            f"{row['missing_at_1024']} | {row['only_at_1024']} |"
        )
    lines.extend([
        "",
        "> 说明：‘1024未匹配1920框’只表示相对1920结果消失，不等同于经过人工标注确认的真实漏检。",
    ])
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image-dir",
        type=Path,
        default=PROJECT_ROOT / "tests" / "tests_data" / "学生行为",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "tests" / "student_imgsz_comparison",
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--match-iou", type=float, default=0.5)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
