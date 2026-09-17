from pathlib import Path
from types import SimpleNamespace

import numpy as np

import pytest

from app.core import dlib_worker


def test_validate_insightface_models_reports_relative_missing_paths(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        dlib_worker.settings.face_detection.insightface,
        "model_path",
        str(tmp_path),
    )

    with pytest.raises(RuntimeError, match="models/buffalo_l/det_10g.onnx"):
        dlib_worker.validate_insightface_models()


def test_validate_insightface_models_accepts_complete_nonempty_set(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        dlib_worker.settings.face_detection.insightface,
        "model_path",
        str(tmp_path),
    )
    model_directory = tmp_path / "models" / "buffalo_l"
    model_directory.mkdir(parents=True)
    for name in dlib_worker._BUFFALO_L_FILES:
        (model_directory / name).write_bytes(b"model")

    assert dlib_worker.validate_insightface_models() == tuple(
        model_directory / name for name in dlib_worker._BUFFALO_L_FILES
    )


def test_collect_worker_status_rejects_cpu_fallback() -> None:
    class Session:
        def get_providers(self):
            return ["CPUExecutionProvider"]

    detector = type(
        "Detector",
        (),
        {"models": {"detection": type("Model", (), {"session": Session()})()}},
    )()

    providers = dlib_worker._collect_model_providers(detector)

    with pytest.raises(RuntimeError, match="禁止回退 CPU"):
        dlib_worker._validate_model_providers("cuda:0", providers)


def test_insightface_detection_filters_low_confidence_and_aligns(monkeypatch) -> None:
    class Detector:
        def get(self, image):
            del image
            return [
                SimpleNamespace(
                    det_score=0.5,
                    bbox=np.array([0, 0, 100, 100]),
                    kps=np.zeros((5, 2), dtype=np.float32),
                ),
                SimpleNamespace(
                    det_score=0.9,
                    bbox=np.array([10, 20, 130, 160]),
                    kps=np.ones((5, 2), dtype=np.float32),
                ),
            ]

    monkeypatch.setattr(dlib_worker, "_detector", Detector())
    monkeypatch.setattr(
        dlib_worker,
        "_norm_crop",
        lambda image, landmark: np.zeros((112, 112, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        dlib_worker.settings.face_detection.insightface,
        "det_thresh",
        0.75,
    )

    results = dlib_worker._detect_insightface(
        np.zeros((200, 200, 3), dtype=np.uint8)
    )

    assert len(results) == 1
    assert results[0][0].shape == (112, 112, 3)
    assert results[0][1] == {"x": 10, "y": 0, "w": 120, "h": 140}
