from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from enum import StrEnum

from .core import HttpRequestSpec, NorthboundTargets, ReproducibleIdentity


class ImageKind(StrEnum):
    VBAS = "vbas"
    FACE = "face"
    SCREEN_DET = "screen_det"
    OCR = "ocr"

    @property
    def work_type(self) -> str:
        return {
            ImageKind.VBAS: "online_vbas",
            ImageKind.FACE: "online_face_recognize",
            ImageKind.SCREEN_DET: "online_image_quality",
            ImageKind.OCR: "online_ocr",
        }[self]


def _decode_base64(value: str) -> bytes:
    if value.startswith("data:"):
        header, separator, encoded = value.partition(",")
        if (
            not separator
            or not header.lower().startswith("data:image/")
            or not header.lower().endswith(";base64")
        ):
            raise ValueError("图片 data URI 必须是 image/*;base64 格式")
    else:
        encoded = value
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("图片不是合法 Base64") from exc
    if not decoded:
        raise ValueError("图片 Base64 不能为空")
    return decoded


def _supported_image_format(decoded: bytes) -> bool:
    return (
        decoded.startswith(b"\x89PNG\r\n\x1a\n")
        or decoded.startswith(b"\xff\xd8\xff")
        or decoded.startswith((b"GIF87a", b"GIF89a", b"BM"))
        or (len(decoded) >= 12 and decoded[:4] == b"RIFF" and decoded[8:12] == b"WEBP")
    )


@dataclass(frozen=True)
class OnlineImageFixture:
    image_id: str
    encoded: str

    def __post_init__(self) -> None:
        if not self.image_id:
            raise ValueError("image_id 不能为空")
        _decode_base64(self.encoded)

    @property
    def decoded_bytes(self) -> int:
        return len(_decode_base64(self.encoded))

    @property
    def supported_format(self) -> bool:
        return _supported_image_format(_decode_base64(self.encoded))


def build_image_request(
    targets: NorthboundTargets,
    kind: ImageKind,
    fixture: OnlineImageFixture,
    *,
    request_index: int,
    trace_id: str | None = None,
) -> HttpRequestSpec:
    expected_rejection = fixture.decoded_bytes > 52_428_800 or not fixture.supported_format
    if kind is ImageKind.VBAS:
        path = "/api/online/vbas/analyze"
        body: dict[str, object] = {
            "stream_type": "student",
            "ImageList": [
                {
                    "ImageId": fixture.image_id,
                    "StoragePath": fixture.encoded,
                }
            ],
        }
    elif kind is ImageKind.FACE:
        path = "/api/online/face/recognize"
        body = {"photo": fixture.encoded}
    elif kind is ImageKind.SCREEN_DET:
        path = "/api/online/image-quality/detect"
        body = {"image": fixture.encoded}
    else:
        path = "/api/online/ocr/recognize"
        body = {
            "image_id": fixture.image_id,
            "image": fixture.encoded,
            "enable_formula": False,
        }
    return HttpRequestSpec(
        request_id=f"{kind.value}-{request_index}",
        method="POST",
        url=targets.gateway_url(path),
        json_body=body,
        headers={} if trace_id is None else {"X-Trace-ID": trace_id},
        work_type=kind.work_type,
        expected_business_rejection=expected_rejection,
        expected_lease_acquisition=not expected_rejection,
    )


def build_invalid_image_request(
    targets: NorthboundTargets,
    kind: ImageKind,
    *,
    request_index: int,
    invalid_encoded: str,
) -> HttpRequestSpec:
    fixture_id = f"invalid-{request_index}"
    if kind is ImageKind.VBAS:
        body: dict[str, object] = {
            "stream_type": "student",
            "ImageList": [{"ImageId": fixture_id, "StoragePath": invalid_encoded}],
        }
        path = "/api/online/vbas/analyze"
    elif kind is ImageKind.FACE:
        body = {"photo": invalid_encoded}
        path = "/api/online/face/recognize"
    elif kind is ImageKind.SCREEN_DET:
        body = {"image": invalid_encoded}
        path = "/api/online/image-quality/detect"
    else:
        body = {"image_id": fixture_id, "image": invalid_encoded, "enable_formula": False}
        path = "/api/online/ocr/recognize"
    return HttpRequestSpec(
        request_id=f"{kind.value}-invalid-{request_index}",
        method="POST",
        url=targets.gateway_url(path),
        json_body=body,
        work_type=f"{kind.work_type}:invalid_image",
        expected_business_rejection=True,
        expected_lease_acquisition=False,
    )


def build_image_staircase() -> tuple[int, ...]:
    return (1, 3, 10, 30, 60, 100, 256, 512, 1000)


