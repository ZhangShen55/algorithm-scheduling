from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path

import pytest

from scripts import milestone_2b_b_level_review as review_module
from scripts.aggregate_milestone_2b_cases import publish_json_once
from scripts.milestone_2b_b_level_review import (
    PHASE_REVIEW_CASE_IDS,
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
    evidence_path: Path,
    case_ids: tuple[str, ...] = ("VIS-025",),
) -> None:
    phase = "vision" if set(case_ids) == {"VIS-025"} else "offline"
    evidence_reference = (
        f"release:business/review-requests/{phase}.json"
        f"#sha256:{sha256(evidence_path.read_bytes()).hexdigest()}"
    )
    document = {
        "git_sha": git_sha,
        "task_id": task_id,
        "reviews": {
            case_id: {
                "status": "通过",
                "reviewer": "qa-reviewer-001",
                "reviewed_at": "2026-08-20T08:00:00+00:00",
                "review_scope": "当前 release 的真实课程结果",
                "method": "核对当前课程证据与对应源视频时间点",
                "observed": _observed(case_id),
                "evidence": [evidence_reference],
                "conclusion": "没有发现证据图片与行为时间明显不一致。",
            }
            for case_id in case_ids
        },
    }
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    path.chmod(0o600)


def _observed(case_id: str) -> dict[str, int | float]:
    return {
        "PPT-012": {
            "reviewed_start_slice_count": 1,
            "black_start_false_positive_count": 0,
        },
        "PPT-013": {
            "reviewed_dynamic_segment_count": 1,
            "slice_count_in_dynamic_segments": 0,
            "obvious_burst_false_positive_count": 0,
        },
        "PPT-014": {
            "reviewed_stable_page_count": 1,
            "obvious_missed_stable_page_count": 0,
        },
        "ASR-012": {
            "reviewed_audio_seconds": 30.0,
            "reviewed_segment_count": 1,
            "obvious_omitted_span_count": 0,
        },
        "ASR-013": {
            "reviewed_bilingual_segment_count": 1,
            "severe_error_segment_count": 0,
        },
        "VIS-025": {"paired_evidence_count": 2, "mismatch_count": 0},
    }[case_id]


def _publish_review_request(
    release_root: Path,
    index: Path,
    *,
    phase: str,
    task_id: str,
) -> Path:
    relative_path = Path("business/review-requests") / f"{phase}.json"
    publish_json_once(
        release_root=release_root,
        relative_path=relative_path,
        document={
            "schema_version": 1,
            "phase": phase,
            "git_sha": release_root.name,
            "task_id": task_id,
            "required_case_ids": sorted(PHASE_REVIEW_CASE_IDS[phase]),
            "review_index_path": str(index),
            "artifact_directory": "business/reviews",
            "status": "等待独立复核",
        },
    )
    return release_root / relative_path


def _update_review_document(path: Path, case_id: str, **changes: object) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["reviews"][case_id].update(changes)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    path.chmod(0o600)


