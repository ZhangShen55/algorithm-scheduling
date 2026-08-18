# app/ai_engine.py
import asyncio
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import fastdeploy as fd
import numpy as np

from app.core import dlib_worker
from app.core.config import settings
from app.core.embedding_matching import filter_candidate_embeddings
from app.core.logger import get_logger
from app.core.runtime_device import configure_runtime_option

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SHAPE_PREDICTOR_PATH = str(
    PROJECT_ROOT / "ai_models" / "shape_predictor_68_face_landmarks.dat"
)

# embadding模型全局加载加载
option = fd.RuntimeOption()
configure_runtime_option(option, settings.gpu.device, fastdeploy_module=fd)
embedding_model = fd.vision.faceid.ArcFace(
    str(PROJECT_ROOT / "ai_models" / "ms1mv3_arcface_r100.onnx"),
    runtime_option=option,
)

# 定义全局变量，会被main.py初始化
GLOBAL_PROCESS_POOL: ProcessPoolExecutor | None = None

_init_dlib_worker = dlib_worker.init_worker
_collect_dlib_worker_status = dlib_worker.collect_startup_status
_dlib_worker_self_check = dlib_worker.self_check
_dlib_task_implementation = dlib_worker.detect_and_align


async def detect_and_extract_face(image: np.ndarray):
    """
    主程序调用的入口。
    它会检查 GLOBAL_PROCESS_POOL 是否已被 main.py 初始化。
    """
    loop = asyncio.get_running_loop()

    if GLOBAL_PROCESS_POOL is None:
        # 如果池子没初始化（比如直接运行此脚本测试），降级为同步或报错
        raise RuntimeError("全局进程池未初始化，请检查main.py是否正确启动")

    try:
        # 提交给进程池
        return await loop.run_in_executor(
            GLOBAL_PROCESS_POOL,
            dlib_worker.detect_and_align,
            image
        )
    except Exception as e:
        print(f"Process Pool Error: {e}")
        return None, None, None


# 异步获取特征向量
async def get_embedding(face_aligned: np.ndarray) -> np.ndarray:
    """
    异步提取 512d 单位化向量（float32, L2norm==1）
    :param face_aligned: 对齐后的 112x112 人脸图像
    :return: 归一化后的 512维特征向量
    """
    # return await asyncio.to_thread(get_embedding_sync, face_aligned)
    emb =  await asyncio.to_thread(get_embedding_sync, face_aligned)
    # 归一化 方便点积计算
    emb_q = emb / (np.linalg.norm(emb) + 1e-12)
    return emb_q

def get_embedding_sync(face_aligned: np.ndarray) -> np.ndarray:
    """同步获取特征向量的方法"""
    result = embedding_model.predict(face_aligned)
    emb = np.asarray(result.embedding, dtype=np.float32)
    # 再做一次归一化
    n = np.linalg.norm(emb) + 1e-12
    emb = emb / n
    return emb

def find_best_match_embedding(
    emb_q: np.ndarray,
    candidate_docs: list[dict],
) -> tuple[float, dict | None]:
    """
    在候选文档列表中寻找最大相似度
    返回: (最佳相似度, 最佳匹配文档)
    """
    db_vecs, valid_docs, rejections = filter_candidate_embeddings(candidate_docs)
    for rejection in rejections:
        logger.warning("[embedding] skip invalid candidate: %s", rejection)

    if not db_vecs:
        return 0.0, None

    # 批量计算点积 (余弦相似度)
    # emb_q 假设已经归一化
    sims = np.dot(db_vecs, emb_q)
    best_idx = int(np.argmax(sims))
    best_sim = float(sims[best_idx])

    return best_sim, valid_docs[best_idx]


def find_top_matches(
    emb_q: np.ndarray,
    candidate_docs: list[dict],
    top_k: int = 3,
    min_threshold: float = 0.0,
):
    """
    在候选文档列表中寻找相似度最高的 top_k 个匹配

    参数:
        emb_q: 查询 embedding（已归一化）
        candidate_docs: 候选文档列表
        top_k: 返回的最大数量
        min_threshold: 最小相似度阈值

    返回: List[(相似度, 文档)]，按相似度降序排列
    """
    db_vecs, valid_docs, rejections = filter_candidate_embeddings(candidate_docs)
    for rejection in rejections:
        logger.warning("[embedding] skip invalid candidate: %s", rejection)

    if not db_vecs:
        return []

    # 批量计算点积 (余弦相似度)
    sims = np.dot(db_vecs, emb_q)

    # 找到所有大于等于阈值的索引
    valid_indices = np.where(sims >= min_threshold)[0]

    if len(valid_indices) == 0:
        return []

    # 按相似度降序排序
    sorted_indices = valid_indices[np.argsort(-sims[valid_indices])]

    # 取前 top_k 个
    top_indices = sorted_indices[:top_k]

    # 返回 (相似度, 文档) 列表
    results = [(float(sims[idx]), valid_docs[idx]) for idx in top_indices]

    return results
