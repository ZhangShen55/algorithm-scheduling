import hashlib
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from app.core.exceptions import ConfigurationError
from app.core.settings import load_settings
from app.engines.paddleocr_v6 import PaddleOCRV6Engine


class FakeDeviceAPI:
    def __init__(
        self,
        *,
        cuda_compiled: bool = False,
        cuda_count: int = 0,
        npu_compiled: bool = False,
        custom_devices: list[str] | None = None,
    ):
        self._cuda_compiled = cuda_compiled
        self._npu_compiled = npu_compiled
        self._custom_devices = custom_devices or []
        self.cuda = SimpleNamespace(device_count=lambda: cuda_count)

    def is_compiled_with_cuda(self) -> bool:
        return self._cuda_compiled

    def is_compiled_with_custom_device(self, device_type: str) -> bool:
        return device_type == "npu" and self._npu_compiled

    def get_available_custom_device(self) -> list[str]:
        return self._custom_devices


def fake_paddle(**device_options):
    return SimpleNamespace(device=FakeDeviceAPI(**device_options))


def load_engine_settings(settings_file):
    settings = load_settings(settings_file).ocr
    for model_dir in (
        settings.detection_model_dir,
        settings.recognition_model_dir,
    ):
        for file_name in ("inference.json", "inference.pdiparams", "inference.yml"):
            (model_dir / file_name).write_bytes(b"test")
    models_root = settings.detection_model_dir.parent
    manifest_lines = []
    for model_dir in (
        settings.detection_model_dir,
        settings.recognition_model_dir,
    ):
        for file_name in ("inference.json", "inference.pdiparams", "inference.yml"):
            model_file = model_dir / file_name
            manifest_lines.append(
                f"{hashlib.sha256(model_file.read_bytes()).hexdigest()}  "
                f"{model_file.relative_to(models_root)}"
            )
    (models_root / "manifest.sha256").write_text(
        "\n".join(manifest_lines) + "\n",
        encoding="utf-8",
    )
    return settings


def test_engine_rejects_model_digest_mismatch(settings_file):
    settings = load_engine_settings(settings_file)
    (settings.detection_model_dir / "inference.json").write_bytes(b"tampered")

    with pytest.raises(ConfigurationError, match="摘要不一致.*inference.json"):
        PaddleOCRV6Engine(
            settings,
            pipeline_factory=lambda **kwargs: object(),
            paddle_module=fake_paddle(),
        )


def test_engine_rejects_model_directories_from_different_roots(settings_file):
    settings = load_engine_settings(settings_file)
    outside_recognition = settings.recognition_model_dir.parent.parent / "outside-rec"
    outside_recognition.mkdir()
    for file_name in ("inference.json", "inference.pdiparams", "inference.yml"):
        (outside_recognition / file_name).write_bytes(b"test")
    settings = settings.model_copy(
        update={"recognition_model_dir": outside_recognition}
    )

    with pytest.raises(ConfigurationError, match="同一 models 根"):
        PaddleOCRV6Engine(
            settings,
            pipeline_factory=lambda **kwargs: object(),
            paddle_module=fake_paddle(),
        )


def test_engine_wraps_paddle_dependency_import_failure(
    settings_file,
    monkeypatch,
):
    settings = load_engine_settings(settings_file)
    monkeypatch.setitem(sys.modules, "paddle", None)

    with pytest.raises(
        ConfigurationError,
        match="OCR.*paddle.*cpu",
    ) as error:
        PaddleOCRV6Engine(
            settings,
            pipeline_factory=lambda **kwargs: object(),
        )

    assert isinstance(error.value.__cause__, ModuleNotFoundError)


def test_engine_wraps_paddleocr_dependency_import_failure(
    settings_file,
    monkeypatch,
):
    settings = load_engine_settings(settings_file)
    monkeypatch.setitem(sys.modules, "paddleocr", None)

    with pytest.raises(
        ConfigurationError,
        match="OCR.*PaddleOCR.*cpu",
    ) as error:
        PaddleOCRV6Engine(
            settings,
            paddle_module=fake_paddle(),
        )

    assert isinstance(error.value.__cause__, ModuleNotFoundError)


