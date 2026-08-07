import logging

from fastapi import APIRouter

from ..services.worker_state import BatchAdmissionController


logger = logging.getLogger(__name__)


def build_worker_ops_router(controller: BatchAdmissionController) -> APIRouter:
    router = APIRouter()

    @router.get("/AE/WorkerStatus")
    async def worker_status():
        return controller.snapshot()

    @router.get("/AE/Health")
    async def health():
        status = controller.snapshot()
        return {
            "status": "ok" if status["status"] != "DRAINING" else "draining",
            "instance_id": status["instance_id"],
            "model_ready": True,
        }

    @router.put("/AE/Drain")
    async def drain():
        controller.set_draining()
        logger.info("TIAS 进入排空状态 instance_id=%s", controller.instance_id)
        return {
            "status": "DRAINING",
            "instance_id": controller.instance_id,
        }

    return router
