from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.run_milestone_2b_business_campaign import (
    CASE_REGRESSION_PATTERNS,
    _build_case_checks,
    _percentile,
    _publish_phase,
    _selected_cases,
    _task_summary,
    parse_args,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "deploy/milestone-2b-case-catalog.yaml"


@pytest.mark.parametrize(
    ("phase", "expected"),
    (("offline", 70), ("vision", 28), ("online", 34), ("final", 9)),
)
def test_business_campaign_selects_the_complete_phase_contract(
    phase: str,
    expected: int,
) -> None:
    assert len(_selected_cases(CATALOG, phase)) == expected


def test_every_business_case_has_an_explicit_regression_evidence_mapping() -> None:
    selected = {
        case.case_id
        for phase in ("offline", "vision", "online", "final")
        for case in _selected_cases(CATALOG, phase)
    }

    assert set(CASE_REGRESSION_PATTERNS) == selected
    assert all(CASE_REGRESSION_PATTERNS[case_id] for case_id in selected)


def test_load_007_requires_live_load_routing_with_equal_load_round_robin() -> None:
    assert CASE_REGRESSION_PATTERNS["LOAD-007"] == (
        "test_equal_load_instances_use_a_persistent_round_robin_cursor",
    )


def test_task_summary_does_not_copy_recognition_text() -> None:
    body = {
        "data": {
            "task_id": "course-1",
            "tasks": [
                {
                    "task_type": "ASR",
                    "status": 60,
                    "reason": "已完成",
                    "nodes": [
                        {
                            "node_code": "ASR_TRANSCRIPTION",
                            "status": 60,
                            "reason": "已完成",
                            "result": {
                                "text": "不得进入证据的转写正文",
                                "segments": [{"text": "正文"}],
                            },
                        }
                    ],
                }
            ],
        }
    }

    summary = _task_summary(body)
    encoded = json.dumps(summary, ensure_ascii=False)

    assert "不得进入证据的转写正文" not in encoded
    assert "正文" not in encoded
    result = summary["tasks"][0]["nodes"][0]["result"]
    assert result == {"type": "object", "keys": ["segments", "text"], "size": 2}


def test_phase_publication_is_complete_and_write_once(tmp_path: Path) -> None:
    release_root = tmp_path / "v1.0_260820" / ("a" * 40)
    release_root.mkdir(parents=True)
    args = SimpleNamespace(release_root=release_root, catalog=CATALOG)
    result = {
        "phase": "vision",
        "regression": {"returncode": 0},
        "cleanup": {"status": "clean", "residual_resources": []},
    }
    result["case_checks"] = {
        case.case_id: {
            "check_id": f"business-case-{case.case_id.lower()}",
            "assertions": [{"name": "test", "passed": True}],
        }
        for case in _selected_cases(CATALOG, "vision")
    }

    _publish_phase(args=args, phase="vision", result=result)
    _publish_phase(args=args, phase="vision", result=result)

    campaign_files = sorted((release_root / "negative/evidence").glob("VIS-*/campaign.json"))
    assert len(campaign_files) == 28
    assert (release_root / "business/vision-campaign.json").is_file()
    sample = json.loads(campaign_files[0].read_text(encoding="utf-8"))
    assert sample["executed"] is True
    assert sample["mock"] is False
    assert sample["artifacts"] == ["business/vision-campaign.json"]


def test_phase_publication_rejects_one_phase_result_used_for_every_case(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "v1.0_260820" / ("c" * 40)
    release_root.mkdir(parents=True)
    args = SimpleNamespace(release_root=release_root, catalog=CATALOG)

    with pytest.raises(RuntimeError, match="逐案提供完整检查结果"):
        _publish_phase(
            args=args,
            phase="online",
            result={
                "phase": "online",
                "regression": {"returncode": 0},
                "cleanup": {"status": "clean", "residual_resources": []},
            },
        )


def test_case_check_builder_requires_manual_quality_review(tmp_path: Path) -> None:
    release_root = tmp_path / "v1.0_260820" / ("d" * 40)
    release_root.mkdir(parents=True)
    args = SimpleNamespace(
        release_root=release_root,
        catalog=CATALOG,
        manual_review_json=None,
    )
    result = {
        "phase": "vision",
        "regression": {
            "passed_testcases": sorted(
                {
                    f"tests.semantic::{pattern}"
                    for case in _selected_cases(CATALOG, "vision")
                    for pattern in CASE_REGRESSION_PATTERNS[case.case_id]
                }
            )
        },
        "visual_tasks": {
            "TEACHER_BEHAVIOR": {"status": 60},
            "STUDENT_BEHAVIOR": {"status": 60},
        },
    }

    with pytest.raises(RuntimeError, match="VIS-025.*B 级质量复核"):
        _build_case_checks(args=args, phase="vision", result=result)


def test_offline_campaign_requires_all_three_video_urls(tmp_path: Path) -> None:
    release_root = tmp_path / "v1.0_260820" / ("b" * 40)
    release_root.mkdir(parents=True)

    with pytest.raises(SystemExit):
        parse_args(
            [
                "--phase",
                "offline",
                "--release-root",
                str(release_root),
                "--teacher-video-url",
                "http://example.test/teacher.mp4",
            ]
        )


def test_percentile_uses_the_nearest_rank() -> None:
    assert _percentile([0.4, 0.1, 0.3, 0.2], 0.95) == 0.4
    assert _percentile([0.4, 0.1, 0.3, 0.2], 0.5) == 0.2
