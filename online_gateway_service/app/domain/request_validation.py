from __future__ import annotations

import base64
import binascii
from typing import Any

JsonObject = dict[str, Any]


def vbas_route(request_body: JsonObject) -> tuple[str, str] | None:
    stream_type = request_body.get("stream_type")
    if not isinstance(stream_type, str):
        return None
    routes = {
        "student": ("student_behavior", "/ImageDetect/student/v1.0.0"),
        "s": ("student_behavior", "/ImageDetect/student/v1.0.0"),
        "teacher": ("teacher_behavior", "/ImageDetect/teacher/v1.0.0"),
        "t": ("teacher_behavior", "/ImageDetect/teacher/v1.0.0"),
    }
    route = routes.get(stream_type.strip().lower())
    image_list = request_body.get("ImageList")
    if route is None or not isinstance(image_list, list) or not image_list:
        return None
    if not all(_valid_vbas_image(item) for item in image_list):
        return None
    return route


def _valid_vbas_image(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    image_id = item.get("ImageId")
    encoded = item.get("StoragePath")
    return (
        isinstance(image_id, str)
        and bool(image_id)
        and isinstance(encoded, str)
        and is_base64_image(encoded)
    )


def is_base64_image(value: str) -> bool:
    return decoded_base64_size(value) is not None


def decoded_base64_size(
    value: str,
    *,
    max_decoded_bytes: int | None = None,
    allow_data_uri: bool = True,
) -> int | None:
    if value.startswith("data:"):
        if not allow_data_uri or "," not in value:
            return None
        encoded = value.split(",", 1)[1]
    else:
        encoded = value
    if not encoded:
        return None
    estimated_size = (len(encoded) * 3) // 4
    if max_decoded_bytes is not None and estimated_size > max_decoded_bytes + 2:
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    if max_decoded_bytes is not None and len(decoded) > max_decoded_bytes:
        return None
    return len(decoded)
