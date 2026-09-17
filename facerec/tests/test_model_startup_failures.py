from pathlib import Path

import pytest

from app.core import ai_engine, dlib_worker


def test_arcface_rejects_nonempty_but_invalid_model(monkeypatch, tmp_path: Path) -> None:
    model_path = tmp_path / "arcface.onnx"
    model_path.write_bytes(b"not-an-onnx-model")
    monkeypatch.setattr(ai_engine, "_ARCFACE_MODEL_PATH", model_path)
    monkeypatch.setattr(ai_engine, "embedding_model", None)
    monkeypatch.setattr(ai_engine, "_embedding_model_error", None)

    class InvalidArcFace:
        initialized = False

    monkeypatch.setattr(
        ai_engine.fd.vision.faceid,
        "ArcFace",
        lambda *_args, **_kwargs: InvalidArcFace(),
    )

    with pytest.raises(RuntimeError, match="ArcFace 模型初始化失败"):
        ai_engine.load_embedding_model()

    status = ai_engine.embedding_status()
    assert status["ready"] is False
    assert status["error"] == "ArcFace 模型初始化失败"


def test_insightface_rejects_nonempty_but_unloadable_models(
    monkeypatch, tmp_path: Path
) -> None:
    import insightface

    model_root = tmp_path / "ai_models"
    model_directory = model_root / "models" / "buffalo_l"
    model_directory.mkdir(parents=True)
    for name in dlib_worker._BUFFALO_L_FILES:
        (model_directory / name).write_bytes(b"not-an-onnx-model")
    monkeypatch.setattr(
        dlib_worker.settings.face_detection.insightface,
        "model_path",
        str(model_root),
    )
    monkeypatch.setattr(dlib_worker.settings.gpu, "device", "cpu")

    def fail_to_load(*_args, **_kwargs):
        raise RuntimeError("InsightFace 模型无法解析")

    monkeypatch.setattr(insightface.app, "FaceAnalysis", fail_to_load)

    with pytest.raises(RuntimeError, match="InsightFace 模型无法解析"):
        dlib_worker._init_insightface()
