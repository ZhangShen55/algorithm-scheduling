from pathlib import Path

import numpy as np

from app.services.person_photo import persist_person_photo


def test_disabled_photo_persistence_does_not_touch_filesystem(tmp_path: Path) -> None:
    def unexpected_write(path: str, image: np.ndarray) -> bool:
        raise AssertionError(f"不应写入人物图片: {path}, {image.shape}")

    result = persist_person_photo(
        np.zeros((112, 112, 3), dtype=np.uint8),
        name="测试教师",
        number="teacher-001",
        project_root=tmp_path,
        enabled=False,
        image_writer=unexpected_write,
    )

    assert result.path == ""
    assert result.attempted is False
    assert result.failed is False


def test_enabled_photo_persistence_returns_public_media_path(tmp_path: Path) -> None:
    written_paths: list[Path] = []

    def successful_write(path: str, image: np.ndarray) -> bool:
        written_paths.append(Path(path))
        return True

    result = persist_person_photo(
        np.zeros((112, 112, 3), dtype=np.uint8),
        name="测试教师",
        number="teacher-001",
        project_root=tmp_path,
        enabled=True,
        image_writer=successful_write,
    )

    assert result.attempted is True
    assert result.failed is False
    assert result.path.startswith("/media/person_photos/测试教师_teacher-001_")
    assert written_paths[0].parent == tmp_path / "media" / "person_photos"


def test_enabled_photo_persistence_reports_writer_failure(tmp_path: Path) -> None:
    result = persist_person_photo(
        np.zeros((112, 112, 3), dtype=np.uint8),
        name="测试教师",
        number="teacher-001",
        project_root=tmp_path,
        enabled=True,
        image_writer=lambda _path, _image: False,
    )

    assert result.path == ""
    assert result.attempted is True
    assert result.failed is True
