from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from .api.stu_tea_behavior import build_behavior_router
from .api.worker_ops import build_worker_ops_router
from .core.settings import operator_deployment, settings
from .core.logging import setup_logging
from .services.worker_state import BatchAdmissionController
from packages.operator_registry_client import install_operator_runtime
import logging
import asyncio
import uvloop

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="TIAS视觉推理服务",
    version="6.0",
)

worker_controller = BatchAdmissionController(
    instance_id=str(getattr(settings, "InstanceId", "tias-8981")),
    base_url=str(getattr(settings, "BaseUrl", "http://127.0.0.1:8981")),
    max_concurrent_batches=int(getattr(settings, "MaxConcurrentBatches", 1)),
    max_queue_size=int(getattr(settings, "MaxQueueSize", 0)),
)
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


@app.on_event("startup")
async def startup_event():
    status = worker_controller.snapshot()
    logger.info(
        "TIAS 启动 instance_id=%s base_url=%s max_concurrent_batches=%s max_queue_size=%s",
        status["instance_id"],
        status["base_url"],
        status["max_concurrent_batches"],
        status["max_queue_size"],
    )


@app.on_event("shutdown")
async def shutdown_event():
    worker_controller.set_draining()


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    # 记录验证错误的详细信息
    logger.error("请求参数校验失败 path=%s errors=%s", request.url.path, exc.errors())
    # 返回标准的 422 错误响应
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()}
    )

app.include_router(build_behavior_router(worker_controller))
app.include_router(build_worker_ops_router(worker_controller))

if bool(getattr(settings, "TiasExposeLegacySyncTasks", False)):
    from .api.tasks import router as tasks_router

    app.include_router(tasks_router)

install_operator_runtime(
    app,
    operator_code="vbas",
    capabilities=["student_behavior", "teacher_behavior"],
    default_port=8981,
    registration_enabled=operator_deployment.platform.registration_enabled,
    control_service_url=operator_deployment.platform.control_service_url,
    heartbeat_interval_seconds=operator_deployment.platform.heartbeat_interval_seconds,
    max_concurrent_requests=operator_deployment.platform.max_concurrent_requests,
)

