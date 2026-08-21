from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.milestone_2b_b_level_review import (
    load_review_index,
    prepare_and_wait_for_reviews,
)
from scripts.publish_milestone_2b_b_level_reviews import publish_reviews


def _release_root(tmp_path: Path) -> Path:
    release_root = tmp_path / "v1.0_260820" / ("a" * 40)
    release_root.mkdir(parents=True)
    return release_root


def _review_document(
    path: Path,
    *,
    git_sha: str,
    task_id: str,
    case_ids: tuple[str, ...] = ("VIS-025",),
) -> None:
    document = {
        "git_sha": git_sha,
        "task_id": task_id,
        "reviews": {
            case_id: {
                "status": "通过",
                "reviewer": "独立质量复核",
                "reviewed_at": "2026-08-20T08:00:00+00:00",
                "review_scope": "当前 release 的真实课程结果",
                "method": "核对当前课程证据与对应源视频时间点",
                "observed": {"paired_evidence_count": 2, "mismatch_count": 0},
                "evidence": ["受限证据编号 review-001", "sha256:abc"],
                "conclusion": "没有发现证据图片与行为时间明显不一致。",
            }
            for case_id in case_ids
        },
    }
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    path.chmod(0o600)


def test_review_publisher_creates_current_release_artifact_and_secure_index(
    tmp_path: Path,
) -> None:
    release_root = _release_root(tmp_path)
    index = tmp_path / "restricted/reviews.json"
    index.parent.mkdir()
    review_document = tmp_path / "restricted/input.json"
    _review_document(
        review_document,
        git_sha=release_root.name,
        task_id="course-001",
    )

    publish_reviews(
        release_root=release_root,
        index_path=index,
        review_document=review_document,
    )

    reviews = load_review_index(
        path=index,
        release_root=release_root,
        task_id="course-001",
        required_case_ids=("VIS-025",),
    )
    artifact = release_root / reviews["VIS-025"]["artifact"]
    artifact_document = json.loads(artifact.read_text(encoding="utf-8"))
    assert artifact_document["git_sha"] == release_root.name
    assert artifact_document["task_id"] == "course-001"
    assert artifact_document["case_id"] == "VIS-025"
    assert index.stat().st_mode & 0o777 == 0o600
    assert index.stat().st_nlink == 1
    assert artifact.stat().st_mode & 0o777 == 0o600
    assert artifact.stat().st_nlink == 1


def test_review_wait_publishes_request_and_accepts_complete_index(tmp_path: Path) -> None:
    release_root = _release_root(tmp_path)
    index = tmp_path / "restricted/reviews.json"
    index.parent.mkdir()
    review_document = tmp_path / "restricted/input.json"
    offline_cases = (
        "PPT-012",
        "PPT-013",
        "PPT-014",
        "ASR-012",
        "ASR-013",
    )
    _review_document(
        review_document,
        git_sha=release_root.name,
        task_id="course-001",
        case_ids=offline_cases,
    )
    publish_reviews(
        release_root=release_root,
        index_path=index,
        review_document=review_document,
    )

    prepare_and_wait_for_reviews(
        phase="offline",
        release_root=release_root,
        task_id="course-001",
        index_path=index,
        timeout_seconds=1,
        poll_interval_seconds=0.01,
    )

    request = release_root / "business/review-requests/offline.json"
    payload = json.loads(request.read_text(encoding="utf-8"))
    assert payload["required_case_ids"] == sorted(offline_cases)
    assert payload["git_sha"] == release_root.name
    assert payload["task_id"] == "course-001"


def test_review_index_rejects_stale_task_and_unsafe_metadata(tmp_path: Path) -> None:
    release_root = _release_root(tmp_path)
    index = tmp_path / "restricted/reviews.json"
    index.parent.mkdir()
    review_document = tmp_path / "restricted/input.json"
    _review_document(
        review_document,
        git_sha=release_root.name,
        task_id="course-old",
    )
    publish_reviews(
        release_root=release_root,
        index_path=index,
        review_document=review_document,
    )

    with pytest.raises(RuntimeError, match="当前课程"):
        load_review_index(
            path=index,
            release_root=release_root,
            task_id="course-new",
            required_case_ids=("VIS-025",),
        )

    index.chmod(0o644)
    with pytest.raises(RuntimeError, match="0600"):
        load_review_index(
            path=index,
            release_root=release_root,
            task_id="course-old",
        )


def test_review_index_rejects_symlink_and_additional_hardlink(tmp_path: Path) -> None:
    release_root = _release_root(tmp_path)
    restricted = tmp_path / "restricted"
    restricted.mkdir()
    index = restricted / "reviews.json"
    review_document = restricted / "input.json"
    _review_document(
        review_document,
        git_sha=release_root.name,
        task_id="course-001",
    )
    publish_reviews(
        release_root=release_root,
        index_path=index,
        review_document=review_document,
    )

    linked = restricted / "reviews-linked.json"
    os.link(index, linked)
    with pytest.raises(RuntimeError, match="硬链接"):
        load_review_index(
            path=index,
            release_root=release_root,
            task_id="course-001",
        )
    linked.unlink()

    alias = restricted / "reviews-alias.json"
    alias.symlink_to(index)
    with pytest.raises(RuntimeError, match="符号链接"):
        load_review_index(
            path=alias,
            release_root=release_root,
            task_id="course-001",
        )


def test_review_index_must_stay_outside_release_tree(tmp_path: Path) -> None:
    release_root = _release_root(tmp_path)
    index = release_root / "business/b-level-reviews.json"

    with pytest.raises(RuntimeError, match="release"):
        prepare_and_wait_for_reviews(
            phase="vision",
            release_root=release_root,
            task_id="course-001",
            index_path=index,
            timeout_seconds=0.01,
            poll_interval_seconds=0.01,
        )
