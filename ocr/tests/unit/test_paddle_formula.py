import hashlib
from pathlib import Path
import sys

import numpy as np
from PIL import Image
import pytest

from app.core.exceptions import ConfigurationError


class FakePipeline:
    def __init__(self, pages):
        self.pages = pages
        self.inputs = []
        self.closed = False

    def predict(self, image):
        self.inputs.append(image)
        return self.pages

    def close(self):
        self.closed = True


def build_settings(tmp_path: Path):
    from app.core.settings import FormulaSettings, OCRSettings

    layout_dir = tmp_path / "PP-DocLayout_plus-L"
    recognition_dir = tmp_path / "PP-FormulaNet_plus-M"
    text_detection_dir = tmp_path / "PP-OCRv6_medium_det"
    text_recognition_dir = tmp_path / "PP-OCRv6_medium_rec"
    for model_dir in (
        layout_dir,
        recognition_dir,
        text_detection_dir,
        text_recognition_dir,
    ):
        model_dir.mkdir()
        for name in ("inference.json", "inference.pdiparams", "inference.yml"):
            (model_dir / name).write_text("model", encoding="utf-8")
    manifest_lines = []
    for model_dir in (
        layout_dir,
        recognition_dir,
        text_detection_dir,
        text_recognition_dir,
    ):
        for name in ("inference.json", "inference.pdiparams", "inference.yml"):
            model_file = model_dir / name
            manifest_lines.append(
                f"{hashlib.sha256(model_file.read_bytes()).hexdigest()}  "
                f"{model_file.relative_to(tmp_path)}"
            )
    (tmp_path / "manifest.sha256").write_text(
        "\n".join(manifest_lines) + "\n",
        encoding="utf-8",
    )
    return (
        FormulaSettings(
            enabled=True,
            layout_model_dir=layout_dir,
            recognition_model_dir=recognition_dir,
            recognition_batch_size=2,
            layout_threshold=0.45,
        ),
        OCRSettings(
            device="cpu",
            detection_model_dir=text_detection_dir,
            recognition_model_dir=text_recognition_dir,
            cpu_threads=3,
            enable_mkldnn=False,
            enable_hpi=False,
        ),
    )


