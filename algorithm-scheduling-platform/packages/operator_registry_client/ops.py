from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field, StrictInt

from packages.operator_registry_client.lifecycle import OperatorLifecycle


class OperatorOpsStatus(BaseModel):
    lifecycle: OperatorLifecycle
    model_ready: bool
    inflight: int = Field(ge=0)
    declared_capacity: Annotated[StrictInt, Field(gt=0)]


class OperatorOpsMetadata(BaseModel):
    instance_id: str
    operator_code: str
    capabilities: list[str]
    model_version: str | None
    api_version: str | None


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