def test_engine_passes_local_models_and_inference_settings(settings_file):
    settings = load_engine_settings(settings_file)
    calls = []

    class Pipeline:
        def predict(self, image):
            return []

    def factory(**kwargs):
        calls.append(kwargs)
        return Pipeline()

    PaddleOCRV6Engine(
        settings,
        pipeline_factory=factory,
        paddle_module=fake_paddle(),
    )

    assert calls == [
        {
            "text_detection_model_dir": str(settings.detection_model_dir),
            "text_recognition_model_dir": str(settings.recognition_model_dir),
            "text_recognition_batch_size": 1,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "text_det_limit_side_len": 960,
            "text_det_thresh": 0.3,
            "text_det_box_thresh": 0.6,
            "text_det_unclip_ratio": 1.5,
            "device": "cpu",
            "enable_hpi": False,
            "enable_mkldnn": True,
            "cpu_threads": 2,
        }
    ]


def test_engine_converts_paddleocr_result(settings_file):
    settings = load_engine_settings(settings_file)

    class Pipeline:
        def predict(self, image):
            assert isinstance(image, np.ndarray)
            return [
                {
                    "rec_texts": ["第一行", "第二行"],
                    "rec_scores": np.array([0.98, 0.875]),
                    "rec_polys": np.array(
                        [
                            [[1, 2], [101, 2], [101, 32], [1, 32]],
                            [[5, 40], [80, 40], [80, 70], [5, 70]],
                        ]
                    ),
                }
            ]

    engine = PaddleOCRV6Engine(
        settings,
        pipeline_factory=lambda **kwargs: Pipeline(),
        paddle_module=fake_paddle(),
    )

    results = engine.predict(np.zeros((10, 10, 3), dtype=np.uint8))

    assert [(item.text, item.confidence, item.text_region) for item in results] == [
        ("第一行", 0.98, [[1, 2], [101, 2], [101, 32], [1, 32]]),
        ("第二行", 0.875, [[5, 40], [80, 40], [80, 70], [5, 70]]),
    ]


def test_engine_filters_incomplete_items_and_uses_box_fallback(settings_file):
    settings = load_engine_settings(settings_file)

    class Pipeline:
        def predict(self, image):
            return [
                {
                    "rec_texts": ["有效", "", "缺坐标", "多余文本"],
                    "rec_scores": [0.9, 0.8, 0.7],
                    "rec_polys": [None, None, None],
                    "rec_boxes": [
                        [1, 2, 11, 12],
                        [2, 3, 12, 13],
                        None,
                    ],
                }
            ]

    engine = PaddleOCRV6Engine(
        settings,
        pipeline_factory=lambda **kwargs: Pipeline(),
        paddle_module=fake_paddle(),
    )

    results = engine.predict(np.zeros((10, 10, 3), dtype=np.uint8))

    assert len(results) == 1
    assert results[0].text == "有效"
    assert results[0].text_region == [[1, 2], [11, 2], [11, 12], [1, 12]]


@pytest.mark.parametrize(
    ("device", "paddle_module", "message"),
    [
        ("cuda:0", fake_paddle(), "GPU 设备 cuda:0 不可用"),
        (
            "cuda:2",
            fake_paddle(cuda_compiled=True, cuda_count=2),
            "GPU 设备 cuda:2 不可用",
        ),
        ("npu:0", fake_paddle(), "NPU 设备 npu:0 不可用"),
        (
            "npu:1",
            fake_paddle(npu_compiled=True, custom_devices=["npu:0"]),
            "NPU 设备 npu:1 不可用",
        ),
    ],
)
def test_engine_rejects_unavailable_accelerator(
    settings_file, device, paddle_module, message
):
    settings = load_engine_settings(settings_file).model_copy(update={"device": device})

    with pytest.raises(ConfigurationError, match=message):
        PaddleOCRV6Engine(
            settings,
            pipeline_factory=lambda **kwargs: object(),
            paddle_module=paddle_module,
        )


@pytest.mark.parametrize(
    ("device", "paddle_module", "paddle_device"),
    [
        ("cuda:1", fake_paddle(cuda_compiled=True, cuda_count=2), "gpu:1"),
        (
            "npu:1",
            fake_paddle(npu_compiled=True, custom_devices=["npu:0", "npu:1"]),
            "npu:1",
        ),
    ],
)
def test_engine_accepts_available_accelerator(
    settings_file, device, paddle_module, paddle_device
):
    settings = load_engine_settings(settings_file).model_copy(update={"device": device})
    captured = {}

    engine = PaddleOCRV6Engine(
        settings,
        pipeline_factory=lambda **kwargs: captured.update(kwargs) or object(),
        paddle_module=paddle_module,
    )

    assert engine.device == device
    assert captured["device"] == paddle_device
