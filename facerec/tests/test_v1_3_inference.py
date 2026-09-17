import asyncio
from types import SimpleNamespace

import numpy as np

from app.main import app
from app.models.api_response import StatusCode
from app.models.request.face_interface_req import PersonRecognizeRequest
from app.router import faces


def test_recognize_openapi_preserves_legacy_request_fields() -> None:
    schema = app.openapi()["components"]["schemas"]["PersonRecognizeRequest"]

    assert set(schema["properties"]) == {"photo", "targets", "threshold"}
    assert "points" not in schema["properties"]


def test_recognize_processes_all_faces_and_deduplicates_by_number(monkeypatch) -> None:
    image = np.zeros((320, 480, 3), dtype=np.uint8)
    aligned_a = np.full((112, 112, 3), 1, dtype=np.uint8)
    aligned_b = np.full((112, 112, 3), 2, dtype=np.uint8)
    documents = [
        {"_id": "a", "name": "A", "number": "001"},
        {"_id": "b", "name": "B", "number": "002"},
        {"_id": "c", "name": "C", "number": "003"},
    ]

    async def decode(_: str):
        return image, "fixture.png"

    async def detect(_: np.ndarray):
        return [
            (aligned_a, {"x": 10, "y": 10, "w": 100, "h": 100}, "ok"),
            (aligned_b, {"x": 150, "y": 20, "w": 140, "h": 140}, "ok"),
        ]

    async def embeddings(_):
        return documents

    async def targets(_, __):
        return []

    async def embedding(face: np.ndarray):
        result = np.zeros(512, dtype=np.float32)
        result[0] = float(face[0, 0, 0])
        return result

    def matches(query, _, *, top_k, min_threshold):
        del top_k, min_threshold
        if query[0] == 1:
            return [(0.80, documents[0]), (0.70, documents[1])]
        return [(0.90, documents[0]), (0.85, documents[2])]

    monkeypatch.setattr(faces, "base64_to_mat", decode)
    monkeypatch.setattr(faces.ai_engine, "detect_and_extract_all_faces", detect)
    monkeypatch.setattr(faces.person, "get_embeddings_for_match", embeddings)
    monkeypatch.setattr(faces.person, "get_targets_embeddings", targets)
    monkeypatch.setattr(faces.ai_engine, "get_embedding", embedding)
    monkeypatch.setattr(faces.ai_engine, "find_top_matches", matches)

    response = asyncio.run(
        faces.recognize_face_api(PersonRecognizeRequest(photo="data:image/png;base64,AA=="))
    )

    assert response.status_code == StatusCode.SUCCESS
    assert response.data["bbox"] == {"x": 150, "y": 20, "w": 140, "h": 140}
    assert [item["number"] for item in response.data["match"]] == ["001", "003", "002"]
    assert [item["similarity"] for item in response.data["match"]] == [
        "90.00%",
        "85.00%",
        "70.00%",
    ]


def test_recognize_filters_faces_by_detected_bbox_size(monkeypatch) -> None:
    image = np.zeros((100, 100, 3), dtype=np.uint8)

    async def decode(_: str):
        return image, "small.png"

    async def detect(_: np.ndarray):
        return [
            (
                np.zeros((112, 112, 3), dtype=np.uint8),
                {"x": 0, "y": 0, "w": faces.REC_MIN_FACE_HW - 1, "h": 100},
                "small",
            )
        ]

    monkeypatch.setattr(faces, "base64_to_mat", decode)
    monkeypatch.setattr(faces.ai_engine, "detect_and_extract_all_faces", detect)

    response = asyncio.run(
        faces.recognize_face_api(PersonRecognizeRequest(photo="data:image/png;base64,AA=="))
    )

    assert response.status_code == StatusCode.FACE_TOO_SMALL
    assert response.data["has_face"] is True
    assert response.data["match"] is None


