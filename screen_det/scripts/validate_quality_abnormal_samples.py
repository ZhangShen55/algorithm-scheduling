#!/usr/bin/env python3
"""服务层验收：验证画面异常四类样例和正常负例。"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.quality_abnormal_detector import (
    ABNORMAL_BLUR,
    ABNORMAL_COLOR_CAST,
    ABNORMAL_GLITCH,
    ABNORMAL_SNOW_NOISE,
    detect_quality_abnormal_from_base64,
)


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
REPRESENTATIVE_CASES = [
    ("虚焦", ROOT / "test/图像检测/画面异常/虚焦/重度虚焦/教室监控图重度虚焦处理.png", ABNORMAL_BLUR),
    ("偏色", ROOT / "test/图像检测/画面异常/偏色/偏色1.png", ABNORMAL_COLOR_CAST),
    ("雪花噪点", ROOT / "test/图像检测/画面异常/雪花噪点/雪花噪点1.png", ABNORMAL_SNOW_NOISE),
    ("花屏", ROOT / "test/图像检测/画面异常/花屏/ChatGPT_Image_2026年7月13日_14_45_52_(1).png", ABNORMAL_GLITCH),
]
NORMAL_NEGATIVE = ROOT / "test/ok_img/snapshot_计算机科学导论.png"


def image_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def image_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(path for path in folder.rglob("*") if path.suffix.lower() in IMAGE_SUFFIXES)


def detect(path: Path) -> dict:
    result = detect_quality_abnormal_from_base64(image_base64(path))
    return {
        "file": str(path.relative_to(ROOT)),
        "is_abnormal": result.is_abnormal,
        "abnormal_types": result.abnormal_types,
        "results": [
            {"type": item.type, "score": item.score, "message": item.message}
            for item in result.results
        ],
        "message": result.message,
    }


def main() -> int:
    failures: list[str] = []
    rows: list[dict] = []

    for name, path, expected_type in REPRESENTATIVE_CASES:
        if not path.exists():
            failures.append(f"{name}: 样例不存在 {path}")
            continue
        row = detect(path)
        row["case"] = name
        row["expected_type"] = expected_type
        row["passed"] = expected_type in row["abnormal_types"]
        rows.append(row)
        if not row["passed"]:
            failures.append(f"{name}: 未命中 expected_type={expected_type}")

    if NORMAL_NEGATIVE.exists():
        row = detect(NORMAL_NEGATIVE)
        row["case"] = "正常负例"
        row["passed"] = not row["is_abnormal"]
        rows.append(row)
        if not row["passed"]:
            failures.append("正常负例: 误判为画面异常")
    else:
        failures.append(f"正常负例不存在 {NORMAL_NEGATIVE}")

    sample_root = ROOT / "test/图像检测/画面异常"
    for path in image_files(sample_root):
        if path in {case_path for _, case_path, _ in REPRESENTATIVE_CASES}:
            continue
        row = detect(path)
        row["case"] = "目录样例"
        rows.append(row)

    print(json.dumps(rows, ensure_ascii=False, indent=2))
    if failures:
        print("\nFAIL:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("\nPASS: 画面异常代表样例与正常负例通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
