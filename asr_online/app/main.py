from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import operator_deployment, settings
from app.core.logging import setup_logging
from app.core.models import load_models_if_needed
from app.api.routes.ws_online import router as ws_router
from app.api.routes.status import router as status_router
from app.utils.asr_stats import reset_stats
from packages.operator_registry_client import install_operator_runtime


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    reset_stats()
    app.state.run_start_time = datetime.utcnow()
    await load_models_if_needed()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="SeaCraftASR-Online", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(status_router)
    app.include_router(ws_router)
    install_operator_runtime(
        app,
        operator_code="asr_online",
        capabilities=["asr_online"],
        default_port=8084,
        registration_enabled=operator_deployment.platform.registration_enabled,
        control_service_url=operator_deployment.platform.control_service_url,
        heartbeat_interval_seconds=(
            operator_deployment.platform.heartbeat_interval_seconds
        ),
        max_concurrent_requests=(
            operator_deployment.platform.max_concurrent_requests
        ),
    )

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8084, reload=False)
