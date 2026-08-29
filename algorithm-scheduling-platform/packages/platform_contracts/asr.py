from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def asr_params_fingerprint(effective_params: Mapping[str, Any]) -> str:
    """为完整 ASR 参数生成与字段顺序无关的稳定指纹。"""

    canonical = json.dumps(
        dict(effective_params),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()

