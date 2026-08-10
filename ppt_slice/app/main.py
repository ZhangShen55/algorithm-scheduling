"""
Main Application
主应用入口
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import video
from app.core.config import settings
from app.core.logger import get_logger
from app.services.task_manager import task_manager
try:
    from packages.operator_registry_client import install_operator_runtime
except ModuleNotFoundError:
    install_operator_runtime = None

logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info(f"{settings.APP_NAME} {settings.APP_VERSION} 启动中...")
    logger.info(f"最大并发任务数: {settings.MAX_CONCURRENT_TASKS}")
    logger.info(f"帧队列最大缓冲: {settings.MAX_QUEUE_SIZE}")
    yield
    logger.info(f"{settings.APP_NAME} 关闭")


def create_app() -> FastAPI:
    """
    创建FastAPI应用

    Returns:
        FastAPI: 应用实例
    """
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan
    )

    # CORS中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册路由
    app.include_router(video.router, tags=["视频处理"])

    @app.get("/", tags=["健康检查"])
    async def root():
        """根路径"""
        return {
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "status": "running"
        }

    @app.get("/health", tags=["健康检查"])
    async def health():
        """健康检查"""
        return {"status": "healthy"}

    if install_operator_runtime is not None:
        install_operator_runtime(
            app,
            operator_code="ppt_slice",
            capabilities=["ppt_slice"],
            default_port=9001,
            declared_capacity=settings.MAX_CONCURRENT_TASKS,
            inflight_provider=task_manager.get_task_count,
        )
    else:
        logger.warning("未安装 operator_registry_client，跳过平台运行时注册接口")

    return app


app = create_app()
