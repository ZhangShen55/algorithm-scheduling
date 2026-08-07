#!/usr/bin/env python3
"""
对 model/ok_img、model/error_img 中的测试图运行 screen.pt 检测。

用法（conda 环境 screen_det）:
  conda activate screen_det
  pip install ultralytics torch   # 若已安装可跳过
  python scripts/detect_screen_samples.py
  python scripts/detect_screen_samples.py --conf 0.25 --save

依赖: ultralytics, torch（screen.pt 为 YOLO12 自定义 10 类权重）
脚本会自动修补旧版 AAttn（qk+v）与新版 ultralytics 的兼容问题。
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.yolo_compat import patch_legacy_aattn

DEFAULT_WEIGHTS = ROOT / "model" / "screen.pt"
IMAGE_DIRS = (
    ("ok", ROOT / "model" / "ok_img"),
    ("error", ROOT / "model" / "error_img"),
)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _check_ultralytics_version() -> str:
    try:
        import ultralytics
    except ImportError as exc:
        print(
            "ERROR: 未安装 ultralytics。请执行:\n"
            "  pip install ultralytics torch",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    return ultralytics.__version__


def _collect_images() -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = []
    for group, folder in IMAGE_DIRS:
        if not folder.is_dir():
            print(f"WARNING: 目录不存在，跳过: {folder}", file=sys.stderr)
            continue
        for path in sorted(folder.iterdir()):
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                items.append((group, path))
    return items


def _format_boxes(result, names: dict) -> list[str]:
    if result.boxes is None or len(result.boxes) == 0:
        return []
    lines = []
    boxes = result.boxes
    for i in range(len(boxes)):
        cls_id = int(boxes.cls[i].item())
        conf = float(boxes.conf[i].item())
        label = names.get(cls_id, str(cls_id))
        xyxy = boxes.xyxy[i].tolist()
        lines.append(
            f"    - {label} conf={conf:.3f} "
            f"box=[{xyxy[0]:.0f},{xyxy[1]:.0f},{xyxy[2]:.0f},{xyxy[3]:.0f}]"
        )
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect test images with screen.pt")
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--conf", type=float, default=0.25, help="置信度阈值")
    parser.add_argument("--iou", type=float, default=0.45, help="NMS IoU")
    parser.add_argument(
        "--save",
        action="store_true",
        help="保存带框结果图到 runs/detect_screen_samples/",
    )
    parser.add_argument(
        "--device",
        default="",
        help="推理设备，如 0 或 cpu，默认自动",
    )
    args = parser.parse_args()

    weights = args.weights.resolve()
    if not weights.exists():
        print(f"ERROR: 权重不存在: {weights}", file=sys.stderr)
        sys.exit(1)

    version = _check_ultralytics_version()
    images = _collect_images()
    if not images:
        print("ERROR: ok_img / error_img 下未找到图片", file=sys.stderr)
        sys.exit(1)

    from ultralytics import YOLO

    print(f"ultralytics: {version}")
    print(f"weights: {weights}")
    print(f"images: {len(images)} (ok + error)")
    print(f"conf={args.conf} iou={args.iou}")
    print()

    try:
        model = YOLO(str(weights))
    except Exception as exc:
        print(f"ERROR: 加载模型失败: {exc}", file=sys.stderr)
        sys.exit(3)

    patched = patch_legacy_aattn(model)
    if patched:
        print(f"已修补旧版 AAttn 模块: {patched} 个")

    names = model.names
    print("classes:", {k: names[k] for k in sorted(names, key=lambda x: int(x))})
    print()

    predict_kw: dict = {
        "conf": args.conf,
        "iou": args.iou,
        "verbose": False,
    }
    if args.device:
        predict_kw["device"] = args.device
    if args.save:
        predict_kw["save"] = True
        predict_kw["project"] = str(ROOT / "runs")
        predict_kw["name"] = "detect_screen_samples"
        predict_kw["exist_ok"] = True

    box_counter: Counter[str] = Counter()
    image_counter: Counter[str] = Counter()
    no_detect = 0

    for group, path in images:
        try:
            result = model.predict(str(path), **predict_kw)[0]
        except Exception as exc:
            print(f"[{group}] {path.name}")
            print(f"  ERROR: {exc}")
            print()
            continue

        label = f"{group}/{path.name}"
        print(f"[{group}] {path.name}")

        if result.boxes is None or len(result.boxes) == 0:
            no_detect += 1
            image_counter["(无检测)"] += 1
            print("  (无检测框)")
        else:
            # 主结果：置信度最高的框
            best_i = int(result.boxes.conf.argmax().item())
            best_cls = int(result.boxes.cls[best_i].item())
            best_name = names[best_cls]
            best_conf = float(result.boxes.conf[best_i].item())
            image_counter[best_name] += 1
            print(f"  主类别: {best_name} conf={best_conf:.3f}  框数={len(result.boxes)}")
            for line in _format_boxes(result, names):
                print(line)
            for i in range(len(result.boxes)):
                cls_id = int(result.boxes.cls[i].item())
                box_counter[names[cls_id]] += 1
        print()

    total_boxes = sum(box_counter.values())
    print("=" * 50)
    print("按图统计（每张取置信度最高的类别）:")
    for name, cnt in image_counter.most_common():
        pct = cnt / len(images) * 100
        print(f"  {name:20s} {cnt:3d}  {pct:5.1f}%")
    print(f"  无检测: {no_detect}")

    if total_boxes:
        print()
        print("按检测框统计（一张图多个框会重复计数）:")
        for name, cnt in box_counter.most_common():
            pct = cnt / total_boxes * 100
            print(f"  {name:20s} {cnt:3d}  {pct:5.1f}%")
        print(f"  总框数: {total_boxes}")

    if args.save:
        print()
        print(f"可视化结果: {ROOT / 'runs' / 'detect_screen_samples'}")


if __name__ == "__main__":
    main()