def test_recognize_returns_no_face_without_loading_candidates(monkeypatch) -> None:
    async def decode(_: str):
        return np.zeros((100, 100, 3), dtype=np.uint8), "none.png"

    async def detect(_: np.ndarray):
        return []

    async def unexpected(_):
        raise AssertionError("无人脸时不应读取人员库")

    monkeypatch.setattr(faces, "base64_to_mat", decode)
    monkeypatch.setattr(faces.ai_engine, "detect_and_extract_all_faces", detect)
    monkeypatch.setattr(faces.person, "get_embeddings_for_match", unexpected)

    response = asyncio.run(
        faces.recognize_face_api(PersonRecognizeRequest(photo="data:image/png;base64,AA=="))
    )

    assert response.status_code == StatusCode.NO_FACE_DETECTED
    assert response.data is None


def test_recognize_returns_no_match_with_legacy_response_fields(monkeypatch) -> None:
    document = {"_id": "a", "name": "A", "number": "001"}

    async def decode(_: str):
        return np.zeros((100, 100, 3), dtype=np.uint8), "face.png"

    async def detect(_: np.ndarray):
        return [
            (
                np.zeros((112, 112, 3), dtype=np.uint8),
                {"x": 1, "y": 2, "w": 80, "h": 90},
                "ok",
            )
        ]

    async def embeddings(_):
        return [document]

    async def embedding(_: np.ndarray):
        return np.ones(512, dtype=np.float32)

    monkeypatch.setattr(faces, "base64_to_mat", decode)
    monkeypatch.setattr(faces.ai_engine, "detect_and_extract_all_faces", detect)
    monkeypatch.setattr(faces.person, "get_embeddings_for_match", embeddings)
    monkeypatch.setattr(faces.ai_engine, "get_embedding", embedding)
    monkeypatch.setattr(faces.ai_engine, "find_top_matches", lambda *args, **kwargs: [])

    response = asyncio.run(
        faces.recognize_face_api(PersonRecognizeRequest(photo="data:image/png;base64,AA=="))
    )

    assert response.status_code == StatusCode.NO_MATCH_FOUND
    assert set(response.data) == {"has_face", "bbox", "threshold", "match", "message"}
    assert response.data["match"] is None


def test_batch_recognize_keeps_single_face_per_frame_semantics(monkeypatch) -> None:
    document = {"_id": "a", "name": "A", "number": "001"}

    async def decode(_: str):
        return np.zeros((100, 100, 3), dtype=np.uint8), "frame.png"

    async def detect(_: np.ndarray):
        return (
            np.zeros((112, 112, 3), dtype=np.uint8),
            {"x": 1, "y": 2, "w": 80, "h": 90},
            "ok",
        )

    async def detect_all(_: np.ndarray):
        raise AssertionError("batch 不应切换为单图多人脸语义")

    async def embeddings(_):
        return [document]

    async def embedding(_: np.ndarray):
        return np.ones(512, dtype=np.float32)

    monkeypatch.setattr(faces, "base64_to_mat", decode)
    monkeypatch.setattr(faces.ai_engine, "detect_and_extract_face", detect)
    monkeypatch.setattr(faces.ai_engine, "detect_and_extract_all_faces", detect_all)
    monkeypatch.setattr(faces.person, "get_embeddings_for_match", embeddings)
    monkeypatch.setattr(faces.ai_engine, "get_embedding", embedding)
    monkeypatch.setattr(
        faces.ai_engine,
        "find_top_matches",
        lambda *args, **kwargs: [(0.88, document)],
    )

    request = faces.BatchRecognizeRequest(
        photos=["data:image/png;base64,AA==", "data:image/png;base64,AA=="]
    )
    response = asyncio.run(faces.recognize_batch_api(request))

    assert response.status_code == StatusCode.SUCCESS
    assert response.data["total_frames"] == 2
    assert response.data["valid_frames"] == 2
    assert [item["number"] for item in response.data["match"]] == ["001"]


def test_merge_matches_keeps_highest_similarity_and_target_flag() -> None:
    destination = {}
    document = {"number": "001"}

    faces._merge_matches(destination, [(0.9, document)], is_target=False)
    faces._merge_matches(destination, [(0.2, document)], is_target=True)

    assert destination["001"] == (0.9, document, True)
