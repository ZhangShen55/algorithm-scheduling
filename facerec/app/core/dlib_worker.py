import logging
import os
import queue
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import dlib
import numpy as np

from app.core.config import settings

logger = logging.getLogger(__name__)

_detector: Any = None
_predictor: Any = None
_norm_crop: Any = None
_worker_status: dict[str, Any] = {}
_detector_kind = ""

_ARCFACE_5PTS = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)
_BUFFALO_L_FILES = (
    "det_10g.onnx",
    "w600k_r50.onnx",
    "2d106det.onnx",
    "1k3d68.onnx",
    "genderage.onnx",
)


def required_insightface_models() -> tuple[Path, ...]:
    config = settings.face_detection.insightface
    model_directory = config.resolved_model_path / "models" / config.model_name
    return tuple(model_directory / name for name in _BUFFALO_L_FILES)


def validate_insightface_models() -> tuple[Path, ...]:
    required = required_insightface_models()
    invalid = [path for path in required if not path.is_file() or path.stat().st_size == 0]
    if invalid:
        relative = [
            str(path.relative_to(settings.face_detection.insightface.resolved_model_path))
            for path in invalid
        ]
        raise RuntimeError(f"InsightFace 模型缺失或为空: {', '.join(relative)}")
    return required


def init_worker(
    status_queue: Any,
    startup_gate: Any,
    predictor_path: str,
    detector_choice: str | None = None,
) -> None:
    global _detector, _predictor, _norm_crop, _worker_status, _detector_kind
    _detector = None
    _predictor = None
    _norm_crop = None
    _worker_status = {}
    _detector_kind = detector_choice or settings.face_detection.detector
    try:
        if _detector_kind == "insightface":
            _init_insightface()
        elif _detector_kind == "dlib":
            _init_dlib(predictor_path)
        else:
            raise RuntimeError(f"不支持的人脸检测器: {_detector_kind}")
    except Exception as exc:
        status_queue.put((os.getpid(), False, str(exc), _module_status()))
        raise
    status_queue.put((os.getpid(), True, None, _module_status()))
    startup_gate.wait()


def _init_insightface() -> None:
    global _detector, _norm_crop, _worker_status
    validate_insightface_models()

    import insightface
    from insightface.utils.face_align import norm_crop

    config = settings.face_detection.insightface
    device = settings.gpu.device
    if device == "cpu":
        providers: list[Any] = ["CPUExecutionProvider"]
        ctx_id = -1
    else:
        device_id = int(device.split(":", 1)[1])
        providers = [("CUDAExecutionProvider", {"device_id": device_id})]
        ctx_id = device_id

    detector = insightface.app.FaceAnalysis(
        name=config.model_name,
        root=str(config.resolved_model_path),
        providers=providers,
    )
    detector.prepare(
        ctx_id=ctx_id,
        det_size=(config.det_size, config.det_size),
        det_thresh=config.det_thresh,
    )

    model_providers = _collect_model_providers(detector)
    _validate_model_providers(device, model_providers)

    _detector = detector
    _norm_crop = norm_crop
    _worker_status = {
        "detector": "insightface",
        "device": device,
        "providers": model_providers,
        "model_name": config.model_name,
    }


def _validate_model_providers(device: str, model_providers: list[list[str]]) -> None:
    if device == "cpu":
        if any(item and item[0] != "CPUExecutionProvider" for item in model_providers):
            raise RuntimeError("InsightFace 未按配置使用 CPU provider")
    elif not model_providers or any(
        not item or item[0] != "CUDAExecutionProvider" for item in model_providers
    ):
        raise RuntimeError("InsightFace CUDA provider 不可用，禁止回退 CPU")


def _collect_model_providers(detector: Any) -> list[list[str]]:
    result: list[list[str]] = []
    for model in detector.models.values():
        session = getattr(model, "session", None)
        if session is not None and hasattr(session, "get_providers"):
            result.append(list(session.get_providers()))
    return result


def _init_dlib(predictor_path: str) -> None:
    global _detector, _predictor, _worker_status
    model_path = Path(predictor_path)
    if not model_path.is_file() or model_path.stat().st_size == 0:
        raise RuntimeError("Dlib 关键点模型缺失或为空: ai_models/shape_predictor_68_face_landmarks.dat")
    _detector = dlib.get_frontal_face_detector()
    _predictor = dlib.shape_predictor(str(model_path))
    _worker_status = {
        "detector": "dlib",
        "device": "cpu",
        "providers": [["dlib"]],
        "model_name": "shape_predictor_68_face_landmarks",
    }


def collect_startup_status(
    status_queue: Any,
    *,
    expected_workers: int,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    ready_pids: set[int] = set()
    statuses: list[dict[str, Any]] = []
    while len(ready_pids) < expected_workers:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                f"检测 worker 预热超时: {len(ready_pids)}/{expected_workers} 已就绪"
            )
        try:
            pid, ready, error, module_status = status_queue.get(timeout=remaining)
        except queue.Empty as exc:
            raise RuntimeError(
                f"检测 worker 预热超时: {len(ready_pids)}/{expected_workers} 已就绪"
            ) from exc
        if not ready:
            raise RuntimeError(f"检测 worker {pid} 初始化失败: {error}")
        if pid in ready_pids:
            raise RuntimeError(f"检测 worker {pid} 重复上报初始化状态")
        ready_pids.add(pid)
        statuses.append(module_status)
    return statuses


