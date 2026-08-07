# app/schemas/error_codes.py
from enum import IntEnum

class AppErrCode(IntEnum):
    SUCCESS                 = 0
    APP_NOT_RUNNING         = 1     # APP不在服务态
    INTERNAL_ERROR          = 2     # 内部错误：异步任务分析过程中出错（解码失败、没有码流）等，需要提供具体的错误描述
    OVERLOAD                = 3     # 超出并发能力
    BAD_JSON                = 4     # JSON格式不规范
    UNSUPPORTED_MEDIA_TYPE  = 5     # 不支持的媒体类型
    INVALID_LOG_LEVEL       = 6     # 错误的日志等级
    TASK_NOT_FOUND          = 7     # 任务不存在
    TASK_ALREADY_EXISTS     = 8     # 任务已存在
    INVALID_JSON_PARAMS     = 9     # JSON参数不正确
    UPDATING_MODEL          = 10    # 正在更新模型
    BAD_URL_QUERY           = 11    # URL查询参数不正确
    MISSING_URL_PARAM       = 12    # URL缺少查询参数
    TRY_AGAIN               = 13    # 请稍后重试

class AdapterErrCode(IntEnum):
    NO_HANDLE_RETURNED      = 0x0100
    UNKNOWN_START_ERROR     = 0x0101
    OBJECT_NOT_ACCESSIBLE   = 0x0102
    OBJECT_INVALID          = 0x0103
    TARGET_INVALID          = 0x0104
    VIDEO_DECODING_ERROR    = 0x0105
    INVALID_IMAGE           = 0x0106
    LIB_NOT_FOUND           = 0x0107
    LIB_ALREADY_EXISTS      = 0x0108
    DOWNLOAD_FAILED         = 0x0109
    OVER_MAX_CONCURRENCY    = 0x010A

class StatusCode(IntEnum):
    OK                      = 1000
    INTERNAL_EXCEPTION      = 1001
    DB_EXCEPTION            = 1002
    BACKEND_EXCEPTION       = 1003
    SERVICE_REG_SUCCESS     = 2000
    SERVICE_REG_FAILED      = 2001
    FIELD_VALIDATION_FAILED = 2002
    SUCCESS_3000            = 3000
    APP_NOT_STARTED         = 3001
    ALGO_ERROR              = 3002
    CONCURRENCY_EXCEEDED    = 3003
    ASYNC_TASK_ERROR        = 3004
    JSON_CONTENT_ERROR      = 3005
    UNSUPPORTED_MEDIA       = 3006
    MISSING_ENGINE_URI      = 3007
    TASK_CREATE_SUCCESS     = 4000
    FIELD_VALIDATION_ERR    = 4001
    LOG_QUERY_FAILED        = 5001
