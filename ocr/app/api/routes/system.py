from datetime import datetime
import subprocess
import time

from fastapi import APIRouter, Depends, Request
import psutil

from app.api.dependencies import get_ocr_service
from app.schemas.system import VersionResponse
from app.services.ocr_service import OCRService


router = APIRouter()


def _format_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _format_duration(seconds: int) -> str:
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days}天 {hours:02d}:{minutes:02d}:{seconds:02d}"


def _gpu_usage(device: str) -> dict:
    if not device.startswith("cuda:"):
        return {"status": "unavailable", "message": "当前设备未使用 NVIDIA GPU"}
    index = device.split(":", maxsplit=1)[1]
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,utilization.memory,memory.used",
                "--format=csv,noheader,nounits",
                "-i",
                index,
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        ).strip()
        compute, memory, used = [part.strip() for part in output.split(",")]
        return {
            "gpu_compute_used_percent": int(compute),
            "gpu_mem_used_percent": int(memory),
            "gpu_mem_used_size(MB)": int(used),
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"status": "unavailable", "message": "NVIDIA GPU 指标不可用"}


@router.get("/ocr/getVersion", response_model=VersionResponse)
def version(
    request: Request,
    service: OCRService = Depends(get_ocr_service),
) -> VersionResponse:
    now = time.time()
    detect_tasks, recognition_tasks = service.counters()
    memory_mb = psutil.Process().memory_info().rss / 1024 / 1024
    return VersionResponse(
        status="success",
        AppVersion=request.app.state.settings.application.version,
        AppStartTime=_format_time(request.app.state.start_time),
        NowTime=_format_time(now),
        RunTime=_format_duration(int(now - request.app.state.start_time)),
        memory_usage=f"{memory_mb:.2f} MB",
        gpu_usage=_gpu_usage(request.app.state.settings.ocr.device),
        Total_RegProcess_Tasks=recognition_tasks,
        Total_DetectProcess_Tasks=detect_tasks,
    )
