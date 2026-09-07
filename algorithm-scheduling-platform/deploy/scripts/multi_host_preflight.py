#!/usr/bin/env python3
"""Preflight checks for cross-host operator URLs and shared NFS paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path


class PreflightError(RuntimeError):
    pass


def _write_and_sync(path: Path, content: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o660)
    try:
        os.write(fd, content)
        os.fsync(fd)
    finally:
        os.close(fd)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def check_shared_path(path: Path, *, protect_result: bool) -> dict[str, object]:
    if not path.is_dir() or path.is_symlink():
        raise PreflightError(f"共享目录必须是非符号链接目录: {path}")
    stat_result = path.stat()
    token = uuid.uuid4().hex
    probe = path / f".multi-host-preflight-{token}"
    renamed = path / f".multi-host-preflight-{token}.renamed"
    content = f"{path}:{token}".encode()
    try:
        _write_and_sync(probe, content)
        if probe.read_bytes() != content:
            raise PreflightError(f"写入后读取不一致: {path}")
        probe.rename(renamed)
        if renamed.read_bytes() != content:
            raise PreflightError(f"同目录原子 rename 验证失败: {path}")
    finally:
        for candidate in (probe, renamed):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass
    result = {
        "path": str(path),
        "uid": stat_result.st_uid,
        "gid": stat_result.st_gid,
        "mode": oct(stat_result.st_mode & 0o777),
        "filesystem": os.statvfs(path).f_fsid,
        "fsync": True,
        "atomic_rename": True,
        "delete": True,
    }
    if protect_result:
        result["result_persistence_path"] = str(path)
        result["cleanup_must_exclude"] = True
    return result


def create_peer_probe(path: Path) -> dict[str, str]:
    token = uuid.uuid4().hex
    marker = path / f".multi-host-peer-{token}"
    _write_and_sync(marker, token.encode())
    return {"marker": str(marker), "sha256": hashlib.sha256(token.encode()).hexdigest()}


def verify_peer_probe(marker: Path, expected_sha256: str) -> dict[str, object]:
    if not marker.is_file() or marker.is_symlink():
        raise PreflightError(f"远程主机不可见共享探针: {marker}")
    digest = hashlib.sha256(marker.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise PreflightError(f"共享探针内容不一致: {marker}")
    marker.unlink()
    return {"marker": str(marker), "cross_host_visible": True, "delete": True}


def check_endpoints(args: argparse.Namespace) -> None:
    results = []
    for endpoint in args.endpoint:
        request = urllib.request.Request(endpoint, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                body = json.load(response)
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise PreflightError(f"地址不可达 {endpoint}: {exc}") from exc
        if response.status != 200 or not isinstance(body, dict):
            raise PreflightError(f"地址返回异常 {endpoint}: HTTP {response.status}")
        expected = args.host_id.get(endpoint)
        if endpoint.endswith("/gpu") and body.get("host_id") != expected:
            raise PreflightError(
                f"GPU 主机身份不一致 {endpoint}: expected={expected}, "
                f"actual={body.get('host_id')}"
            )
        results.append({
            "endpoint": endpoint,
            "status": response.status,
            "host_id": body.get("host_id"),
        })
    print(json.dumps({
        "status": "ok",
        "hostname": socket.gethostname(),
        "endpoints": results,
    }, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    shared = subparsers.add_parser("shared-path")
    shared.add_argument("path", type=Path)
    shared.add_argument("--result", action="store_true")
    peer_create = subparsers.add_parser("create-peer-probe")
    peer_create.add_argument("path", type=Path)
    peer_verify = subparsers.add_parser("verify-peer-probe")
    peer_verify.add_argument("marker", type=Path)
    peer_verify.add_argument("sha256")
    endpoint = subparsers.add_parser("endpoints")
    endpoint.add_argument("--timeout", type=float, default=3.0)
    endpoint.add_argument("--endpoint", action="append", required=True)
    endpoint.add_argument("--host-id", action="append", default=[], metavar="URL=IP")
    args = parser.parse_args()
    try:
        if args.command == "shared-path":
            print(json.dumps(
                check_shared_path(args.path, protect_result=args.result),
                ensure_ascii=False,
            ))
        elif args.command == "create-peer-probe":
            print(json.dumps(create_peer_probe(args.path), ensure_ascii=False))
        elif args.command == "verify-peer-probe":
            print(json.dumps(verify_peer_probe(args.marker, args.sha256), ensure_ascii=False))
        else:
            args.host_id = dict(item.split("=", 1) for item in args.host_id)
            check_endpoints(args)
    except (OSError, ValueError, PreflightError) as exc:
        print(f"multi-host-preflight: FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
