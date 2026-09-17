import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from bson.binary import Binary

from app.models.api_response import StatusCode
from app.models.request.person_interface_req import (
    PersonBatchFeatureRequest,
    PersonFeatureRequest,
    PersonsBatchFeatureRequest,
)
from app.router import persons
from app.services import person as person_service


class MemoryCursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents

    def skip(self, count: int):
        self.documents = self.documents[count:]
        return self

    def limit(self, count: int):
        self.documents = self.documents[:count]
        return self

    async def to_list(self, length: int | None) -> list[dict]:
        if length is None:
            return list(self.documents)
        return list(self.documents[:length])


class SharedPersonsCollection:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}
        self.next_id = 1

    async def find_one(self, query: dict, projection: dict | None = None):
        del projection
        for document in self.documents.values():
            if all(document.get(key) == value for key, value in query.items()):
                return dict(document)
        return None

    async def insert_one(self, document: dict):
        stored = dict(document)
        stored["_id"] = str(self.next_id)
        self.next_id += 1
        self.documents[stored["number"]] = stored
        return SimpleNamespace(inserted_id=stored["_id"])

    async def update_one(self, query: dict, update: dict):
        self.documents[query["number"]].update(update["$set"])
        return SimpleNamespace(modified_count=1)

    async def delete_one(self, query: dict):
        number = query.get("number")
        if number in self.documents:
            del self.documents[number]
            return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    def find(self, query: dict, projection: dict | None = None) -> MemoryCursor:
        del projection
        documents = list(self.documents.values())
        numbers = query.get("number", {}).get("$in") if query else None
        if numbers is not None:
            documents = [doc for doc in documents if doc.get("number") in numbers]
        return MemoryCursor([dict(document) for document in documents])


class SharedDatabase:
    def __init__(self, collection: SharedPersonsCollection) -> None:
        self.persons = collection

    def __getitem__(self, name: str) -> SharedPersonsCollection:
        assert name == "persons"
        return self.persons


def _embedding(value: float = 1.0) -> np.ndarray:
    result = np.full(512, value, dtype=np.float32)
    return result


def _install_successful_inference(monkeypatch) -> None:
    async def decode(_: str):
        return np.zeros((160, 160, 3), dtype=np.uint8), "fixture.png"

    async def detect(_: np.ndarray):
        return (
            np.zeros((112, 112, 3), dtype=np.uint8),
            {"x": 1, "y": 2, "w": 112, "h": 112},
            "ok",
        )

    async def embed(_: np.ndarray):
        return _embedding()

    monkeypatch.setattr(persons, "base64_to_mat", decode)
    monkeypatch.setattr(persons.ai_engine, "detect_and_extract_face", detect)
    monkeypatch.setattr(persons.ai_engine, "get_embedding", embed)


def test_facerec_has_no_direct_redis_runtime_boundary() -> None:
    project_root = Path(__file__).resolve().parents[1]
    sources = list((project_root / "app").rglob("*.py"))
    sources.extend(
        project_root / name
        for name in ("config.toml", "config.example.toml", "requirements.txt")
    )

    matches = [
        path.relative_to(project_root)
        for path in sources
        if "redis" in path.read_text(encoding="utf-8").lower()
    ]

    assert matches == []


def test_mongodb_client_keeps_retryable_reads_and_writes_enabled() -> None:
    from app.core.database import client

    assert client.options.retry_reads is True
    assert client.options.retry_writes is True


