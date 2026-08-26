"""
API Routes - Video Processing
视频处理相关路由
"""
import time
from datetime import datetime
from functools import partial
from queue import Queue

from fastapi import APIRouter, BackgroundTasks

from app.core.config import settings
from app.core.logger import get_logger
from app.models.task import LocalVideoAnalysisTaskObject
from app.schemas import TaskAcceptedResponse, VideoPPTCutRequest
from app.services.shared_result import SharedResultWriter, TerminalResultPublisher
from app.services.task_manager import TaskAdmission, task_manager
from app.services.video_processor import send_terminal_callback, start_rtsp
from app.utils.uri import redact_uri_for_log

logger = get_logger("api.video")
router = APIRouter()

# 全局统计
app_start_time = time.time()
total_have_process_tasks = 0


@router.post("/LocalVideoPPTSliceTasks/v1.0.0", response_model=TaskAcceptedResponse)
async def process_rtsp(
    task_params: VideoPPTCutRequest,
    background_tasks: BackgroundTasks
):
    """
    处理视频流PPT切片任务

    Args:
        task_params: 任务参数
        background_tasks: 后台任务

    Returns:
        TaskAcceptedResponse: 任务受理结果
    """
    global total_have_process_tasks

    # 判断当前处理任务数目
    task_sum = task_manager.get_task_count()
    logger.debug(
        f"收到任务请求 - 当前任务数:[{task_sum}] 最大任务数:[{settings.MAX_CONCURRENT_TASKS}] "
        f"最大队列:[{settings.MAX_QUEUE_SIZE}] "
        f"video_path:[{redact_uri_for_log(task_params.video_path)}] "
        f"task_id:[{task_params.task_id}] operator_task_id:[{task_params.operator_task_id}] "
        f"threshold:[{task_params.threshold}]"
    )

    # 初始化队列
    frame_queue = Queue(maxsize=settings.MAX_QUEUE_SIZE)

    request_data = TaskAcceptedResponse(
        task_id=task_params.task_id,
        operator_task_id=task_params.operator_task_id,
        status=50,
        reason="",
    )

    task = LocalVideoAnalysisTaskObject(
        task_id=task_params.task_id,
        operator_task_id=task_params.operator_task_id,
        video_id=task_params.task_id,
        video_path=task_params.video_path,
        result_callback_uri=str(task_params.result_callback_uri),
        saved_frame_similarity=float(task_params.threshold),
        frame_queue=frame_queue,
        task_status_code=1,
    )

    admission = task_manager.admit_task(
        task_params.operator_task_id,
        task,
        max_tasks=settings.MAX_CONCURRENT_TASKS,
    )
    if admission is TaskAdmission.DUPLICATE:
        logger.info(
            "相同 PPT 切片任务重复提交，返回既有受理状态: %s",
            task_params.operator_task_id,
        )
        return TaskAcceptedResponse(
            task_id=task_params.task_id,
            operator_task_id=task_params.operator_task_id,
            status=50,
            reason="相同 PPT 切片任务已受理",
        )
    if admission is TaskAdmission.CONFLICT:
        error_msg = "operator_task_id 已存在且请求内容不一致"
        logger.warning(error_msg)
        return TaskAcceptedResponse(
            task_id=task_params.task_id,
            operator_task_id=task_params.operator_task_id,
            status=70,
            reason=error_msg,
        )
    if admission is TaskAdmission.CAPACITY:
        error_msg = f"当前任务数已达到最大值[{settings.MAX_CONCURRENT_TASKS}]，请稍后重试"
        logger.warning(error_msg)
        return TaskAcceptedResponse(
            task_id=task_params.task_id,
            operator_task_id=task_params.operator_task_id,
            status=70,
            reason=error_msg,
        )

    try:
        result_writer = SharedResultWriter(
            result_root=settings.RESULT_ROOT,
            task_id=task_params.task_id,
            operator_task_id=task_params.operator_task_id,
        )
        task.result_writer = result_writer
        task.terminal_publisher = TerminalResultPublisher(
            result_writer,
            partial(
                send_terminal_callback,
                callback_uri=task_params.result_callback_uri,
            ),
        )
    except Exception as exc:
        task_manager.del_task(task_params.operator_task_id)
        logger.error(f"初始化共享结果目录失败: {exc}", exc_info=True)
        return TaskAcceptedResponse(
            task_id=task_params.task_id,
            operator_task_id=task_params.operator_task_id,
            status=70,
            reason=f"初始化共享结果目录失败: {exc}",
        )

    task_sum2 = task_manager.get_task_count()
    logger.debug(f"任务已添加 - 当前任务总数:[{task_sum2}]")

    try:
        logger.info(f"process_rtsp 接口参数: {task}")
        # 启动后台任务
        background_tasks.add_task(start_rtsp, task)
        total_have_process_tasks += 1
        logger.info(f"process_rtsp 任务添加完成: task_id={task_params.task_id}")
        return request_data
    except Exception as e:
        logger.error(f"process_rtsp 接口错误: {e}", exc_info=True)
        task.task_status_code = 3
        task.cancel_event.set()
        task_manager.del_task(task_params.operator_task_id)
        request_data.status = 70
        request_data.reason = str(e)
        return request_data


@router.get("/LocalVideoPPTSliceTasks/v1.0.0/cancel")
async def process_rtsp_cancel(operator_task_id: str):
    """
    取消视频流处理任务

    Args:
        operator_task_id: 算子任务ID

    Returns:
        dict: 取消结果
    """
    task = task_manager.get_task(operator_task_id)
    if task:
        task.cancel_event.set()
        logger.info(f"任务已取消: {operator_task_id}")
        return {"status": "success", "message": f"任务 {operator_task_id} 已取消"}
    else:
        logger.warning(f"任务未找到: {operator_task_id}")
        return {"status": "error", "message": f"任务 {operator_task_id} 未找到"}


@router.get("/LocalVideoPPTSliceTasks/v1.0.0/getVersion")
async def get_version():
    """
    获取版本信息和任务状态

    Returns:
        dict: 版本信息
    """
    logger.debug("get_version 请求")

    now_time = time.time()
    dt = datetime.fromtimestamp(now_time)
    date_string = dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    total_processing_tasks = task_manager.get_task_count()
    total_fail_tasks = task_manager.get_fail_task_count()

    dt_time = datetime.fromtimestamp(app_start_time)
    start_time_str = dt_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    logger.info(f"get_version, start_time_str={start_time_str}")

    run_time_sec = now_time - app_start_time
    from app.utils.helpers import format_duration
    run_time_str = format_duration(int(run_time_sec))

    return {
        "status": "success",
        "AppVersion": settings.APP_VERSION,
        "AppStartTime": start_time_str,
        "NowTime": date_string,
        "RunTime": run_time_str,
        "Total_Fail_Tasks": total_fail_tasks,
        "Total_Processing_Tasks": total_processing_tasks,
        "Total_HaveDoneProcess_Tasks": total_have_process_tasks
    }


@router.get("/task-status/{task_id}")
async def get_task_status(task_id: str):
    """
    查询任务状态和进度

    Args:
        task_id: 任务唯一标识符

    Returns:
        dict: 任务状态
    """
    task = task_manager.get_task(task_id)
    if task:
        return {
            "status": "success",
            "message": {"task_id_status": task.task_status_code}
        }
    else:
        return {
            "status": "error",
            "message": "Task ID not found"
        }
