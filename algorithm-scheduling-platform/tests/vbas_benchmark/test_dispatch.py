from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from vision_orchestrator_service.app.infrastructure.cache import VisionStream

from scripts.vbas_benchmark.dispatch import (
    DispatchEvent,
    QueueSample,
    batches_from_paths,
    classify_dispatch_gaps,
    run_dispatch_replay,
)


@pytest.mark.asyncio
async def test_dispatch_replay_consumes_prepared_batches_with_fixed_workers(
    tmp_path: Path,
) -> None:
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"image")
    batches = batches_from_paths([image] * 8, batch_size=2, stream=VisionStream.STUDENT)
    active = 0
    peak = 0

    class Client:
        async def analyze(self, *, task_id, stream, frames, trace_id=None):
            nonlocal active, peak
            del task_id, stream, trace_id
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.005)
            active -= 1
            return [{"image_id": frame.image_id} for frame in frames]

    result = await run_dispatch_replay(
        client=Client(),
        stream=VisionStream.STUDENT,
        batches=batches,
        target_slots=3,
        campaign_id="campaign-test",
        sample_interval_seconds=0.001,
    )

    assert result.status == "passed"
    assert result.success_count == 4
    assert peak == 3
    assert result.summary["attribution"] in {"none", "feed", "dispatch"}


def test_gap_classifier_separates_feed_and_dispatch_idle_time() -> None:
    samples = (
        QueueSample(0.0, ready_depth=1, active_requests=0, target_slots=3),
        QueueSample(1.0, ready_depth=3, active_requests=0, target_slots=3),
        QueueSample(2.0, ready_depth=0, active_requests=3, target_slots=3),
    )
    events = (
        DispatchEvent("lease_requested", "b1", 0.0),
        DispatchEvent("lease_acquired", "b1", 0.2, "gpu0"),
        DispatchEvent("vbas_started", "b1", 0.3, "gpu0"),
        DispatchEvent("vbas_finished", "b1", 1.3, "gpu0"),
    )

    result = classify_dispatch_gaps(samples, stage_events=events)

    assert result["feed_gap_seconds"] == 1.0
    assert result["dispatch_gap_seconds"] == 1.0
    assert result["lease_wait_p95_seconds"] == pytest.approx(0.2)
    assert result["vbas_service_p95_seconds"] == pytest.approx(1.0)
