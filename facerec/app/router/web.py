from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from app.core.config import settings

router = APIRouter(tags=["Web UI"])

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "static/templates")

USERNAME = settings.frontlogin.username
PASSWORD = settings.frontlogin.password

security = HTTPBasic()

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.username != USERNAME or credentials.password != PASSWORD:
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials

@router.get("/", response_class=HTMLResponse)
async def read_root(request: Request, credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    return templates.TemplateResponse("index.html", {"request": request})