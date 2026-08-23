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
    partition_person_dataset,
    person_dataset_id,
    person_dataset_tiers,
    recognition_expected_number,
    validate_cross_instance_person_views,
    validate_no_person_photo_residue,
    validate_person_management_response,
    validate_person_recognition_response,
)

TARGETS = NorthboundTargets(
    control_origin="http://192.168.29.11:18100",
    gateway_origin="http://192.168.29.11:18103",
)
PHOTO = base64.b64encode(b"face-image").decode()


def test_person_dataset_and_management_routes_use_gateway_only() -> None:
    identity = ReproducibleIdentity("campaign-persons", 10)
    dataset = build_person_dataset(identity, "PERSONS-001", count=500, encoded_photo=PHOTO)
    plan = build_person_management_requests(TARGETS, dataset[:3], batch_size=2)

    assert len(dataset) == 500
    assert len({person.number for person in dataset}) == 500
    assert all(":18103/api/online/face/persons" in request.url for request in plan.requests)
    assert all(":8003" not in request.url for request in plan.requests)
    assert [request.work_type for request in plan.requests].count("face_person_create") == 1
    assert [request.work_type for request in plan.requests].count(
        "face_person_batch_create"
    ) == 1
    assert tuple(name for name, _ in plan.phases) == (
        "create_batch",
        "list_search",
        "exact_delete",
    )
    assert plan.retained_persons == dataset[:2]
    assert plan.deleted_persons == dataset[2:3]
    assert plan.delete[0].json_body == {"number": dataset[2].number}
    assert FaceManagementBoundary().management_instance_count == 1


def test_recognition_requests_use_leased_gateway_route_and_three_instance_pool() -> None:
    persons = [PersonFixture(name="甲", number="P-001", photo=PHOTO)]
    requests = build_recognition_requests(TARGETS, persons, repeats=3)

    assert len(requests) == 3
    assert all(request.url.endswith(":18103/api/online/face/recognize") for request in requests)
    assert all(recognition_expected_number(request) == "P-001" for request in requests)
    assert FaceManagementBoundary().recognition_instance_count == 3


def test_person_tiers_are_fixed() -> None:
    assert person_dataset_tiers() == (500, 1000, 5000)


@pytest.mark.parametrize("tier", person_dataset_tiers())
def test_management_and_recognition_case_ids_share_tier_dataset(tier: int) -> None:
    identity = ReproducibleIdentity("campaign-persons", 10)
    managed = build_person_dataset(
        identity,
        f"FACE-MANAGE-{tier}",
        count=tier,
        encoded_photo=PHOTO,
    )
    recognized = build_person_dataset(
        identity,
        f"FACE-RECOGNIZE-{tier}",
        count=tier,
        encoded_photo=PHOTO,
    )

    assert person_dataset_id(tier) == f"FACE-DATASET-{tier}"
    assert [person.number for person in managed] == [
        person.number for person in recognized
    ]


def test_recognition_selection_excludes_exactly_deleted_person() -> None:
    persons = tuple(
        PersonFixture(name=f"人物-{index}", number=f"P-{index}", photo=PHOTO)
        for index in range(3)
    )
    partition = partition_person_dataset(persons)
    requests = build_recognition_requests(TARGETS, partition.retained, repeats=5)

    assert partition.deleted == persons[-1:]
    assert {recognition_expected_number(request) for request in requests} == {
        "P-0",
        "P-1",
    }
    assert all(
        recognition_expected_number(request) != partition.deleted[0].number
        for request in requests
    )


def _gateway_response(status_code: int, data: object) -> dict[str, object]:
    return {
        "code": 0,
        "instance_id": "facerec-gpu0",
        "route": "/recognize",
        "data": {"status_code": status_code, "message": "ok", "data": data},
    }


def test_management_response_validates_nested_counts_and_exact_delete() -> None:
    persons = tuple(
        PersonFixture(name=f"人物-{index}", number=f"P-{index}", photo=PHOTO)
        for index in range(3)
    )
    plan = build_person_management_requests(TARGETS, persons, batch_size=2)
    responses = {
        "person-create": _gateway_response(200, {"number": "P-0"}),
        "person-batch-0": _gateway_response(
            200,
            {"persons": [{"number": "P-1"}, {"number": "P-2"}]},
        ),
        "person-list": _gateway_response(
            200,
            {"persons": [{"number": "P-2"}, {"number": "P-0"}, {"number": "P-1"}]},
        ),
        "person-search": _gateway_response(200, {"persons": [{"number": "P-0"}]}),
        "person-delete": _gateway_response(
            200,
            {"deleted_count": 1, "info": [{"number": "P-2"}]},
        ),
    }

    validations = [
        validate_person_management_response(
            request,
            responses[request.request_id],
            expected_numbers=plan.expected_numbers(request),
        )
        for request in plan.requests
    ]

    assert all(item.valid for item in validations)
    assert {instance for item in validations for instance in item.instance_ids} == {
        "facerec-gpu0"
    }
    wrong_delete = _gateway_response(
        200,
        {"deleted_count": 2, "info": [{"number": "P-1"}, {"number": "P-2"}]},
    )
    assert not validate_person_management_response(
        plan.delete[0],
        wrong_delete,
        expected_numbers=plan.expected_numbers(plan.delete[0]),
    ).valid


def test_batch_partial_failure_and_wrong_recognition_number_fail_semantics() -> None:
    persons = tuple(
        PersonFixture(name=f"人物-{index}", number=f"P-{index}", photo=PHOTO)
        for index in range(3)
    )
    plan = build_person_management_requests(TARGETS, persons, batch_size=2)
    partial = _gateway_response(
        207,
        {"success_count": 1, "failed_count": 1, "persons": []},
    )
    batch_validation = validate_person_management_response(
        plan.create_batch[1],
        partial,
        expected_numbers=plan.expected_numbers(plan.create_batch[1]),
    )

    assert not batch_validation.valid
    assert batch_validation.successful_person_count == 1
    assert batch_validation.failed_person_count == 1
    assert validate_person_recognition_response(
        _gateway_response(200, {"match": [{"number": "P-expected"}]}),
        expected_number="P-expected",
    ).valid
    assert not validate_person_recognition_response(
        _gateway_response(200, {"match": [{"number": "P-wrong"}]}),
        expected_number="P-expected",
    ).valid
    assert not validate_person_recognition_response(
        _gateway_response(200, {"match": []}),
        expected_number="P-expected",
    ).valid


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
