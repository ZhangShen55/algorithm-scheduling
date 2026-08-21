from __future__ import annotations

from pathlib import Path

from scripts.check_sensitive_logging import find_unsafe_logging


def test_current_service_logging_does_not_pass_request_or_media_objects() -> None:
    workspace_root = Path(__file__).resolve().parents[2]
    assert find_unsafe_logging(workspace_root) == []
