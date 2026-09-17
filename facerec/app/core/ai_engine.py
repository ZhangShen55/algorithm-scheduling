import asyncio
import threading
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import fastdeploy as fd
import numpy as np

from app.core import dlib_worker
from app.core.config import PROJECT_ROOT, settings
from app.core.embedding_matching import filter_candidate_embeddings
from app.core.logger import get_logger
from app.core.runtime_device import configure_runtime_option

logger = get_logger(__name__)

_SHAPE_PREDICTOR_PATH = str(
    PROJECT_ROOT / "ai_models" / "shape_predictor_68_face_landmarks.dat"
)
_ARCFACE_MODEL_PATH = PROJECT_ROOT / "ai_models" / "ms1mv3_arcface_r100.onnx"

GLOBAL_PROCESS_POOL: ProcessPoolExecutor | None = None
embedding_model: Any | None = None
_embedding_model_lock = threading.Lock()
_embedding_model_error: str | None = None


def load_embedding_model() -> Any:
    global embedding_model, _embedding_model_error
    if embedding_model is not None:
        return embedding_model
    with _embedding_model_lock:
        if embedding_model is not None:
            return embedding_model
        if not _ARCFACE_MODEL_PATH.is_file() or _ARCFACE_MODEL_PATH.stat().st_size == 0:
            _embedding_model_error = (
                "ArcFace 模型缺失或为空: ai_models/ms1mv3_arcface_r100.onnx"
            )
            raise RuntimeError(_embedding_model_error)
        try:
            option = fd.RuntimeOption()
            configure_runtime_option(option, settings.gpu.device, fastdeploy_module=fd)
            model = fd.vision.faceid.ArcFace(
                str(_ARCFACE_MODEL_PATH),
                runtime_option=option,
            )
            if getattr(model, "initialized", True) is False:
                raise RuntimeError("ArcFace 模型初始化失败")
            embedding_model = model
            _embedding_model_error = None
            return model
        except Exception as exc:
            _embedding_model_error = str(exc)
            raise


def embedding_status() -> dict[str, Any]:
    return {
        "ready": embedding_model is not None
        and getattr(embedding_model, "initialized", True) is not False,
        "device": settings.gpu.device,
        "error": _embedding_model_error,
    }


async def detect_and_extract_face(
    image: np.ndarray,
) -> tuple[np.ndarray | None, dict[str, int] | None, str | None]:
    return await _run_detector(dlib_worker.detect_and_align, image)


async def detect_and_extract_all_faces(
    image: np.ndarray,
) -> list[tuple[np.ndarray, dict[str, int], str]]:
    return await _run_detector(dlib_worker.detect_and_align_all, image)


async def _run_detector(function: Any, image: np.ndarray) -> Any:
    if GLOBAL_PROCESS_POOL is None:
        raise RuntimeError("全局检测进程池未初始化，请检查 app.main lifespan")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(GLOBAL_PROCESS_POOL, function, image)


async def get_embedding(face_aligned: np.ndarray) -> np.ndarray:
    embedding = await asyncio.to_thread(get_embedding_sync, face_aligned)
    norm = np.linalg.norm(embedding)
    if not np.isfinite(norm) or norm <= 1e-12:
        raise RuntimeError("ArcFace 返回了无效 embedding")
    return np.asarray(embedding / norm, dtype=np.float32)


def get_embedding_sync(face_aligned: np.ndarray) -> np.ndarray:
    model = load_embedding_model()
    result = model.predict(face_aligned)
    embedding = np.asarray(result.embedding, dtype=np.float32).reshape(-1)
    if embedding.size != 512 or not np.isfinite(embedding).all():
        raise RuntimeError("ArcFace embedding 必须是有限的 512 维向量")
    return embedding


def find_best_match_embedding(
    emb_q: np.ndarray,
    candidate_docs: list[dict],
) -> tuple[float, dict | None]:
    db_vecs, valid_docs = _valid_candidates(candidate_docs)
    if not db_vecs:
        return 0.0, None
    similarities = np.dot(db_vecs, emb_q)
    best_index = int(np.argmax(similarities))
    return float(similarities[best_index]), valid_docs[best_index]


def find_top_matches(
    emb_q: np.ndarray,
    candidate_docs: list[dict],
    top_k: int = 3,
    min_threshold: float = 0.0,
) -> list[tuple[float, dict]]:
    db_vecs, valid_docs = _valid_candidates(candidate_docs)
    if not db_vecs:
        return []
    similarities = np.dot(db_vecs, emb_q)
    valid_indices = np.where(similarities >= min_threshold)[0]
    sorted_indices = valid_indices[np.argsort(-similarities[valid_indices])]
    return [
        (float(similarities[index]), valid_docs[index])
        for index in sorted_indices[:top_k]
    ]


def _valid_candidates(
    candidate_docs: list[dict],
) -> tuple[list[np.ndarray], list[dict]]:
    db_vecs, valid_docs, rejections = filter_candidate_embeddings(candidate_docs)
    if rejections:
        reasons = Counter(rejection["reason"] for rejection in rejections)
        logger.warning(
            "[embedding] 跳过损坏候选 count=%s reasons=%s",
            len(rejections),
            dict(reasons),
        )
    return db_vecs, valid_docs
