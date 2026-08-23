from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .core import HttpRequestSpec, NorthboundTargets, ReproducibleIdentity


def _validate_base64(value: str) -> None:
    if value.startswith("data:"):
        header, separator, value = value.partition(",")
        if (
            not separator
            or not header.lower().startswith("data:image/")
            or not header.lower().endswith(";base64")
        ):
            raise ValueError("人物照片 data URI 格式不合法")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("人物照片不是合法 Base64") from exc
    if not decoded:
        raise ValueError("人物照片 Base64 不能为空")


@dataclass(frozen=True)
class PersonFixture:
    name: str
    number: str
    photo: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.number.strip():
            raise ValueError("人物姓名和编号不能为空")
        _validate_base64(self.photo)


@dataclass(frozen=True)
class FaceManagementBoundary:
    management_instance_count: int = 1
    recognition_instance_count: int = 3
    save_person_photo: bool = False

    def __post_init__(self) -> None:
        if self.management_instance_count != 1:
            raise ValueError("人脸管理请求必须固定转发到单实例")
        if self.recognition_instance_count != 3:
            raise ValueError("人脸识别请求必须使用三实例租约池")
        if self.save_person_photo:
            raise ValueError("极限负载 Campaign 必须使用 save_person_photo=false")


def person_dataset_tiers() -> tuple[int, ...]:
    return (500, 1000, 5000)


def person_dataset_id(count: int) -> str:
    if count not in person_dataset_tiers():
        raise ValueError("人物数据集只允许 500/1000/5000")
    return f"FACE-DATASET-{count}"


def build_person_dataset(
    identity: ReproducibleIdentity,
    case_id: str,
    *,
    count: int,
    encoded_photo: str,
) -> tuple[PersonFixture, ...]:
    if count not in person_dataset_tiers():
        raise ValueError("人物数据集只允许 500/1000/5000")
    if not case_id:
        raise ValueError("人物用例 ID 不能为空")
    _validate_base64(encoded_photo)
    dataset_id = person_dataset_id(count)
    return tuple(
        PersonFixture(
            name=f"压测人物-{index:05d}",
            number=f"P-{identity.request_id(dataset_id, index)[-18:]}",
            photo=encoded_photo,
        )
        for index in range(count)
    )


def _person_body(person: PersonFixture) -> dict[str, str]:
    return {"name": person.name, "number": person.number, "photo": person.photo}


@dataclass(frozen=True)
class PersonDatasetPartition:
    retained: tuple[PersonFixture, ...]
    deleted: tuple[PersonFixture, ...]

    def __post_init__(self) -> None:
        all_numbers = [person.number for person in (*self.retained, *self.deleted)]
        if not self.retained or not self.deleted:
            raise ValueError("人物数据集必须同时包含保留和删除分区")
        if len(all_numbers) != len(set(all_numbers)):
            raise ValueError("人物数据集编号不能重复")


def partition_person_dataset(
    persons: Sequence[PersonFixture],
    *,
    delete_count: int = 1,
) -> PersonDatasetPartition:
    if delete_count <= 0 or delete_count >= len(persons):
        raise ValueError("删除分区必须为正且小于人物总数")
    retained_count = len(persons) - delete_count
    return PersonDatasetPartition(
        retained=tuple(persons[:retained_count]),
        deleted=tuple(persons[retained_count:]),
    )


@dataclass(frozen=True)
class PersonManagementRequestPlan:
    create_batch: tuple[HttpRequestSpec, ...]
    read: tuple[HttpRequestSpec, ...]
    delete: tuple[HttpRequestSpec, ...]
    persons: tuple[PersonFixture, ...]
    retained_persons: tuple[PersonFixture, ...]
    deleted_persons: tuple[PersonFixture, ...]

    @property
    def phases(self) -> tuple[tuple[str, tuple[HttpRequestSpec, ...]], ...]:
        return (
            ("create_batch", self.create_batch),
            ("list_search", self.read),
            ("exact_delete", self.delete),
        )

    @property
    def requests(self) -> tuple[HttpRequestSpec, ...]:
        return (*self.create_batch, *self.read, *self.delete)

    def expected_numbers(self, request: HttpRequestSpec) -> tuple[str, ...]:
        if request.work_type == "face_person_list":
            return tuple(person.number for person in self.persons)
        body = request.json_body
        if request.work_type == "face_person_batch_create":
            raw_persons = body.get("persons") if body is not None else None
            if not isinstance(raw_persons, Sequence) or isinstance(raw_persons, str):
                raise ValueError("批量人物请求缺少 persons")
            numbers = tuple(
                str(item.get("number"))
                for item in raw_persons
                if isinstance(item, Mapping) and isinstance(item.get("number"), str)
            )
            if len(numbers) != len(raw_persons):
                raise ValueError("批量人物请求缺少 number")
            return numbers
        number = body.get("number") if body is not None else None
        if not isinstance(number, str) or not number:
            raise ValueError("人物管理请求缺少 number")
        return (number,)


