# /AE/SyncTasks
from fastapi import APIRouter, HTTPException
from ..schemas.task import TaskInfo, SyncTasksResponse
from ..services.task_service import sync_tasks
from ..services.task_service_base64 import sync_tasks_data
import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/AE", tags=["AE"])
@router.post("/SyncTasks", response_model=SyncTasksResponse)
async def sync_tasks_endpoint(task_info: TaskInfo):
    """
    IAS 服务发起同步分析任务
    """
    try:
        return await sync_tasks(task_info)
    except Exception as e:
        # 记录详细的错误信息到日志
        logger.error(f"Error processing task: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/SyncTasks2", response_model=SyncTasksResponse)
async def sync_tasks_endpoint_base(task_info: TaskInfo):
    """
    IAS 服务发起同步分析任务
    """
    try:
        return await sync_tasks_data(task_info)
    except Exception as e:
        # 记录详细的错误信息到日志
        logger.error(f"Error processing task: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
