import base64
from pathlib import Path

import pytest


PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/"
    "AAX+Av4N70a4AAAAAElFTkSuQmCC"
)


@pytest.fixture
def image_base64() -> str:
    return PNG_1X1_BASE64


@pytest.fixture
def settings_file(tmp_path: Path) -> Path:
    detection_dir = tmp_path / "models" / "PP-OCRv6_medium_det"
    recognition_dir = tmp_path / "models" / "PP-OCRv6_medium_rec"
    detection_dir.mkdir(parents=True)
    recognition_dir.mkdir(parents=True)
    config = tmp_path / "config.toml"
    config.write_text(
        """
[application]
name = "test-ocr-service"
version = "OCR_TEST"

[server]
host = "127.0.0.1"
port = 8866
workers = 1

[ocr]
device = "cpu"
detection_model_dir = "models/PP-OCRv6_medium_det"
recognition_model_dir = "models/PP-OCRv6_medium_rec"
recognition_batch_size = 1
cpu_threads = 2
enable_mkldnn = true
enable_hpi = false
max_concurrency = 1
image_max_bytes = 1048576

[ocr.detection]
limit_side_len = 960
threshold = 0.3
box_threshold = 0.6
unclip_ratio = 1.5

[formula]
enabled = false
layout_model_dir = "models/PP-DocLayout_plus-L"
recognition_model_dir = "models/PP-FormulaNet_plus-M"
recognition_batch_size = 1
layout_threshold = 0.5

[logging]
level = "INFO"
directory = "logs"
max_size_mb = 10
backup_count = 2
""".strip(),
        encoding="utf-8",
    )
    return config


@pytest.fixture
def image_bytes(image_base64: str) -> bytes:
    return base64.b64decode(image_base64)
