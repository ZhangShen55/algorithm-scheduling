from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

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


def build_person_dataset(
    identity: ReproducibleIdentity,
    case_id: str,
    *,
    count: int,
    encoded_photo: str,
) -> tuple[PersonFixture, ...]:
    if count not in person_dataset_tiers():
        raise ValueError("人物数据集只允许 500/1000/5000")
    _validate_base64(encoded_photo)
    return tuple(
        PersonFixture(
            name=f"压测人物-{index:05d}",
            number=f"P-{identity.request_id(case_id, index)[-18:]}",
            photo=encoded_photo,
        )
        for index in range(count)
    )


def _person_body(person: PersonFixture) -> dict[str, str]:
    return {"name": person.name, "number": person.number, "photo": person.photo}


def build_person_management_requests(
    targets: NorthboundTargets,
    persons: Sequence[PersonFixture],
    *,
    batch_size: int,
) -> tuple[HttpRequestSpec, ...]:
    if not persons or batch_size <= 0:
        raise ValueError("人物管理负载和 batch_size 必须为正")
    first = persons[0]
    requests: list[HttpRequestSpec] = [
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
        requests.append(
            HttpRequestSpec(
                request_id=f"person-batch-{batch_index}",
                method="POST",
                url=targets.gateway_url("/api/online/face/persons/batch"),
                json_body={"persons": [_person_body(person) for person in batch]},
                work_type="face_person_batch_create",
                expected_lease_acquisition=False,
            )
        )
    requests.extend(
        (
            HttpRequestSpec(
                request_id="person-list",
                method="GET",
                url=targets.gateway_url("/api/online/face/persons"),
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
            HttpRequestSpec(
                request_id="person-delete",
                method="DELETE",
                url=targets.gateway_url("/api/online/face/persons/delete"),
                json_body={"number": persons[-1].number},
                work_type="face_person_delete",
                expected_lease_acquisition=False,
            ),
        )
    )
    return tuple(requests)


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
