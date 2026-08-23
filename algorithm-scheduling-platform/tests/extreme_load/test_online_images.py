from __future__ import annotations

import base64
from collections import Counter

import pytest

from scripts.extreme_load.core import NorthboundTargets, ReproducibleIdentity
from scripts.extreme_load.online_images import (
    GatewayPoolRequirement,
    ImageKind,
    OnlineImageFixture,
    build_image_request,
    build_image_staircase,
    build_invalid_image_request,
    build_mixed_image_requests,
    build_s_stream_plan,
    build_s_stream_requests,
    image_boundary_cases,
    image_syntax_and_format_cases,
)

TARGETS = NorthboundTargets(
    control_origin="http://192.168.29.11:18100",
    gateway_origin="http://192.168.29.11:18103",
)
IMAGE = OnlineImageFixture(
    image_id="frame-001",
    encoded=base64.b64encode(b"\x89PNG\r\n\x1a\nimage").decode(),
)


@pytest.mark.parametrize(
    ("kind", "path", "required_key"),
    [
        (ImageKind.VBAS, "/api/online/vbas/analyze", "ImageList"),
        (ImageKind.FACE, "/api/online/face/recognize", "photo"),
        (ImageKind.SCREEN_DET, "/api/online/image-quality/detect", "image"),
        (ImageKind.OCR, "/api/online/ocr/recognize", "image"),
    ],
)
def test_online_image_requests_use_gateway_and_one_image(
    kind: ImageKind,
    path: str,
    required_key: str,
) -> None:
    request = build_image_request(TARGETS, kind, IMAGE, request_index=1)

    assert request.url == f"http://192.168.29.11:18103{path}"
    assert required_key in request.json_body
    assert "rtsp" not in str(request.json_body).lower()
    if kind is ImageKind.VBAS:
        assert len(request.json_body["ImageList"]) == 1


def test_image_staircase_and_mixed_distribution_match_contract() -> None:
    staircase = build_image_staircase()
    assert staircase == (1, 3, 10, 30, 60, 100, 256, 512, 1000)

    requests = build_mixed_image_requests(
        TARGETS,
        ReproducibleIdentity("campaign-online", 3),
        "ONLINE-MIXED",
        IMAGE,
        count=100,
    )
    counts = Counter(request.work_type for request in requests)
    assert counts == {
        "online_vbas": 60,
        "online_face_recognize": 15,
        "online_image_quality": 15,
        "online_ocr": 10,
    }


def test_s_stream_plan_converts_logical_streams_to_expected_rps() -> None:
    plan = build_s_stream_plan(stream_count=1000, interval_seconds=5)

    assert plan.expected_rps == 200
    assert plan.single_image_per_request
    assert plan.performs_rtsp_ingestion is False
    scheduled = build_s_stream_requests(
        TARGETS,
        ReproducibleIdentity("campaign-online", 3),
        "ONLINE-S-STREAMS",
        IMAGE,
        stream_count=100,
        interval_seconds=5,
        frame_rounds=2,
    )
    assert len(scheduled) == 200
    assert {item.scheduled_offset_seconds for item in scheduled} == {0.0, 5.0}


def test_base64_boundaries_include_regular_49mib_and_over_limit() -> None:
    cases = image_boundary_cases()

    assert [case.decoded_bytes for case in cases] == [524_288, 5_242_880, 51_380_224, 52_428_801]
    assert [case.expect_rejected for case in cases] == [False, False, False, True]


def test_invalid_base64_is_rejected_before_request_generation() -> None:
    with pytest.raises(ValueError, match="Base64"):
        OnlineImageFixture(image_id="bad", encoded="not-base64")

    cases = image_syntax_and_format_cases()
    requests = [
        build_invalid_image_request(
            TARGETS,
            ImageKind.OCR,
            request_index=index,
            invalid_encoded=case.encoded,
        )
        for index, case in enumerate(cases)
    ]
    assert all(request.expected_business_rejection for request in requests)
    assert all(request.expected_lease_acquisition is False for request in requests)


def test_required_gateway_pool_contract_is_explicit() -> None:
    requirement = GatewayPoolRequirement()
    assert requirement.max_connections == 2048
    assert requirement.max_keepalive_connections == 512
    assert requirement.pool_timeout_seconds > 0
