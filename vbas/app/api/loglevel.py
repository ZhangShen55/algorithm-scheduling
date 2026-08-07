# GET/PUT /AE/LogLevel

from fastapi import APIRouter
from ..schemas.loglevel import LogLevelResponse, LogLevelUpdate
from ..schemas.response import Response as GenericResponse
from ..services.loglevel_service import loglevel_service, set_loglevel_service

router = APIRouter(prefix="/AE", tags=["AE"])

@router.get("/LogLevel", response_model=LogLevelResponse)
async def get_loglevel():
    """
    IAS 服务查询日志等级
    """
    return await loglevel_service()

@router.put("/LogLevel", response_model=GenericResponse)
async def put_loglevel(update: LogLevelUpdate):
    """
    IAS 服务设置日志等级
    """
    return await set_loglevel_service(update)