def build_person_management_requests(
    targets: NorthboundTargets,
    persons: Sequence[PersonFixture],
    *,
    batch_size: int,
) -> PersonManagementRequestPlan:
    if not persons or batch_size <= 0:
        raise ValueError("人物管理负载和 batch_size 必须为正")
    partition = partition_person_dataset(persons)
    first = persons[0]
    create_batch: list[HttpRequestSpec] = [
        HttpRequestSpec(
            request_id="person-create",
            method="POST",
            url=targets.gateway_url("/api/online/face/persons"),
            json_body=_person_body(first),
            work_type="face_person_create",
            expected_lease_acquisition=False,
        )
    ]
    for batch_index, start in enumerate(range(1, len(persons), batch_size)):
        batch = persons[start : start + batch_size]
        create_batch.append(
            HttpRequestSpec(
                request_id=f"person-batch-{batch_index}",
                method="POST",
                url=targets.gateway_url("/api/online/face/persons/batch"),
                json_body={"persons": [_person_body(person) for person in batch]},
                work_type="face_person_batch_create",
                expected_lease_acquisition=False,
            )
        )
    read = (
        HttpRequestSpec(
            request_id="person-list",
            method="GET",
            url=(
                targets.gateway_url("/api/online/face/persons")
                + f"?skip=0&limit={len(persons)}"
            ),
            work_type="face_person_list",
            expected_lease_acquisition=False,
        ),
        HttpRequestSpec(
            request_id="person-search",
            method="POST",
            url=targets.gateway_url("/api/online/face/persons/search"),
            json_body={"number": first.number},
            work_type="face_person_search",
            expected_lease_acquisition=False,
        ),
    )
    delete = (
        HttpRequestSpec(
            request_id="person-delete",
            method="DELETE",
            url=targets.gateway_url("/api/online/face/persons/delete"),
            json_body={"number": partition.deleted[0].number},
            work_type="face_person_delete",
            expected_lease_acquisition=False,
        ),
    )
    return PersonManagementRequestPlan(
        create_batch=tuple(create_batch),
        read=read,
        delete=delete,
        persons=tuple(persons),
        retained_persons=partition.retained,
        deleted_persons=partition.deleted,
    )


def build_recognition_requests(
    targets: NorthboundTargets,
    persons: Sequence[PersonFixture],
    *,
    repeats: int,
) -> tuple[HttpRequestSpec, ...]:
    if not persons or repeats <= 0:
        raise ValueError("识别负载必须包含人物且 repeats 为正数")
    return tuple(
        HttpRequestSpec(
            request_id=f"person-recognize-{index}",
            method="POST",
            url=targets.gateway_url("/api/online/face/recognize"),
            json_body={
                "photo": persons[index % len(persons)].photo,
                "targets": [persons[index % len(persons)].number],
            },
            work_type="online_face_recognize",
            expected_lease_acquisition=True,
        )
        for index in range(repeats)
    )


def recognition_expected_number(request: HttpRequestSpec) -> str:
    body = request.json_body
    targets = body.get("targets") if body is not None else None
    if (
        not isinstance(targets, Sequence)
        or isinstance(targets, str)
        or len(targets) != 1
        or not isinstance(targets[0], str)
        or not targets[0]
    ):
        raise ValueError("人脸识别请求必须包含唯一预期 number")
    return targets[0]


@dataclass(frozen=True)
class PersonResponseValidation:
    valid: bool
    reason: str
    successful_person_count: int
    failed_person_count: int
    observed_numbers: tuple[str, ...] = ()
    instance_ids: tuple[str, ...] = ()
    response_routes: tuple[str, ...] = ()


_INSTANCE_EVIDENCE_KEYS = frozenset(
    {"instance_id", "operator_instance_id", "selected_instance_id"}
)
_ROUTE_EVIDENCE_KEYS = frozenset(
    {"route", "route_name", "endpoint", "operator_route"}
)


