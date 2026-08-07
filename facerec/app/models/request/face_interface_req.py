from pydantic import BaseModel
from app.models.schemas import PersonBase
from typing import Optional, List

"""
用于存放/recognize接口的请求模型
"""

class PersonRecognizeRequest(BaseModel):
    # 人脸recognize请求模型
    photo: str
    # targets: Optional[list[PersonBase]] = None
    targets: Optional[list[str]] = None
    threshold: Optional[float] = None

class BatchRecognizeRequest(BaseModel):
    """
    批量识别请求（单人多帧）
    用于通过多张图片提高识别准确率
    """
    photos: List[str]  # Base64 编码的图片列表（单人多帧）
    targets: Optional[List[str]] = None  # 可选的候选人编号列表
    threshold: Optional[float] = None  # 可选的阈值
