from __future__ import annotations

import time

import psutil
from fastapi import APIRouter, Response

from app.api.v1.common import format_elapsed
from app.core.config import get_settings
from app.core.state import app_state
from app.services.screen_detector import _holder as screen_model_holder
from app.services.screen_detector import is_screen_model_ready
from app.services.screen_detector import resolve_yolo_device
from app.services.occlusion_detector import _yolo_holder as occlusion_model_holder
from app.services.occlusion_detector import is_occlusion_model_ready


router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(response: Response) -> dict:
    settings = get_settings()
    elapsed = time.time() - app_state.start_time
    process = psutil.Process()
    ready = is_screen_model_ready() and is_occlusion_model_ready()
    screen_model = {
        key: value
        for key, value in screen_model_holder.status.items()
        if key != "weights"
    }
    occlusion_model = {
        key: value
        for key, value in occlusion_model_holder.status.items()
        if key != "weights"
    }

    if not ready:
        response.status_code = 503

    return {
        "status": "success" if ready else "not_ready",
        "ready": ready,
        "elapsed_time": format_elapsed(elapsed),
        "total_requests": app_state.request_count,
        "memory_mb": round(process.memory_info().rss / 1024 / 1024, 2),
        "yolo": {
            **settings.yolo.__dict__,
            "tilt_inference_device": "cpu",
            "yolo_device_resolved": (
                screen_model_holder.device
                if screen_model["loaded"]
                else resolve_yolo_device(settings.yolo.device)
            ),
            "cuda_visible_devices": __import__("os").environ.get("CUDA_VISIBLE_DEVICES"),
        },
        "screen_model": screen_model,
        "occlusion_model": occlusion_model,
    }
