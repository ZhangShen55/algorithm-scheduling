import logging
import os
import queue
import sys
import time
from typing import Any

import cv2
import dlib
import numpy as np

logger = logging.getLogger(__name__)

_detector: Any = None
_predictor: Any = None

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


def init_worker(status_queue: Any, startup_gate: Any, predictor_path: str) -> None:
    global _detector, _predictor
    try:
        _detector = dlib.get_frontal_face_detector()
        _predictor = dlib.shape_predictor(predictor_path)
    except Exception as exc:
        status_queue.put((os.getpid(), False, str(exc), _module_status()))
        raise
    status_queue.put((os.getpid(), True, None, _module_status()))
    startup_gate.wait()


def collect_startup_status(
    status_queue: Any,
    *,
    expected_workers: int,
    timeout_seconds: float,
) -> list[dict[str, int | bool]]:
    deadline = time.monotonic() + timeout_seconds
    ready_pids: set[int] = set()
    statuses: list[dict[str, int | bool]] = []
    while len(ready_pids) < expected_workers:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                f"Dlib worker 预热超时: {len(ready_pids)}/{expected_workers} 已就绪"
            )
        try:
            pid, ready, error, module_status = status_queue.get(timeout=remaining)
        except queue.Empty as exc:
            raise RuntimeError(
                f"Dlib worker 预热超时: {len(ready_pids)}/{expected_workers} 已就绪"
            ) from exc
        if not ready:
            raise RuntimeError(f"Dlib worker {pid} 初始化失败: {error}")
        if pid in ready_pids:
            raise RuntimeError(f"Dlib worker {pid} 重复上报初始化状态")
        ready_pids.add(pid)
        statuses.append(module_status)
    return statuses


def self_check() -> dict[str, int | bool]:
    if _detector is None or _predictor is None:
        raise RuntimeError("Dlib worker 模型未初始化")
    return _module_status()


def _module_status() -> dict[str, int | bool]:
    return {
        "pid": os.getpid(),
        "fastdeploy_loaded": "fastdeploy" in sys.modules,
        "ai_engine_loaded": "app.core.ai_engine" in sys.modules,
    }


def detect_and_align(image: np.ndarray) -> tuple[np.ndarray | None, dict | None, str | None]:
    started_at = time.monotonic()
    if _detector is None or _predictor is None:
        raise RuntimeError("Dlib worker 模型未初始化")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    faces = _detector(gray, 1)
    if not faces:
        logger.info("Dlib 未检测到人脸，耗时 %.2fms", (time.monotonic() - started_at) * 1000)
        return None, None, None

    face = max(faces, key=lambda item: item.width() * item.height())
    x, y, width, height = face.left(), face.top(), face.width(), face.height()
    tip = "人脸特征像素正常，可以使用"
    if width < 200 or height < 200:
        tip = "人脸特征像素过低，或影响检测效果"

    landmarks = _shape_to_np(_predictor(gray, face))
    aligned = _align_by_5pts(image, _five_from_68(landmarks))
    if not aligned.flags["C_CONTIGUOUS"]:
        aligned = np.ascontiguousarray(aligned)
    bbox = {
        "x": int(x),
        "y": int(max(0, y - height * 0.4)),
        "w": int(width),
        "h": int(height),
    }
    return aligned, bbox, tip


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
    return cv2.warpAffine(
        image,
        matrix,
        (112, 112),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT,
    )
