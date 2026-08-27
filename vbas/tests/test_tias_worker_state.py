import asyncio
import unittest

from app.services.worker_state import BatchAdmissionController, BatchRejectedError


class BatchAdmissionControllerTest(unittest.IsolatedAsyncioTestCase):
    def test_rejects_invalid_or_misleading_capacity_configuration(self):
        with self.assertRaisesRegex(ValueError, "MaxConcurrentBatches"):
            BatchAdmissionController(
                instance_id="vbas-test",
                base_url="http://127.0.0.1:8981",
                max_concurrent_batches=0,
                max_queue_size=0,
            )

        with self.assertRaisesRegex(ValueError, "MaxQueueSize"):
            BatchAdmissionController(
                instance_id="vbas-test",
                base_url="http://127.0.0.1:8981",
                max_concurrent_batches=1024,
                max_queue_size=1,
            )

    async def test_rejects_batch_when_running_is_full_without_local_queue(self):
        controller = BatchAdmissionController(
            instance_id="tias-test",
            base_url="http://127.0.0.1:8981",
            max_concurrent_batches=1,
            max_queue_size=0,
        )

        async with controller.admit("task-1", "batch-1", "student", 1):
            status = controller.snapshot()
            self.assertEqual(status["running_batches"], 1)
            self.assertEqual(status["available_slots"], 0)

            with self.assertRaises(BatchRejectedError) as ctx:
                async with controller.admit("task-1", "batch-2", "student", 1):
                    pass

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(controller.snapshot()["running_batches"], 0)
        self.assertEqual(controller.snapshot()["queued_batches"], 0)

    async def test_eight_image_batch_counts_as_one_running_batch(self):
        controller = BatchAdmissionController(
            instance_id="vbas-test",
            base_url="http://127.0.0.1:8981",
            max_concurrent_batches=1024,
            max_queue_size=0,
        )

        async with controller.admit("task-8", "batch-8", "student", 8):
            status = controller.snapshot()
            self.assertEqual(status["running_batches"], 1)
            self.assertEqual(status["queued_batches"], 0)
            self.assertEqual(status["available_slots"], 1023)

        self.assertEqual(controller.snapshot()["running_batches"], 0)

    async def test_cancelled_batch_releases_running_count(self):
        controller = BatchAdmissionController(
            instance_id="vbas-test",
            base_url="http://127.0.0.1:8981",
            max_concurrent_batches=1024,
            max_queue_size=0,
        )
        entered = asyncio.Event()

        async def run_batch() -> None:
            async with controller.admit("task-cancel", "batch-cancel", "student", 8):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(run_batch())
        await entered.wait()
        self.assertEqual(controller.snapshot()["running_batches"], 1)

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        status = controller.snapshot()
        self.assertEqual(status["running_batches"], 0)
        self.assertEqual(status["queued_batches"], 0)
        self.assertEqual(status["failure_count"], 1)

    async def test_records_latency_and_failures(self):
        controller = BatchAdmissionController(
            instance_id="tias-test",
            base_url="http://127.0.0.1:8981",
            max_concurrent_batches=1,
            max_queue_size=0,
        )

        async with controller.admit("task-1", "batch-ok", "student", 1):
            pass

        with self.assertRaises(RuntimeError):
            async with controller.admit("task-1", "batch-fail", "teacher", 1):
                raise RuntimeError("推理失败")

        status = controller.snapshot()
        self.assertEqual(status["success_count"], 1)
        self.assertEqual(status["failure_count"], 1)
        self.assertEqual(status["recent_failure_count"], 1)
        self.assertIn("推理失败", status["last_error"])
        self.assertIsNotNone(status["avg_latency_ms"])

    async def test_drain_rejects_new_batch(self):
        controller = BatchAdmissionController(
            instance_id="tias-test",
            base_url="http://127.0.0.1:8981",
            max_concurrent_batches=1,
            max_queue_size=0,
        )

        controller.set_draining()

        with self.assertRaises(BatchRejectedError) as ctx:
            async with controller.admit("task-1", "batch-1", "student", 1):
                pass

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(controller.snapshot()["status"], "DRAINING")


if __name__ == "__main__":
    unittest.main()
