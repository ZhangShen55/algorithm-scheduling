import asyncio
import os
import uuid
from pathlib import Path

import numpy as np
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from app.models.api_response import StatusCode
from app.models.request.person_interface_req import (
    DeletePersonRequest,
    PersonBatchFeatureRequest,
    PersonFeatureRequest,
    PersonsBatchFeatureRequest,
    SearchPersonRequest,
)
from app.router import persons
from app.services import person as person_service


def test_person_crud_is_consistent_across_three_mongodb_clients(
    monkeypatch, tmp_path: Path
) -> None:
    uri = os.getenv("FACEREC_TEST_MONGODB_URI")
    if not uri:
        pytest.skip("需要显式设置 FACEREC_TEST_MONGODB_URI")
    database_name = f"facerec_v13_test_{uuid.uuid4().hex}"

    async def exercise() -> None:
        clients = [AsyncIOMotorClient(uri) for _ in range(3)]
        databases = [client[database_name] for client in clients]
        try:
            for database in databases:
                await database.command({"ping": 1})
            monkeypatch.setattr(persons, "db", databases[0])
            monkeypatch.setattr(persons, "PROJECT_ROOT", tmp_path)
            monkeypatch.setattr(
                persons.settings.feature_image,
                "save_person_photo",
                False,
            )

            async def decode(_: str):
                return np.zeros((160, 160, 3), dtype=np.uint8), "fixture.png"

            async def detect(_: np.ndarray):
                return (
                    np.zeros((112, 112, 3), dtype=np.uint8),
                    {"x": 1, "y": 2, "w": 112, "h": 112},
                    "ok",
                )

            embedding_value = 1.0

            async def embed(_: np.ndarray):
                return np.full(512, embedding_value, dtype=np.float32)

            monkeypatch.setattr(persons, "base64_to_mat", decode)
            monkeypatch.setattr(persons.ai_engine, "detect_and_extract_face", detect)
            monkeypatch.setattr(persons.ai_engine, "get_embedding", embed)

            batch = await persons.create_persons_batch_api(
                PersonsBatchFeatureRequest(
                    persons=[
                        PersonBatchFeatureRequest(
                            photo="photo-a", name="人员 A", number="001"
                        ),
                        PersonBatchFeatureRequest(
                            photo="photo-b", name="人员 B", number="002"
                        ),
                    ]
                )
            )
            assert batch.status_code == StatusCode.SUCCESS

            listed = await persons.read_persons_api(skip=0, limit=10)
            assert listed.status_code == StatusCode.SUCCESS
            assert {item["number"] for item in listed.data["persons"]} == {"001", "002"}

            searched = await persons.search_person_api(SearchPersonRequest(number="002"))
            assert searched.status_code == StatusCode.SUCCESS
            assert [item["number"] for item in searched.data["persons"]] == ["002"]

            embedding_value = 2.0
            updated = await persons.create_person_api(
                PersonFeatureRequest(
                    photo="photo-updated",
                    name="人员 A 更新",
                    number="001",
                )
            )
            assert updated.status_code == StatusCode.SUCCESS

            gpu1_view = await person_service.get_targets_embeddings(
                databases[1], ["001"]
            )
            gpu2_view = await person_service.get_targets_embeddings(
                databases[2], ["001"]
            )
            assert gpu1_view[0]["name"] == "人员 A 更新"
            assert gpu2_view[0]["name"] == "人员 A 更新"
            for document in (*gpu1_view, *gpu2_view):
                vector = np.frombuffer(bytes(document["embedding"]), dtype=np.float32)
                assert vector.shape == (512,)
                assert np.all(vector == 2.0)
                assert document["photo_path"] == ""

            deleted = await persons.delete_person_general_api(
                DeletePersonRequest(number="001")
            )
            assert deleted.status_code == StatusCode.SUCCESS
            assert await person_service.get_targets_embeddings(databases[1], ["001"]) == []
            assert await person_service.get_targets_embeddings(databases[2], ["001"]) == []
            assert not (tmp_path / "media" / "person_photos").exists()
        finally:
            await clients[0].drop_database(database_name)
            for client in clients:
                client.close()

    asyncio.run(exercise())
