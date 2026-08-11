from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np


ImageWriter = Callable[[str, np.ndarray], bool]


@dataclass(frozen=True, slots=True)
class PhotoPersistenceResult:
    path: str
    attempted: bool
    failed: bool


def persist_person_photo(
    face_image: np.ndarray,
    *,
    name: str,
    number: str,
    project_root: Path,
    enabled: bool,
    image_writer: ImageWriter = cv2.imwrite,
) -> PhotoPersistenceResult:
    if not enabled:
        return PhotoPersistenceResult(path="", attempted=False, failed=False)

    media_dir = project_root / "media" / "person_photos"
    media_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{name}_{number}_{uuid.uuid4().hex[:8]}.jpg"
    save_path = media_dir / filename
    try:
        written = image_writer(str(save_path), face_image)
    except Exception:
        return PhotoPersistenceResult(path="", attempted=True, failed=True)
    if not written:
        return PhotoPersistenceResult(path="", attempted=True, failed=True)
    return PhotoPersistenceResult(
        path=f"/media/person_photos/{filename}",
        attempted=True,
        failed=False,
    )
