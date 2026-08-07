"""
人脸识别服务层
封装人脸检测、特征提取、人脸比对等业务逻辑
"""
from typing import Optional, Tuple, List, Dict, Any
import numpy as np
from app.core import ai_engine
from app.core.config import settings
from app.core.logger import get_logger
from app.core.constants import MatchReason
from app.services import person as person_service

logger = get_logger(__name__)


class FaceRecognitionService:
    """人脸识别服务"""

    def __init__(self):
        self.threshold = settings.face.threshold
        self.candidate_threshold = settings.face.candidate_threshold
        self.rec_min_face_hw = int(settings.face.rec_min_face_hw)

    async def detect_and_extract(self, image_data: np.ndarray) -> Tuple[Optional[np.ndarray], Optional[dict], Optional[str]]:
        """
        检测并提取人脸

        Returns:
            (face_image, bbox, tip): 人脸图像、边界框、质量提示
        """
        try:
            return await ai_engine.detect_and_extract_face(image_data)
        except Exception as e:
            logger.error(f"[FaceService] 人脸检测失败: {e}")
            raise

    async def extract_feature(self, face_image: np.ndarray) -> np.ndarray:
        """
        提取人脸特征向量

        Returns:
            512维归一化特征向量
        """
        try:
            return await ai_engine.get_embedding(face_image)
        except Exception as e:
            logger.error(f"[FaceService] 特征提取失败: {e}")
            raise

    def validate_face_size(self, face_image: np.ndarray) -> bool:
        """验证人脸尺寸是否满足要求"""
        h, w = face_image.shape[:2]
        return h >= self.rec_min_face_hw and w >= self.rec_min_face_hw

    async def recognize(
        self,
        image_data: np.ndarray,
        db,
        targets: Optional[List[Dict[str, str]]] = None,
        custom_threshold: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        人脸识别主流程

        Args:
            image_data: 图像数据
            db: 数据库连接
            targets: 优先匹配的候选人列表
            custom_threshold: 自定义阈值

        Returns:
            识别结果字典
        """
        # 1. 人脸检测
        face_image, bbox, tip = await self.detect_and_extract(image_data)

        if face_image is None:
            return {
                "has_face": False,
                "bbox": None,
                "matched": False,
                "best_sim": 0.0,
                "threshold": self.threshold,
                "reason": MatchReason.NO_FACE,
                "person_doc": None
            }

        # 2. 验证人脸尺寸
        if not self.validate_face_size(face_image):
            h, w = face_image.shape[:2]
            return {
                "has_face": True,
                "bbox": bbox,
                "matched": False,
                "best_sim": 0.0,
                "threshold": self.threshold,
                "reason": f"人脸像素过小({h}x{w}px)，无法识别",
                "person_doc": None
            }

        # 3. 特征提取
        embedding = await self.extract_feature(face_image)

        # 4. 优先匹配（如果提供了候选人）
        if targets:
            logger.info("[FaceService] 优先targets比对...")
            candidate_docs = await person_service.get_targets_embeddings(db, targets)

            if candidate_docs:
                best_sim, best_doc = ai_engine.find_best_match_embedding(embedding, candidate_docs)

                if best_sim > self.candidate_threshold:
                    logger.info(f"[FaceService] 优先比对命中: {best_doc.get('name')} (Sim: {best_sim})")
                    return {
                        "has_face": True,
                        "bbox": bbox,
                        "matched": True,
                        "best_sim": best_sim,
                        "threshold": self.candidate_threshold,
                        "reason": MatchReason.PRIORITY,
                        "person_doc": best_doc
                    }

            logger.info("[FaceService] 优先persons比对未命中，转入全局比对...")

        # 5. 全局匹配
        logger.info("[FaceService] 全局比对...")
        all_docs = await person_service.get_embeddings_for_match(db)

        if not all_docs:
            logger.info("[FaceService] 数据库中没有有效人脸特征")
            return {
                "has_face": True,
                "bbox": bbox,
                "matched": False,
                "best_sim": 0.0,
                "threshold": custom_threshold or self.threshold,
                "reason": MatchReason.NO_DATA,
                "person_doc": None
            }

        best_sim, best_doc = ai_engine.find_best_match_embedding(embedding, all_docs)
        threshold = custom_threshold if custom_threshold else self.threshold

        logger.info(f"[FaceService] 全局匹配结果: {best_doc.get('name')} (Sim: {best_sim})")

        return {
            "has_face": True,
            "bbox": bbox,
            "matched": best_sim >= threshold,
            "best_sim": best_sim,
            "threshold": threshold,
            "reason": MatchReason.GLOBAL if best_sim >= threshold else MatchReason.NO_MATCH,
            "person_doc": best_doc
        }


# 全局单例
face_recognition_service = FaceRecognitionService()
