import os
from ..schemas.loglevel import LogLevelResponse, LogLevelInfo, LogLevelUpdate
from ..schemas.response import Response as GenericResponse
from ..schemas.error_codes import AppErrCode

async def loglevel_service() -> LogLevelResponse:
    """
    返回当前日志等级以及所有可用等级（用于查询）。
    """
    # 真实场景可从配置/环境或日志框架里取
    current = os.getenv("LOG_LEVEL", "NOTICE")
    all_lvls = ["DEBUG", "INFO", "NOTICE", "WARN", "ERROR", "FATAL"]

    info = LogLevelInfo(
        Level=current,
        AllLevel=all_lvls
    )

    return LogLevelResponse(
        Response=GenericResponse(ErrCode=AppErrCode.SUCCESS, Desc="成功"),
        LogLevel=info
    )

async def set_loglevel_service(update: LogLevelUpdate) -> GenericResponse:
    """
    设置新的日志等级。
    """
    # 真实项目中你可能要去调用日志框架的接口；这里简单写到环境变量
    os.environ["LOG_LEVEL"] = update.Level
    return GenericResponse(ErrCode=AppErrCode.SUCCESS, Desc="成功")