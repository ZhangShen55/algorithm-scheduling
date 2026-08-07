import unittest

from app.services.worker_state import BatchAdmissionController, BatchRejectedError


class BatchAdmissionControllerTest(unittest.IsolatedAsyncioTestCase):
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
