import asyncio
import threading
import unittest

import numpy as np

from app.core.correction_scheduler import CorrectionScheduler, CorrectionSnapshot


class RecordingModel:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        marker = int(kwargs["input"][0])
        self.calls.append(marker)
        return [{"text": f"纠正{marker}。"}]


class BlockingModel(RecordingModel):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def generate(self, **kwargs):
        marker = int(kwargs["input"][0])
        self.calls.append(marker)
        if marker == 1:
            self.started.set()
            self.release.wait(timeout=2)
        return [{"text": f"纠正{marker}。"}]


class FailingModel:
    def generate(self, **kwargs):
        raise RuntimeError("inference failed")


def snapshot(
    connection_id: str,
    revision_id: int,
    marker: int,
    kind: str = "intermediate",
):
    return CorrectionSnapshot(
        connection_id=connection_id,
        segment_key="segment-000001",
        revision_id=revision_id,
        text_version=revision_id,
        start_sample=0,
        end_sample=revision_id * 100,
        audio=np.full(160, marker, dtype=np.float32),
        kind=kind,
        trigger="test",
    )


class CorrectionSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_latest_only_keeps_one_active_and_newest_pending(self):
        model = BlockingModel()
        scheduler = CorrectionScheduler([model], 4, 0.2)
        await scheduler.start()
        results = await scheduler.register_session("session")
        try:
            self.assertTrue(await scheduler.submit(snapshot("session", 1, 1)))
            await asyncio.to_thread(model.started.wait, 1)
            self.assertTrue(await scheduler.submit(snapshot("session", 2, 2)))
            self.assertTrue(await scheduler.submit(snapshot("session", 3, 3)))
            model.release.set()
            first = await asyncio.wait_for(results.get(), 1)
            second = await asyncio.wait_for(results.get(), 1)

            self.assertEqual([first.snapshot.revision_id, second.snapshot.revision_id], [1, 3])
            self.assertEqual(model.calls, [1, 3])
            self.assertEqual(scheduler.metrics_snapshot()["coalesced"], 2)
        finally:
            model.release.set()
            await scheduler.close()

    async def test_final_tasks_have_priority_over_queued_intermediate_tasks(self):
        model = BlockingModel()
        scheduler = CorrectionScheduler([model], 4, 0.2)
        await scheduler.start()
        queues = {
            session: await scheduler.register_session(session)
            for session in ("active", "intermediate", "final")
        }
        try:
            await scheduler.submit(snapshot("active", 1, 1))
            await asyncio.to_thread(model.started.wait, 1)
            await scheduler.submit(snapshot("intermediate", 1, 2))
            await scheduler.submit(snapshot("final", 1, 3, kind="final"))
            model.release.set()
            await asyncio.wait_for(queues["active"].get(), 1)
            await asyncio.wait_for(queues["final"].get(), 1)
            await asyncio.wait_for(queues["intermediate"].get(), 1)

            self.assertEqual(model.calls, [1, 3, 2])
        finally:
            model.release.set()
            await scheduler.close()

    async def test_intermediate_is_dropped_when_queue_is_full(self):
        model = BlockingModel()
        scheduler = CorrectionScheduler([model], 1, 0.05)
        await scheduler.start()
        for session in ("active", "queued", "dropped"):
            await scheduler.register_session(session)
        try:
            await scheduler.submit(snapshot("active", 1, 1))
            await asyncio.to_thread(model.started.wait, 1)
            self.assertTrue(await scheduler.submit(snapshot("queued", 1, 2)))
            self.assertFalse(await scheduler.submit(snapshot("dropped", 1, 3)))
            self.assertEqual(scheduler.metrics_snapshot()["queue_full"], 1)
        finally:
            model.release.set()
            await scheduler.close()

    async def test_model_failure_is_returned_without_raising_in_worker(self):
        scheduler = CorrectionScheduler([FailingModel()], 1, 0.1)
        await scheduler.start()
        results = await scheduler.register_session("session")
        try:
            await scheduler.submit(snapshot("session", 1, 1, kind="final"))
            result = await asyncio.wait_for(results.get(), 1)
            self.assertEqual(result.text, "")
            self.assertIn("RuntimeError", result.error)
            self.assertEqual(scheduler.metrics_snapshot()["failed"], 1)
        finally:
            await scheduler.close()

    async def test_unregister_discards_results_and_close_cleans_state(self):
        model = BlockingModel()
        scheduler = CorrectionScheduler([model], 2, 0.1)
        await scheduler.start()
        await scheduler.register_session("session")
        await scheduler.submit(snapshot("session", 1, 1))
        await asyncio.to_thread(model.started.wait, 1)
        await scheduler.unregister_session("session")
        model.release.set()
        await asyncio.sleep(0.05)
        await scheduler.close()

        metrics = scheduler.metrics_snapshot()
        self.assertEqual(metrics["sessions"], 0)
        self.assertEqual(metrics["workers"], 0)
        self.assertEqual(metrics["queue_depth"], 0)


if __name__ == "__main__":
    unittest.main()
