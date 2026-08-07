import logging
import threading

from PIL import Image

from app.core.exceptions import ConfigurationError
from app.engines.base import FormulaEngine


LOGGER = logging.getLogger(__name__)


class FormulaService:
    def __init__(
        self,
        configured_enabled: bool = False,
        engine: FormulaEngine | None = None,
    ):
        if configured_enabled and engine is None:
            raise ConfigurationError("公式识别已启用但公式引擎未初始化")
        self.configured_enabled = configured_enabled
        self.engine = engine
        self._engine_lock = threading.Lock()

    def predict(
        self,
        request_enabled: bool,
        image_ids: list[str],
        images: list[Image.Image],
    ) -> list[dict]:
        if not request_enabled:
            return []
        if not self.configured_enabled:
            return [
                self._group(
                    image_id,
                    status="disabled",
                    message="服务端未启用公式识别功能",
                )
                for image_id in image_ids
            ]

        results = []
        for image_id, image in zip(image_ids, images):
            try:
                with self._engine_lock:
                    engine_results = self.engine.predict(image)
                formulas = [
                    {
                        "latex": item.latex,
                        "formula_region": item.formula_region,
                        "detection_confidence": item.detection_confidence,
                    }
                    for item in engine_results
                ]
            except Exception:
                LOGGER.exception("图片 %s 公式识别异常", image_id)
                results.append(
                    self._group(
                        image_id,
                        status="error",
                        message="公式识别失败",
                    )
                )
                continue
            results.append(self._group(image_id, formulas=formulas))
        return results

    def close(self) -> None:
        engine = self.engine
        self.engine = None
        if engine is not None:
            engine.close()

    @staticmethod
    def _group(
        image_id: str,
        status: str = "success",
        message: str = "",
        formulas: list[dict] | None = None,
    ) -> dict:
        return {
            "image_id": image_id,
            "status": status,
            "message": message,
            "formulas": formulas or [],
        }
