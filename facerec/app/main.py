# app/main.py
import os
from pathlib import Path
from contextlib import asynccontextmanager
from concurrent.futures import ProcessPoolExecutor
from contextvars import ContextVar
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.database import db
from app.core import ai_engine
from app.core.config import settings
from app.core.logger import get_logger
from app.core.runtime_paths import ensure_runtime_directories
from app.router import faces, persons, web, ops
from app.core.logger import request_id_ctx, new_request_id
from app.middleware import APIStatsMiddleware
from app.models.api_response import StatusCode, ApiResponse
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

    try:
        await db.command({"ping": 1})
        logger.debug("MongoDB ping ok")
    except Exception as e:
        logger.exception("MongoDB ping failed: %s", e)

    # 2. Dlib 进程池初始化 (注入到 ai_engine)
    # 确保 max_workers 设置合理 (建议 1 或 2，防止内存爆炸)
    # 当前 settings.thread.max_workers 建议设置为 2
    logger.info(f"Initializing Dlib Process Pool with {MAX_WORKERS} workers...")
    pool = ProcessPoolExecutor(
        max_workers=MAX_WORKERS,
        initializer=ai_engine._init_dlib_worker
    )
    ai_engine.GLOBAL_PROCESS_POOL = pool

    yield  # 应用运行中...

    # ================= 关闭 (Shutdown) =================
    logger.info("系统关闭: 释放资源...")

    # 3. 资源清理
    pool.shutdown(wait=True)
    ai_engine.GLOBAL_PROCESS_POOL = None
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
)

# ---------------- 调试入口 ----------------
if __name__ == "__main__":
    import uvicorn

    # 开发环境调试用
    uvicorn.run(app, host="0.0.0.0", port=8003, reload=False)
    # PYTHONPATH=/root/workspace/FaceRecAPI_DEV OMP_NUM_THREADS=1 uvicorn app.main:app --host 0.0.0.0 --port 8003 --workers 4 --env-file .env


    # cd app
    # PYTHONPATH=/root/workspace/FaceRecAPI_DEV OMP_NUM_THREADS=1 uvicorn app.main:app --host 0.0.0.0 --port 8003  --env-file .env --workers 1

    # ============ 后台启动命令 ============
    # conda activate facerecapi
    # cd /root/workspace/FaceRecAPI_DEV/app
    # nohup env PYTHONPATH=/root/workspace/FaceRecAPI_DEV OMP_NUM_THREADS=1 uvicorn app.main:app --host 0.0.0.0 --port 8003 --env-file .env --workers 1 > /root/workspace/FaceRecAPI_DEV/app/logs/facerec_server_uvicorn.log 2>&1 & echo $! > /root/workspace/FaceRecAPI_DEV/app/logs/facerec_server_uvicorn.pid

    # ============ 查看运行状态 ============
    # ps aux | grep "facerec_server_uvicorn app.main:app"
    # tail -f /root/workspace/FaceRecAPI_DEV/app/logs/facerec_server_uvicorn.log

    # ============ 关闭服务 ============
    # 方法1: 使用 PID 文件关闭（推荐，带进程检查）
    # PID_FILE=/root/workspace/FaceRecAPI_DEV/app/logs/facerec_server_uvicorn.pid
    # if [ -f $PID_FILE ]; then
    #     PID=$(cat $PID_FILE)
    #     if ps -p $PID > /dev/null 2>&1; then
    #         kill $PID && echo "进程 $PID 已终止"
    #     else
    #         echo "进程 $PID 不存在，可能已经停止"
    #     fi
    #     rm $PID_FILE
    # else
    #     echo "PID 文件不存在"
    # fi

    # 方法2: 简单关闭（不检查进程是否存在）
    # kill $(cat /root/workspace/FaceRecAPI_DEV/app/logs/facerec_server_uvicorn.pid) 2>/dev/null
    # rm /root/workspace/FaceRecAPI_DEV/app/logs/facerec_server_uvicorn.pid 2>/dev/null

    # 方法3: 查找进程并关闭
    # ps aux | grep "facerec_server_uvicorn app.main:app" | grep -v grep | awk '{print $2}' | xargs kill

    # 方法4: 强制关闭 (慎用)
    # ps aux | grep "facerec_server_uvicorn app.main:app" | grep -v grep | awk '{print $2}' | xargs kill -9
