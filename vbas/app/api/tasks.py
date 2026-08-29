# /AE/SyncTasks
from fastapi import APIRouter, HTTPException, Request
from ..schemas.task import TaskInfo, SyncTasksResponse
from ..services.worker_state import BatchAdmissionController, BatchRejectedError
import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/AE", tags=["AE"])


def build_sync_tasks2_router(controller: BatchAdmissionController) -> APIRouter:
    online_router = APIRouter(prefix="/AE", tags=["AE"])

    @online_router.post("/SyncTasks2", response_model=SyncTasksResponse)
    async def sync_tasks_endpoint_base(task_info: TaskInfo, request: Request):
        task_id = task_info.TaskID or "-"
        batch_id = f"{task_id}-person-count"
        try:
            from ..services.task_service_base64 import sync_tasks_data
            async with controller.admit(
                task_id,
                batch_id,
                "person-count",
                len(task_info.ImageList or []),
                work_type=request.headers.get("X-Algorithm-Work-Type", "offline"),
            ):
                return await sync_tasks_data(task_info)
        except BatchRejectedError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        except Exception as exc:
            logger.error("SyncTasks2 处理失败 task_id=%s reason=%s", task_id, exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return online_router


@router.post("/SyncTasks", response_model=SyncTasksResponse)
async def sync_tasks_endpoint(task_info: TaskInfo):
    """
    IAS 服务发起同步分析任务
    """
    try:
        from ..services.task_service import sync_tasks
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
        from ..services.task_service_base64 import sync_tasks_data
        return await sync_tasks_data(task_info)
    except Exception as e:
        # 记录详细的错误信息到日志
        logger.error(f"Error processing task: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
