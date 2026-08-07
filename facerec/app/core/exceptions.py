# app/core/exceptions.py
class BaseAPIException(Exception):
    """
    系统基础异常类，方便后续统一捕获
    """
    # pass
    def __init__(self, status_code:int,detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(status_code,detail)

class ImageValidationError(BaseAPIException):
    """
    图片验证失败异常 (尺寸、大小、格式不符)
    """
    pass

class FaceDetectionError(BaseAPIException):
    """
    人脸检测相关异常 (如未检测到人脸)
    """
    pass

class DatabaseError(BaseAPIException):
    """
    数据库操作异常
    """
    pass

