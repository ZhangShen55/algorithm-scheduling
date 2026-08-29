import asyncio

import pytest

from app.services.worker_state import BatchAdmissionController, BatchRejectedError


@pytest.mark.asyncio
async def test_online_and_offline_capacity_are_independent() -> None:
    controller = BatchAdmissionController(
        "vbas-test", "http://127.0.0.1:8981",
        max_concurrent_offline_batches=1,
        max_concurrent_online_requests=2,
        max_queue_online_size=1,
    )
    async with controller.admit("task", "offline", "student", 8):
        async with controller.admit("task", "online-1", "student", 1, work_type="online"):
            async with controller.admit("task", "online-2", "student", 1, work_type="online"):
                status = controller.snapshot()
                assert status["running_offline_batches"] == 1
                assert status["running_online_requests"] == 2


@pytest.mark.asyncio
async def test_online_queue_is_fifo_and_bounded() -> None:
    controller = BatchAdmissionController(
        "vbas-test", "http://127.0.0.1:8981",
        max_concurrent_offline_batches=1,
        max_concurrent_online_requests=1,
        max_queue_online_size=1,
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def first() -> None:
        async with controller.admit("t", "first", "student", 1, work_type="online"):
            entered.set()
            await release.wait()

    first_task = asyncio.create_task(first())
    await entered.wait()
    second_entered = asyncio.Event()

    async def second() -> None:
        async with controller.admit("t", "second", "student", 1, work_type="online"):
            second_entered.set()

    second_task = asyncio.create_task(second())
    await asyncio.sleep(0)
    with pytest.raises(BatchRejectedError, match="队列已满"):
        async with controller.admit("t", "third", "student", 1, work_type="online"):
            pass
    assert controller.snapshot()["queued_online_requests"] == 1
    release.set()
    await asyncio.wait_for(second_entered.wait(), timeout=1)
    await first_task
    await second_task
    assert controller.snapshot()["running_online_requests"] == 0
