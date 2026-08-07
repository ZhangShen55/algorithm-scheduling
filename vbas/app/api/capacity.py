from fastapi import APIRouter,Request
from ..schemas.capacity import CapacityResponse, CapacityV2Response
from ..services.capacity_service import capacity_service, capacity_v2_service

router = APIRouter(prefix="/AE", tags=["AE"])

@router.get("/Capacity", response_model=CapacityResponse)
async def get_capacity(request: Request):
    """
    IAS 服务查询智能分析能力信息
    """
    # instance_id = request.app.state.instance_id or "unknown"
    return await capacity_service()
    # return await capacity_service(request.app.state.instance_id)


@router.get("/Capacity_v2", response_model=CapacityV2Response)
async def get_capacity_v2(request: Request):
    """
    IAS 服务查询增强版智能分析能力信息，包括连接数、处理图片数和运行时间
    """
    return await capacity_v2_service()
    # return await capacity_v2_service(request.app.state.instance_id)
