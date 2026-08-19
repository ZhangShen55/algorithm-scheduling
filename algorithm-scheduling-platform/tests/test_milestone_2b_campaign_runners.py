from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from scripts.milestone_2b_case_catalog import load_case_catalog
from scripts.milestone_2b_case_runners.campaign import (
    publish_campaign_case,
)
from scripts.run_milestone_2b_case_batch import resolve_runner, run_case_batch

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "deploy/milestone-2b-case-catalog.yaml"
GIT_SHA = "a" * 40


def _release_root(tmp_path: Path) -> Path:
    release_root = tmp_path / "releases" / "v1.0_260820" / GIT_SHA
    release_root.mkdir(parents=True)
    return release_root


def _case(case_id: str):
    return next(
        case for case in load_case_catalog(CATALOG).cases if case.case_id == case_id
    )


def _observed(case_id: str) -> dict[str, object]:
    case = _case(case_id)
    return {
        "check_id": f"business-case-{case_id.lower()}",
        "method": "real-runtime-and-targeted-regression",
        "case_title": case.title,
        "expected": case.expected,
        "runtime_probe": {"probe": "test-runtime"},
        "related_passed_testcases": ["tests.test_runtime::test_case"],
        "assertions": [{"name": "runtime", "passed": True}],
        "manual_review": None,
    }


def test_all_previously_missing_campaign_runners_resolve() -> None:
    catalog = load_case_catalog(CATALOG)
    selected = [
        case
        for case in catalog.cases
        if case.phase in {"offline", "vision", "online"}
        or case.case_id in {f"LOAD-{number:03d}" for number in range(1, 10)}
    ]
    assert len(selected) == 150
    for case in selected:
        runner = resolve_runner(case.runner)
        assert callable(runner.run), case.runner
        assert callable(getattr(runner, "cleanup", None)), case.runner


def test_campaign_case_requires_real_same_release_evidence(tmp_path: Path) -> None:
    release_root = _release_root(tmp_path)
    case = _case("JOB-001")
    artifact = release_root / "negative" / "job-001-input.json"
    artifact.parent.mkdir()
    artifact.write_text('{"request": "missing-task-id"}\n', encoding="utf-8")
    publish_campaign_case(
        release_root=release_root,
        case_id=case.case_id,
        phase="offline",
        status="通过",
        reason="缺少 task_id 的请求已被业务校验拒绝",
        observed=_observed(case.case_id),
        artifacts=("negative/job-001-input.json",),
    )
    result = asyncio.run(
        run_case_batch(
            cases=(case,),
            release_root=release_root,
            concurrency=1,
            require_cleanup=True,
            require_all_selected=True,
            run_id="campaign-test",
            target="localhost",
        )
    )
    outcome = result.outcomes[case.case_id]

    assert outcome.status == "通过"
    assert [path.as_posix() for path in outcome.evidence] == [
        "negative/evidence/JOB-001/campaign.json",
        "negative/evidence/JOB-001/campaign-attestation.json",
    ]


def test_campaign_case_rejects_failed_or_unclean_result(tmp_path: Path) -> None:
    release_root = _release_root(tmp_path)
    case = _case("VIS-005")
    publish_campaign_case(
        release_root=release_root,
        case_id=case.case_id,
        phase="vision",
        status="失败",
        reason="容量等待没有恢复",
        observed=_observed(case.case_id),
        cleanup={"status": "dirty", "residual_resources": ["lease-1"]},
    )
    result = asyncio.run(
        run_case_batch(
            cases=(case,),
            release_root=release_root,
            concurrency=1,
            require_cleanup=True,
            require_all_selected=True,
            run_id="campaign-test",
            target="localhost",
        )
    )
    assert result.exit_code == 1
    assert result.outcomes[case.case_id].status == "失败"


def test_campaign_publication_is_write_once(tmp_path: Path) -> None:
    release_root = _release_root(tmp_path)
    arguments = {
        "release_root": release_root,
        "case_id": "LOAD-001",
        "phase": "final",
        "status": "通过",
        "reason": "单请求基线通过",
        "observed": _observed("LOAD-001"),
    }
    publish_campaign_case(**arguments)
    publish_campaign_case(**arguments)

    changed = dict(arguments)
    changed["observed"] = {
        **_observed("LOAD-001"),
        "runtime_probe": {"probe": "changed"},
    }
    with pytest.raises(ValueError, match="different bytes"):
        publish_campaign_case(**changed)

    source = json.loads(
        (
            release_root / "load/evidence/LOAD-001/campaign.json"
        ).read_text(encoding="utf-8")
    )
    assert source["git_sha"] == GIT_SHA
    assert source["executed"] is True
    assert source["mock"] is False
