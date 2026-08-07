from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from app.core.exceptions import ImageDecodeError, InferenceError, RequestFormatError
from app.engines.base import EngineResult
from app.schemas.ocr import OCRRequest
from app.services.formula_service import FormulaService
from app.services.ocr_service import OCRService


class RecordingEngine:
    def __init__(self, results, fail_on_call=None):
        self.results = results
        self.fail_on_call = fail_on_call
        self.calls = 0
        self.close_calls = 0

    def predict(self, image):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("推理失败")
        return self.results

    def close(self):
        self.close_calls += 1


class RecordingFormulaService:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.close_calls = 0

    def predict(self, enabled, image_ids, images):
        self.calls.append((enabled, image_ids, images))
        return []

    def close(self):
        self.closed = True
        self.close_calls += 1


class FailingCloseFormulaService(RecordingFormulaService):
    def close(self):
        super().close()
        raise RuntimeError("formula close failed")


class ObservableLock:
    def __init__(self):
        self._lock = threading.Lock()
        self._attempt_lock = threading.Lock()
        self._attempts = 0
        self.second_attempted = threading.Event()

    def __enter__(self):
        with self._attempt_lock:
            self._attempts += 1
            if self._attempts == 2:
                self.second_attempted.set()
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._lock.release()


class CoordinatedEngine:
    def __init__(self):
        self._calls_lock = threading.Lock()
        self.calls = 0
        self.first_entered = threading.Event()
        self.second_entered = threading.Event()
        self.release_first = threading.Event()

    def predict(self, image):
        with self._calls_lock:
            self.calls += 1
            call_number = self.calls
        if call_number == 1:
            self.first_entered.set()
            if not self.release_first.wait(timeout=2):
                raise TimeoutError("first predictor call was not released")
        else:
            self.second_entered.set()
        return []

    def close(self):
        return None


def test_service_serializes_legacy_value(settings_file, image_base64):
    engine = RecordingEngine(
        [
            EngineResult(
                text="测试",
                confidence=0.875,
                text_region=[[1, 2], [3, 4], [5, 6], [7, 8]],
            )
        ]
    )
    service = OCRService(engine=engine, image_max_bytes=1024)

    response = service.predict(
        OCRRequest(key=["image"], value=[image_base64], enable_formula=True)
    )

    assert engine.calls == 1
    assert response.err_no == 0
    assert response.key == ["image"]
    assert response.model_dump()["formula_results"] == [
        {
            "image_id": "image",
            "status": "disabled",
            "message": "服务端未启用公式识别功能",
            "formulas": [],
        }
    ]
    assert json.loads(response.value[0]) == [
        {
            "text": "测试",
            "confidence": 0.875,
            "text_region": [[1, 2], [3, 4], [5, 6], [7, 8]],
        }
    ]


def test_service_skips_incomplete_results(image_base64):
    engine = RecordingEngine(
        [
            EngineResult(text="", confidence=0.9, text_region=[[0, 0]] * 4),
            EngineResult(text="有效", confidence=0.8, text_region=[[0, 0]] * 4),
        ]
    )
    service = OCRService(engine=engine, image_max_bytes=1024)

    response = service.predict(OCRRequest(key=["image"], value=[image_base64]))

    assert [item["text"] for item in json.loads(response.value[0])] == ["有效"]


def test_service_processes_multiple_image_ids_in_order(image_base64):
    engine = RecordingEngine(
        [
            EngineResult(
                text="批量",
                confidence=0.9,
                text_region=[[0, 0], [1, 0], [1, 1], [0, 1]],
            )
        ]
    )
    service = OCRService(engine=engine, image_max_bytes=1024)

    response = service.predict(
        OCRRequest(
            key=["图片-001", "invoice-20260723"],
            value=[
                image_base64,
                f"data:image/png;base64,{image_base64}",
            ],
        )
    )

    assert engine.calls == 2
    assert response.key == ["图片-001", "invoice-20260723"]
    assert len(response.value) == 2
    assert [json.loads(value)[0]["text"] for value in response.value] == [
        "批量",
        "批量",
    ]
    assert service.counters() == (2, 2)


