import asyncio
from pathlib import Path

import pytest
from orchestrator_service.app.domain.ppt_work import (
    PptImageWork,
    PptWorkLimits,
    iter_work_batches,
    make_ppt_image_id,
    run_bounded_work,
)


def test_ppt_image_id_is_stable_for_same_slice_identity() -> None:
    first = make_ppt_image_id("course-001", frame_seq=217, snap_time=216)
    duplicate = make_ppt_image_id("course-001", frame_seq=217, snap_time=216)
    another = make_ppt_image_id("course-001", frame_seq=300, snap_time=299)

    assert first == duplicate
    assert first.startswith("ppt-")
    assert another != first


def test_dynamic_ppt_work_uses_configured_batch_size() -> None:
    work = [
        PptImageWork(
            ppt_image_id=f"ppt-{index:03d}",
            image_path=Path(f"/data/result/course-001/ppt/slices/ppt-{index:03d}.jpg"),
            ordinal=index,
        )
        for index in range(30)
    ]
    limits = PptWorkLimits(batch_size=8, max_concurrency=3)

    batches = list(iter_work_batches(work, limits))

    assert [len(batch) for batch in batches] == [8, 8, 8, 6]
    assert limits.max_concurrency == 3


@pytest.mark.asyncio
async def test_ppt_work_runner_enforces_max_concurrency() -> None:
    work = [
        PptImageWork(f"ppt-{index}", Path(f"/{index}.jpg"), index)
        for index in range(10)
    ]
    active = 0
    peak = 0

    async def worker(item: PptImageWork) -> str:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return item.ppt_image_id

    result = await run_bounded_work(
        work,
        PptWorkLimits(batch_size=8, max_concurrency=3),
        worker,
    )

    assert peak == 3
    assert result == [item.ppt_image_id for item in work]
