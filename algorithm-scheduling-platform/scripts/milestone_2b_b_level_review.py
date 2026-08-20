from __future__ import annotations

import json
import os
import stat
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.aggregate_milestone_2b_cases import (
    B_LEVEL_REVIEW_CASE_IDS,
    publish_json_once,
)
from scripts.milestone_2b_case_runners.evidence import release_identity

REVIEW_CASE_IDS = B_LEVEL_REVIEW_CASE_IDS
PHASE_REVIEW_CASE_IDS: Mapping[str, frozenset[str]] = {
    "offline": REVIEW_CASE_IDS - {"VIS-025"},
    "vision": frozenset({"VIS-025"}),
}
INDEX_FIELDS = {"status", "reviewer", "artifact", "observed"}
MAX_REVIEW_BYTES = 256 * 1024
PLATFORM_ROOT = Path(__file__).resolve().parents[1]


class MissingReviewEvidence(RuntimeError):
    pass


def _require_plain_string(value: object, context: str) -> str:
    if type(value) is not str or not value.strip():
        raise RuntimeError(f"{context} 必须是非空字符串")
    return value


def require_external_review_index(path: Path, release_root: Path) -> None:
    if not path.is_absolute() or not release_root.is_absolute():
        raise RuntimeError("B 级质量复核索引和 release root 必须使用绝对路径")
    if path == release_root or release_root in path.parents:
        raise RuntimeError("B 级质量复核索引必须位于 release 和 Git 证据目录之外")
    if path == PLATFORM_ROOT or PLATFORM_ROOT in path.parents:
        raise RuntimeError("B 级质量复核索引必须位于 Git 工作区之外")


def _require_safe_regular_file(path: Path, context: str) -> os.stat_result:
    if not path.is_absolute():
        raise RuntimeError(f"{context} 必须使用绝对路径")
    current = Path(path.anchor)
    parts = path.parts[1:]
    for index, part in enumerate(parts):
        current /= part
        try:
            metadata = os.lstat(current)
        except OSError as error:
            raise RuntimeError(f"{context} 不存在: {path}") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeError(f"{context} 路径不得包含符号链接: {current}")
        if index < len(parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError(f"{context} 祖先不是目录: {current}")
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"{context} 必须是普通文件")
    if metadata.st_uid != os.getuid():
        raise RuntimeError(f"{context} 必须属于当前 UID")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RuntimeError(f"{context} 权限必须是 0600")
    if metadata.st_nlink != 1:
        raise RuntimeError(f"{context} 必须只有一个硬链接")
    if metadata.st_size > MAX_REVIEW_BYTES:
        raise RuntimeError(f"{context} 超过允许的摘要大小")
    return metadata


def require_safe_directory(path: Path, context: str) -> os.stat_result:
    if not path.is_absolute():
        raise RuntimeError(f"{context} 必须使用绝对路径")
    current = Path(path.anchor)
    metadata = os.lstat(current)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except OSError as error:
            raise RuntimeError(f"{context} 不存在: {path}") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeError(f"{context} 路径不得包含符号链接: {current}")
        if not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError(f"{context} 不是目录: {current}")
    if metadata.st_uid != os.getuid():
        raise RuntimeError(f"{context} 必须属于当前 UID")
    return metadata


def _read_secure_object(path: Path, context: str) -> dict[str, Any]:
    before = _require_safe_regular_file(path, context)
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise RuntimeError(f"{context} 在打开期间发生替换")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_REVIEW_BYTES:
                raise RuntimeError(f"{context} 超过允许的摘要大小")
            chunks.append(chunk)
        opened_after = os.fstat(descriptor)
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{context} 不是合法 JSON") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    after = _require_safe_regular_file(path, context)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or (opened_after.st_dev, opened_after.st_ino, opened_after.st_size)
        != (after.st_dev, after.st_ino, after.st_size)
    ):
        raise RuntimeError(f"{context} 在读取期间发生替换")
    if type(payload) is not dict:
        raise RuntimeError(f"{context} 必须是对象")
    return payload


def _artifact_path(release_root: Path, case_id: str, relative: str) -> Path:
    artifact = Path(relative)
    expected = Path("business/reviews") / f"{case_id}.json"
    if artifact != expected or artifact.is_absolute() or ".." in artifact.parts:
        raise RuntimeError(f"{case_id} B 级质量复核证据路径不符合约束")
    return release_root / artifact