def test_three_instances_observe_shared_mongodb_updates_without_cache() -> None:
    collection = SharedPersonsCollection()
    gpu0 = SharedDatabase(collection)
    gpu1 = SharedDatabase(collection)
    gpu2 = SharedDatabase(collection)
    initial = Binary(_embedding(1.0).tobytes())
    updated = Binary(_embedding(2.0).tobytes())

    asyncio.run(
        person_service.update_or_create_person(
            gpu0,
            {"name": "测试人员", "number": "001", "embedding": initial},
        )
    )
    observed = asyncio.run(person_service.get_embeddings_for_match(gpu1))
    assert bytes(observed[0]["embedding"]) == bytes(initial)

    asyncio.run(
        person_service.update_or_create_person(
            gpu0,
            {"name": "测试人员-更新", "number": "001", "embedding": updated},
        )
    )
    observed = asyncio.run(person_service.get_targets_embeddings(gpu2, ["001"]))
    assert observed[0]["name"] == "测试人员-更新"
    assert bytes(observed[0]["embedding"]) == bytes(updated)

    deleted, _ = asyncio.run(person_service.delete_person_by_number(gpu1, "001"))
    assert deleted == 1
    assert asyncio.run(person_service.get_embeddings_for_match(gpu2)) == []


def test_reading_existing_embeddings_does_not_rewrite_or_migrate_them() -> None:
    collection = SharedPersonsCollection()
    database = SharedDatabase(collection)
    existing = Binary(_embedding(3.0).tobytes())
    collection.documents["legacy-001"] = {
        "_id": "legacy-id",
        "name": "旧记录",
        "number": "legacy-001",
        "embedding": existing,
    }

    all_documents = asyncio.run(person_service.get_embeddings_for_match(database))
    target_documents = asyncio.run(
        person_service.get_targets_embeddings(database, ["legacy-001"])
    )

    assert bytes(all_documents[0]["embedding"]) == bytes(existing)
    assert bytes(target_documents[0]["embedding"]) == bytes(existing)
    assert bytes(collection.documents["legacy-001"]["embedding"]) == bytes(existing)


def test_single_create_persists_embedding_without_photo(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    _install_successful_inference(monkeypatch)
    monkeypatch.setattr(persons, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(persons.settings.feature_image, "save_person_photo", False)
    captured: list[dict] = []

    async def save(_, document: dict):
        captured.append(document)
        return {"_id": "person-id", **document}, False

    monkeypatch.setattr(persons.person_crud, "update_or_create_person", save)

    sensitive_name = "never-log-person-name"
    sensitive_number = "never-log-person-number"
    with caplog.at_level(logging.INFO, logger="app.router.persons"):
        response = asyncio.run(
            persons.create_person_api(
                PersonFeatureRequest(
                    photo="data:image/png;base64,AA==",
                    name=sensitive_name,
                    number=sensitive_number,
                )
            )
        )

    assert response.status_code == StatusCode.SUCCESS
    assert response.data["photo_path"] == ""
    assert len(np.frombuffer(bytes(captured[0]["embedding"]), dtype=np.float32)) == 512
    assert not (tmp_path / "media" / "person_photos").exists()
    assert sensitive_name not in caplog.text
    assert sensitive_number not in caplog.text
    assert "embedding" not in caplog.text.lower()


def test_batch_create_reports_partial_failure_and_keeps_embeddings(
    monkeypatch, tmp_path: Path
) -> None:
    _install_successful_inference(monkeypatch)
    monkeypatch.setattr(persons, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(persons.settings.feature_image, "save_person_photo", False)
    captured: list[dict] = []

    async def save(_, document: dict):
        captured.append(document)
        if document["number"] == "002":
            raise RuntimeError("模拟 MongoDB 写入失败")
        return {"_id": "person-id", **document}, False

    monkeypatch.setattr(persons.person_crud, "update_or_create_person", save)
    request = PersonsBatchFeatureRequest(
        persons=[
            PersonBatchFeatureRequest(photo="photo-a", name="A", number="001"),
            PersonBatchFeatureRequest(photo="photo-b", name="B", number="002"),
        ]
    )

    response = asyncio.run(persons.create_persons_batch_api(request))

    assert response.status_code == StatusCode.PARTIAL_SUCCESS
    assert response.data["success_count"] == 1
    assert response.data["failed_count"] == 1
    assert response.data["failed_numbers"] == ["002"]
    assert response.data["persons"][0]["photo_path"] == ""
    assert all(
        len(np.frombuffer(bytes(document["embedding"]), dtype=np.float32)) == 512
        for document in captured
    )
    assert not (tmp_path / "media" / "person_photos").exists()
