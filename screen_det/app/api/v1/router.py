from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import aggregate, config, health, inspect, occlusion, quality_abnormal, screen, tilt

router = APIRouter()
router.include_router(tilt.router)
router.include_router(screen.router)
router.include_router(inspect.router)
router.include_router(aggregate.router)
router.include_router(quality_abnormal.router)
router.include_router(occlusion.router)
router.include_router(health.router)
router.include_router(config.router)
