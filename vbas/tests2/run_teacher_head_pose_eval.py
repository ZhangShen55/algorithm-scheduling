import argparse
import csv
import importlib.util
import json
import shutil
import sys
import tempfile
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
DEFAULT_IMAGE_DIR = ROOT / "tests2" / "images"
DEFAULT_OUTPUT_DIR = ROOT / "tests2" / "head_pose_outputs"
DEFAULT_DIRECTMHP_ROOT = ROOT / "app" / "vendor" / "DirectMHP"
DEFAULT_DIRECTMHP_WEIGHTS = ROOT / "app" / "models" / "cmu_m_1280_e200_t40_lw010_best.pt"
DEFAULT_DIRECTMHP_DATA = ROOT / "app" / "models" / "cmu_panoptic_coco.yaml"
DIRECTMHP_RUNTIME_DEPENDENCIES = [
    (
        "pkg_resources",
        "setuptools<81",
        "DirectMHP 的 YOLOv5 旧代码依赖 pkg_resources；当前新版本 setuptools 可能不再提供该模块",
    ),
    (
        "seaborn",
        "seaborn>=0.11.0",
        "DirectMHP 导入 utils.plots 时需要 seaborn",
    ),
]
FONT_CANDIDATES = [
    Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/System/Library/Fonts/Supplemental/Songti.ttc"),
]


@dataclass(frozen=True)
class DirectionThresholds:
    front_yaw: float = 20.0
    side_yaw: float = 25.0
    down_pitch: float = 25.0
    board_yaw: float = 35.0


@dataclass(frozen=True)
class FaceDirection:
    direction: str
    direction_name: str
    reason: str


def parse_args():
    parser = argparse.ArgumentParser(
        description="Evaluate teacher face/head direction with teacher_behavior + DirectMHP."
    )
    parser.add_argument("--image-dir", default=str(DEFAULT_IMAGE_DIR), help="Input image directory.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory.")
    parser.add_argument("--directmhp-root", default=str(DEFAULT_DIRECTMHP_ROOT), help="Offline DirectMHP source path.")
    parser.add_argument("--directmhp-weights", default=str(DEFAULT_DIRECTMHP_WEIGHTS), help="DirectMHP .pt weight path.")
    parser.add_argument("--directmhp-data", default=str(DEFAULT_DIRECTMHP_DATA), help="DirectMHP data yaml path.")
    parser.add_argument("--device", default="0", help="DirectMHP device, e.g. 0 or cpu.")
    parser.add_argument("--imgsz", type=int, default=1280, help="DirectMHP inference image size.")
    parser.add_argument("--conf-thres", type=float, default=0.35, help="DirectMHP head confidence threshold.")
    parser.add_argument("--iou-thres", type=float, default=0.45, help="DirectMHP NMS IoU threshold.")
    parser.add_argument("--crop-scale", type=float, default=1.35, help="Teacher subject crop expansion scale.")
    parser.add_argument("--front-yaw", type=float, default=20.0)
    parser.add_argument("--side-yaw", type=float, default=25.0)
    parser.add_argument("--down-pitch", type=float, default=25.0)
    parser.add_argument("--board-yaw", type=float, default=35.0)
    parser.add_argument("--limit", type=int, default=0, help="Limit processed image count; 0 means all.")
    return parser.parse_args()


