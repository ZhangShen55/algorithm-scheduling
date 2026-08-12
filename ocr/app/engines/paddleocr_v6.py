from __future__ import annotations

import logging
import math
import os
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import numpy as np
from PIL import Image

from app.core.exceptions import ConfigurationError
from app.core.model_verification import (
    ModelVerificationError,
    verify_configured_models,
)
from app.core.settings import OCRSettings, parse_device, to_paddle_device
from app.engines.base import EngineResult

LOGGER = logging.getLogger(__name__)


class PaddleOCRV6Engine:
    def __init__(
        self,
        settings: OCRSettings,
        pipeline_factory: Callable[..., Any] | None = None,
        paddle_module: Any | None = None,
    ):
        self.device = settings.device
        try:
            verify_configured_models(
                [
                    settings.detection_model_dir,
                    settings.recognition_model_dir,
                ]
            )
        except ModelVerificationError as error:
            raise ConfigurationError(str(error)) from error

        if paddle_module is None:
            try:
                import paddle as paddle_module
            except Exception as error:
                raise ConfigurationError(
                    "OCR 引擎依赖 paddle 导入失败，"
                    f"目标设备为 {settings.device}"
                ) from error

        self._validate_device(paddle_module)

        if pipeline_factory is None:
            try:
                from paddleocr import PaddleOCR
            except Exception as error:
                raise ConfigurationError(
                    "OCR 引擎依赖 PaddleOCR 导入失败，"
                    f"目标设备为 {settings.device}"
                ) from error

            pipeline_factory = PaddleOCR

        detection = settings.detection
        try:
            self._pipeline = pipeline_factory(
                text_detection_model_dir=str(settings.detection_model_dir),
                text_recognition_model_dir=str(settings.recognition_model_dir),
                text_recognition_batch_size=settings.recognition_batch_size,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                text_det_limit_side_len=detection.limit_side_len,
                text_det_thresh=detection.threshold,
                text_det_box_thresh=detection.box_threshold,
                text_det_unclip_ratio=detection.unclip_ratio,
                device=to_paddle_device(settings.device),
                enable_hpi=settings.enable_hpi,
                enable_mkldnn=settings.enable_mkldnn,
                cpu_threads=settings.cpu_threads,
            )
        except Exception as error:
            raise ConfigurationError(
                f"OCR 引擎初始化失败，目标设备为 {settings.device}"
            ) from error

    def predict(self, image: Image.Image | np.ndarray) -> list[EngineResult]:
        if self._pipeline is None:
            raise RuntimeError("OCR 引擎已关闭")
        if isinstance(image, Image.Image):
            image_data = np.asarray(image.convert("RGB"))
        else:
            image_data = np.asarray(image)

        results: list[EngineResult] = []
        for page in self._pipeline.predict(image_data):
            results.extend(self._convert_page(page))
        return results

    def close(self) -> None:
        pipeline = self._pipeline
        self._pipeline = None
        close = getattr(pipeline, "close", None)
        if callable(close):
            close()

    def _validate_device(self, paddle_module: Any) -> None:
        kind, index = parse_device(self.device)
        require_gpu = os.getenv("REQUIRE_GPU", "false").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        if require_gpu and kind != "cuda":
            raise ConfigurationError(
                "部署要求使用 GPU，但 OCR 配置不是 cuda:<index>"
            )
        if kind == "cpu":
            return

        if kind == "cuda":
            try:
                available = (
                    paddle_module.device.is_compiled_with_cuda()
                    and paddle_module.device.cuda.device_count() > index
                )
            except Exception:
                available = False
            if not available:
                raise ConfigurationError(f"GPU 设备 {self.device} 不可用")
            return

        try:
            custom_devices = paddle_module.device.get_available_custom_device() or []
            available = (
                paddle_module.device.is_compiled_with_custom_device("npu")
                and self.device in custom_devices
            )
        except Exception:
            available = False
        if not available:
            raise ConfigurationError(f"NPU 设备 {self.device} 不可用")

    @classmethod
    def _convert_page(cls, page: Any) -> list[EngineResult]:
        try:
            texts = cls._as_list(page.get("rec_texts"))
            scores = cls._as_list(page.get("rec_scores"))
            polygons = cls._as_list(page.get("rec_polys"))
            boxes = cls._as_list(page.get("rec_boxes"))
        except (AttributeError, TypeError):
            LOGGER.warning("跳过无法解析的 PaddleOCR 结果页")
            return []

        if len({len(texts), len(scores), max(len(polygons), len(boxes))}) > 1:
            LOGGER.warning(
                "PaddleOCR 结果数量不一致：texts=%d scores=%d regions=%d",
                len(texts),
                len(scores),
                max(len(polygons), len(boxes)),
            )

        converted: list[EngineResult] = []
        for index, text in enumerate(texts):
            score = scores[index] if index < len(scores) else None
            polygon = polygons[index] if index < len(polygons) else None
            box = boxes[index] if index < len(boxes) else None
            item = cls._convert_item(text, score, polygon, box)
            if item is None:
                LOGGER.debug(
                    "跳过结构不完整的 OCR 结果项，索引为 %d",
                    index,
                )
                continue
            converted.append(item)
        return converted

    @staticmethod
    def _convert_item(
        text: Any,
        score: Any,
        polygon: Any,
        box: Any,
    ) -> EngineResult | None:
        if not isinstance(text, str) or not text:
            return None
        try:
            confidence = float(score)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            return None

        region = PaddleOCRV6Engine._polygon_region(polygon)
        if region is None:
            region = PaddleOCRV6Engine._box_region(box)
        if region is None:
            return None
        return EngineResult(
            text=text,
            confidence=confidence,
            text_region=region,
        )

    @staticmethod
    def _polygon_region(value: Any) -> list[list[int]] | None:
        try:
            points = list(value)
            if len(points) != 4:
                return None
            return [[int(point[0]), int(point[1])] for point in points]
        except (IndexError, TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _box_region(value: Any) -> list[list[int]] | None:
        try:
            left, top, right, bottom = [int(item) for item in value]
        except (TypeError, ValueError, OverflowError):
            return None
        return [
            [left, top],
            [right, top],
            [right, bottom],
            [left, bottom],
        ]

    @staticmethod
    def _as_list(value: Sequence[Any] | Iterable[Any] | None) -> list[Any]:
        if value is None:
            return []
        return list(value)