def build_mixed_image_requests(
    targets: NorthboundTargets,
    identity: ReproducibleIdentity,
    case_id: str,
    fixture: OnlineImageFixture,
    *,
    count: int,
    weights: dict[ImageKind, int] | None = None,
) -> tuple[HttpRequestSpec, ...]:
    if count <= 0:
        raise ValueError("count 必须为正数")
    selected_weights = (
        {
            ImageKind.VBAS: 60,
            ImageKind.FACE: 15,
            ImageKind.SCREEN_DET: 15,
            ImageKind.OCR: 10,
        }
        if weights is None
        else weights
    )
    if not selected_weights or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in selected_weights.values()
    ):
        raise ValueError("图片负载权重不合法")
    total_weight = sum(selected_weights.values())
    if total_weight <= 0:
        raise ValueError("图片负载权重总和必须为正数")
    exact = {kind: count * weight / total_weight for kind, weight in selected_weights.items()}
    allocations = {kind: int(value) for kind, value in exact.items()}
    remaining = count - sum(allocations.values())
    for kind in sorted(
        selected_weights,
        key=lambda item: (-(exact[item] - allocations[item]), item.value),
    ):
        if remaining == 0:
            break
        allocations[kind] += 1
        remaining -= 1
    kinds = [kind for kind, allocated in allocations.items() for _ in range(allocated)]
    identity.random(f"{case_id}:image-mix").shuffle(kinds)
    return tuple(
        build_image_request(
            targets,
            kind,
            fixture,
            request_index=index,
            trace_id=identity.trace_id(case_id, index),
        )
        for index, kind in enumerate(kinds)
    )


@dataclass(frozen=True)
class SStreamPlan:
    stream_count: int
    interval_seconds: int
    expected_rps: float
    single_image_per_request: bool = True
    performs_rtsp_ingestion: bool = False


@dataclass(frozen=True)
class ScheduledImageRequest:
    stream_id: str
    scheduled_offset_seconds: float
    request: HttpRequestSpec


def build_s_stream_plan(stream_count: int, interval_seconds: int) -> SStreamPlan:
    if stream_count not in {100, 300, 1000}:
        raise ValueError("S 流数量只允许 100/300/1000")
    if interval_seconds not in {5, 10, 30}:
        raise ValueError("S 流抽样间隔只允许 5/10/30 秒")
    return SStreamPlan(
        stream_count=stream_count,
        interval_seconds=interval_seconds,
        expected_rps=stream_count / interval_seconds,
    )


def build_s_stream_requests(
    targets: NorthboundTargets,
    identity: ReproducibleIdentity,
    case_id: str,
    fixture: OnlineImageFixture,
    *,
    stream_count: int,
    interval_seconds: int,
    frame_rounds: int,
) -> tuple[ScheduledImageRequest, ...]:
    build_s_stream_plan(stream_count, interval_seconds)
    if frame_rounds <= 0:
        raise ValueError("frame_rounds 必须为正数")
    return tuple(
        ScheduledImageRequest(
            stream_id=f"s-stream-{stream_index}",
            scheduled_offset_seconds=float(round_index * interval_seconds),
            request=build_image_request(
                targets,
                ImageKind.VBAS,
                fixture,
                request_index=round_index * stream_count + stream_index,
                trace_id=identity.trace_id(
                    case_id,
                    round_index * stream_count + stream_index,
                ),
            ),
        )
        for round_index in range(frame_rounds)
        for stream_index in range(stream_count)
    )


@dataclass(frozen=True)
class ImageBoundaryCase:
    decoded_bytes: int
    expect_rejected: bool
    expect_lease_acquisition: bool


def image_boundary_cases() -> tuple[ImageBoundaryCase, ...]:
    return (
        ImageBoundaryCase(512 * 1024, False, True),
        ImageBoundaryCase(5 * 1024 * 1024, False, True),
        ImageBoundaryCase(49 * 1024 * 1024, False, True),
        ImageBoundaryCase(50 * 1024 * 1024 + 1, True, False),
    )


@dataclass(frozen=True)
class ImageNegativeCase:
    name: str
    encoded: str
    expect_rejected: bool = True
    expect_lease_acquisition: bool = False


def image_syntax_and_format_cases() -> tuple[ImageNegativeCase, ...]:
    return (
        ImageNegativeCase("invalid_base64", "not-base64!"),
        ImageNegativeCase("unsupported_format", base64.b64encode(b"not-an-image").decode()),
        ImageNegativeCase("invalid_data_uri", "data:text/plain;base64,QQ=="),
    )


@dataclass(frozen=True)
class GatewayPoolRequirement:
    max_connections: int = 2048
    max_keepalive_connections: int = 512
    pool_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.max_connections < 1000:
            raise ValueError("Gateway 连接池不足以执行 1000 合法并发")
        if not 0 < self.max_keepalive_connections <= self.max_connections:
            raise ValueError("Gateway keepalive 连接池不合法")
        if self.pool_timeout_seconds <= 0:
            raise ValueError("Gateway pool timeout 必须有界")
