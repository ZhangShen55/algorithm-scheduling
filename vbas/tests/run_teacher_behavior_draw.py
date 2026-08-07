import csv
import json
import os
import argparse
import shutil
import sys
from pathlib import Path

import cv2
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.teacher_behavior_service import (
    TEACHER_BEHAVIOR_OBJECT_TYPES,
    collect_teacher_behavior_group_details,
    collect_teacher_behavior_results,
    get_teacher_behavior_image_size,
    get_teacher_behavior_keep_only_main_subject,
    get_teacher_behavior_main_subject_strategy,
    get_teacher_behavior_merge_iou,
    get_teacher_behavior_predict_conf,
    get_teacher_behavior_subject_cluster_iou,
    yolo_teacher_behavior_model,
)


DEFAULT_IMAGE_DIR = ROOT / "tests" / "images"
DEFAULT_OUTPUT_DIR = ROOT / "tests" / "teacher_behavior_drawn"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
FONT_CANDIDATES = [
    Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/System/Library/Fonts/Supplemental/Songti.ttc"),
]
OBJECT_TYPE_LABELS = {
    TEACHER_BEHAVIOR_OBJECT_TYPES["platform_person"]: "主体",
    TEACHER_BEHAVIOR_OBJECT_TYPES["sitting"]: "坐着",
    TEACHER_BEHAVIOR_OBJECT_TYPES["standing"]: "站立",
    TEACHER_BEHAVIOR_OBJECT_TYPES["writing"]: "板书",
    TEACHER_BEHAVIOR_OBJECT_TYPES["teaching"]: "讲授",
}
BEHAVIOR_LABEL_ORDER = (
    ("platform_person", TEACHER_BEHAVIOR_OBJECT_TYPES["platform_person"]),
    ("sitting", TEACHER_BEHAVIOR_OBJECT_TYPES["sitting"]),
    ("standing", TEACHER_BEHAVIOR_OBJECT_TYPES["standing"]),
    ("writing", TEACHER_BEHAVIOR_OBJECT_TYPES["writing"]),
    ("teaching", TEACHER_BEHAVIOR_OBJECT_TYPES["teaching"]),
)


def parse_args():
    parser = argparse.ArgumentParser(description="Run teacher_behavior detection and draw Chinese labels.")
    parser.add_argument(
        "--image-dir",
        default=str(DEFAULT_IMAGE_DIR),
        help="Input image directory.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Output directory for summary and annotated images.",
    )
    return parser.parse_args()


