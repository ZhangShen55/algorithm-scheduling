from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

class BBox(BaseModel):
    # 人脸位置
    x: int
    y: int
    w: int
    h: int

class MatchItem(BaseModel):
    id: str
    name: Optional[str] = None
    number: Optional[str] = None
    similarity: str
    is_target: bool = False  # 是否是 targets 中指定的人物

class RecognizeResp(BaseModel):
    has_face: bool
    bbox: Optional[BBox] = None
    threshold: float
    match: Optional[List[MatchItem]] = None  # 改为列表，支持多个匹配结果
    message: str

class FrameInfo(BaseModel):
    """单帧图片的处理信息"""
    index: int
    has_face: bool
    bbox: Optional[BBox] = None
    error: Optional[str] = None

class BatchRecognizeResp(BaseModel):
    """批量识别响应（融合多帧结果）"""
    total_frames: int  # 总帧数
    valid_frames: int  # 有效帧数（检测到人脸的）
    threshold: float
    frames: List[FrameInfo]  # 每一帧的处理信息
    match: Optional[List[MatchItem]] = None  # 融合后的最终匹配结果
    # confidence: float  # 综合置信度（有效帧数 / 总帧数）
    message: str