def _safe_response_evidence(value: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    instances: set[str] = set()
    routes: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for raw_key, child in item.items():
                key = str(raw_key).lower()
                if key in _INSTANCE_EVIDENCE_KEYS and isinstance(child, str) and child:
                    instances.add(child)
                elif key in _ROUTE_EVIDENCE_KEYS and isinstance(child, str) and child:
                    routes.add(child)
                visit(child)
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            for child in item:
                visit(child)

    visit(value)
    return tuple(sorted(instances)), tuple(sorted(routes))


def _operator_response(
    response: Mapping[str, Any],
) -> tuple[int | None, Mapping[str, Any] | None, tuple[str, ...], tuple[str, ...]]:
    instances, routes = _safe_response_evidence(response)
    gateway_code = response.get("code")
    upstream = response.get("data")
    if gateway_code != 0 or not isinstance(upstream, Mapping):
        return None, None, instances, routes
    status_code = upstream.get("status_code")
    data = upstream.get("data")
    return (
        status_code if type(status_code) is int else None,
        data if isinstance(data, Mapping) else None,
        instances,
        routes,
    )


def _person_numbers(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(
        str(number)
        for item in value
        if isinstance(item, Mapping) and isinstance(number := item.get("number"), str)
    )


def validate_person_management_response(
    request: HttpRequestSpec,
    response: Mapping[str, Any],
    *,
    expected_numbers: Sequence[str],
) -> PersonResponseValidation:
    expected = tuple(expected_numbers)
    status_code, data, instances, routes = _operator_response(response)
    if status_code != 200 or data is None:
        successful = 0
        failed = len(expected)
        if data is not None:
            raw_success = data.get("success_count")
            raw_failed = data.get("failed_count")
            if type(raw_success) is int and raw_success >= 0:
                successful = raw_success
            if type(raw_failed) is int and raw_failed >= 0:
                failed = raw_failed
        return PersonResponseValidation(
            False,
            f"FaceRec 人物管理 status_code 非成功: {status_code}",
            successful,
            failed,
            instance_ids=instances,
            response_routes=routes,
        )

    observed: tuple[str, ...]
    valid = False
    if request.work_type == "face_person_create":
        number = data.get("number")
        observed = (number,) if isinstance(number, str) else ()
        valid = observed == expected
    elif request.work_type == "face_person_batch_create":
        observed = _person_numbers(data.get("persons"))
        raw_success = data.get("success_count", len(observed))
        raw_failed = data.get("failed_count", 0)
        valid = (
            type(raw_success) is int
            and raw_success == len(expected)
            and type(raw_failed) is int
            and raw_failed == 0
            and len(observed) == len(set(observed))
            and set(observed) == set(expected)
        )
    elif request.work_type in {"face_person_list", "face_person_search"}:
        observed = _person_numbers(data.get("persons"))
        valid = len(observed) == len(set(observed)) and set(expected).issubset(observed)
        if request.work_type == "face_person_search":
            valid = valid and set(observed) == set(expected)
    elif request.work_type == "face_person_delete":
        observed = _person_numbers(data.get("info"))
        deleted_count = data.get("deleted_count")
        valid = (
            type(deleted_count) is int
            and deleted_count == len(expected) == 1
            and observed == expected
        )
    else:
        raise ValueError(f"未知人物管理 work_type: {request.work_type}")

    return PersonResponseValidation(
        valid,
        "人物管理响应事实符合预期" if valid else "人物管理响应数量或 number 事实不符合预期",
        len(expected) if valid else len(set(observed).intersection(expected)),
        0 if valid else len(set(expected).difference(observed)),
        observed_numbers=observed,
        instance_ids=instances,
        response_routes=routes,
    )


def validate_person_recognition_response(
    response: Mapping[str, Any],
    *,
    expected_number: str,
) -> PersonResponseValidation:
    status_code, data, instances, routes = _operator_response(response)
    observed = () if data is None else _person_numbers(data.get("match"))
    valid = status_code == 200 and expected_number in observed
    return PersonResponseValidation(
        valid,
        "识别结果包含预期 number" if valid else "识别结果缺少预期 number",
        1 if valid else 0,
        0 if valid else 1,
        observed_numbers=observed,
        instance_ids=instances,
        response_routes=routes,
    )


@dataclass(frozen=True)
class PersonViewVerdict:
    consistent: bool
    reason: str = ""


def validate_cross_instance_person_views(
    views: Mapping[str, Sequence[str]],
    expected_numbers: Sequence[str] | None = None,
) -> PersonViewVerdict:
    if len(views) != 3:
        return PersonViewVerdict(False, "必须核对三个识别实例")
    normalized: list[set[str]] = []
    for instance_id, numbers in views.items():
        if len(numbers) != len(set(numbers)):
            return PersonViewVerdict(False, f"{instance_id} 存在重复人物")
        normalized.append(set(numbers))
    if any(view != normalized[0] for view in normalized[1:]):
        return PersonViewVerdict(False, "三个实例的人物事实不一致")
    if expected_numbers is not None and normalized[0] != set(expected_numbers):
        return PersonViewVerdict(False, "三个实例缺少已成功写入的人物事实")
    return PersonViewVerdict(True)


@dataclass(frozen=True)
class ResidueObservation:
    scope: str
    matched_paths: tuple[str, ...]
    matched_document_count: int

    def __post_init__(self) -> None:
        if self.matched_document_count < 0:
            raise ValueError("残留文档计数不能为负数")


@dataclass(frozen=True)
class ResidueVerdict:
    clean: bool
    reasons: tuple[str, ...]


def validate_no_person_photo_residue(
    observations: Sequence[ResidueObservation],
) -> ResidueVerdict:
    required = {"container", "mongodb", "logs", "persistent_directory"}
    scopes = [observation.scope for observation in observations]
    reasons: list[str] = []
    if set(scopes) != required or len(scopes) != len(required):
        reasons.append("四类原图残留证据缺失或重复")
    for observation in observations:
        if observation.matched_paths or observation.matched_document_count:
            reasons.append(f"{observation.scope} 发现人脸原图残留")
    return ResidueVerdict(clean=not reasons, reasons=tuple(reasons))
