from __future__ import annotations

import base64

import pytest

from scripts.extreme_load.core import NorthboundTargets, ReproducibleIdentity
from scripts.extreme_load.persons import (
    FaceManagementBoundary,
    PersonFixture,
    ResidueObservation,
    build_person_dataset,
    build_person_management_requests,
    build_recognition_requests,
    person_dataset_tiers,
    validate_cross_instance_person_views,
    validate_no_person_photo_residue,
)

TARGETS = NorthboundTargets(
    control_origin="http://192.168.29.11:18100",
    gateway_origin="http://192.168.29.11:18103",
)
PHOTO = base64.b64encode(b"face-image").decode()


def test_person_dataset_and_management_routes_use_gateway_only() -> None:
    identity = ReproducibleIdentity("campaign-persons", 10)
    dataset = build_person_dataset(identity, "PERSONS-001", count=500, encoded_photo=PHOTO)
    requests = build_person_management_requests(TARGETS, dataset[:3], batch_size=2)

    assert len(dataset) == 500
    assert len({person.number for person in dataset}) == 500
    assert all(":18103/api/online/face/persons" in request.url for request in requests)
    assert all(":8003" not in request.url for request in requests)
    assert [request.work_type for request in requests].count("face_person_create") == 1
    assert [request.work_type for request in requests].count("face_person_batch_create") == 1
    assert FaceManagementBoundary().management_instance_count == 1


def test_recognition_requests_use_leased_gateway_route_and_three_instance_pool() -> None:
    persons = [PersonFixture(name="甲", number="P-001", photo=PHOTO)]
    requests = build_recognition_requests(TARGETS, persons, repeats=3)

    assert len(requests) == 3
    assert all(request.url.endswith(":18103/api/online/face/recognize") for request in requests)
    assert FaceManagementBoundary().recognition_instance_count == 3


def test_person_tiers_are_fixed() -> None:
    assert person_dataset_tiers() == (500, 1000, 5000)


def test_cross_instance_views_require_same_numbers_without_duplicates() -> None:
    assert validate_cross_instance_person_views(
        {
            "facerec-gpu0": ("P-001", "P-002"),
            "facerec-gpu1": ("P-002", "P-001"),
            "facerec-gpu2": ("P-001", "P-002"),
        }
    ).consistent
    assert not validate_cross_instance_person_views(
        {
            "facerec-gpu0": ("P-001", "P-002"),
            "facerec-gpu1": ("P-001",),
            "facerec-gpu2": ("P-001", "P-001"),
        }
    ).consistent


def test_no_photo_residue_requires_all_four_scopes_clean() -> None:
    clean = [
        ResidueObservation(scope=scope, matched_paths=(), matched_document_count=0)
        for scope in ("container", "mongodb", "logs", "persistent_directory")
    ]
    assert validate_no_person_photo_residue(clean).clean
    dirty = [
        *clean,
        ResidueObservation(
            scope="mongodb",
            matched_paths=("photo_path",),
            matched_document_count=1,
        ),
    ]
    assert not validate_no_person_photo_residue(dirty).clean


def test_person_fixture_rejects_invalid_base64() -> None:
    with pytest.raises(ValueError, match="Base64"):
        PersonFixture(name="甲", number="P-001", photo="not-base64")
