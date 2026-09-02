#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

GIT_SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class SourceManifestError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SourceRoot:
    source: Path
    logical: PurePosixPath


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_revision(value: str) -> str:
    if GIT_SHA_PATTERN.fullmatch(value) is None:
        raise SourceManifestError("Git revision 必须是完整的 40 位十六进制 SHA")
    return value.lower()


def _parse_source(value: str) -> SourceRoot:
    source_value, separator, logical_value = value.partition("=")
    if not separator or not source_value or not logical_value:
        raise SourceManifestError("source 必须使用 SOURCE=LOGICAL 格式")
    source = Path(source_value).expanduser().resolve(strict=True)
    logical = PurePosixPath(logical_value)
    if not source.is_dir():
        raise SourceManifestError(f"受管源码目录不存在: {source}")
    if logical.is_absolute() or ".." in logical.parts or logical == PurePosixPath("."):
        raise SourceManifestError(f"逻辑源码路径不安全: {logical_value}")
    return SourceRoot(source=source, logical=logical)


def build_manifest(sources: list[SourceRoot], revision: str) -> dict[str, Any]:
    revision = _normalize_revision(revision)
    if not sources:
        raise SourceManifestError("至少需要一个受管源码目录")
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in sorted(sources, key=lambda item: item.logical.as_posix()):
        for path in sorted(root.source.rglob("*")):
            relative = path.relative_to(root.source)
            if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            if path.is_symlink():
                raise SourceManifestError(f"受管源码不得包含符号链接: {path}")
            if not path.is_file():
                continue
            logical_path = (root.logical / PurePosixPath(relative.as_posix())).as_posix()
            if logical_path in seen:
                raise SourceManifestError(f"受管源码逻辑路径重复: {logical_path}")
            seen.add(logical_path)
            files.append(
                {
                    "path": logical_path,
                    "sha256": _sha256(path),
                    "size": path.stat().st_size,
                }
            )
    if not files:
        raise SourceManifestError("受管源码目录中没有文件")
    return {
        "schema_version": 1,
        "revision": revision,
        "files": sorted(files, key=lambda item: item["path"]),
    }


def manifest_bytes(document: dict[str, Any]) -> bytes:
    serialized = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{serialized}\n".encode()


def write_manifest(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(manifest_bytes(document))
    os.replace(temporary, path)


def _load_manifest(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        document = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceManifestError(f"无法读取源码 manifest: {path}") from exc
    if not isinstance(document, dict):
        raise SourceManifestError("源码 manifest 根节点必须是对象")
    return document, raw


def verify_manifest(
    *,
    sources: list[SourceRoot],
    revision: str,
    manifest_path: Path,
    digest_path: Path,
    expected_manifest_path: Path | None = None,
) -> None:
    embedded, raw = _load_manifest(manifest_path)
    actual = build_manifest(sources, revision)
    if embedded != actual:
        raise SourceManifestError("容器内实际受管源码与嵌入 manifest 不一致")
    try:
        declared_digest = digest_path.read_text(encoding="ascii").strip().split()[0]
    except (OSError, IndexError, UnicodeError) as exc:
        raise SourceManifestError("无法读取源码 manifest 摘要") from exc
    actual_digest = hashlib.sha256(raw).hexdigest()
    if SHA256_PATTERN.fullmatch(declared_digest) is None or declared_digest != actual_digest:
        raise SourceManifestError("源码 manifest 摘要不一致")
    if expected_manifest_path is not None:
        expected, _ = _load_manifest(expected_manifest_path)
        if expected != embedded:
            raise SourceManifestError("镜像源码 manifest 与目标 Git checkout 不一致")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", allow_abbrev=False)
    generate.add_argument("--revision", required=True)
    generate.add_argument("--source", action="append", required=True)
    generate.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify", allow_abbrev=False)
    verify.add_argument("--revision", required=True)
    verify.add_argument("--source", action="append", required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--digest", type=Path, required=True)
    verify.add_argument("--expected-manifest", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        sources = [_parse_source(value) for value in args.source]
        if args.command == "generate":
            document = build_manifest(sources, args.revision)
            write_manifest(args.output, document)
            print(hashlib.sha256(manifest_bytes(document)).hexdigest())
        else:
            verify_manifest(
                sources=sources,
                revision=args.revision,
                manifest_path=args.manifest,
                digest_path=args.digest,
                expected_manifest_path=args.expected_manifest,
            )
    except SourceManifestError as exc:
        print(f"source-manifest: FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
