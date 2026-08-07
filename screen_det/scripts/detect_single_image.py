#!/usr/bin/env python3
"""
单张图片 YOLO 检测（model/screen.pt）。

用法（conda 环境 screen_det）:
  conda activate screen_det
  python scripts/detect_single_image.py model/ok_img/snapshot_英语视听说（Ⅱ）.png
  python scripts/detect_single_image.py test/text0.jpg --conf 0.3 --save
  python scripts/detect_single_image.py /path/to/image.jpg --device 0
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_WEIGHTS = ROOT / "model" / "screen.pt"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

from app.services.yolo_compat import patch_legacy_aattn


def _boxes_to_list(result, names: dict) -> list[dict]:
    if result.boxes is None or len(result.boxes) == 0:
        return []
    items = []
    boxes = result.boxes
    for i in range(len(boxes)):
        cls_id = int(boxes.cls[i].item())
        items.append(
            {
                "class_id": cls_id,
                "label": names.get(cls_id, str(cls_id)),
                "confidence": round(float(boxes.conf[i].item()), 4),
                "box_xyxy": [round(float(v), 1) for v in boxes.xyxy[i].tolist()],
            }
        )
    items.sort(key=lambda x: x["confidence"], reverse=True)
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description="单图 screen.pt 检测")
    parser.add_argument("image", type=Path, help="图片路径")
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS, help="权重路径")
    parser.add_argument("--conf", type=float, default=0.25, help="置信度阈值")
    parser.add_argument("--iou", type=float, default=0.45, help="NMS IoU")
    parser.add_argument("--device", default="", help="设备：0 / cpu，默认自动")
    parser.add_argument(
        "--save",
        action="store_true",
        help="保存带框图片到 runs/detect_single/",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出结果",
    )
    args = parser.parse_args()

    image = args.image.expanduser().resolve()
    if not image.exists():
        print(f"ERROR: 图片不存在: {image}", file=sys.stderr)
        sys.exit(1)
    if image.suffix.lower() not in IMAGE_SUFFIXES:
        print(f"WARNING: 后缀可能不受支持: {image.suffix}", file=sys.stderr)

    weights = args.weights.resolve()
    if not weights.exists():
        print(f"ERROR: 权重不存在: {weights}", file=sys.stderr)
        sys.exit(1)

    try:
        from ultralytics import YOLO
    except ImportError:
        print("ERROR: 请安装 ultralytics: pip install ultralytics torch", file=sys.stderr)
        sys.exit(2)

    model = YOLO(str(weights))
    patched = patch_legacy_aattn(model)
    names = model.names

    predict_kw: dict = {"conf": args.conf, "iou": args.iou, "verbose": False}
    if args.device:
        predict_kw["device"] = args.device
    if args.save:
        predict_kw["save"] = True
        predict_kw["project"] = str(ROOT / "runs")
        predict_kw["name"] = "detect_single"
        predict_kw["exist_ok"] = True

    try:
        result = model.predict(str(image), **predict_kw)[0]
    except Exception as exc:
        print(f"ERROR: 推理失败: {exc}", file=sys.stderr)
        sys.exit(3)

    detections = _boxes_to_list(result, names)
    primary = detections[0] if detections else None

    output = {
        "image": str(image),
        "weights": str(weights),
        "conf_threshold": args.conf,
        "num_detections": len(detections),
        "primary": primary,
        "detections": detections,
    }

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    print(f"image:   {image}")
    print(f"weights: {weights}")
    print(f"conf:    {args.conf}")
    if patched:
        print(f"patch:   AAttn x{patched}")
    print()

    if not detections:
        print("结果: 无检测框（可降低 --conf 或换图重试）")
    else:
        print(f"主结果: {primary['label']}  conf={primary['confidence']:.3f}")
        print(f"检测框数: {len(detections)}")
        print("--- 全部检测 ---")
        for i, det in enumerate(detections, 1):
            x1, y1, x2, y2 = det["box_xyxy"]
            print(
                f"  [{i}] {det['label']:16s} conf={det['confidence']:.3f}  "
                f"box=[{x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f}]"
            )

    if args.save:
        print()
        print(f"可视化: {ROOT / 'runs' / 'detect_single'}")


if __name__ == "__main__":
    main()
