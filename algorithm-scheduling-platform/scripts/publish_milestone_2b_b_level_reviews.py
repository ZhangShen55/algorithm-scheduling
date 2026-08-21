#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.aggregate_milestone_2b_cases import publish_json_once
from scripts.milestone_2b_b_level_review import (
    INDEX_FIELDS,
    REVIEW_CASE_IDS,
    _read_secure_object,
    _require_plain_string,
    _validate_reviewed_at,
    _validate_reviewer,
    load_review_index,
    require_external_review_index,
    require_external_review_path,
    require_matching_review_request,
    require_safe_directory,
    validate_evidence_references,
    validate_observed,
)
from scripts.milestone_2b_case_runners.evidence import release_identity


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _atomic_write_index(path: Path, payload: Mapping[str, Any]) -> None:
    parent = path.parent
    require_safe_directory(parent, "B 级质量复核索引目录")
    content = (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    temp = parent / f".{path.name}.{secrets.token_hex(16)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temp, flags, 0o600)
    try:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise RuntimeError("B 级质量复核索引发生短写")
            offset += written
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temp, path)
        directory_descriptor = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temp.exists():
            temp.unlink()


def publish_reviews(
    *,
    release_root: Path,
    index_path: Path,
    review_document: Path,
) -> None:
    _, git_sha = release_identity(release_root)
    require_external_review_index(index_path, release_root)
    require_external_review_path(
        review_document,
        release_root,
        "B 级独立复核输入",
    )
    if review_document == index_path:
        raise RuntimeError("B 级独立复核输入和索引必须使用不同文件")
    document = _read_secure_object(review_document, "B 级独立复核输入")
    if set(document) != {"git_sha", "task_id", "reviews"}:
        raise RuntimeError("B 级独立复核输入字段不完整")
    if document["git_sha"] != git_sha:
        raise RuntimeError("B 级独立复核输入不属于当前 release")
    task_id = document["task_id"]
    if type(task_id) is not str or not task_id:
        raise RuntimeError("B 级独立复核输入缺少 task_id")
    raw_reviews = document["reviews"]
    if type(raw_reviews) is not dict or not raw_reviews:
        raise RuntimeError("B 级独立复核输入没有用例")
    require_matching_review_request(
        release_root=release_root,
        index_path=index_path,
        git_sha=git_sha,
        task_id=task_id,
        case_ids=set(raw_reviews),
    )

    merged: dict[str, dict[str, Any]] = {}
    if index_path.exists() or index_path.is_symlink():
        merged.update(
            load_review_index(
                path=index_path,
                release_root=release_root,
                task_id=task_id,
            )
        )

    for case_id, raw in raw_reviews.items():
        if case_id not in REVIEW_CASE_IDS or type(raw) is not dict:
            raise RuntimeError(f"B 级独立复核输入包含未知用例: {case_id}")
        review = dict(raw)
        required = {
            "status",
            "reviewer",
            "reviewed_at",
            "review_scope",
            "method",
            "observed",
            "evidence",
            "conclusion",
        }
        optional = {"limitation"}
        if not required.issubset(review) or not set(review).issubset(required | optional):
            raise RuntimeError(f"B 级独立复核输入字段不完整: {case_id}")
        if review["status"] != "通过":
            raise RuntimeError(f"B 级独立复核没有通过: {case_id}")
        _validate_reviewer(review["reviewer"], f"{case_id}.reviewer")
        _validate_reviewed_at(review["reviewed_at"], f"{case_id}.reviewed_at")
        for field in ("review_scope", "method", "conclusion"):
            _require_plain_string(review[field], f"{case_id}.{field}")
        if "limitation" in review:
            _require_plain_string(review["limitation"], f"{case_id}.limitation")
        validate_observed(case_id, review["observed"])
        validate_evidence_references(
            release_root=release_root,
            case_id=case_id,
            value=review["evidence"],
        )
        artifact = Path("business/reviews") / f"{case_id}.json"
        artifact_document = {
            "schema_version": 1,
            "case_id": case_id,
            "git_sha": git_sha,
            "task_id": task_id,
            **review,
        }
        publish_json_once(
            release_root=release_root,
            relative_path=artifact,
            document=artifact_document,
        )
        index_item = {
            "status": review["status"],
            "reviewer": review["reviewer"],
            "artifact": artifact.as_posix(),
            "observed": review["observed"],
        }
        if set(index_item) != INDEX_FIELDS:
            raise AssertionError("B 级质量复核索引字段漂移")
        existing = merged.get(case_id)
        if existing is not None and existing != index_item:
            raise RuntimeError(f"B 级质量复核不得改写既有用例: {case_id}")
        merged[case_id] = index_item

    _atomic_write_index(index_path, merged)
    load_review_index(
        path=index_path,
        release_root=release_root,
        task_id=task_id,
        required_case_ids=tuple(raw_reviews),
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--release-root", type=_path, required=True)
    parser.add_argument("--index", type=_path, required=True)
    parser.add_argument("--review-document", type=_path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    publish_reviews(
        release_root=args.release_root,
        index_path=args.index,
        review_document=args.review_document,
    )
    print(
        json.dumps(
            {"status": "通过", "index": str(args.index)},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
