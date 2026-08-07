#!/usr/bin/env python3
"""Run student behavior model on a local image directory and draw detections."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.settings import settings, use_half, yolo_student_model  # noqa: E402


CLASS_NAMES = {
    0: "Using_phone",
    1: "Hand_raising",
    2: "Sleep",
    3: "standing",
    4: "Read_W",
}

THRESHOLD_FIELD_TO_CLASS = {
    "phone": "Using_phone",
    "hand": "Hand_raising",
    "sleep": "Sleep",
    "stand": "standing",
    "read": "Read_W",
}

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


def resolve_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for candidate in candidates:
        try:
            path = Path(candidate)
            if path.exists():
                return ImageFont.truetype(str(path), size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def iter_images(image_dir: Path) -> Iterable[Path]:
    for path in sorted(image_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            yield path


def label_text(class_name: str, confidence: float) -> str:
    name = CLASS_NAMES_CN.get(class_name, class_name)
    return f"{name} {confidence:.3f}"


def draw_detections(image: np.ndarray, detections: List[dict]) -> np.ndarray:
    if not detections:
        return image.copy()

    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil_image)
    font = resolve_font(max(18, int(min(image.shape[:2]) * 0.025)))

    for det in detections:
        x1, y1, x2, y2 = [int(round(v)) for v in det["xyxy"]]
        class_name = det["class_name"]
        color = CLASS_COLORS.get(class_name, (255, 255, 255))
        text = label_text(class_name, det["confidence"])

        draw.rectangle((x1, y1, x2, y2), outline=color, width=3)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        text_w = right - left
        text_h = bottom - top
        label_y = max(0, y1 - text_h - 8)
        draw.rectangle((x1, label_y, x1 + text_w + 10, label_y + text_h + 8), fill=color)
        draw.text((x1 + 5, label_y + 4), text, fill=(255, 255, 255), font=font)

    return cv2.cvtColor(np.asarray(pil_image), cv2.COLOR_RGB2BGR)


def to_record(image_path: Path, rel_path: Path, det: dict, mode: str) -> dict:
    x1, y1, x2, y2 = det["xyxy"]
    return {
        "mode": mode,
        "image": rel_path.as_posix(),
        "class_id": det["class_id"],
        "class_name": det["class_name"],
        "class_name_cn": CLASS_NAMES_CN.get(det["class_name"], det["class_name"]),
        "confidence": f"{det['confidence']:.6f}",
        "threshold": f"{det['threshold']:.6f}",
        "x1": int(round(x1)),
        "y1": int(round(y1)),
        "x2": int(round(x2)),
        "y2": int(round(y2)),
        "source": str(image_path),
    }


def quantiles(values: List[float]) -> dict:
    if not values:
        return {}
    sorted_values = sorted(values)
    return {
        "count": len(values),
        "min": round(sorted_values[0], 4),
        "p25": round(float(np.quantile(sorted_values, 0.25)), 4),
        "p50": round(statistics.median(sorted_values), 4),
        "p75": round(float(np.quantile(sorted_values, 0.75)), 4),
        "max": round(sorted_values[-1], 4),
    }


def write_csv(path: Path, rows: List[dict]) -> None:
    fieldnames = [
        "mode",
        "image",
        "class_id",
        "class_name",
        "class_name_cn",
        "confidence",
        "threshold",
        "x1",
        "y1",
        "x2",
        "y2",
        "source",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(counter: Counter) -> str:
    lines = ["| 标签 | 数量 |", "| --- | ---: |"]
    for class_name in CLASS_NAMES.values():
        label = CLASS_NAMES_CN.get(class_name, class_name)
        lines.append(f"| {label} ({class_name}) | {counter[class_name]} |")
    return "\n".join(lines)


def parse_threshold_overrides(values: List[str]) -> Dict[str, float]:
    overrides: Dict[str, float] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"Invalid --threshold value: {value}. Expected ClassName=0.5")
        key, raw_threshold = value.split("=", 1)
        key = key.strip()
        class_name = THRESHOLD_FIELD_TO_CLASS.get(key)
        if class_name is None:
            raise SystemExit(f"Invalid threshold field: {key}. Expected one of {list(THRESHOLD_FIELD_TO_CLASS)}")
        try:
            threshold = float(raw_threshold)
        except ValueError as exc:
            raise SystemExit(f"Invalid threshold number for {key}: {raw_threshold}") from exc
        if threshold < 0 or threshold > 1:
            raise SystemExit(f"Invalid threshold for {key}: {threshold}. Expected 0..1")
        overrides[class_name] = threshold
    return overrides


def run_eval(args: argparse.Namespace) -> None:
    image_dir = args.image_dir.resolve()
    output_dir = args.output_dir.resolve()
    raw_dir = output_dir / "raw_all_labels"
    filtered_dir = output_dir / "filtered_by_config"
    raw_dir.mkdir(parents=True, exist_ok=True)
    filtered_dir.mkdir(parents=True, exist_ok=True)

    image_paths = list(iter_images(image_dir))
    if not image_paths:
        raise SystemExit(f"No images found under {image_dir}")

    thresholds: Dict[str, float] = {
        class_name: float(settings.Student_Thresd.get(field_name, args.default_threshold))
        for field_name, class_name in THRESHOLD_FIELD_TO_CLASS.items()
    }
    thresholds.update(parse_threshold_overrides(args.threshold))
    raw_rows: List[dict] = []
    filtered_rows: List[dict] = []
    raw_counter: Counter = Counter()
    filtered_counter: Counter = Counter()
    raw_conf_by_class: Dict[str, List[float]] = defaultdict(list)
    images_with_raw: Counter = Counter()
    images_with_filtered: Counter = Counter()

    for index, image_path in enumerate(image_paths, start=1):
        rel_path = image_path.relative_to(image_dir)
        image = cv2.imread(str(image_path))
        if image is None:
            print(f"[WARN] cannot read image: {image_path}", file=sys.stderr)
            continue

        height, width = image.shape[:2]
        pred = yolo_student_model.predict(
            image,
            conf=args.raw_conf,
            imgsz=(height, width),
            half=use_half,
            verbose=False,
        )[0]

        raw_detections: List[dict] = []
        filtered_detections: List[dict] = []
        for box in pred.boxes.data.tolist():
            *xyxy, confidence, class_id = box
            class_id = int(class_id)
            class_name = CLASS_NAMES.get(class_id, f"class_{class_id}")
            threshold = thresholds.get(class_name, args.default_threshold)
            det = {
                "xyxy": [float(v) for v in xyxy],
                "confidence": float(confidence),
                "class_id": class_id,
                "class_name": class_name,
                "threshold": float(threshold),
            }

            raw_detections.append(det)
            raw_counter[class_name] += 1
            raw_conf_by_class[class_name].append(float(confidence))
            raw_rows.append(to_record(image_path, rel_path, det, "raw"))

            if float(confidence) >= float(threshold):
                filtered_detections.append(det)
                filtered_counter[class_name] += 1
                filtered_rows.append(to_record(image_path, rel_path, det, "filtered"))

        for class_name in {d["class_name"] for d in raw_detections}:
            images_with_raw[class_name] += 1
        for class_name in {d["class_name"] for d in filtered_detections}:
            images_with_filtered[class_name] += 1

        raw_out = raw_dir / rel_path
        filtered_out = filtered_dir / rel_path
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        filtered_out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(raw_out), draw_detections(image, raw_detections))
        cv2.imwrite(str(filtered_out), draw_detections(image, filtered_detections))
        print(f"[{index}/{len(image_paths)}] {rel_path}: raw={len(raw_detections)} filtered={len(filtered_detections)}")

    write_csv(output_dir / "student_behavior_raw_detections.csv", raw_rows)
    write_csv(output_dir / "student_behavior_filtered_detections.csv", filtered_rows)

    summary = {
        "image_dir": str(image_dir),
        "output_dir": str(output_dir),
        "image_count": len(image_paths),
        "raw_conf_floor": args.raw_conf,
        "thresholds": thresholds,
        "raw_counts": dict(raw_counter),
        "filtered_counts": dict(filtered_counter),
        "raw_images_with_class": dict(images_with_raw),
        "filtered_images_with_class": dict(images_with_filtered),
        "raw_confidence_distribution": {
            class_name: quantiles(values) for class_name, values in raw_conf_by_class.items()
        },
    }
    with (output_dir / "student_behavior_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    lines = [
        "# 学生行为检测评估",
        "",
        f"- 输入目录: `{image_dir}`",
        f"- 输出目录: `{output_dir}`",
        f"- 图片数量: {len(image_paths)}",
        f"- 原始候选框最低 conf: {args.raw_conf}",
        "",
        "## 当前配置阈值",
        "",
        "| 标签 | 阈值 |",
        "| --- | ---: |",
    ]
    for class_name in CLASS_NAMES.values():
        label = CLASS_NAMES_CN.get(class_name, class_name)
        lines.append(f"| {label} ({class_name}) | {thresholds.get(class_name, args.default_threshold):.3f} |")
    lines.extend(["", "## 原始候选框数量", "", markdown_table(raw_counter)])
    lines.extend(["", "## 按配置过滤后数量", "", markdown_table(filtered_counter)])
    lines.extend(["", "## 原始候选框置信度分布", "", "| 标签 | count | min | p25 | p50 | p75 | max |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"])
    for class_name in CLASS_NAMES.values():
        stats = quantiles(raw_conf_by_class.get(class_name, []))
        label = CLASS_NAMES_CN.get(class_name, class_name)
        if not stats:
            lines.append(f"| {label} ({class_name}) | 0 | - | - | - | - | - |")
        else:
            lines.append(
                f"| {label} ({class_name}) | {stats['count']} | {stats['min']:.4f} | {stats['p25']:.4f} | "
                f"{stats['p50']:.4f} | {stats['p75']:.4f} | {stats['max']:.4f} |"
            )
    lines.extend([
        "",
        "## 文件说明",
        "",
        "- `raw_all_labels/`: 模型原始候选框标注图，最低 conf 与线上推理入口一致为 0.1。",
        "- `filtered_by_config/`: 按 `tias/config.toml` 的 `[Student_Thresd]` 过滤后的标注图。",
        "- `student_behavior_raw_detections.csv`: 原始候选框明细。",
        "- `student_behavior_filtered_detections.csv`: 过滤后明细。",
    ])
    (output_dir / "student_behavior_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-dir", type=Path, default=PROJECT_ROOT / "tests" / "tests_data" / "学生行为")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "tests" / "student_behavior_eval")
    parser.add_argument("--raw-conf", type=float, default=0.1, help="Raw YOLO confidence floor, matching service code.")
    parser.add_argument("--default-threshold", type=float, default=0.15)
    parser.add_argument(
        "--threshold",
        action="append",
        default=[],
        help="Override one student behavior threshold, e.g. --threshold stand=0.99. Can be repeated.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    run_eval(parse_args())
