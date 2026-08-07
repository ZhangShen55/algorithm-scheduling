import csv
import json
import os
import shutil
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.teacher_behavior_service import (
    TEACHER_BEHAVIOR_OBJECT_TYPES,
    collect_teacher_behavior_results,
    get_teacher_behavior_image_size,
    get_teacher_behavior_keep_only_main_subject,
    get_teacher_behavior_main_subject_strategy,
    get_teacher_behavior_merge_iou,
    get_teacher_behavior_predict_conf,
    get_teacher_behavior_subject_cluster_iou,
    yolo_teacher_behavior_model,
)


IMAGE_DIR = ROOT / "tests" / "images"
OUTPUT_DIR = ROOT / "tests" / "teacher_behavior_eval"


def point_dump(position):
    return {
        "LeftTopX": position.LeftTopX,
        "LeftTopY": position.LeftTopY,
        "RightBtmX": position.RightBtmX,
        "RightBtmY": position.RightBtmY,
    }


def result_items(behavior_results):
    return [
        {
            "ObjectType": TEACHER_BEHAVIOR_OBJECT_TYPES["platform_person"],
            "ObjectCount": len(behavior_results["platform_person"]),
            "ObjectPostList": [point_dump(p) for p in behavior_results["platform_person"]],
        },
        {
            "ObjectType": TEACHER_BEHAVIOR_OBJECT_TYPES["standing"],
            "ObjectCount": len(behavior_results["standing"]),
            "ObjectPostList": [point_dump(p) for p in behavior_results["standing"]],
        },
        {
            "ObjectType": TEACHER_BEHAVIOR_OBJECT_TYPES["sitting"],
            "ObjectCount": len(behavior_results["sitting"]),
            "ObjectPostList": [point_dump(p) for p in behavior_results["sitting"]],
        },
        {
            "ObjectType": TEACHER_BEHAVIOR_OBJECT_TYPES["writing"],
            "ObjectCount": len(behavior_results["writing"]),
            "ObjectPostList": [point_dump(p) for p in behavior_results["writing"]],
        },
        {
            "ObjectType": TEACHER_BEHAVIOR_OBJECT_TYPES["teaching"],
            "ObjectCount": len(behavior_results["teaching"]),
            "ObjectPostList": [point_dump(p) for p in behavior_results["teaching"]],
        },
    ]


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    images = sorted(
        p for p in IMAGE_DIR.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    )

    image_size = get_teacher_behavior_image_size()
    predict_conf = get_teacher_behavior_predict_conf()
    merge_iou = get_teacher_behavior_merge_iou()
    subject_cluster_iou = get_teacher_behavior_subject_cluster_iou()
    keep_only_main_subject = get_teacher_behavior_keep_only_main_subject()
    main_subject_strategy = get_teacher_behavior_main_subject_strategy()
    names = yolo_teacher_behavior_model.names

    summary_rows = []
    raw_rows = []
    response_results = []

    for index, image_path in enumerate(images, start=1):
        img = cv2.imread(str(image_path))
        if img is None:
            summary_rows.append({
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
            img,
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

        for det_index, row in enumerate(detections):
            cls_id = int(row[5])
            raw_rows.append({
                "image": image_path.name,
                "det_index": det_index,
                "label": names.get(cls_id, cls_id),
                "confidence": f"{row[4]:.6f}",
                "left": int(row[0]),
                "top": int(row[1]),
                "right": int(row[2]),
                "bottom": int(row[3]),
            })

        summary_rows.append({
            "image": image_path.name,
            "status": "success",
            "raw_detection_count": len(detections),
            "object100_count": len(behavior_results["platform_person"]),
            "standing_count": len(behavior_results["standing"]),
            "sitting_count": len(behavior_results["sitting"]),
            "writing_count": len(behavior_results["writing"]),
            "teaching_count": len(behavior_results["teaching"]),
        })
        response_results.append({
            "ImageId": image_path.name,
            "ResultList": result_items(behavior_results),
        })

        if index % 50 == 0:
            print(f"processed {index}/{len(images)}")

    with (OUTPUT_DIR / "summary.csv").open("w", newline="", encoding="utf-8") as f:
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
        writer.writerows(summary_rows)

    with (OUTPUT_DIR / "raw_detections.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "image",
            "det_index",
            "label",
            "confidence",
            "left",
            "top",
            "right",
            "bottom",
        ])
        writer.writeheader()
        writer.writerows(raw_rows)

    with (OUTPUT_DIR / "response_results.json").open("w", encoding="utf-8") as f:
        json.dump(response_results, f, ensure_ascii=False, indent=2)

    multi_100 = [
        row for row in summary_rows
        if row["status"] == "success" and int(row["object100_count"]) > 1
    ]
    problem_100 = [
        row for row in summary_rows
        if row["status"] == "success" and int(row["object100_count"]) != 1
    ]
    with (OUTPUT_DIR / "multi_100_images.txt").open("w", encoding="utf-8") as f:
        for row in multi_100:
            f.write(f"{row['image']},object100_count={row['object100_count']}\n")

    annotated_dir = OUTPUT_DIR / "annotated_problem"
    if annotated_dir.exists():
        shutil.rmtree(annotated_dir)
    annotated_dir.mkdir(parents=True, exist_ok=True)
    response_by_image = {item["ImageId"]: item for item in response_results}
    colors = {
        100: (0, 255, 255),
        201: (0, 255, 0),
        202: (255, 0, 0),
        203: (0, 0, 255),
        204: (255, 0, 255),
    }
    for row in problem_100:
        image_name = row["image"]
        img = cv2.imread(str(IMAGE_DIR / image_name))
        if img is None:
            continue
        response_result = response_by_image.get(image_name, {"ResultList": []})
        for item in response_result["ResultList"]:
            object_type = item["ObjectType"]
            for box in item.get("ObjectPostList") or []:
                color = colors.get(object_type, (255, 255, 255))
                x1 = box["LeftTopX"]
                y1 = box["LeftTopY"]
                x2 = box["RightBtmX"]
                y2 = box["RightBtmY"]
                cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
                cv2.putText(
                    img,
                    str(object_type),
                    (x1, max(20, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    color,
                    2,
                )
        cv2.putText(
            img,
            f"100={row['object100_count']} raw={row['raw_detection_count']}",
            (20, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 0, 255),
            2,
        )
        cv2.imwrite(str(annotated_dir / image_name), img)

    run_config = {
        "image_dir": str(IMAGE_DIR),
        "image_count": len(images),
        "model": str(ROOT / "app" / "models" / "teacher_behavior.pt"),
        "names": names,
        "image_size": image_size,
        "predict_conf": predict_conf,
        "merge_iou": merge_iou,
        "subject_cluster_iou": subject_cluster_iou,
        "keep_only_main_subject": keep_only_main_subject,
        "main_subject_strategy": main_subject_strategy,
        "outputs": {
            "summary_csv": str(OUTPUT_DIR / "summary.csv"),
            "raw_detections_csv": str(OUTPUT_DIR / "raw_detections.csv"),
            "response_results_json": str(OUTPUT_DIR / "response_results.json"),
            "multi_100_images_txt": str(OUTPUT_DIR / "multi_100_images.txt"),
            "annotated_problem_dir": str(annotated_dir),
        },
    }
    with (OUTPUT_DIR / "run_config.json").open("w", encoding="utf-8") as f:
        json.dump(run_config, f, ensure_ascii=False, indent=2)

    object100_counts = {}
    for row in summary_rows:
        count = row["object100_count"]
        object100_counts[count] = object100_counts.get(count, 0) + 1

    report_lines = [
        "# teacher_behavior.pt eval",
        "",
        f"- images: {len(images)}",
        f"- model names: {names}",
        f"- image_size: {image_size}",
        f"- predict_conf: {predict_conf}",
        f"- merge_iou: {merge_iou}",
        f"- subject_cluster_iou: {subject_cluster_iou}",
        f"- keep_only_main_subject: {keep_only_main_subject}",
        f"- main_subject_strategy: {main_subject_strategy}",
        f"- object100 count distribution: {object100_counts}",
        f"- images with object100_count > 1: {len(multi_100)}",
        f"- images with object100_count != 1: {len(problem_100)}",
        "",
        "## Outputs",
        "",
        "- summary.csv",
        "- raw_detections.csv",
        "- response_results.json",
        "- multi_100_images.txt",
        "- run_config.json",
        "- annotated_problem/",
    ]
    (OUTPUT_DIR / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(f"output_dir={OUTPUT_DIR}")
    print(f"images={len(images)}")
    print(f"object100_count_distribution={object100_counts}")
    print(f"multi_100_images={len(multi_100)}")
    for row in multi_100[:20]:
        print(f"multi100 {row['image']} count={row['object100_count']}")

    os._exit(0)


if __name__ == "__main__":
    main()
