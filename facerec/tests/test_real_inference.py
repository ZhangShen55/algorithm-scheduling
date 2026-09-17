import asyncio
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.core import ai_engine
from app.main import app, lifespan
from app.models.api_response import StatusCode
from app.models.request.face_interface_req import PersonRecognizeRequest
from app.models.request.person_interface_req import PersonFeatureRequest
from app.router import faces, persons


def _require_real_inference() -> None:
    if os.getenv("FACEREC_RUN_REAL_INFERENCE") != "1":
        pytest.skip("需要显式设置 FACEREC_RUN_REAL_INFERENCE=1")


def _two_face_fixture(image: np.ndarray, bbox: dict[str, int]) -> np.ndarray:
    height, width = image.shape[:2]
    face_center_x = bbox["x"] + bbox["w"] / 2
    face_center_y = bbox["y"] + bbox["h"] * 0.9
    half_size = max(bbox["w"], bbox["h"])
    x1 = max(0, int(face_center_x - half_size))
    x2 = min(width, int(face_center_x + half_size))
    y1 = max(0, int(face_center_y - half_size))
    y2 = min(height, int(face_center_y + half_size))
    crop = image[y1:y2, x1:x2]
    assert crop.size > 0
    tile = cv2.resize(crop, (280, 280), interpolation=cv2.INTER_AREA)
    canvas = np.full((320, 640, 3), 127, dtype=np.uint8)
    canvas[20:300, 20:300] = tile
    canvas[20:300, 340:620] = tile
    return canvas


def test_real_single_and_multi_face_inference(monkeypatch, tmp_path: Path) -> None:
    _require_real_inference()
    fixture_path = Path(__file__).parent / "data" / "常泽宇.png"
    image = cv2.imread(str(fixture_path))
    assert image is not None
    captured_document: dict = {}

    async def exercise() -> None:
        async with lifespan(app):
            async def decode_single(_: str):
                return image.copy(), fixture_path.name

            async def save_person(_, document: dict):
                captured_document.update({"_id": "real-person-id", **document})
                return dict(captured_document), False

            monkeypatch.setattr(persons, "PROJECT_ROOT", tmp_path)
            monkeypatch.setattr(persons.settings.feature_image, "save_person_photo", False)
            monkeypatch.setattr(persons, "base64_to_mat", decode_single)
            monkeypatch.setattr(
                persons.person_crud,
                "update_or_create_person",
                save_person,
            )

            created = await persons.create_person_api(
                PersonFeatureRequest(
                    photo="real-single-fixture",
                    name="真实推理测试人员",
                    number="real-001",
                )
            )
            assert created.status_code == StatusCode.SUCCESS
            stored_vector = np.frombuffer(
                bytes(captured_document["embedding"]), dtype=np.float32
            )
            assert stored_vector.shape == (512,)
            assert np.all(np.isfinite(stored_vector))
            assert np.linalg.norm(stored_vector) > 0
            assert captured_document["photo_path"] == ""
            assert not (tmp_path / "media" / "person_photos").exists()

            async def candidates(_):
                return [captured_document]

            async def no_targets(_, __):
                return []

            monkeypatch.setattr(faces, "base64_to_mat", decode_single)
            monkeypatch.setattr(faces.person, "get_embeddings_for_match", candidates)
            monkeypatch.setattr(faces.person, "get_targets_embeddings", no_targets)
            recognized = await faces.recognize_face_api(
                PersonRecognizeRequest(photo="real-single-fixture")
            )
            assert recognized.status_code == StatusCode.SUCCESS
            assert recognized.data["match"][0]["number"] == "real-001"

            single_faces = await ai_engine.detect_and_extract_all_faces(image)
            assert len(single_faces) >= 1
            multi_image = _two_face_fixture(image, single_faces[0][1])
            detected_multi = await ai_engine.detect_and_extract_all_faces(multi_image)
            assert len(detected_multi) >= 2

            async def decode_multi(_: str):
                return multi_image.copy(), "two-face-fixture.png"

            embedding_calls = 0
            real_get_embedding = ai_engine.get_embedding

            async def counted_embedding(face_image: np.ndarray):
                nonlocal embedding_calls
                embedding_calls += 1
                return await real_get_embedding(face_image)

            monkeypatch.setattr(faces, "base64_to_mat", decode_multi)
            monkeypatch.setattr(faces.ai_engine, "get_embedding", counted_embedding)
            multi_response = await faces.recognize_face_api(
                PersonRecognizeRequest(photo="real-multi-fixture")
            )

            assert embedding_calls >= 2
            assert multi_response.status_code == StatusCode.SUCCESS
            assert set(multi_response.data) == {
                "has_face",
                "bbox",
                "threshold",
                "match",
                "message",
            }
            assert multi_response.data["has_face"] is True
            assert multi_response.data["bbox"] is not None
            assert [item["number"] for item in multi_response.data["match"]] == [
                "real-001"
            ]

    asyncio.run(exercise())
