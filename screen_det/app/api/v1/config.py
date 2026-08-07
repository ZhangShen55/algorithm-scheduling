from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.config import StartupConfigChangedError, get_settings, reload_settings


router = APIRouter(tags=["config"])


def _screen_detection_dict() -> dict:
    cfg = get_settings().screen_detection
    return {
        **cfg.__dict__,
        "allowed_class_ids": list(cfg.allowed_class_ids),
    }


def _quality_abnormal_detection_dict() -> dict:
    return get_settings().quality_abnormal_detection.__dict__


def _occlusion_detection_dict() -> dict:
    return get_settings().occlusion_detection.__dict__


def _aggregate_detection_dict() -> dict:
    cfg = get_settings().aggregate_detection
    return {
        **cfg.__dict__,
        "default_modules": list(cfg.default_modules),
    }


@router.get("/config")
async def get_runtime_config() -> dict:
    settings = get_settings()
    return {
        "app": settings.app.__dict__,
        "server": settings.server.__dict__,
        "yolo": settings.yolo.__dict__,
        "model_protection": settings.model_protection.__dict__,
        "detection": settings.detection.__dict__,
        "screen_detection": _screen_detection_dict(),
        "quality_abnormal_detection": _quality_abnormal_detection_dict(),
        "occlusion_detection": _occlusion_detection_dict(),
        "aggregate_detection": _aggregate_detection_dict(),
        "runtime": settings.runtime.__dict__,
    }


@router.post("/config/reload")
async def reload_runtime_config() -> dict:
    try:
        settings = reload_settings()
    except StartupConfigChangedError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": 409, "msg": str(exc)},
        ) from exc
    return {
        "code": 200,
        "msg": "Config reloaded",
        "detection": settings.detection.__dict__,
        "screen_detection": {
            **settings.screen_detection.__dict__,
            "allowed_class_ids": list(settings.screen_detection.allowed_class_ids),
        },
        "quality_abnormal_detection": settings.quality_abnormal_detection.__dict__,
        "occlusion_detection": settings.occlusion_detection.__dict__,
        "aggregate_detection": {
            **settings.aggregate_detection.__dict__,
            "default_modules": list(settings.aggregate_detection.default_modules),
        },
        "yolo": settings.yolo.__dict__,
    }
