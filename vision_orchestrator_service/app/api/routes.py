from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/ready")
async def readiness(request: Request) -> dict[str, str]:
    """Report process readiness without claiming worker-loop liveness."""
    settings = request.app.state.service_settings
    return {"service": settings.service.name, "status": "ready"}
