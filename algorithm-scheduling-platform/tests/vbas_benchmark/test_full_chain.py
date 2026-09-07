from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.vbas_benchmark.full_chain import (
    FullChainStageEvent,
    analyze_full_chain,
    load_gpu_assessment,
    load_stage_events,
)


def _event(
    event: str,
    timestamp: float,
    *,
    batch_id: str = "batch-1",
    instance_id: str | None = None,
) -> FullChainStageEvent:
    return FullChainStageEvent(
        event=event,
        task_id="task-1",
        stream="student",
        batch_id=batch_id,
        operation="-",
        instance_id=instance_id,
        outcome=None,
        monotonic_seconds=timestamp,
    )


def test_load_stage_events_ignores_regular_json_logs(tmp_path: Path) -> None:
    log = tmp_path / "vision.log"
    stage = (
        "视觉基准阶段 event=vbas_started task_id=task-1 stream=student "
        "batch_id=batch-1 operation=- instance_id=vbas-gpu0 outcome=- "
        "monotonic_seconds=10.5"
    )
    log.write_text(
        json.dumps({"event": "服务启动"}, ensure_ascii=False)
        + "\n"
        + json.dumps({"event": stage}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    events = load_stage_events(log)

    assert len(events) == 1
    assert events[0].instance_id == "vbas-gpu0"
    assert events[0].monotonic_seconds == 10.5


def test_full_chain_analysis_separates_feed_and_dispatch_gaps() -> None:
    events = (
        _event("media_queued", 0.0, batch_id="media-1"),
        _event("lease_requested", 2.0),
        _event("lease_acquired", 3.0),
        _event("vbas_started", 3.1, instance_id="vbas-gpu0"),
        _event("vbas_finished", 5.0, instance_id="vbas-gpu0"),
    )

    result = analyze_full_chain(events, capacity=1)

    assert result["status"] == "measured"
    assert result["feed_gap_seconds"] == pytest.approx(2.1)
    assert result["dispatch_gap_seconds"] == pytest.approx(1.0)
    assert result["average_active_slots"] == pytest.approx(1.9 / 5.0)
    assert result["max_active_slots"] == 1
    assert result["instance_batch_counts"] == {"vbas-gpu0": 1}


def test_full_chain_analysis_marks_unpaired_vbas_events_incomplete() -> None:
    result = analyze_full_chain(
        (
            _event("media_queued", 0.0),
            _event("vbas_started", 1.0, instance_id="vbas-gpu0"),
        ),
        capacity=1,
    )

    assert result["status"] == "incomplete_evidence"
    assert result["missing_vbas_finished"] == ["batch-1"]


def test_full_chain_analysis_closes_transport_retry_on_lease_release() -> None:
    result = analyze_full_chain(
        (
            _event("media_queued", 0.0),
            _event("lease_requested", 1.0),
            _event("lease_acquired", 1.1),
            _event("vbas_started", 1.2, instance_id="vbas-gpu0"),
            _event("lease_released", 1.4, instance_id="vbas-gpu0"),
            _event("lease_requested", 1.5),
            _event("lease_acquired", 1.6),
            _event("vbas_started", 1.7, instance_id="vbas-gpu1"),
            _event("vbas_finished", 2.0, instance_id="vbas-gpu1"),
            _event("lease_released", 2.1, instance_id="vbas-gpu1"),
        ),
        capacity=1,
    )

    assert result["status"] == "measured"
    assert result["vbas_attempt_count"] == 2
    assert result["vbas_batch_count"] == 1
    assert result["released_without_finish_count"] == 1
    assert result["max_active_slots"] == 1
    assert result["instance_attempt_counts"] == {"vbas-gpu0": 1, "vbas-gpu1": 1}
    assert result["instance_batch_counts"] == {"vbas-gpu1": 1}


def test_load_gpu_assessment_reports_each_gpu(tmp_path: Path) -> None:
    source = tmp_path / "gpu.csv"
    source.write_text(
        "\n".join(
            f"2026/09/07 12:00:0{sample}.000, {gpu}, GPU, 24564, "
            f"{1000 + gpu * 10}, {20 + gpu}, 100"
            for sample in range(4)
            for gpu in range(3)
        )
        + "\n",
        encoding="utf-8",
    )

    result = load_gpu_assessment(source)

    assert set(result) == {"0", "1", "2"}
    assert result["0"]["status"] == "stable"
    assert result["0"]["sample_count"] == 4
