from __future__ import annotations

from collections.abc import Callable, Sequence
import logging
import math
from typing import Any

import numpy as np
from PIL import Image

from app.core.exceptions import ConfigurationError
from app.core.model_verification import (
    ModelVerificationError,
    verify_configured_models,
)
from app.core.settings import FormulaSettings, OCRSettings, to_paddle_device
from app.engines.base import FormulaEngineResult


LOGGER = logging.getLogger(__name__)


class PaddleFormulaEngine:
    def __init__(
        self,
        formula_settings: FormulaSettings,
        ocr_settings: OCRSettings,
        pipeline_factory: Callable[..., Any] | None = None,
    ):
        try:
            verify_configured_models(
                [
                    formula_settings.layout_model_dir,
                    formula_settings.recognition_model_dir,
                ],
                configured_model_dirs=[
                    ocr_settings.detection_model_dir,
                    ocr_settings.recognition_model_dir,
                    formula_settings.layout_model_dir,
                    formula_settings.recognition_model_dir,
                ],
            )
        except ModelVerificationError as error:
            raise ConfigurationError(str(error)) from error

        if pipeline_factory is None:
            try:
                from paddleocr import FormulaRecognitionPipeline
            except Exception as error:
                raise ConfigurationError(
                    "公式引擎依赖 FormulaRecognitionPipeline 导入失败，"
                    f"目标设备为 {ocr_settings.device}"
                ) from error

            pipeline_factory = FormulaRecognitionPipeline

        try:
            self._pipeline = pipeline_factory(
                layout_detection_model_dir=str(formula_settings.layout_model_dir),
                formula_recognition_model_dir=str(
                    formula_settings.recognition_model_dir
                ),
                formula_recognition_batch_size=(
                    formula_settings.recognition_batch_size
                ),
                layout_threshold=formula_settings.layout_threshold,
                use_layout_detection=True,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                device=to_paddle_device(ocr_settings.device),
                enable_hpi=ocr_settings.enable_hpi,
                enable_mkldnn=ocr_settings.enable_mkldnn,
                cpu_threads=ocr_settings.cpu_threads,
            )
        except Exception as error:
            raise ConfigurationError(
                f"公式引擎初始化失败，目标设备为 {ocr_settings.device}"
            ) from error

    def predict(self, image: Image.Image | np.ndarray) -> list[FormulaEngineResult]:
        if self._pipeline is None:
            raise RuntimeError("公式引擎已关闭")
        if isinstance(image, Image.Image):
            image_data = np.asarray(image.convert("RGB"))
        else:
            image_data = np.asarray(image)

        results: list[FormulaEngineResult] = []
        for page in self._pipeline.predict(image_data):
            results.extend(self._convert_page(page))
        return results

    def close(self) -> None:
        pipeline = self._pipeline
        self._pipeline = None
        close = getattr(pipeline, "close", None)
        if callable(close):
            close()

    @classmethod
    def _convert_page(cls, page: Any) -> list[FormulaEngineResult]:
        try:
            formula_items = list(page.get("formula_res_list") or [])
            layout_result = page.get("layout_det_res") or {}
            layout_boxes = list(layout_result.get("boxes") or [])
        except (AttributeError, TypeError):
            LOGGER.warning("跳过无法解析的公式结果页")
            return []

        scores = cls._formula_scores(layout_boxes)
        converted = []
        for item in formula_items:
            result = cls._convert_item(item, scores)
            if result is not None:
                converted.append(result)
        return converted

    @classmethod
    def _formula_scores(cls, boxes: list[Any]) -> dict[tuple[float, ...], float]:
        scores = {}
        for box in boxes:
            try:
                if str(box.get("label", "")).lower() != "formula":
                    continue
                key = cls._coordinate_key(box["coordinate"])
                score = float(box["score"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if key is not None and math.isfinite(score) and 0.0 <= score <= 1.0:
                scores[key] = score
        return scores

    @classmethod
    def _convert_item(
        cls,
        item: Any,
        scores: dict[tuple[float, ...], float],
    ) -> FormulaEngineResult | None:
        try:
            latex = item.get("rec_formula")
            coordinates = item.get("dt_polys")
        except AttributeError:
            return None
        if not isinstance(latex, str) or not latex.strip():
            return None
        key = cls._coordinate_key(coordinates)
        if key is None or key not in scores:
            return None
        left, top, right, bottom = [int(value) for value in key]
        return FormulaEngineResult(
            latex=latex,
            formula_region=[
                [left, top],
                [right, top],
                [right, bottom],
                [left, bottom],
            ],
            detection_confidence=scores[key],
        )

    @staticmethod
    def _coordinate_key(value: Sequence[Any] | None) -> tuple[float, ...] | None:
        try:
            coordinates = tuple(float(item) for item in value)
        except (TypeError, ValueError, OverflowError):
            return None
        if len(coordinates) != 4 or not all(map(math.isfinite, coordinates)):
            return None
        return coordinates
