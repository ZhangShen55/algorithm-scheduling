from __future__ import annotations

import json
import math
import os
import re
import stat
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from hashlib import sha256
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
WORKSPACE_ROOT = PLATFORM_ROOT.parent
REQUEST_FIELDS = {
    "schema_version",
    "phase",
    "git_sha",
    "task_id",
    "required_case_ids",
    "review_index_path",
    "artifact_directory",
    "status",
}
EVIDENCE_REFERENCE_PATTERN = re.compile(
    r"release:(?P<path>[^#]+)#sha256:(?P<digest>[0-9a-f]{64})"
)
GENERIC_REVIEWERS = frozenset(
    {
        "独立质量复核",
        "质量复核",
        "运行控制器",
        "系统",
        "未知",
        "canonical",
        "canonical-controller",
        "controller",
        "reviewer",
        "unknown",
        "n/a",
        "na",
    }
)
OBSERVED_FIELDS: Mapping[str, Mapping[str, str]] = {
    "PPT-012": {
        "reviewed_start_slice_count": "count",
        "black_start_false_positive_count": "count",
    },
    "PPT-013": {
        "reviewed_dynamic_segment_count": "count",
        "slice_count_in_dynamic_segments": "count",
        "obvious_burst_false_positive_count": "count",
    },
    "PPT-014": {
        "reviewed_stable_page_count": "count",
        "obvious_missed_stable_page_count": "count",
    },
    "ASR-012": {
        "reviewed_audio_seconds": "number",
        "reviewed_segment_count": "count",
        "obvious_omitted_span_count": "count",
    },
    "ASR-013": {
        "reviewed_bilingual_segment_count": "count",
        "severe_error_segment_count": "count",
    },
    "VIS-025": {
        "paired_evidence_count": "count",
        "mismatch_count": "count",
    },
}


class MissingReviewEvidence(RuntimeError):
    pass


def _require_plain_string(value: object, context: str) -> str:
    if type(value) is not str or not value.strip():
        raise RuntimeError(f"{context} 必须是非空字符串")
    return value


def require_external_review_path(
    path: Path,
    release_root: Path,
    context: str,
) -> None:
    if not path.is_absolute() or not release_root.is_absolute():
        raise RuntimeError(f"{context} 和 release root 必须使用绝对路径")
    if ".." in path.parts:
        raise RuntimeError(f"{context} 路径不得包含上级目录")
    if path == release_root or release_root in path.parents:
        raise RuntimeError(f"{context} 必须位于 release 之外")
    if path == WORKSPACE_ROOT or WORKSPACE_ROOT in path.parents:
        raise RuntimeError(f"{context} 必须位于整个 Git 工作区之外")


def require_external_review_index(path: Path, release_root: Path) -> None:
    require_external_review_path(path, release_root, "B 级质量复核索引")


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


def _validate_reviewer(value: object, context: str) -> str:
    reviewer = _require_plain_string(value, context).strip()
    if reviewer.casefold() in GENERIC_REVIEWERS:
        raise RuntimeError(f"{context} 不得使用运行控制器或空泛默认身份")
    return reviewer


def _validate_reviewed_at(value: object, context: str) -> str:
    reviewed_at = _require_plain_string(value, context)
    try:
        parsed = datetime.fromisoformat(reviewed_at)
    except ValueError as error:
        raise RuntimeError(f"{context} 必须是有效 ISO 8601 时间") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError(f"{context} 必须包含时区")
    return reviewed_at


def validate_observed(case_id: str, value: object) -> dict[str, Any]:
    if type(value) is not dict:
        raise RuntimeError(f"{case_id}.observed 必须是对象")
    expected = OBSERVED_FIELDS[case_id]
    if set(value) != set(expected):
        raise RuntimeError(f"{case_id}.observed 字段不符合逐案 schema")
    observed = dict(value)
    for field, kind in expected.items():
        item = observed[field]
        if kind == "count":
            if type(item) is not int or item < 0:
                raise RuntimeError(f"{case_id}.observed.{field} 必须是非负整数")
        elif (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            or item < 0
        ):
            raise RuntimeError(f"{case_id}.observed.{field} 必须是有限非负数")
    return observed


def _release_evidence_digest(path: Path, context: str) -> str:
    before = _require_safe_regular_file(path, context)
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise RuntimeError(f"{context} 在打开期间发生替换")
        digest = sha256()
        while chunk := os.read(descriptor, 64 * 1024):
            digest.update(chunk)
        opened_after = os.fstat(descriptor)
    except OSError as error:
        raise RuntimeError(f"{context} 无法读取") from error
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
    return digest.hexdigest()


def validate_evidence_references(
    *,
    release_root: Path,
    case_id: str,
    value: object,
) -> list[str]:
    if type(value) is not list or not value:
        raise RuntimeError(f"{case_id}.evidence 必须是非空数组")
    references: list[str] = []
    for index, item in enumerate(value):
        reference = _require_plain_string(item, f"{case_id}.evidence[{index}]")
        matched = EVIDENCE_REFERENCE_PATTERN.fullmatch(reference)
        if matched is None:
            raise RuntimeError(
                f"{case_id}.evidence[{index}] 必须引用当前 release 证据及 SHA-256"
            )
        relative = Path(matched.group("path"))
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() == ".":
            raise RuntimeError(f"{case_id}.evidence[{index}] 路径不安全")
        source = release_root / relative
        actual = _release_evidence_digest(
            source,
            f"{case_id}.evidence[{index}] 当前 release 证据",
        )
        if actual != matched.group("digest"):
            raise RuntimeError(f"{case_id}.evidence[{index}] 摘要不一致")
        references.append(reference)
    return references


def require_matching_review_request(
    *,
    release_root: Path,
    index_path: Path,
    git_sha: str,
    task_id: str,
    case_ids: set[str],
) -> str:
    phases = [
        phase
        for phase, required in PHASE_REVIEW_CASE_IDS.items()
        if case_ids == set(required)
    ]
    if len(phases) != 1:
        raise RuntimeError("B 级独立复核输入必须恰好覆盖单一已请求阶段")
    phase = phases[0]
    request_path = release_root / "business/review-requests" / f"{phase}.json"
    if not request_path.exists() and not request_path.is_symlink():
        raise RuntimeError(f"{phase} B 级质量复核请求不存在，禁止提前发布")
    request = _read_secure_object(request_path, f"{phase} B 级质量复核请求")
    if set(request) != REQUEST_FIELDS:
        raise RuntimeError(f"{phase} B 级质量复核请求字段不完整")
    expected = {
        "schema_version": 1,
        "phase": phase,
        "git_sha": git_sha,
        "task_id": task_id,
        "required_case_ids": sorted(PHASE_REVIEW_CASE_IDS[phase]),
        "review_index_path": str(index_path),
        "artifact_directory": "business/reviews",
        "status": "等待独立复核",
    }
    for field, expected_value in expected.items():
        if request[field] != expected_value:
            raise RuntimeError(f"{phase} B 级质量复核请求 {field} 不匹配")
    return phase


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
    _validate_reviewer(document["reviewer"], f"{case_id}.reviewer")
    _validate_reviewed_at(document["reviewed_at"], f"{case_id}.reviewed_at")
    _require_plain_string(document["review_scope"], f"{case_id}.review_scope")
    _require_plain_string(document["method"], f"{case_id}.method")
    _require_plain_string(document["conclusion"], f"{case_id}.conclusion")
    validate_observed(case_id, document["observed"])
    validate_evidence_references(
        release_root=release_root,
        case_id=case_id,
        value=document["evidence"],
    )


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
