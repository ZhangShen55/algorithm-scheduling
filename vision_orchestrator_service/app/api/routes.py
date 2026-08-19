from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/ready")
async def readiness(request: Request) -> JSONResponse:
    runtime = request.app.state.vision_runtime
    result = await runtime.readiness()
    return JSONResponse(
        result,
        status_code=200 if result["status"] == "ready" else 503,
    )
