"""
常量定义模块
集中管理项目中使用的常量，避免魔法数字和重复字符串
"""

# ==================== HTTP 状态码 ====================
HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_UNPROCESSABLE_ENTITY = 422
HTTP_INTERNAL_SERVER_ERROR = 500

# ==================== 业务常量 ====================

# 匹配原因
class MatchReason:
    PRIORITY = "优先比对命中"
    GLOBAL = "全局比对命中"
    NO_MATCH = "相似度低于阈值，与已知人脸库不匹配"
    NO_FACE = "未检测到人脸"
    FACE_TOO_SMALL = "人脸像素过小，无法识别"
    NO_DATA = "数据库中暂无人脸数据，请先录入"

# 响应消息
class ResponseMessage:
    # 成功消息
    PERSON_ADDED = "人物添加成功"
    PERSON_UPDATED = "人物更新成功"
    PERSON_DELETED = "人物已删除"
    BATCH_UPLOAD_SUCCESS = "批量入库完成"

    # 错误消息
    INVALID_IMAGE = "未接收到有效图片数据或图像数据存在异常"
    FACE_DETECTION_ERROR = "人脸检测服务内部错误"
    NO_FACE_DETECTED = "未检测到有效人脸"
    FACE_TOO_SMALL_MSG = "检测到的人脸过小，无法识别，请重新捕捉人脸"
    FEATURE_EXTRACTION_ERROR = "人脸特征提取失败"
    NO_DATABASE_DATA = "数据库中没有有效人脸特征，请先录入人脸数据"
    PERSON_NOT_FOUND = "人物不存在"
    PERSON_ALREADY_EXISTS = "该人物已存在请勿重复创建"
    DELETE_FAILED = "删除操作失败"
    SEARCH_FAILED = "搜索人物失败"

    # 匹配消息
    MATCH_SUCCESS = "匹配成功"
    MATCH_FAILED = "匹配失败，未能够匹配到目标人物"
    NO_FACE_IN_IMAGE = "图像中未检测到人脸，请重新捕捉人脸"

# 数据库字段
class DBFields:
    ID = "_id"
    NAME = "name"
    NUMBER = "number"
    PHOTO_PATH = "photo_path"
    EMBEDDING = "embedding"
    TIP = "tip"

# 文件相关
class FileConstants:
    MEDIA_DIR = "media"
    PERSON_PHOTOS_DIR = "person_photos"
    DETECTIONS_DIR = "detections"
    DEFAULT_PHOTO_EXT = ".jpg"

# 图像质量提示
class ImageQualityTip:
    GOOD = "人脸特征像素正常，可以使用"
    LOW_RESOLUTION = "人脸特征像素过低，可能影响检测效果"
