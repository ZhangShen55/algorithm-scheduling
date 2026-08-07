#!/usr/bin/env python3
"""服务层验收：验证镜头遮挡代表样例和正常负例。"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.occlusion_detector import detect_occlusion_from_base64


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
VALIDATED_YOLO_POSITIVES = [
    ROOT / "test/图像检测/遮挡/横幅遮挡.png",
    ROOT / "test/图像检测/遮挡/易拉宝遮挡.png",
    ROOT / "test/图像检测/遮挡/条幅遮挡.png",
    ROOT / "test/图像检测/遮挡/红色横幅遮挡大部分.jpg",
]
NORMAL_NEGATIVE = ROOT / "test/ok_img/snapshot_计算机科学导论.png"


def image_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def image_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(path for path in folder.rglob("*") if path.suffix.lower() in IMAGE_SUFFIXES)


def detect(path: Path) -> dict:
    result = detect_occlusion_from_base64(image_base64(path))
    return {
        "file": str(path.relative_to(ROOT)),
        "is_occluded": result.is_occluded,
        "occlusion_area_ratio": result.occlusion_area_ratio,
        "score": result.score,
        "threshold": result.threshold,
        "area_ratio": result.area_ratio,
        "message": result.message,
    }


def main() -> int:
    failures: list[str] = []
    rows: list[dict] = []

    for path in VALIDATED_YOLO_POSITIVES:
        if not path.exists():
            failures.append(f"遮挡样例不存在 {path}")
            continue
        row = detect(path)
        row["case"] = "YOLO已确认遮挡正例"
        row["passed"] = row["is_occluded"] and row["occlusion_area_ratio"] > 0
        rows.append(row)
        if not row["passed"]:
            failures.append(f"{path.relative_to(ROOT)}: 未判定为遮挡")

    if NORMAL_NEGATIVE.exists():
        row = detect(NORMAL_NEGATIVE)
        row["case"] = "正常负例"
        row["passed"] = not row["is_occluded"]
        rows.append(row)
        if not row["passed"]:
            failures.append("正常负例: 误判为镜头遮挡")
    else:
        failures.append(f"正常负例不存在 {NORMAL_NEGATIVE}")

    sample_root = ROOT / "test/图像检测/遮挡"
    representative_set = set(VALIDATED_YOLO_POSITIVES)
    for path in image_files(sample_root):
        if path in representative_set:
            continue
        row = detect(path)
        row["case"] = "目录样例-仅报告"
        rows.append(row)

    print(json.dumps(rows, ensure_ascii=False, indent=2))
    if failures:
        print("\nFAIL:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("\nPASS: YOLO已确认遮挡正例与正常负例通过；其他目录样例仅报告不作为硬性失败")
    return 0


if __name__ == "__main__":
    sys.exit(main())
