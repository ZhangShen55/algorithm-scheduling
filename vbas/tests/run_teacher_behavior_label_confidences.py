import argparse
import csv
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.teacher_behavior_service import (
    collect_teacher_behavior_group_details,
    get_teacher_behavior_image_size,
    get_teacher_behavior_merge_iou,
    get_teacher_behavior_predict_conf,
    yolo_teacher_behavior_model,
)


LABEL_MAP = {
    "platform_person": ("100", "主体"),
    "sitting": ("201", "坐着"),
    "standing": ("202", "站立"),
    "writing": ("203", "板书"),
    "teaching": ("204", "讲授"),
}
LABEL_ORDER = ["platform_person", "sitting", "standing", "writing", "teaching"]


def parse_args():
    parser = argparse.ArgumentParser(description="Export final teacher_behavior label confidences.")
    parser.add_argument("--image-dir", default=str(ROOT / "tests" / "images2"))
    parser.add_argument(
        "--image-list",
        default=str(ROOT / "tests" / "teacher_behavior_drawn_images2" / "sit_and_stand_images.txt"),
    )
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "tests" / "teacher_behavior_drawn_images2" / "label_confidences.csv"),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    image_dir = Path(args.image_dir).resolve()
    image_list_path = Path(args.image_list).resolve()
    output_csv = Path(args.output_csv).resolve()

    image_names = [
        line.split(",", 1)[0]
        for line in image_list_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows = []
    for image_name in image_names:
        img = cv2.imread(str(image_dir / image_name))
        if img is None:
            continue
        result = yolo_teacher_behavior_model.predict(
            img,
            imgsz=get_teacher_behavior_image_size(),
            conf=get_teacher_behavior_predict_conf(),
            verbose=False,
        )[0]
        detections = result.boxes.data.tolist() if result.boxes is not None else []
        names = getattr(result, "names", None) or getattr(yolo_teacher_behavior_model, "names", {})
        details = collect_teacher_behavior_group_details(
            detections,
            names,
            offset=(0, 0),
            merge_iou=get_teacher_behavior_merge_iou(),
        )
        for subject_index, detail in enumerate(details):
            position = detail["position"]
            for label in LABEL_ORDER:
                confidence = detail["confidences"].get(label)
                if confidence is None:
                    continue
                object_type, label_cn = LABEL_MAP[label]
                rows.append({
                    "image": image_name,
                    "subject_index": subject_index,
                    "object_type": object_type,
                    "label": label,
                    "label_cn": label_cn,
                    "confidence": f"{confidence:.6f}",
                    "left": position.LeftTopX,
                    "top": position.LeftTopY,
                    "right": position.RightBtmX,
                    "bottom": position.RightBtmY,
                })

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "image",
                "subject_index",
                "object_type",
                "label",
                "label_cn",
                "confidence",
                "left",
                "top",
                "right",
                "bottom",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"rows={len(rows)}")
    print(f"output_csv={output_csv}")


if __name__ == "__main__":
    main()
