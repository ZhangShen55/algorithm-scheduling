"""
统一 API 响应格式模型
所有接口 HTTP 状态码永远是 200，通过 statusCode 字段区分成功/失败
"""
from typing import Optional, Any
from pydantic import BaseModel
from enum import IntEnum


class STATUS_CODE(IntEnum):
    """统一状态码枚举"""
    # 成功
    SUCCESS = 200                # 识别成功且匹配到人物（data.match 不为空）

    # 失败
    ERROR = 400                 # 通用请求参数错误（如：缺少必填参数）  

class api_response(BaseModel):
    """统一 API 响应格式 - 所有接口 HTTP 状态码都是 200"""
    status_code: int  # 直接使用驼峰命名
    message: str
    data: Optional[Any] = None

    class Config:
        # Pydantic v2 配置
        populate_by_name = True  # 允许使用 status_code 或 statusCode 初始化

    @classmethod
    def success(cls, data: Any = None, message: str = "操作成功"):
        """成功响应"""
        return cls(status_code=STATUS_CODE.SUCCESS, message=message, data=data)

    @classmethod
    def error(cls, data: Any = None, message: str = "操作失败"):
        """错误响应"""
        return cls(status_code=STATUS_CODE.ERROR, message=message, data=data)
