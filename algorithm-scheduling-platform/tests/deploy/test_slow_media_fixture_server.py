from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from http.server import ThreadingHTTPServer

import pytest

from deploy.scripts.slow_media_fixture_server import build_handler


@pytest.fixture
def slow_media_origin() -> Generator[str, None, None]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(0.05))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_health_is_immediate_and_unknown_path_is_404(slow_media_origin: str) -> None:
    started = time.monotonic()
    with urllib.request.urlopen(  # noqa: S310 - 测试只访问本地临时监听
        f"{slow_media_origin}/healthz", timeout=1
    ) as response:
        assert response.status == 200
        assert response.read() == b"ok\n"
    assert time.monotonic() - started < 0.5

    with pytest.raises(urllib.error.HTTPError) as captured:
        urllib.request.urlopen(  # noqa: S310 - 测试只访问本地临时监听
            f"{slow_media_origin}/missing.mp4", timeout=1
        )
    assert captured.value.code == 404


def test_timeout_path_delays_then_returns_504(slow_media_origin: str) -> None:
    started = time.monotonic()
    with pytest.raises(urllib.error.HTTPError) as captured:
        urllib.request.urlopen(  # noqa: S310 - 测试只访问本地临时监听
            f"{slow_media_origin}/timeout.mp4", timeout=1
        )

    assert captured.value.code == 504
    assert time.monotonic() - started >= 0.04


def test_handler_rejects_non_positive_delay() -> None:
    with pytest.raises(ValueError, match="延迟必须大于 0"):
        build_handler(0)
