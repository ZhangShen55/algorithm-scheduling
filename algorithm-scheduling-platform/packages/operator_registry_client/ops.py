from collections.abc import Callable

from fastapi import APIRouter
from pydantic import BaseModel, Field

from packages.platform_common.operator_registry import OperatorLifecycle


class OperatorOpsStatus(BaseModel):
    lifecycle: OperatorLifecycle
    model_ready: bool
    inflight: int = Field(ge=0)
    declared_capacity: int = Field(gt=0)


def create_operator_ops_router(
    *,
    status_provider: Callable[[], OperatorOpsStatus],
    drain_callback: Callable[[], None],
) -> APIRouter:
    router = APIRouter(tags=["operator-ops"])

    @router.get("/ops/health")
    async def health() -> dict[str, str]:
        return {"status": "alive"}

    @router.get("/ops/status")
    async def status() -> OperatorOpsStatus:
        return status_provider()

    @router.post("/ops/drain")
    async def drain() -> OperatorOpsStatus:
        drain_callback()
        return status_provider()

    return router
