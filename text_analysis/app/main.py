import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import operator_deployment, settings
from app.core.logging import setup_logging, get_logger, request_id_ctx, new_request_id
from app.api.v1.routes.meta import router as meta_router
from app.api.v1.routes.extract_keywords_segments import router as keywords_segments_router
from app.api.v1.routes.extract_keywords_segments2 import router as keywords_segments_router2
from app.api.v1.routes.extract_keywords_text import router as keywords_text_router
from app.api.v1.routes.course_overviews import router as course_overviews_router
from app.api.v1.routes.course_time_analysis import router as course_time_analysis_router
from app.api.v1.routes.language_expression_analysis import router as language_expression_analysis_router
from app.api.v1.routes.course_knowledge_corpus_analysis import router as course_knowledge_corpus_analysis_router
from app.api.v1.routes.student_interaction_analysis import router as student_interaction_analysis_router
from app.api.v1.routes.question_classification import router as question_classification_router
from app.api.v1.routes.extract_knowledge import router as extract_knowledge_router
from app.api.v1.routes.extract_knowledge_v2 import router as extract_knowledge_v2_router
from app.api.v1.routes.course_evaluation import router as course_evaluation_router
from app.api.v1.routes.stats import router as stats_router
from app.core.metrics import metrics

from app.api.v1.routes.ai_review import router as ai_generated_evaluation_router
from app.api.v1.routes.ai_review_polish import router as ai_polish_router
from app.api.v1.routes.ai_summaries import router as ai_summaries
from app.api.v1.routes.multi_country_translate import router as multi_country_translate_router
from packages.operator_registry_client import install_operator_runtime


# 初始化日志
setup_logging()
log = get_logger("app.main")

app = FastAPI(title="Text Analysis API", version="1.0.0")


# 2) 为每个请求打上 request_id，并打印收/发日志
class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or new_request_id()
        token = request_id_ctx.set(rid)
        try:
            log.info(f"--> {request.method} {request.url.path}")
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            log.info(f"<-- {request.method} {request.url.path} {response.status_code}")
            return response
        finally:
            # 清理上下文，避免脏数据串到下一个请求
            request_id_ctx.reset(token)

app.add_middleware(RequestIdMiddleware)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        # 未被 FastAPI 捕获的异常，按失败记一次
        status = 500
        raise
    finally:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        path = request.url.path
        success = 200 <= status < 400
        # 仅统计关心的几个接口
        await metrics.record(path, success, elapsed_ms)
    return response

# 获取配置信息
@app.get("/config")
def get_config():
    log.debug("fetch /config")
    return {
        "openai": {
            "api_key": "***" if settings.OPENAI_API_KEY else "",
            "base_url": settings.OPENAI_BASE_URL,
            "model": settings.OPENAI_MODEL,
            "eval_weight": {
                "base_score": settings.EVAL_WEIGHT.base_score,
                "knob": settings.EVAL_WEIGHT.knob, 
                "politics": settings.EVAL_WEIGHT.politics,
                "content": settings.EVAL_WEIGHT.content,
                "attitude": settings.EVAL_WEIGHT.attitude,
                "method": settings.EVAL_WEIGHT.method,
                "effect": settings.EVAL_WEIGHT.effect
            }
        }
    }


app.include_router(meta_router)
app.include_router(stats_router)
app.include_router(keywords_text_router)
app.include_router(keywords_segments_router)
app.include_router(course_overviews_router)
app.include_router(course_time_analysis_router)
app.include_router(language_expression_analysis_router)
app.include_router(course_knowledge_corpus_analysis_router)
app.include_router(student_interaction_analysis_router)
app.include_router(question_classification_router)
app.include_router(extract_knowledge_router)
app.include_router(extract_knowledge_v2_router)
app.include_router(course_evaluation_router)
app.include_router(keywords_segments_router2)
app.include_router(ai_generated_evaluation_router)
app.include_router(ai_polish_router)
app.include_router(ai_summaries)
app.include_router(multi_country_translate_router)
install_operator_runtime(
    app,
    operator_code="text_analysis",
    capabilities=["course_overviews", "extract_keywords"],
    default_port=8000,
    registration_enabled=operator_deployment.platform.registration_enabled,
    control_service_url=operator_deployment.platform.control_service_url,
    heartbeat_interval_seconds=operator_deployment.platform.heartbeat_interval_seconds,
    max_concurrent_requests=operator_deployment.platform.max_concurrent_requests,
)

log.info("FastAPI app initialized")

# uvicorn app.main:app --reload --port 8000  
# uvicorn app.main:app --host 0.0.0.0 --port 8002 --reload
