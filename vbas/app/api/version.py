# /AE/Version

from fastapi import APIRouter
from ..schemas.version import VersionResponse
from ..services.version_service import version_service

router = APIRouter(prefix="/AE", tags=["AE"])

@router.get("/Version", response_model=VersionResponse)
async def get_version():
    """
    IAS 获取算法版本信息
    """
    return await version_service()