@pytest.mark.parametrize(
    ("keys", "values"),
    [
        ([], []),
        (["图片-001"], []),
        (["图片-001"], ["a", "b"]),
        ([""], ["a"]),
        (["   "], ["a"]),
    ],
)
def test_service_rejects_invalid_batch_mapping_without_inference(keys, values):
    engine = RecordingEngine([])
    service = OCRService(engine=engine, image_max_bytes=1024)

    with pytest.raises(RequestFormatError, match="key.*value|图片 ID"):
        service.predict(OCRRequest(key=keys, value=values))

    assert engine.calls == 0


def test_service_decodes_entire_batch_before_inference(image_base64):
    engine = RecordingEngine([])
    service = OCRService(engine=engine, image_max_bytes=1024)

    with pytest.raises(ImageDecodeError):
        service.predict(
            OCRRequest(
                key=["图片-001", "图片-002"],
                value=[image_base64, "not-base64"],
            )
        )

    assert engine.calls == 0


def test_service_does_not_return_partial_results_when_batch_inference_fails(
    image_base64,
):
    engine = RecordingEngine([], fail_on_call=2)
    service = OCRService(engine=engine, image_max_bytes=1024)

    with pytest.raises(InferenceError):
        service.predict(
            OCRRequest(
                key=["图片-001", "图片-002"],
                value=[image_base64, image_base64],
            )
        )

    assert engine.calls == 2
    assert service.counters() == (0, 0)


def test_service_passes_decoded_batch_to_formula_service(image_base64):
    engine = RecordingEngine([])
    formula_service = RecordingFormulaService()
    service = OCRService(
        engine=engine,
        image_max_bytes=1024,
        formula_service=formula_service,
    )

    service.predict(
        OCRRequest(
            key=["图片-001", "图片-002"],
            value=[image_base64, image_base64],
            enable_formula=True,
        )
    )

    assert len(formula_service.calls) == 1
    enabled, image_ids, decoded_images = formula_service.calls[0]
    assert enabled is True
    assert image_ids == ["图片-001", "图片-002"]
    assert len(decoded_images) == 2


def test_service_closes_text_and_formula_services(image_base64):
    engine = RecordingEngine([])
    formula_service = RecordingFormulaService()
    service = OCRService(
        engine=engine,
        image_max_bytes=1024,
        formula_service=formula_service,
    )

    service.close()

    assert formula_service.closed is True


def test_service_close_is_idempotent():
    engine = RecordingEngine([])
    formula_service = RecordingFormulaService()
    service = OCRService(
        engine=engine,
        image_max_bytes=1024,
        formula_service=formula_service,
    )

    service.close()
    service.close()

    assert formula_service.close_calls == 1
    assert engine.close_calls == 1


def test_service_close_remains_idempotent_when_formula_close_fails():
    engine = RecordingEngine([])
    formula_service = FailingCloseFormulaService()
    service = OCRService(
        engine=engine,
        image_max_bytes=1024,
        formula_service=formula_service,
    )

    with pytest.raises(RuntimeError, match="formula close failed"):
        service.close()
    service.close()

    assert formula_service.close_calls == 1
    assert engine.close_calls == 1


def test_service_serializes_each_predictor_but_allows_pipeline_overlap(
    image_base64,
):
    ocr_engine = CoordinatedEngine()
    formula_engine = CoordinatedEngine()
    formula_service = FormulaService(
        configured_enabled=True,
        engine=formula_engine,
    )
    service = OCRService(
        engine=ocr_engine,
        image_max_bytes=1024,
        max_concurrency=2,
        formula_service=formula_service,
    )
    ocr_lock = ObservableLock()
    formula_lock = ObservableLock()
    service._engine_lock = ocr_lock
    formula_service._engine_lock = formula_lock
    request = OCRRequest(
        key=["image"],
        value=[image_base64],
        enable_formula=True,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.predict, request)
        assert ocr_engine.first_entered.wait(timeout=1)
        second = executor.submit(service.predict, request)
        try:
            assert ocr_lock.second_attempted.wait(timeout=1)
            assert not ocr_engine.second_entered.is_set()

            ocr_engine.release_first.set()
            assert formula_engine.first_entered.wait(timeout=1)
            assert ocr_engine.second_entered.wait(timeout=1)
            assert formula_lock.second_attempted.wait(timeout=1)
            assert not formula_engine.second_entered.is_set()
        finally:
            ocr_engine.release_first.set()
            formula_engine.release_first.set()

        assert first.result(timeout=1).err_no == 0
        assert second.result(timeout=1).err_no == 0