def _validate_artifact(
    *,
    release_root: Path,
    task_id: str,
    case_id: str,
    review: Mapping[str, Any],
) -> None:
    _, git_sha = release_identity(release_root)
    source = _artifact_path(release_root, case_id, str(review["artifact"]))
    document = _read_secure_object(source, f"{case_id} B 级质量复核证据")
    required = {
        "case_id",
        "git_sha",
        "task_id",
        "status",
        "reviewer",
        "observed",
        "review_scope",
        "method",
        "evidence",
        "conclusion",
    }
    if not required.issubset(document):
        raise RuntimeError(f"{case_id} B 级质量复核证据字段不完整")
    if document["case_id"] != case_id or document["git_sha"] != git_sha:
        raise RuntimeError(f"{case_id} B 级质量复核证据不属于当前 release")
    if document["task_id"] != task_id or document["status"] != "通过":
        raise RuntimeError(f"{case_id} B 级质量复核证据不属于当前课程或未通过")
    if document["reviewer"] != review["reviewer"]:
        raise RuntimeError(f"{case_id} B 级质量复核 reviewer 不一致")
    if document["observed"] != review["observed"]:
        raise RuntimeError(f"{case_id} B 级质量复核 observed 不一致")
    _require_plain_string(document["review_scope"], f"{case_id}.review_scope")
    _require_plain_string(document["method"], f"{case_id}.method")
    _require_plain_string(document["conclusion"], f"{case_id}.conclusion")
    evidence = document["evidence"]
    if type(evidence) is not list or not evidence:
        raise RuntimeError(f"{case_id}.evidence 必须是非空数组")
    for index, item in enumerate(evidence):
        _require_plain_string(item, f"{case_id}.evidence[{index}]")


def load_review_index(
    *,
    path: Path,
    release_root: Path,
    task_id: str,
    required_case_ids: Sequence[str] = (),
) -> dict[str, dict[str, Any]]:
    require_external_review_index(path, release_root)
    payload = _read_secure_object(path, "B 级质量复核索引")
    reviews: dict[str, dict[str, Any]] = {}
    for case_id, raw in payload.items():
        if case_id not in REVIEW_CASE_IDS or type(raw) is not dict:
            raise RuntimeError(f"B 级质量复核包含未知或非法用例: {case_id}")
        review = dict(raw)
        if set(review) != INDEX_FIELDS:
            raise RuntimeError(f"B 级质量复核索引字段不完整: {case_id}")
        if review.get("status") != "通过":
            raise RuntimeError(f"B 级质量复核未通过: {case_id}")
        _require_plain_string(review.get("reviewer"), f"{case_id}.reviewer")
        _require_plain_string(review.get("artifact"), f"{case_id}.artifact")
        if type(review.get("observed")) is not dict or not review["observed"]:
            raise RuntimeError(f"B 级质量复核 observed 不完整: {case_id}")
        _validate_artifact(
            release_root=release_root,
            task_id=task_id,
            case_id=case_id,
            review=review,
        )
        reviews[case_id] = review
    missing = set(required_case_ids) - set(reviews)
    if missing:
        raise MissingReviewEvidence(
            "B 级质量复核尚未完成: " + ", ".join(sorted(missing))
        )
    return reviews


def prepare_and_wait_for_reviews(
    *,
    phase: str,
    release_root: Path,
    task_id: str,
    index_path: Path,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> None:
    required = PHASE_REVIEW_CASE_IDS.get(phase)
    if not required:
        return
    require_external_review_index(index_path, release_root)
    _, git_sha = release_identity(release_root)
    request_path = Path("business/review-requests") / f"{phase}.json"
    publish_json_once(
        release_root=release_root,
        relative_path=request_path,
        document={
            "schema_version": 1,
            "phase": phase,
            "git_sha": git_sha,
            "task_id": task_id,
            "required_case_ids": sorted(required),
            "review_index_path": str(index_path),
            "artifact_directory": "business/reviews",
            "status": "等待独立复核",
        },
    )
    print(
        "B_LEVEL_REVIEW_REQUIRED "
        f"phase={phase} task_id={task_id} "
        f"request={release_root / request_path} index={index_path}",
        flush=True,
    )
    deadline = time.monotonic() + timeout_seconds
    while True:
        if index_path.exists() or index_path.is_symlink():
            try:
                load_review_index(
                    path=index_path,
                    release_root=release_root,
                    task_id=task_id,
                    required_case_ids=tuple(required),
                )
            except MissingReviewEvidence:
                pass
            else:
                print(
                    f"B_LEVEL_REVIEW_COMPLETE phase={phase} task_id={task_id}",
                    flush=True,
                )
                return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"{phase} B 级质量复核等待超时")
        time.sleep(min(poll_interval_seconds, remaining))
