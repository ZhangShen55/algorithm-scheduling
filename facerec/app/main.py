# app/main.py
import asyncio
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core import ai_engine
from app.core.config import settings
from app.core.logger import get_logger, new_request_id, request_id_ctx
from app.core.runtime_paths import ensure_runtime_directories
from app.middleware import APIStatsMiddleware
from app.models.api_response import StatusCode
from app.router import faces, ops, persons, web
from packages.operator_registry_client import install_operator_runtime

logger = get_logger(__name__)

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
MAX_WORKERS = settings.thread.max_workers
ensure_runtime_directories(PROJECT_ROOT)

# ---------------- 生命周期管理 (核心) ----------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ================= 启动 (Startup) =================
    logger.info("System Startup: Initializing resources...")

    # 2. Dlib 进程池初始化 (注入到 ai_engine)
    # 确保 max_workers 设置合理 (建议 1 或 2，防止内存爆炸)
    # 当前 settings.thread.max_workers 建议设置为 2
    logger.info(f"Initializing Dlib Process Pool with {MAX_WORKERS} workers...")
    pool = None
    startup_gate = None
    try:
        process_context = multiprocessing.get_context()
        status_queue = process_context.Queue()
        startup_gate = process_context.Event()
        pool = ProcessPoolExecutor(
            max_workers=MAX_WORKERS,
            mp_context=process_context,
            initializer=ai_engine._init_dlib_worker,
            initargs=(status_queue, startup_gate),
        )
        self_checks = [
            pool.submit(ai_engine._dlib_worker_self_check)
            for _ in range(MAX_WORKERS)
        ]
        await asyncio.to_thread(
            ai_engine._collect_dlib_worker_status,
            status_queue,
            expected_workers=MAX_WORKERS,
            timeout_seconds=30.0,
        )
        startup_gate.set()
        await asyncio.gather(*(asyncio.wrap_future(check) for check in self_checks))
        ai_engine.GLOBAL_PROCESS_POOL = pool
        ops.readiness.set_dlib_workers_ready(True)
        logger.info("全部 Dlib worker 预热完成")
    except Exception as e:
        logger.exception("Dlib worker 预热失败: %s", e)
        ops.readiness.set_dlib_workers_ready(False)
        ai_engine.GLOBAL_PROCESS_POOL = None
        if startup_gate is not None:
            startup_gate.set()
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
            pool = None

    if not await ops.readiness.check():
        logger.error("MongoDB、ArcFace 或 Dlib worker 未就绪")

    yield  # 应用运行中...

    # ================= 关闭 (Shutdown) =================
    logger.info("系统关闭: 释放资源...")

    # 3. 资源清理
    if pool is not None:
        pool.shutdown(wait=True)
    ai_engine.GLOBAL_PROCESS_POOL = None
    ops.readiness.set_dlib_workers_ready(False)
    logger.info("Dlib 进程池关闭成功.")


# ---------------- App 初始化 ----------------
app = FastAPI(
    title="人脸识别API系统",
    lifespan=lifespan
)

# ---------------- 全局异常处理器 ----------------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    处理 Pydantic 验证错误，返回统一的 ApiResponse 格式
    HTTP 状态码永远是 200，通过 status_code 字段区分错误
    """
    # 提取第一个错误信息
    errors = exc.errors()
    if errors:
        error = errors[0]
        # 获取字段名
        field = error.get('loc', [])[-1] if error.get('loc') else 'unknown'
        # 获取错误类型
        error_type = error.get('type', '')

        # 根据错误类型生成友好的错误信息
        if error_type == 'missing':
            message = f"缺少{field}参数"
        else:
            # 使用自定义的错误信息（来自 field_validator）
            message = error.get('msg', '参数验证失败')

        logger.error(f"[ValidationError] 参数验证失败: {message}, path: {request.url.path}")

        # 返回 JSONResponse，HTTP 状态码为 200
        return JSONResponse(
            status_code=200,
            content={
                "status_code": StatusCode.BAD_REQUEST,
                "message": message,
                "data": None
            }
        )

    return JSONResponse(
        status_code=200,
        content={
            "status_code": StatusCode.BAD_REQUEST,
            "message": "请求参数验证失败",
            "data": None
        }
    )

# ---------------- 注册中间件 ----------------
# 1. API 统计中间件（必须在 request_id 中间件之后）
app.add_middleware(APIStatsMiddleware)

@app.middleware("http")
async def add_request_id(request: Request, call_next):
    rid = new_request_id()
    request_id_ctx.set(rid)
    response = await call_next(request)
    # 响应头中添加 X-Request-Id（前端就不返回了）
    # response.headers["X-Request-Id"] = rid
    return response

# ---------------- 挂载静态资源 ----------------
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.mount("/media", StaticFiles(directory=PROJECT_ROOT / "media"), name="media")

# ---------------- 注册路由 ----------------
app.include_router(ops.router)
app.include_router(faces.router)
app.include_router(persons.router)
app.include_router(web.router)
install_operator_runtime(
    app,
    operator_code="facerec",
    capabilities=["recognize"],
    default_port=8003,
    model_ready_provider=ops.readiness.model_ready,
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8003, reload=False)