def load_font(image_width: int, image_height: int):
    size = max(22, min(38, min(image_width, image_height) // 30))
    for font_path in FONT_CANDIDATES:
        if font_path.exists():
            return ImageFont.truetype(str(font_path), size=size)
    return ImageFont.load_default()


def validate_directmhp_runtime_dependencies():
    missing = []
    for module_name, package_spec, reason in DIRECTMHP_RUNTIME_DEPENDENCIES:
        if importlib.util.find_spec(module_name) is None:
            missing.append((module_name, package_spec, reason))
    if not missing:
        return

    lines = ["缺少 DirectMHP 运行依赖:"]
    for module_name, package_spec, reason in missing:
        lines.append(f"- {module_name}，请安装 {package_spec}；{reason}")
    package_specs = " ".join(f'"{package_spec}"' for _, package_spec, _ in missing)
    lines.extend([
        "",
        "可执行:",
        f"  conda run -n jy-tias python -m pip install {package_specs}",
    ])
    raise RuntimeError("\n".join(lines))


def expand_box(
        box: Sequence[float],
        image_width: int,
        image_height: int,
        scale: float) -> Tuple[int, int, int, int]:
    x1, y1, x2, y2 = [float(v) for v in box[:4]]
    width = max(1.0, x2 - x1)
    height = max(1.0, y2 - y1)
    center_x = (x1 + x2) / 2.0
    center_y = (y1 + y2) / 2.0
    new_width = width * scale
    new_height = height * scale
    left = max(0, int(round(center_x - new_width / 2.0)))
    top = max(0, int(round(center_y - new_height / 2.0)))
    right = min(image_width, int(round(center_x + new_width / 2.0)))
    bottom = min(image_height, int(round(center_y + new_height / 2.0)))
    return left, top, right, bottom


def classify_face_direction(
        yaw: Optional[float],
        pitch: Optional[float],
        is_writing: bool,
        thresholds: DirectionThresholds) -> FaceDirection:
    if yaw is None or pitch is None:
        return FaceDirection("unknown", "未知", "未检测到头部姿态")

    if is_writing and abs(yaw) >= thresholds.board_yaw:
        return FaceDirection("board", "看黑板/板书方向", "检测到板书且头部明显侧向")

    if pitch >= thresholds.down_pitch:
        return FaceDirection("down", "低头", "pitch 超过低头阈值")

    if yaw <= -thresholds.side_yaw:
        return FaceDirection("left", "向左", "yaw 小于左向阈值")

    if yaw >= thresholds.side_yaw:
        return FaceDirection("right", "向右", "yaw 大于右向阈值")

    if abs(yaw) <= thresholds.front_yaw:
        return FaceDirection("front", "正面/面向学生", "yaw 在正面阈值内")

    return FaceDirection("front", "正面/面向学生", "yaw 未达到侧向阈值，按正面处理")


def select_head_prediction(predictions: List[Dict]) -> Optional[Dict]:
    if not predictions:
        return None
    return max(
        predictions,
        key=lambda prediction: (
            float(prediction["confidence"]),
            -float(prediction["box"][1]),
        ),
    )


def image_files(image_dir: Path) -> List[Path]:
    return sorted(path for path in image_dir.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES)


def box_from_position(position) -> Tuple[int, int, int, int]:
    return (
        int(position.LeftTopX),
        int(position.LeftTopY),
        int(position.RightBtmX),
        int(position.RightBtmY),
    )


def draw_label(draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], text: str, font, image_width: int):
    x1, y1, x2, y2 = box
    draw.rectangle((x1, y1, x2, y2), outline=(255, 218, 0), width=4)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    padding_x = 8
    padding_y = 5
    label_w = text_w + padding_x * 2
    label_h = text_h + padding_y * 2
    label_x = max(0, min(x1, image_width - label_w))
    label_y = max(0, y1 - label_h)
    draw.rectangle((label_x, label_y, label_x + label_w, label_y + label_h), fill=(0, 0, 0))
    draw.text((label_x + padding_x, label_y + padding_y), text, font=font, fill=(255, 255, 255))


def draw_head_pose_axis(
        draw: ImageDraw.ImageDraw,
        head_box: Tuple[int, int, int, int],
        yaw: float,
        pitch: float,
        scale: float = 1.8):
    x1, y1, x2, y2 = head_box
    center_x = (x1 + x2) / 2.0
    center_y = (y1 + y2) / 2.0
    length = max(x2 - x1, y2 - y1) * scale
    end_x = center_x + np.sin(np.radians(yaw)) * length
    end_y = center_y + np.sin(np.radians(pitch)) * length
    draw.line((center_x, center_y, end_x, end_y), fill=(0, 255, 255), width=4)
    draw.ellipse((center_x - 4, center_y - 4, center_x + 4, center_y + 4), fill=(0, 255, 255))


class DirectMHPBackend:
    def __init__(
            self,
            root: Path,
            weights: Path,
            data_yaml: Path,
            device: str,
            imgsz: int,
            conf_thres: float,
            iou_thres: float):
        self.root = root.resolve()
        self.weights = weights.resolve()
        self.data_yaml = data_yaml.resolve()
        self.device_arg = device
        self.imgsz_arg = imgsz
        self.conf_thres = conf_thres
        self.iou_thres = iou_thres
        self._loaded = False

    def validate_files(self):
        missing = []
        for path, label in (
                (self.root, "DirectMHP source"),
                (self.weights, "DirectMHP weights"),
                (self.data_yaml, "DirectMHP data yaml")):
            if not path.exists():
                missing.append(f"{label}: {path}")
        if missing:
            raise FileNotFoundError("缺少 DirectMHP 离线文件:\n" + "\n".join(missing))

    def validate_runtime_dependencies(self):
        validate_directmhp_runtime_dependencies()

    def load(self):
        if self._loaded:
            return
        self.validate_files()
        self.validate_runtime_dependencies()
        sys.path.insert(0, str(self.root))
        import torch
        import yaml
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="pkg_resources is deprecated as an API.*",
                category=UserWarning,
            )
            from models.experimental import attempt_load
            from utils.general import check_img_size, non_max_suppression, scale_coords
            from utils.torch_utils import select_device
            from utils.datasets import LoadImages

        with self.data_yaml.open(encoding="utf-8") as f:
            self.data = yaml.safe_load(f)
        self.torch = torch
        self.check_img_size = check_img_size
        self.non_max_suppression = non_max_suppression
        self.scale_coords = scale_coords
        self.LoadImages = LoadImages
        self.device = select_device(self.device_arg, batch_size=1)
        self.model = attempt_load(str(self.weights), map_location=self.device)
        self.stride = int(self.model.stride.max())
        self.imgsz = self.check_img_size(self.imgsz_arg, s=self.stride)
        self._loaded = True

    def predict_file(self, image_path: Path) -> List[Dict]:
        self.load()
        dataset = self.LoadImages(str(image_path), img_size=self.imgsz, stride=self.stride, auto=True)
        dataset_iter = iter(dataset)
        _, img, im0, _ = next(dataset_iter)
        img = self.torch.from_numpy(img).to(self.device)
        img = img / 255.0
        if len(img.shape) == 3:
            img = img[None]
        out_ori = self.model(img, augment=True, scales=[1])[0]
        out = self.non_max_suppression(
            out_ori,
            self.conf_thres,
            self.iou_thres,
            num_angles=self.data["num_angles"],
        )
        if not out or out[0] is None or len(out[0]) == 0:
            return []
        bboxes = self.scale_coords(img.shape[2:], out[0][:, :4], im0.shape[:2]).cpu().numpy()
        scores = out[0][:, 4].cpu().numpy()
        pitch_yaw_roll = out[0][:, 6:].cpu().numpy()
        predictions = []
        for index, box in enumerate(bboxes):
            pitch = float((pitch_yaw_roll[index][0] - 0.5) * 180)
            yaw = float((pitch_yaw_roll[index][1] - 0.5) * 360)
            roll = float((pitch_yaw_roll[index][2] - 0.5) * 180)
            predictions.append({
                "box": tuple(int(round(v)) for v in box[:4]),
                "confidence": float(scores[index]),
                "pitch": pitch,
                "yaw": yaw,
                "roll": roll,
            })
        return predictions


