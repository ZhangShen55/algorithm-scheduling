from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from ..schemas.stu_tea_behavior import (
    StudentBehaviorRequest,
    Stu_Tea_BehaviorResponse,
    TeacherBehaviorRequest,
    TeacherBehaviorResponse,
)
from ..services.student_behavior_service import analyze_student_behavior
from ..services.teacher_behavior_service import analyze_teacher_behavior_by_model
from ..services.worker_state import BatchAdmissionController, BatchRejectedError
import logging

logger = logging.getLogger(__name__)


def build_behavior_router(controller: Optional[BatchAdmissionController] = None) -> APIRouter:
    router = APIRouter()

    @router.post("/ImageDetect/student/v1.0.0", response_model=Stu_Tea_BehaviorResponse)
    async def student_behavior_analysis(request: StudentBehaviorRequest, http_request: Request):
        task_id = getattr(request, "task_id", None) or getattr(request, "TaskId", None) or "-"
        batch_id = getattr(request, "batch_id", None) or getattr(request, "BatchId", None) or "-"
        try:
            if controller is None:
                logger.info("收到学生推理批次 task_id=%s batch_id=%s frames=%s", task_id, batch_id, len(request.ImageList))
                return await analyze_student_behavior(request)
            async with controller.admit(str(task_id), str(batch_id), "student", len(request.ImageList), work_type=http_request.headers.get("X-Algorithm-Work-Type", "offline")):
                status = controller.snapshot()
                logger.info(
                    "收到学生推理批次 task_id=%s batch_id=%s frames=%s running_batches=%s queued_batches=%s",
                    task_id,
                    batch_id,
                    len(request.ImageList),
                    status["running_batches"],
                    status["queued_batches"],
                )
                return await analyze_student_behavior(request)
        except BatchRejectedError as exc:
            status = controller.snapshot() if controller is not None else {}
            logger.warning(
                "拒绝学生推理批次 task_id=%s batch_id=%s running_batches=%s max_concurrent_batches=%s reason=%s",
                task_id,
                batch_id,
                status.get("running_batches"),
                status.get("max_concurrent_offline_batches"),
                exc.message,
            )
            raise HTTPException(status_code=exc.status_code, detail=exc.message)
        except HTTPException:
            raise
        except Exception as e:
            logger.error("学生推理批次失败 task_id=%s batch_id=%s reason=%s", task_id, batch_id, str(e))
            raise HTTPException(status_code=500, detail=f"分析失败: {str(e)}")

    @router.post(
        "/ImageDetect/teacher/v1.0.0",
        response_model=TeacherBehaviorResponse,
        response_model_exclude_none=True,
    )
    async def teacher_behavior_analysis(request: TeacherBehaviorRequest, http_request: Request):
        task_id = getattr(request, "task_id", None) or getattr(request, "TaskId", None) or "-"
        batch_id = getattr(request, "batch_id", None) or getattr(request, "BatchId", None) or "-"
        try:
            if controller is None:
                logger.info("收到教师推理批次 task_id=%s batch_id=%s frames=%s", task_id, batch_id, len(request.ImageList))
                return await analyze_teacher_behavior_by_model(request)
            async with controller.admit(str(task_id), str(batch_id), "teacher", len(request.ImageList), work_type=http_request.headers.get("X-Algorithm-Work-Type", "offline")):
                status = controller.snapshot()
                logger.info(
                    "收到教师推理批次 task_id=%s batch_id=%s frames=%s running_batches=%s queued_batches=%s",
                    task_id,
                    batch_id,
                    len(request.ImageList),
                    status["running_batches"],
                    status["queued_batches"],
                )
                return await analyze_teacher_behavior_by_model(request)
        except BatchRejectedError as exc:
            status = controller.snapshot() if controller is not None else {}
            logger.warning(
                "拒绝教师推理批次 task_id=%s batch_id=%s running_batches=%s max_concurrent_batches=%s reason=%s",
                task_id,
                batch_id,
                status.get("running_batches"),
                status.get("max_concurrent_offline_batches"),
                exc.message,
            )
            raise HTTPException(status_code=exc.status_code, detail=exc.message)
        except HTTPException:
            raise
        except Exception as e:
            logger.error("教师推理批次失败 task_id=%s batch_id=%s reason=%s", task_id, batch_id, str(e))
            raise HTTPException(status_code=500, detail=f"分析失败: {str(e)}")

    return router


router = build_behavior_router()
student_behavior_analysis = next(
    route.endpoint
    for route in router.routes
    if getattr(route, "path", None) == "/ImageDetect/student/v1.0.0"
)
teacher_behavior_analysis = next(
    route.endpoint
    for route in router.routes
    if getattr(route, "path", None) == "/ImageDetect/teacher/v1.0.0"
)
