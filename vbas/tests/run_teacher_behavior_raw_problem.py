import csv
import json
import shutil
import sys
from pathlib import Path

import cv2
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.teacher_behavior_service import (
    get_teacher_behavior_image_size,
    get_teacher_behavior_predict_conf,
    yolo_teacher_behavior_model,
)


IMAGE_IDS = [19, 181, 205, 62, 63, 64, 66, 647, 55, 688, 125, 213]
PROBLEM_CATEGORIES = {
    19: "检测结果不是人物",
    181: "检测结果不是人物",
    205: "检测结果不是人物",
    62: "检测结果不是人物",
    63: "检测结果不是人物",
    64: "检测结果不是人物",
    66: "检测结果不是人物",
    647: "检测结果不是人物",
    55: "讲台老师没检测到，检测到坐在下面的学生",
    688: "讲台老师没检测到，检测到坐在下面的学生",
    125: "未检测到对象",
    213: "未检测到对象",
}
LABEL_CN = {
    "sit": "坐着",
    "stand": "站立",
    "bbwriting": "板书",
    "teach": "讲授",
}
COLORS = {
    "sit": (40, 120, 255),
    "stand": (40, 220, 40),
    "bbwriting": (255, 80, 60),
    "teach": (230, 80, 230),
}
FONT_CANDIDATES = [
    Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/System/Library/Fonts/Supplemental/Songti.ttc"),
]
OUTPUT_DIR = ROOT / "tests" / "teacher_behavior_raw_outputs"
IMAGE_OUTPUT_DIR = OUTPUT_DIR / "images"


def load_font(width, height):
    size = max(22, min(40, min(width, height) // 30))
    for font_path in FONT_CANDIDATES:
        if font_path.exists():
            return ImageFont.truetype(str(font_path), size=size)
    return ImageFont.load_default()


def text_size(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def draw_label(draw, x1, y1, text, font, color, image_width):
    text_w, text_h = text_size(draw, text, font)
    padding_x = 8
    padding_y = 5
    label_w = text_w + padding_x * 2
    label_h = text_h + padding_y * 2
    label_x = max(0, min(x1, image_width - label_w))
    label_y = max(0, y1 - label_h)
    draw.rectangle(
        (label_x, label_y, label_x + label_w, label_y + label_h),
        fill=(0, 0, 0),
        outline=color,
        width=2,
    )
    draw.text((label_x + padding_x, label_y + padding_y), text, font=font, fill=(255, 255, 255))


def draw_no_raw_detection(draw, font):
    text = "无原始检测框"
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


def main():
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    IMAGE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    image_size = get_teacher_behavior_image_size()
    predict_conf = get_teacher_behavior_predict_conf()
    names = yolo_teacher_behavior_model.names
    summary_rows = []
    detection_rows = []

    for image_id in IMAGE_IDS:
        matches = sorted((ROOT / "tests" / "images").glob(f"{image_id:08d}-*.jpg"))
        if not matches:
            summary_rows.append({
                "id": image_id,
                "image": "",
                "problem_category": PROBLEM_CATEGORIES[image_id],
                "raw_detection_count": 0,
                "status": "missing_file",
            })
            continue

        image_path = matches[0]
        img_bgr = cv2.imread(str(image_path))
        if img_bgr is None:
            summary_rows.append({
                "id": image_id,
                "image": image_path.name,
                "problem_category": PROBLEM_CATEGORIES[image_id],
                "raw_detection_count": 0,
                "status": "failed_read",
            })
            continue

        result = yolo_teacher_behavior_model.predict(
            img_bgr,
            imgsz=image_size,
            conf=predict_conf,
            verbose=False,
        )[0]
        detections = result.boxes.data.tolist() if result.boxes is not None else []

        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        draw = ImageDraw.Draw(pil_img)
        font = load_font(pil_img.width, pil_img.height)

        for det_index, row in enumerate(detections):
            x1, y1, x2, y2, score, cls_id = row[:6]
            label = names.get(int(cls_id), str(int(cls_id)))
            label_cn = LABEL_CN.get(label, label)
            color = COLORS.get(label, (255, 218, 0))
            x1i, y1i, x2i, y2i = map(int, [x1, y1, x2, y2])
            draw.rectangle((x1i, y1i, x2i, y2i), outline=color, width=4)
            draw_label(draw, x1i, y1i, f"{label_cn} {score:.2f}", font, color, pil_img.width)
            detection_rows.append({
                "id": image_id,
                "image": image_path.name,
                "problem_category": PROBLEM_CATEGORIES[image_id],
                "det_index": det_index,
                "label": label,
                "label_cn": label_cn,
                "confidence": f"{score:.6f}",
                "left": x1i,
                "top": y1i,
                "right": x2i,
                "bottom": y2i,
            })

        if not detections:
            draw_no_raw_detection(draw, font)

        pil_img.save(str(IMAGE_OUTPUT_DIR / image_path.name), quality=95)
        summary_rows.append({
            "id": image_id,
            "image": image_path.name,
            "problem_category": PROBLEM_CATEGORIES[image_id],
            "raw_detection_count": len(detections),
            "status": "success",
        })

    with (OUTPUT_DIR / "raw_detections.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "id",
            "image",
            "problem_category",
            "det_index",
            "label",
            "label_cn",
            "confidence",
            "left",
            "top",
            "right",
            "bottom",
        ])
        writer.writeheader()
        writer.writerows(detection_rows)

    with (OUTPUT_DIR / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "id",
            "image",
            "problem_category",
            "raw_detection_count",
            "status",
        ])
        writer.writeheader()
        writer.writerows(summary_rows)

    run_config = {
        "image_size": image_size,
        "predict_conf": predict_conf,
        "model": str(ROOT / "app" / "models" / "teacher_behavior.pt"),
        "names": names,
        "note": "Raw YOLO predict boxes only; no IoU merge, no main-subject filtering, no post-processing.",
    }
    (OUTPUT_DIR / "run_config.json").write_text(
        json.dumps(run_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report_lines = [
        "# teacher_behavior raw outputs",
        "",
        f"- images: {len(summary_rows)}",
        f"- output images: {len(list(IMAGE_OUTPUT_DIR.glob('*.jpg')))}",
        f"- image_size: {image_size}",
        f"- predict_conf: {predict_conf}",
        "- postprocess: disabled",
        "",
    ]
    for row in summary_rows:
        report_lines.append(
            f"- {row['id']} {row['image']}: raw_detection_count={row['raw_detection_count']} "
            f"({row['problem_category']})"
        )
    (OUTPUT_DIR / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(f"output_dir={OUTPUT_DIR}")
    print(f"images={len(summary_rows)}")
    print(f"detections={len(detection_rows)}")


if __name__ == "__main__":
    main()
