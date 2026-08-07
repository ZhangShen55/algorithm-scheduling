from contextlib import asynccontextmanager
import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import uvicorn

from app.api.routes import ocr, system
from app.core.logging import configure_logging
from app.core.settings import Settings, load_settings
from app.engines.base import FormulaEngine, OCREngine
from app.schemas.ocr import OCRResponse
from app.services.ocr_service import OCRService
from app.services.formula_service import FormulaService
from packages.operator_registry_client import install_operator_runtime


LOGGER = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    engine: OCREngine | None = None,
    formula_engine: FormulaEngine | None = None,
) -> FastAPI:
    resolved_settings = settings or load_settings()
    configure_logging(resolved_settings.logging)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_engine = engine
        resolved_formula_engine = formula_engine
        formula_service = None
        service = None
        try:
            if resolved_engine is None:
                from app.engines.paddleocr_v6 import PaddleOCRV6Engine

                resolved_engine = PaddleOCRV6Engine(resolved_settings.ocr)
            if (
                resolved_settings.formula.enabled
                and resolved_formula_engine is None
            ):
                from app.engines.paddle_formula import PaddleFormulaEngine

                resolved_formula_engine = PaddleFormulaEngine(
                    resolved_settings.formula,
                    resolved_settings.ocr,
                )
            formula_service = FormulaService(
                configured_enabled=resolved_settings.formula.enabled,
                engine=resolved_formula_engine,
            )
            service = OCRService(
                engine=resolved_engine,
                image_max_bytes=resolved_settings.ocr.image_max_bytes,
                max_concurrency=resolved_settings.ocr.max_concurrency,
                formula_service=formula_service,
            )
            app.state.settings = resolved_settings
            app.state.start_time = time.time()
            app.state.ocr_service = service
            yield
        finally:
            if service is not None:
                service.close()
            else:
                try:
                    if formula_service is not None:
                        formula_service.close()
                    elif resolved_formula_engine is not None:
                        resolved_formula_engine.close()
                finally:
                    if resolved_engine is not None:
                        resolved_engine.close()

    application = FastAPI(
        title=resolved_settings.application.name,
        version=resolved_settings.application.version,
        docs_url="/docs",
        lifespan=lifespan,
    )
    application.include_router(ocr.router)
    application.include_router(system.router)
    install_operator_runtime(
        application,
        operator_code="ocr",
        capabilities=["ocr"],
        default_port=8866,
        model_ready_provider=lambda: hasattr(application.state, "ocr_service"),
    )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        LOGGER.info("OCR 请求格式错误：%s", error.errors())
        response = OCRResponse.error(4000, "请求格式错误")
        return JSONResponse(status_code=200, content=response.model_dump())

    return application


def run() -> None:
    settings = load_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.server.host,
        port=settings.server.port,
        workers=settings.server.workers,
    )


app = create_app()


if __name__ == "__main__":
    run()
