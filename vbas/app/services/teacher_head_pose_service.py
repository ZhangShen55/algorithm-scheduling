import importlib.util
import os
import sys
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np
import yaml

from ..schemas.stu_tea_behavior import BoxPosition, HeadPoseResult, ObjectPosition


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIRECTMHP_ROOT = PROJECT_ROOT / "app" / "vendor" / "DirectMHP"
DEFAULT_DIRECTMHP_WEIGHTS = PROJECT_ROOT / "models" / "cmu_m_1280_e200_t40_lw010_best.pt"
DEFAULT_DIRECTMHP_DATA = PROJECT_ROOT / "models" / "cmu_panoptic_coco.yaml"

DIRECTMHP_RUNTIME_DEPENDENCIES = [
    (
        "pkg_resources",
        "setuptools<81",
        "DirectMHP 的 YOLOv5 旧代码依赖 pkg_resources",
    ),
    (
        "seaborn",
        "seaborn>=0.11.0",
        "DirectMHP 导入 utils.plots 时需要 seaborn",
    ),
]


@dataclass(frozen=True)
class TeacherHeadPoseThresholds:
    side_yaw: float = 25.0
    down_pitch: float = 25.0


@dataclass(frozen=True)
class TeacherHeadPoseConfig:
    directmhp_root: Path
    directmhp_weights: Path
    directmhp_data: Path
    device: str
    image_size: int = 1280
    conf_thres: float = 0.35
    iou_thres: float = 0.45
    crop_scale: float = 1.35
    thresholds: TeacherHeadPoseThresholds = TeacherHeadPoseThresholds()


@dataclass(frozen=True)
class HeadPosePrediction:
    box: Tuple[int, int, int, int]
    confidence: float
    pitch: float
    yaw: float
    roll: float


def resolve_project_path(value, default_path: Path) -> Path:
    if value is None or str(value).strip() == "":
        return default_path
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def get_teacher_head_pose_raw_config(key: str, default_value):
    from ..core.settings import settings

    head_pose_config = getattr(settings, "Teacher_Head_Pose", {})
    return head_pose_config.get(key, default_value)


def get_teacher_head_pose_float_config(key: str, default_value: float) -> float:
    try:
        return float(get_teacher_head_pose_raw_config(key, default_value))
    except (TypeError, ValueError):
        return default_value


def get_teacher_head_pose_int_config(key: str, default_value: int) -> int:
    try:
        return int(get_teacher_head_pose_raw_config(key, default_value))
    except (TypeError, ValueError):
        return default_value


def get_teacher_head_pose_config() -> TeacherHeadPoseConfig:
    from ..core.settings import settings
    from ..core.settings import model_path_resolver

    return TeacherHeadPoseConfig(
        directmhp_root=resolve_project_path(
            get_teacher_head_pose_raw_config("DirectMHPRoot", DEFAULT_DIRECTMHP_ROOT),
            DEFAULT_DIRECTMHP_ROOT,
        ),
        directmhp_weights=model_path_resolver.prepare_model_path(
            resolve_project_path(
                get_teacher_head_pose_raw_config("DirectMHPWeights", DEFAULT_DIRECTMHP_WEIGHTS),
                DEFAULT_DIRECTMHP_WEIGHTS,
            )
        ),
        directmhp_data=resolve_project_path(
            get_teacher_head_pose_raw_config("DirectMHPData", DEFAULT_DIRECTMHP_DATA),
            DEFAULT_DIRECTMHP_DATA,
        ),
        device=str(get_teacher_head_pose_raw_config("Device", settings.GPU_ID)),
        image_size=get_teacher_head_pose_int_config("ImageSize", 1280),
        conf_thres=get_teacher_head_pose_float_config("ConfThres", 0.35),
        iou_thres=get_teacher_head_pose_float_config("IouThres", 0.45),
        crop_scale=get_teacher_head_pose_float_config("CropScale", 1.35),
        thresholds=TeacherHeadPoseThresholds(
            side_yaw=get_teacher_head_pose_float_config("SideYawThreshold", 25.0),
            down_pitch=get_teacher_head_pose_float_config("DownPitchThreshold", 25.0),
        ),
    )


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


def box_position_from_tuple(box: Sequence[int]) -> BoxPosition:
    x1, y1, x2, y2 = [int(v) for v in box[:4]]
    return BoxPosition(
        LeftTopX=x1,
        LeftTopY=y1,
        RightBtmX=x2,
        RightBtmY=y2,
    )


def object_position_to_box(position: ObjectPosition) -> Tuple[int, int, int, int]:
    return (
        int(position.LeftTopX),
        int(position.LeftTopY),
        int(position.RightBtmX),
        int(position.RightBtmY),
    )


