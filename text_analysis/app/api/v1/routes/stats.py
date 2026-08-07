from fastapi import APIRouter, Request
from app.core.metrics import metrics

router = APIRouter(tags=["stats"])

@router.get("/meta/stats")
def get_stats(request: Request):
    # 避免从 app.main 导入，防循环依赖；版本从 request.app 取
    version = getattr(request.app, "version", "")
    return metrics.snapshot(version=version)