def detect_teacher_subject(img_bgr) -> Tuple[Optional[Dict], List[Dict]]:
    from app.services.teacher_behavior_service import (
        collect_teacher_behavior_group_details,
        get_teacher_behavior_image_size,
        get_teacher_behavior_merge_iou,
        get_teacher_behavior_predict_conf,
        yolo_teacher_behavior_model,
    )

    result = yolo_teacher_behavior_model.predict(
        img_bgr,
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
    if not details:
        return None, []
    detail = details[0]
    subject = {
        "box": box_from_position(detail["position"]),
        "confidence": detail["confidences"].get("platform_person"),
        "confidences": detail["confidences"],
        "is_writing": "writing" in detail["confidences"],
        "is_teaching": "teaching" in detail["confidences"],
    }
    return subject, details


def create_response_row(
        image_name: str,
        status: str,
        subject: Optional[Dict],
        head: Optional[Dict],
        direction: FaceDirection,
        use_time_ms: int) -> Dict:
    subject_box = subject["box"] if subject else (None, None, None, None)
    head_box = head["box"] if head else (None, None, None, None)
    return {
        "image": image_name,
        "status": status,
        "face_direction": direction.direction,
        "face_direction_name": direction.direction_name,
        "direction_reason": direction.reason,
        "yaw": "" if head is None else f"{head['yaw']:.6f}",
        "pitch": "" if head is None else f"{head['pitch']:.6f}",
        "roll": "" if head is None else f"{head['roll']:.6f}",
        "head_confidence": "" if head is None else f"{head['confidence']:.6f}",
        "teacher_confidence": "" if subject is None or subject["confidence"] is None else f"{subject['confidence']:.6f}",
        "is_writing": bool(subject and subject["is_writing"]),
        "is_teaching": bool(subject and subject["is_teaching"]),
        "subject_left": subject_box[0],
        "subject_top": subject_box[1],
        "subject_right": subject_box[2],
        "subject_bottom": subject_box[3],
        "head_left": head_box[0],
        "head_top": head_box[1],
        "head_right": head_box[2],
        "head_bottom": head_box[3],
        "use_time_ms": use_time_ms,
    }


def response_payload(row: Dict) -> Dict:
    return {
        "ImageId": row["image"],
        "Status": row["status"],
        "TeacherFaceDirection": {
            "FaceDirection": row["face_direction"],
            "FaceDirectionName": row["face_direction_name"],
            "DirectionReason": row["direction_reason"],
            "Yaw": None if row["yaw"] == "" else float(row["yaw"]),
            "Pitch": None if row["pitch"] == "" else float(row["pitch"]),
            "Roll": None if row["roll"] == "" else float(row["roll"]),
            "HeadPoseConfidence": None if row["head_confidence"] == "" else float(row["head_confidence"]),
            "TeacherConfidence": None if row["teacher_confidence"] == "" else float(row["teacher_confidence"]),
            "IsWriting": row["is_writing"],
            "IsTeaching": row["is_teaching"],
            "TeacherSubjectBox": {
                "LeftTopX": row["subject_left"],
                "LeftTopY": row["subject_top"],
                "RightBtmX": row["subject_right"],
                "RightBtmY": row["subject_bottom"],
            },
            "HeadBox": {
                "LeftTopX": row["head_left"],
                "LeftTopY": row["head_top"],
                "RightBtmX": row["head_right"],
                "RightBtmY": row["head_bottom"],
            },
            "Source": "teacher_behavior.pt + DirectMHP",
        }
    }


def write_csv(path: Path, rows: List[Dict]):
    fieldnames = [
        "image", "status", "face_direction", "face_direction_name", "direction_reason",
        "yaw", "pitch", "roll", "head_confidence", "teacher_confidence",
        "is_writing", "is_teaching",
        "subject_left", "subject_top", "subject_right", "subject_bottom",
        "head_left", "head_top", "head_right", "head_bottom",
        "use_time_ms",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_eval(args):
    image_dir = Path(args.image_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    annotated_dir = output_dir / "images"
    crop_dir = output_dir / "crops"
    if not image_dir.exists():
        raise FileNotFoundError(f"image directory does not exist: {image_dir}")
    backend = DirectMHPBackend(
        root=Path(args.directmhp_root),
        weights=Path(args.directmhp_weights),
        data_yaml=Path(args.directmhp_data),
        device=args.device,
        imgsz=args.imgsz,
        conf_thres=args.conf_thres,
        iou_thres=args.iou_thres,
    )
    backend.validate_files()
    backend.validate_runtime_dependencies()

    if output_dir.exists():
        shutil.rmtree(output_dir)
    annotated_dir.mkdir(parents=True, exist_ok=True)
    crop_dir.mkdir(parents=True, exist_ok=True)

    thresholds = DirectionThresholds(
        front_yaw=args.front_yaw,
        side_yaw=args.side_yaw,
        down_pitch=args.down_pitch,
        board_yaw=args.board_yaw,
    )

    images = image_files(image_dir)
    if args.limit > 0:
        images = images[:args.limit]
    rows = []
    responses = []
    run_config = {
        "image_dir": str(image_dir),
        "output_dir": str(output_dir),
        "directmhp_root": str(Path(args.directmhp_root).resolve()),
        "directmhp_weights": str(Path(args.directmhp_weights).resolve()),
        "directmhp_data": str(Path(args.directmhp_data).resolve()),
        "device": args.device,
        "imgsz": args.imgsz,
        "conf_thres": args.conf_thres,
        "iou_thres": args.iou_thres,
        "crop_scale": args.crop_scale,
        "thresholds": thresholds.__dict__,
    }

    for index, image_path in enumerate(images, start=1):
        started = time.time()
        img_bgr = cv2.imread(str(image_path))
        if img_bgr is None:
            row = create_response_row(
                image_path.name,
                "failed_read",
                None,
                None,
                FaceDirection("unknown", "未知", "图片读取失败"),
                int((time.time() - started) * 1000),
            )
            rows.append(row)
            responses.append(response_payload(row))
            continue

        subject, _ = detect_teacher_subject(img_bgr)
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        draw = ImageDraw.Draw(pil_img)
        font = load_font(pil_img.width, pil_img.height)

        if subject is None:
            row = create_response_row(
                image_path.name,
                "no_teacher",
                None,
                None,
                FaceDirection("unknown", "未知", "未检测到老师主体"),
                int((time.time() - started) * 1000),
            )
            draw_label(draw, (16, 16, min(500, pil_img.width - 1), min(120, pil_img.height - 1)), "未检测到老师主体", font, pil_img.width)
            pil_img.save(annotated_dir / image_path.name, quality=95)
            rows.append(row)
            responses.append(response_payload(row))
            continue

        subject_box = subject["box"]
        crop_box = expand_box(subject_box, img_bgr.shape[1], img_bgr.shape[0], args.crop_scale)
        left, top, right, bottom = crop_box
        crop = img_bgr[top:bottom, left:right]
        crop_path = crop_dir / image_path.name
        cv2.imwrite(str(crop_path), crop)
        head_predictions = backend.predict_file(crop_path)
        head = select_head_prediction(head_predictions)
        if head is not None:
            hx1, hy1, hx2, hy2 = head["box"]
            head = {
                **head,
                "box": (hx1 + left, hy1 + top, hx2 + left, hy2 + top),
            }
            direction = classify_face_direction(
                head["yaw"],
                head["pitch"],
                subject["is_writing"],
                thresholds,
            )
            status = "success"
        else:
            direction = classify_face_direction(None, None, subject["is_writing"], thresholds)
            status = "no_head"

        row = create_response_row(
            image_path.name,
            status,
            subject,
            head,
            direction,
            int((time.time() - started) * 1000),
        )
        label = f"{direction.direction_name}"
        if head is not None:
            label += f" yaw={head['yaw']:.1f} pitch={head['pitch']:.1f} conf={head['confidence']:.2f}"
            draw_label(draw, head["box"], label, font, pil_img.width)
            draw_head_pose_axis(draw, head["box"], head["yaw"], head["pitch"])
        else:
            label += " 未检测到头部"
        draw_label(draw, subject_box, f"老师主体 {label}", font, pil_img.width)
        pil_img.save(annotated_dir / image_path.name, quality=95)
        rows.append(row)
        responses.append(response_payload(row))

        if index % 20 == 0:
            print(f"processed {index}/{len(images)}")

    write_csv(output_dir / "summary.csv", rows)
    (output_dir / "response_results.json").write_text(
        json.dumps(responses, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "run_config.json").write_text(
        json.dumps(run_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"images={len(images)}")
    print(f"summary={output_dir / 'summary.csv'}")
    print(f"responses={output_dir / 'response_results.json'}")
    print(f"annotated={annotated_dir}")


def main():
    args = parse_args()
    run_eval(args)


if __name__ == "__main__":
    main()
