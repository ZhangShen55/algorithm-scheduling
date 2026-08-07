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
    encoded = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
    if not encoded:
        return False
    try:
        base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return False
    return True