def self_check() -> dict[str, Any]:
    if _detector is None:
        raise RuntimeError("检测 worker 模型未初始化")
    if _detector_kind == "dlib" and _predictor is None:
        raise RuntimeError("Dlib worker 关键点模型未初始化")
    return _module_status()


def _module_status() -> dict[str, Any]:
    return {
        "pid": os.getpid(),
        "fastdeploy_loaded": "fastdeploy" in sys.modules,
        "ai_engine_loaded": "app.core.ai_engine" in sys.modules,
        **_worker_status,
    }


def detect_and_align_all(
    image: np.ndarray,
) -> list[tuple[np.ndarray, dict[str, int], str]]:
    if _detector is None:
        raise RuntimeError("检测 worker 模型未初始化")
    if _detector_kind == "insightface":
        return _detect_insightface(image)
    return _detect_dlib(image)


def detect_and_align(
    image: np.ndarray,
) -> tuple[np.ndarray | None, dict[str, int] | None, str | None]:
    faces = detect_and_align_all(image)
    if not faces:
        return None, None, None
    return max(faces, key=lambda item: item[1]["w"] * item[1]["h"])


def _detect_insightface(
    image: np.ndarray,
) -> list[tuple[np.ndarray, dict[str, int], str]]:
    started_at = time.monotonic()
    faces = _detector.get(image)
    results: list[tuple[np.ndarray, dict[str, int], str]] = []
    threshold = settings.face_detection.insightface.det_thresh
    for face in faces:
        confidence = float(getattr(face, "det_score", 1.0))
        if confidence < threshold:
            continue
        x1, y1, x2, y2 = np.asarray(face.bbox, dtype=np.int32).tolist()
        width = max(0, int(x2 - x1))
        height = max(0, int(y2 - y1))
        if width == 0 or height == 0:
            continue
        aligned = _norm_crop(image, landmark=np.asarray(face.kps, dtype=np.float32))
        if not aligned.flags["C_CONTIGUOUS"]:
            aligned = np.ascontiguousarray(aligned)
        tip = _face_size_tip(width, height)
        results.append(
            (
                aligned,
                {
                    "x": int(x1),
                    "y": int(max(0, y1 - height * 0.4)),
                    "w": width,
                    "h": height,
                },
                tip,
            )
        )
    logger.info(
        "InsightFace 检测完成 faces=%s duration_ms=%.2f",
        len(results),
        (time.monotonic() - started_at) * 1000,
    )
    return results


def _detect_dlib(
    image: np.ndarray,
) -> list[tuple[np.ndarray, dict[str, int], str]]:
    started_at = time.monotonic()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    results: list[tuple[np.ndarray, dict[str, int], str]] = []
    for face in _detector(gray, 1):
        x, y, width, height = face.left(), face.top(), face.width(), face.height()
        landmarks = _shape_to_np(_predictor(gray, face))
        aligned = _align_by_5pts(image, _five_from_68(landmarks))
        if not aligned.flags["C_CONTIGUOUS"]:
            aligned = np.ascontiguousarray(aligned)
        results.append(
            (
                aligned,
                {
                    "x": int(x),
                    "y": int(max(0, y - height * 0.4)),
                    "w": int(width),
                    "h": int(height),
                },
                _face_size_tip(width, height),
            )
        )
    logger.info(
        "Dlib 检测完成 faces=%s duration_ms=%.2f",
        len(results),
        (time.monotonic() - started_at) * 1000,
    )
    return results


def _face_size_tip(width: int, height: int) -> str:
    if width < 200 or height < 200:
        return "人脸特征像素过低，或影响检测效果"
    return "人脸特征像素正常，可以使用"


def _shape_to_np(shape: dlib.full_object_detection) -> np.ndarray:
    return np.array(
        [(shape.part(index).x, shape.part(index).y) for index in range(68)],
        dtype=np.float32,
    )


def _five_from_68(landmarks: np.ndarray) -> np.ndarray:
    return np.stack(
        [
            landmarks[36:42].mean(axis=0),
            landmarks[42:48].mean(axis=0),
            landmarks[30],
            landmarks[48],
            landmarks[54],
        ]
    ).astype(np.float32)


def _align_by_5pts(image: np.ndarray, points: np.ndarray) -> np.ndarray:
    matrix = cv2.estimateAffinePartial2D(points, _ARCFACE_5PTS, method=cv2.LMEDS)[0]
    if matrix is None:
        raise RuntimeError("人脸关键点对齐失败")
    return cv2.warpAffine(
        image,
        matrix,
        (112, 112),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT,
    )
