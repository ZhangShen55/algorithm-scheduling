"""
统一 API 响应格式模型
所有接口 HTTP 状态码永远是 200，通过 status_code 字段区分成功/失败
"""
from typing import Optional, Any
from pydantic import BaseModel
from enum import IntEnum


class StatusCode(IntEnum):
    """统一状态码枚举"""
    # 成功
    SUCCESS = 200                # 识别成功且匹配到人物（data.match 不为空）

    # 识别相关特殊状态 2xx
    NO_FACE_DETECTED = 201       # 未检测到人脸（图片有效但没有人脸，data 为 null）
    FACE_TOO_SMALL = 202         # 人脸尺寸过小（有 bbox 但 match 为空）
    PARTIAL_SUCCESS = 207        # 部分成功（批量操作时部分失败）

    # 数据库相关 25x
    DB_EMPTY = 251               # 数据库为空（有 bbox 但 match 为空）
    NO_MATCH_FOUND = 252         # 未匹配到对象，相似度低于阈值（有 bbox 但 match 为空）

    # 客户端错误 4xx - 图片数据相关 40x
    BAD_REQUEST = 400            # 通用请求参数错误（如：缺少必填参数、photos列表为空）
    BASE64_DECODE_ERROR = 401    # base64 解码失败
    INVALID_IMAGE_FORMAT = 402   # 图片格式错误（cv2 无法解析）
    INVALID_IMAGE_DATA = 403     # 未接收到有效图片数据（image_data 为 None 或空）
    NOT_FOUND = 404              # 资源未找到
    UNPROCESSABLE_ENTITY = 422   # 无法处理的实体（保留，暂未使用）

    # 服务器错误 5xx
    INTERNAL_ERROR = 500         # 服务器内部错误（数据库操作失败等）
    FACE_DETECTION_ERROR = 501   # 人脸检测服务内部错误
    FEATURE_EXTRACT_ERROR = 502  # 特征提取失败
    FILE_SAVE_ERROR = 503        # 文件保存失败


class ApiResponse(BaseModel):
    """统一 API 响应格式 - 所有接口 HTTP 状态码都是 200"""
    status_code: int
    message: str
    data: Optional[Any] = None

    @classmethod
    def success(cls, data: Any = None, message: str = "操作成功"):
        """成功响应"""
        return cls(status_code=StatusCode.SUCCESS, message=message, data=data)

    @classmethod
    def error(cls, status_code: int, message: str, data: Any = None):
        """错误响应"""
        return cls(status_code=status_code, message=message, data=data)