def expand_box(
        box: Sequence[int],
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


def select_head_prediction(predictions: List[HeadPosePrediction]) -> Optional[HeadPosePrediction]:
    if not predictions:
        return None
    return max(
        predictions,
        key=lambda prediction: (
            float(prediction.confidence),
            -float(prediction.box[1]),
        ),
    )


def classify_student_view_horizontal_direction(yaw: float, side_yaw: float) -> str:
    if yaw <= -side_yaw:
        return "left"
    if yaw >= side_yaw:
        return "right"
    return "front"


def build_success_head_pose_result(
        prediction: HeadPosePrediction,
        teacher_confidence: Optional[float],
        teacher_box: Sequence[int],
        crop_offset: Tuple[int, int],
        thresholds: TeacherHeadPoseThresholds) -> HeadPoseResult:
    crop_left, crop_top = crop_offset
    hx1, hy1, hx2, hy2 = prediction.box
    head_box = (
        hx1 + crop_left,
        hy1 + crop_top,
        hx2 + crop_left,
        hy2 + crop_top,
    )
    student_view_yaw = -float(prediction.yaw)
    angle = max(0.0, abs(student_view_yaw) - thresholds.side_yaw)
    return HeadPoseResult(
        Enabled=True,
        Status="success",
        FaceDirection=classify_student_view_horizontal_direction(
            student_view_yaw,
            thresholds.side_yaw,
        ),
        Yaw=student_view_yaw,
        Pitch=float(prediction.pitch),
        Roll=float(prediction.roll),
        Angle=angle,
        IsLookingDown=float(prediction.pitch) >= thresholds.down_pitch,
        HeadPoseConfidence=float(prediction.confidence),
        TeacherConfidence=teacher_confidence,
        TeacherSubjectBox=box_position_from_tuple(teacher_box),
        HeadBox=box_position_from_tuple(head_box),
    )


def disabled_head_pose_result() -> HeadPoseResult:
    return HeadPoseResult(
        Enabled=False,
        Status="disabled",
        FaceDirection="unknown",
    )


def no_teacher_head_pose_result() -> HeadPoseResult:
    return HeadPoseResult(
        Enabled=True,
        Status="no_teacher",
        FaceDirection="unknown",
    )


def no_head_head_pose_result(
        teacher_confidence: Optional[float],
        teacher_box: Sequence[int]) -> HeadPoseResult:
    return HeadPoseResult(
        Enabled=True,
        Status="no_head",
        FaceDirection="unknown",
        TeacherConfidence=teacher_confidence,
        TeacherSubjectBox=box_position_from_tuple(teacher_box),
    )


def failed_head_pose_result(message: str) -> HeadPoseResult:
    return HeadPoseResult(
        Enabled=True,
        Status="failed",
        FaceDirection="unknown",
        Message=message,
    )


class DirectMHPBackend:
    def __init__(self, config: TeacherHeadPoseConfig):
        self.config = config
        self._loaded = False

    def validate_files(self):
        missing = []
        for path, label in (
                (self.config.directmhp_root, "DirectMHP source"),
                (self.config.directmhp_weights, "DirectMHP weights"),
                (self.config.directmhp_data, "DirectMHP data yaml")):
            if not path.exists():
                missing.append(f"{label}: {path}")
        if missing:
            raise FileNotFoundError("缺少 DirectMHP 离线文件:\n" + "\n".join(missing))

    def load(self):
        if self._loaded:
            return
        self.validate_files()
        validate_directmhp_runtime_dependencies()
        root = str(self.config.directmhp_root.resolve())
        if root not in sys.path:
            sys.path.insert(0, root)
        matplotlib_cache_dir = Path(tempfile.gettempdir()) / "jy_tias_matplotlib"
        matplotlib_cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", str(matplotlib_cache_dir))

        import torch
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

        with self.config.directmhp_data.open(encoding="utf-8") as f:
            self.data = yaml.safe_load(f)
        self.torch = torch
        self.check_img_size = check_img_size
        self.non_max_suppression = non_max_suppression
        self.scale_coords = scale_coords
        self.LoadImages = LoadImages
        self.device = select_device(self.config.device, batch_size=1)
        self.model = attempt_load(str(self.config.directmhp_weights), map_location=self.device)
        self._cleanup_prepared_weight()
        self.stride = int(self.model.stride.max())
        self.imgsz = self.check_img_size(self.config.image_size, s=self.stride)
        self._loaded = True

    @staticmethod
    def _cleanup_prepared_weight():
        from ..core.settings import model_path_resolver, model_protection_config

        if model_protection_config.cleanup_after_load:
            model_path_resolver.cleanup()

    def predict_file(self, image_path: Path) -> List[HeadPosePrediction]:
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
            self.config.conf_thres,
            self.config.iou_thres,
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
            predictions.append(HeadPosePrediction(
                box=tuple(int(round(v)) for v in box[:4]),
                confidence=float(scores[index]),
                pitch=pitch,
                yaw=yaw,
                roll=roll,
            ))
        return predictions


_directmhp_backend: Optional[DirectMHPBackend] = None


def get_directmhp_backend() -> DirectMHPBackend:
    global _directmhp_backend
    config = get_teacher_head_pose_config()
    if _directmhp_backend is None or _directmhp_backend.config != config:
        _directmhp_backend = DirectMHPBackend(config)
    return _directmhp_backend


def predict_teacher_head_pose(
        crop: np.ndarray,
        backend: Optional[DirectMHPBackend] = None) -> List[HeadPosePrediction]:
    selected_backend = backend or get_directmhp_backend()
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=True) as temp_file:
        cv2.imwrite(temp_file.name, crop)
        return selected_backend.predict_file(Path(temp_file.name))


def analyze_teacher_head_pose(
        img_bgr: np.ndarray,
        teacher_position: ObjectPosition,
        backend: Optional[DirectMHPBackend] = None) -> HeadPoseResult:
    config = get_teacher_head_pose_config()
    teacher_box = object_position_to_box(teacher_position)
    crop_box = expand_box(
        teacher_box,
        image_width=img_bgr.shape[1],
        image_height=img_bgr.shape[0],
        scale=config.crop_scale,
    )
    left, top, right, bottom = crop_box
    crop = img_bgr[top:bottom, left:right]
    predictions = predict_teacher_head_pose(crop, backend)
    prediction = select_head_prediction(predictions)
    teacher_confidence = teacher_position.Confidence
    if prediction is None:
        return no_head_head_pose_result(teacher_confidence, teacher_box)
    return build_success_head_pose_result(
        prediction,
        teacher_confidence=teacher_confidence,
        teacher_box=teacher_box,
        crop_offset=(left, top),
        thresholds=config.thresholds,
    )
