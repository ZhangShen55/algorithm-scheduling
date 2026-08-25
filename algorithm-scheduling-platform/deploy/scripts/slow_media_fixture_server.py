#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


def build_handler(delay_seconds: float) -> type[BaseHTTPRequestHandler]:
    if delay_seconds <= 0:
        raise ValueError("慢媒体响应延迟必须大于 0")

    class SlowMediaHandler(BaseHTTPRequestHandler):
        server_version = "CampaignSlowMedia/1.0"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 固定入口
            path = urlsplit(self.path).path
            if path == "/healthz":
                self._respond(HTTPStatus.OK, b"ok\n")
                return
            if path == "/timeout.mp4":
                time.sleep(delay_seconds)
                self._respond(HTTPStatus.GATEWAY_TIMEOUT, b"")
                return
            self._respond(HTTPStatus.NOT_FOUND, b"")

        def log_message(self, format: str, *args: object) -> None:
            # 只记录方法、路径和状态，不输出请求头或媒体内容。
            super().log_message(format, *args)

        def _respond(self, status: HTTPStatus, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

    return SlowMediaHandler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="里程碑 2B 受控慢媒体探针")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--delay-seconds", type=float, default=5.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not 1 <= args.port <= 65535:
        raise ValueError("监听端口必须位于 1..65535")
    server = ThreadingHTTPServer(
        (args.host, args.port),
        build_handler(args.delay_seconds),
    )
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