def test_formula_engine_rejects_missing_formula_manifest_entry(tmp_path):
    from app.engines.paddle_formula import PaddleFormulaEngine

    formula_settings, ocr_settings = build_settings(tmp_path)
    manifest = tmp_path / "manifest.sha256"
    manifest.write_text(
        "\n".join(
            line
            for line in manifest.read_text(encoding="utf-8").splitlines()
            if "PP-FormulaNet_plus-M/inference.yml" not in line
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="清单缺少.*inference.yml"):
        PaddleFormulaEngine(
            formula_settings,
            ocr_settings,
            pipeline_factory=lambda **kwargs: object(),
        )


def test_formula_engine_requires_all_model_directories_under_ocr_models_root(
    tmp_path,
):
    from app.engines.paddle_formula import PaddleFormulaEngine

    formula_settings, ocr_settings = build_settings(tmp_path)
    outside_layout = tmp_path.parent / f"{tmp_path.name}-outside-layout"
    outside_layout.mkdir()
    for name in ("inference.json", "inference.pdiparams", "inference.yml"):
        (outside_layout / name).write_text("model", encoding="utf-8")
    formula_settings = formula_settings.model_copy(
        update={"layout_model_dir": outside_layout}
    )

    with pytest.raises(ConfigurationError, match="同一 models 根"):
        PaddleFormulaEngine(
            formula_settings,
            ocr_settings,
            pipeline_factory=lambda **kwargs: object(),
        )


def test_formula_engine_wraps_pipeline_dependency_import_failure(
    tmp_path,
    monkeypatch,
):
    from app.engines.paddle_formula import PaddleFormulaEngine

    formula_settings, ocr_settings = build_settings(tmp_path)
    monkeypatch.setitem(sys.modules, "paddleocr", None)

    with pytest.raises(
        ConfigurationError,
        match="公式.*FormulaRecognitionPipeline.*cpu",
    ) as error:
        PaddleFormulaEngine(formula_settings, ocr_settings)

    assert isinstance(error.value.__cause__, ModuleNotFoundError)


def test_formula_engine_uses_local_official_pipeline_configuration(tmp_path):
    from app.engines.paddle_formula import PaddleFormulaEngine

    formula_settings, ocr_settings = build_settings(tmp_path)
    captured = {}
    pipeline = FakePipeline([])

    def factory(**kwargs):
        captured.update(kwargs)
        return pipeline

    engine = PaddleFormulaEngine(
        formula_settings,
        ocr_settings,
        pipeline_factory=factory,
    )

    assert captured == {
        "layout_detection_model_dir": str(formula_settings.layout_model_dir),
        "formula_recognition_model_dir": str(
            formula_settings.recognition_model_dir
        ),
        "formula_recognition_batch_size": 2,
        "layout_threshold": 0.45,
        "use_layout_detection": True,
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "device": "cpu",
        "enable_hpi": False,
        "enable_mkldnn": False,
        "cpu_threads": 3,
    }


def test_formula_engine_converts_cuda_device_for_paddle_pipeline(tmp_path):
    from app.engines.paddle_formula import PaddleFormulaEngine

    formula_settings, ocr_settings = build_settings(tmp_path)
    ocr_settings = ocr_settings.model_copy(update={"device": "cuda:2"})
    captured = {}

    PaddleFormulaEngine(
        formula_settings,
        ocr_settings,
        pipeline_factory=lambda **kwargs: captured.update(kwargs) or FakePipeline([]),
    )

    assert captured["device"] == "gpu:2"


def test_formula_engine_converts_latex_region_and_detection_score(tmp_path):
    from app.engines.paddle_formula import PaddleFormulaEngine

    formula_settings, ocr_settings = build_settings(tmp_path)
    page = {
        "formula_res_list": [
            {
                "rec_formula": r"\frac{a}{b}",
                "dt_polys": np.array([10.2, 20.8, 100.9, 50.1]),
            }
        ],
        "layout_det_res": {
            "boxes": [
                {
                    "label": "formula",
                    "coordinate": np.array([10.2, 20.8, 100.9, 50.1]),
                    "score": 0.963,
                }
            ]
        },
    }
    pipeline = FakePipeline([page])
    engine = PaddleFormulaEngine(
        formula_settings,
        ocr_settings,
        pipeline_factory=lambda **kwargs: pipeline,
    )

    results = engine.predict(Image.new("RGB", (200, 100), "white"))

    assert len(pipeline.inputs) == 1
    assert isinstance(pipeline.inputs[0], np.ndarray)
    assert len(results) == 1
    assert results[0].latex == r"\frac{a}{b}"
    assert results[0].formula_region == [
        [10, 20],
        [100, 20],
        [100, 50],
        [10, 50],
    ]
    assert results[0].detection_confidence == 0.963


def test_formula_engine_filters_incomplete_results(tmp_path):
    from app.engines.paddle_formula import PaddleFormulaEngine

    formula_settings, ocr_settings = build_settings(tmp_path)
    page = {
        "formula_res_list": [
            {"rec_formula": "", "dt_polys": [0, 0, 10, 10]},
            {"rec_formula": "x", "dt_polys": [0, 0, 10]},
            {"rec_formula": "y", "dt_polys": [0, 0, 10, 10]},
        ],
        "layout_det_res": {
            "boxes": [
                {
                    "label": "formula",
                    "coordinate": [0, 0, 10, 10],
                    "score": "invalid",
                }
            ]
        },
    }
    engine = PaddleFormulaEngine(
        formula_settings,
        ocr_settings,
        pipeline_factory=lambda **kwargs: FakePipeline([page]),
    )

    assert engine.predict(Image.new("RGB", (20, 20), "white")) == []


def test_formula_engine_closes_pipeline(tmp_path):
    from app.engines.paddle_formula import PaddleFormulaEngine

    formula_settings, ocr_settings = build_settings(tmp_path)
    pipeline = FakePipeline([])
    engine = PaddleFormulaEngine(
        formula_settings,
        ocr_settings,
        pipeline_factory=lambda **kwargs: pipeline,
    )

    engine.close()

    assert pipeline.closed is True