def load_font(image_width: int, image_height: int):
    size = max(24, min(42, min(image_width, image_height) // 28))
    for font_path in FONT_CANDIDATES:
        if font_path.exists():
            return ImageFont.truetype(str(font_path), size=size)
    return ImageFont.load_default()


def position_key(position):
    return (
        position.LeftTopX,
        position.LeftTopY,
        position.RightBtmX,
        position.RightBtmY,
    )


def format_label_confidence(label, confidence):
    return f"{label} {confidence:.2f}"


def collect_box_labels(group_details):
    labels_by_box = {}
    for detail in group_details:
        box = position_key(detail["position"])
        confidences = detail["confidences"]
        labels = []
        for behavior_key, object_type in BEHAVIOR_LABEL_ORDER:
            confidence = confidences.get(behavior_key)
            if confidence is None:
                continue
            labels.append(format_label_confidence(OBJECT_TYPE_LABELS[object_type], confidence))
        labels_by_box[box] = labels
    return labels_by_box


def text_size(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def draw_labeled_box(draw, box, labels, font, image_width):
    x1, y1, x2, y2 = box
    label_text = " | ".join(labels)
    box_color = (255, 218, 0)
    fill_color = (0, 0, 0)
    text_color = (255, 255, 255)
    line_width = 4
    padding_x = 8
    padding_y = 5

    draw.rectangle((x1, y1, x2, y2), outline=box_color, width=line_width)

    text_w, text_h = text_size(draw, label_text, font)
    label_w = text_w + padding_x * 2
    label_h = text_h + padding_y * 2
    label_x1 = max(0, min(x1, image_width - label_w))
    label_y1 = max(0, y1 - label_h)
    label_y2 = label_y1 + label_h
    draw.rectangle(
        (label_x1, label_y1, label_x1 + label_w, label_y2),
        fill=fill_color,
        outline=box_color,
        width=2,
    )
    draw.text(
        (label_x1 + padding_x, label_y1 + padding_y),
        label_text,
        font=font,
        fill=text_color,
    )


def draw_no_detection(draw, font):
    text = "未检测到老师主体"
    text_w, text_h = text_size(draw, text, font)
    padding_x = 10
    padding_y = 6
    draw.rectangle(
        (16, 16, 16 + text_w + padding_x * 2, 16 + text_h + padding_y * 2),
        fill=(0, 0, 0),
        outline=(255, 0, 0),
        width=2,
    )
    draw.text((16 + padding_x, 16 + padding_y), text, font=font, fill=(255, 255, 255))


def count_result(behavior_results, key):
    return len(behavior_results[key])


def main():
    args = parse_args()
    image_dir = Path(args.image_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    annotated_dir = output_dir / "images"

    if not image_dir.exists():
        raise FileNotFoundError(f"image directory does not exist: {image_dir}")

    if annotated_dir.exists():
        shutil.rmtree(annotated_dir)
    annotated_dir.mkdir(parents=True, exist_ok=True)

    images = sorted(
        p for p in image_dir.iterdir()
        if p.suffix.lower() in IMAGE_SUFFIXES
    )

    image_size = get_teacher_behavior_image_size()
    predict_conf = get_teacher_behavior_predict_conf()
    merge_iou = get_teacher_behavior_merge_iou()
    subject_cluster_iou = get_teacher_behavior_subject_cluster_iou()
    keep_only_main_subject = get_teacher_behavior_keep_only_main_subject()
    main_subject_strategy = get_teacher_behavior_main_subject_strategy()
    names = yolo_teacher_behavior_model.names
    rows = []

    for index, image_path in enumerate(images, start=1):
        img_bgr = cv2.imread(str(image_path))
        if img_bgr is None:
            rows.append({
                "image": image_path.name,
                "status": "failed_read",
                "raw_detection_count": 0,
                "object100_count": 0,
                "standing_count": 0,
                "sitting_count": 0,
                "writing_count": 0,
                "teaching_count": 0,
            })
            continue

        result = yolo_teacher_behavior_model.predict(
            img_bgr,
            imgsz=image_size,
            conf=predict_conf,
            verbose=False,
        )[0]
        detections = result.boxes.data.tolist() if result.boxes is not None else []
        behavior_results = collect_teacher_behavior_results(
            detections,
            names,
            offset=(0, 0),
            merge_iou=merge_iou,
        )
        group_details = collect_teacher_behavior_group_details(
            detections,
            names,
            offset=(0, 0),
            merge_iou=merge_iou,
        )

        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        draw = ImageDraw.Draw(pil_img)
        font = load_font(pil_img.width, pil_img.height)
        labels_by_box = collect_box_labels(group_details)
        if labels_by_box:
            for box, labels in labels_by_box.items():
                draw_labeled_box(draw, box, labels, font, pil_img.width)
        else:
            draw_no_detection(draw, font)

        output_path = annotated_dir / image_path.name
        pil_img.save(str(output_path), quality=95)

        rows.append({
            "image": image_path.name,
            "status": "success",
            "raw_detection_count": len(detections),
            "object100_count": count_result(behavior_results, "platform_person"),
            "standing_count": count_result(behavior_results, "standing"),
            "sitting_count": count_result(behavior_results, "sitting"),
            "writing_count": count_result(behavior_results, "writing"),
            "teaching_count": count_result(behavior_results, "teaching"),
        })

        if index % 50 == 0:
            print(f"processed {index}/{len(images)}")

    with (output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "image",
            "status",
            "raw_detection_count",
            "object100_count",
            "standing_count",
            "sitting_count",
            "writing_count",
            "teaching_count",
        ])
        writer.writeheader()
        writer.writerows(rows)

    run_config = {
        "image_dir": str(image_dir),
        "output_dir": str(annotated_dir),
        "image_count": len(images),
        "model": str(ROOT / "app" / "models" / "teacher_behavior.pt"),
        "names": names,
        "image_size": image_size,
        "predict_conf": predict_conf,
        "merge_iou": merge_iou,
        "subject_cluster_iou": subject_cluster_iou,
        "keep_only_main_subject": keep_only_main_subject,
        "main_subject_strategy": main_subject_strategy,
        "label_confidence": True,
        "labels": OBJECT_TYPE_LABELS,
    }
    with (output_dir / "run_config.json").open("w", encoding="utf-8") as f:
        json.dump(run_config, f, ensure_ascii=False, indent=2)

    object100_counts = {}
    for row in rows:
        count = row["object100_count"]
        object100_counts[count] = object100_counts.get(count, 0) + 1

    report_lines = [
        "# teacher_behavior drawn results",
        "",
        f"- images: {len(images)}",
        f"- output images: {len(list(annotated_dir.glob('*')))}",
        f"- model names: {names}",
        f"- image_size: {image_size}",
        f"- predict_conf: {predict_conf}",
        f"- merge_iou: {merge_iou}",
        f"- subject_cluster_iou: {subject_cluster_iou}",
        f"- keep_only_main_subject: {keep_only_main_subject}",
        f"- main_subject_strategy: {main_subject_strategy}",
        "- label_confidence: true",
        f"- object100 count distribution: {object100_counts}",
        "",
        "## Chinese labels",
        "",
        "- 100 主体",
        "- 201 坐着",
        "- 202 站立",
        "- 203 板书",
        "- 204 讲授",
    ]
    (output_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(f"output_dir={output_dir}")
    print(f"annotated_dir={annotated_dir}")
    print(f"images={len(images)}")
    print(f"object100_count_distribution={object100_counts}")
    os._exit(0)


if __name__ == "__main__":
    main()
