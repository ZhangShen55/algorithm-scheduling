import json
import logging
import threading

from app.core.exceptions import InferenceError, RequestFormatError
from app.engines.base import EngineResult, OCREngine
from app.schemas.ocr import OCRRequest, OCRResponse
from app.services.formula_service import FormulaService
from app.utils.image import decode_base64_image


LOGGER = logging.getLogger(__name__)


class OCRService:
    def __init__(
        self,
        engine: OCREngine,
        image_max_bytes: int,
        max_concurrency: int = 1,
        formula_service: FormulaService | None = None,
    ):
        self.engine = engine
        self.image_max_bytes = image_max_bytes
        self.formula_service = formula_service or FormulaService()
        self._semaphore = threading.BoundedSemaphore(max_concurrency)
        self._engine_lock = threading.Lock()
        self._counter_lock = threading.Lock()
        self._detect_tasks = 0
        self._recognition_tasks = 0
        self._closed = False

    def predict(self, request: OCRRequest) -> OCRResponse:
        self._validate_batch(request)
        images = [
            decode_base64_image(value, self.image_max_bytes)
            for value in request.value
        ]

        serialized_results = []
        formula_results = []
        try:
            with self._semaphore:
                for image in images:
                    with self._engine_lock:
                        engine_results = self.engine.predict(image)
                    results = [
                        normalized
                        for result in engine_results
                        if (normalized := self._normalize_result(result)) is not None
                    ]
                    serialized_results.append(
                        json.dumps(
                            results,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    )
                formula_results = self.formula_service.predict(
                    request.enable_formula,
                    list(request.key),
                    images,
                )
        except Exception as error:
            LOGGER.exception("OCR 推理异常")
            raise InferenceError() from error

        with self._counter_lock:
            self._detect_tasks += len(images)
            self._recognition_tasks += len(images)

        # 每个结果保持为 JSON 字符串，与同下标图片 ID 对应。
        return OCRResponse(
            key=list(request.key),
            value=serialized_results,
            formula_results=formula_results,
        )

    @staticmethod
    def _validate_batch(request: OCRRequest) -> None:
        if not request.key or len(request.key) != len(request.value):
            raise RequestFormatError()
        if any(not image_id.strip() for image_id in request.key):
            raise RequestFormatError("图片 ID 不能为空")

    def counters(self) -> tuple[int, int]:
        with self._counter_lock:
            return self._detect_tasks, self._recognition_tasks

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.formula_service.close()
        finally:
            self.engine.close()

    @staticmethod
    def _normalize_result(result: EngineResult) -> dict | None:
        if not result.text or len(result.text_region) != 4:
            return None
        try:
            region = [[int(point[0]), int(point[1])] for point in result.text_region]
        except (IndexError, TypeError, ValueError):
            return None
        return {
            "text": str(result.text),
            "confidence": float(result.confidence),
            "text_region": region,
        }
