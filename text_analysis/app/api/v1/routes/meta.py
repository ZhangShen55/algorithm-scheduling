import requests
from fastapi import APIRouter, HTTPException
from app.core.config import settings

router = APIRouter(tags=["meta"])

@router.get("/api/models")
def get_models():
    if not settings.OPENAI_API_KEY:
        raise HTTPException(status_code=400, detail="API key not configured")
    try:
        resp = requests.get(f"{settings.OPENAI_BASE_URL}/models", headers={
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}"
        })
        data = resp.json()
        return {"models": [{"name": m["id"]} for m in data.get("data", [])]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))