def test_review_publisher_creates_current_release_artifact_and_secure_index(
    tmp_path: Path,
) -> None:
    release_root = _release_root(tmp_path)
    index = tmp_path / "restricted/reviews.json"
    index.parent.mkdir()
    review_document = tmp_path / "restricted/input.json"
    request = _publish_review_request(
        release_root,
        index,
        phase="vision",
        task_id="course-001",
    )
    _review_document(
        review_document,
        git_sha=release_root.name,
        task_id="course-001",
        evidence_path=request,
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


def test_review_publisher_merges_offline_then_vision_requests(tmp_path: Path) -> None:
    release_root = _release_root(tmp_path)
    restricted = tmp_path / "restricted"
    restricted.mkdir()
    index = restricted / "reviews.json"
    offline_cases = tuple(sorted(PHASE_REVIEW_CASE_IDS["offline"]))

    offline_request = _publish_review_request(
        release_root,
        index,
        phase="offline",
        task_id="course-001",
    )
    offline_document = restricted / "offline-input.json"
    _review_document(
        offline_document,
        git_sha=release_root.name,
        task_id="course-001",
        evidence_path=offline_request,
        case_ids=offline_cases,
    )
    publish_reviews(
        release_root=release_root,
        index_path=index,
        review_document=offline_document,
    )

    vision_request = _publish_review_request(
        release_root,
        index,
        phase="vision",
        task_id="course-001",
    )
    vision_document = restricted / "vision-input.json"
    _review_document(
        vision_document,
        git_sha=release_root.name,
        task_id="course-001",
        evidence_path=vision_request,
    )
    publish_reviews(
        release_root=release_root,
        index_path=index,
        review_document=vision_document,
    )

    reviews = load_review_index(
        path=index,
        release_root=release_root,
        task_id="course-001",
        required_case_ids=tuple(sorted(PHASE_REVIEW_CASE_IDS["offline"] | {"VIS-025"})),
    )
    assert set(reviews) == PHASE_REVIEW_CASE_IDS["offline"] | {"VIS-025"}


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
    request = _publish_review_request(
        release_root,
        index,
        phase="offline",
        task_id="course-001",
    )
    _review_document(
        review_document,
        git_sha=release_root.name,
        task_id="course-001",
        evidence_path=request,
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

    payload = json.loads(request.read_text(encoding="utf-8"))
    assert payload["required_case_ids"] == sorted(offline_cases)
    assert payload["git_sha"] == release_root.name
    assert payload["task_id"] == "course-001"


def test_review_index_rejects_stale_task_and_unsafe_metadata(tmp_path: Path) -> None:
    release_root = _release_root(tmp_path)
    index = tmp_path / "restricted/reviews.json"
    index.parent.mkdir()
    review_document = tmp_path / "restricted/input.json"
    request = _publish_review_request(
        release_root,
        index,
        phase="vision",
        task_id="course-old",
    )
    _review_document(
        review_document,
        git_sha=release_root.name,
        task_id="course-old",
        evidence_path=request,
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
    request = _publish_review_request(
        release_root,
        index,
        phase="vision",
        task_id="course-001",
    )
    _review_document(
        review_document,
        git_sha=release_root.name,
        task_id="course-001",
        evidence_path=request,
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


def test_review_index_must_stay_outside_the_entire_git_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    release_root = _release_root(tmp_path / "release")
    index = workspace / "outside-platform-but-inside-workspace.json"
    monkeypatch.setattr(review_module, "WORKSPACE_ROOT", workspace)

    with pytest.raises(RuntimeError, match="Git 工作区"):
        prepare_and_wait_for_reviews(
            phase="vision",
            release_root=release_root,
            task_id="course-001",
            index_path=index,
            timeout_seconds=0.01,
            poll_interval_seconds=0.01,
        )


def test_review_document_must_stay_outside_the_entire_git_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    release_root = _release_root(tmp_path / "release")
    index = tmp_path / "restricted/reviews.json"
    index.parent.mkdir()
    review_document = workspace / "input.json"
    request = _publish_review_request(
        release_root,
        index,
        phase="vision",
        task_id="course-001",
    )
    _review_document(
        review_document,
        git_sha=release_root.name,
        task_id="course-001",
        evidence_path=request,
    )
    monkeypatch.setattr(review_module, "WORKSPACE_ROOT", workspace)

    with pytest.raises(RuntimeError, match="Git 工作区"):
        publish_reviews(
            release_root=release_root,
            index_path=index,
            review_document=review_document,
        )


def test_review_publisher_rejects_publication_before_phase_request(tmp_path: Path) -> None:
    release_root = _release_root(tmp_path)
    index = tmp_path / "restricted/reviews.json"
    index.parent.mkdir()
    review_document = tmp_path / "restricted/input.json"
    request = _publish_review_request(
        release_root,
        index,
        phase="vision",
        task_id="course-001",
    )
    _review_document(
        review_document,
        git_sha=release_root.name,
        task_id="course-001",
        evidence_path=request,
    )
    request.unlink()

    with pytest.raises(RuntimeError, match="复核请求.*不存在"):
        publish_reviews(
            release_root=release_root,
            index_path=index,
            review_document=review_document,
        )


def test_review_publisher_rejects_cases_outside_one_requested_phase(tmp_path: Path) -> None:
    release_root = _release_root(tmp_path)
    index = tmp_path / "restricted/reviews.json"
    index.parent.mkdir()
    request = _publish_review_request(
        release_root,
        index,
        phase="offline",
        task_id="course-001",
    )
    review_document = tmp_path / "restricted/input.json"
    _review_document(
        review_document,
        git_sha=release_root.name,
        task_id="course-001",
        evidence_path=request,
        case_ids=tuple(sorted(PHASE_REVIEW_CASE_IDS["offline"] | {"VIS-025"})),
    )

    with pytest.raises(RuntimeError, match="单一已请求阶段"):
        publish_reviews(
            release_root=release_root,
            index_path=index,
            review_document=review_document,
        )


def test_review_publisher_rejects_request_identity_mismatch(tmp_path: Path) -> None:
    release_root = _release_root(tmp_path)
    index = tmp_path / "restricted/reviews.json"
    index.parent.mkdir()
    request = _publish_review_request(
        release_root,
        index,
        phase="vision",
        task_id="course-other",
    )
    review_document = tmp_path / "restricted/input.json"
    _review_document(
        review_document,
        git_sha=release_root.name,
        task_id="course-001",
        evidence_path=request,
    )

    with pytest.raises(RuntimeError, match="task_id"):
        publish_reviews(
            release_root=release_root,
            index_path=index,
            review_document=review_document,
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"reviewer": "独立质量复核"}, "reviewer"),
        ({"reviewed_at": "2026-08-20 08:00:00"}, "时区"),
        ({"observed": {"paired_evidence_count": 2}}, "observed"),
        ({"evidence": ["sha256:abc"]}, "evidence"),
    ],
)
def test_review_publisher_rejects_weak_review_metadata(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    release_root = _release_root(tmp_path)
    index = tmp_path / "restricted/reviews.json"
    index.parent.mkdir()
    request = _publish_review_request(
        release_root,
        index,
        phase="vision",
        task_id="course-001",
    )
    review_document = tmp_path / "restricted/input.json"
    _review_document(
        review_document,
        git_sha=release_root.name,
        task_id="course-001",
        evidence_path=request,
    )
    _update_review_document(review_document, "VIS-025", **changes)

    with pytest.raises(RuntimeError, match=message):
        publish_reviews(
            release_root=release_root,
            index_path=index,
            review_document=review_document,
        )


def test_review_publisher_rejects_missing_or_changed_release_evidence(
    tmp_path: Path,
) -> None:
    release_root = _release_root(tmp_path)
    index = tmp_path / "restricted/reviews.json"
    index.parent.mkdir()
    request = _publish_review_request(
        release_root,
        index,
        phase="vision",
        task_id="course-001",
    )
    review_document = tmp_path / "restricted/input.json"
    _review_document(
        review_document,
        git_sha=release_root.name,
        task_id="course-001",
        evidence_path=request,
    )
    _update_review_document(
        review_document,
        "VIS-025",
        evidence=["release:business/review-requests/vision.json#sha256:" + "0" * 64],
    )

    with pytest.raises(RuntimeError, match="摘要不一致"):
        publish_reviews(
            release_root=release_root,
            index_path=index,
            review_document=review_document,
        )
