import asyncio
import types
import unittest

from app.services.inference_execution import cuda_memory_snapshot, execute_indexed


class InferenceExecutionTest(unittest.IsolatedAsyncioTestCase):
    def test_cuda_memory_snapshot_reads_allocator_metrics(self):
        fake_cuda = types.SimpleNamespace(
            is_available=lambda: True,
            memory_allocated=lambda: 10,
            memory_reserved=lambda: 20,
            max_memory_allocated=lambda: 30,
            max_memory_reserved=lambda: 40,
        )

        self.assertEqual(
            cuda_memory_snapshot(types.SimpleNamespace(cuda=fake_cuda)),
            {
                "allocated_bytes": 10,
                "reserved_bytes": 20,
                "max_allocated_bytes": 30,
                "max_reserved_bytes": 40,
            },
        )

    def test_cuda_memory_snapshot_skips_cpu_runtime(self):
        fake_cuda = types.SimpleNamespace(is_available=lambda: False)

        self.assertIsNone(cuda_memory_snapshot(types.SimpleNamespace(cuda=fake_cuda)))

    async def test_sequential_mode_completes_each_item_before_next(self):
        events = []

        async def operation(index, item):
            events.append(("start", index, item))
            await asyncio.sleep(0)
            events.append(("end", index, item))
            return item.upper()

        results = await execute_indexed(
            ["first", "second", "third"],
            operation,
            sequential=True,
        )

        self.assertEqual(results, ["FIRST", "SECOND", "THIRD"])
        self.assertEqual(
            events,
            [
                ("start", 0, "first"),
                ("end", 0, "first"),
                ("start", 1, "second"),
                ("end", 1, "second"),
                ("start", 2, "third"),
                ("end", 2, "third"),
            ],
        )

    async def test_parallel_compatibility_mode_preserves_result_order(self):
        all_started = asyncio.Event()
        started = []

        async def operation(index, item):
            started.append(index)
            if len(started) == 3:
                all_started.set()
            await asyncio.wait_for(all_started.wait(), timeout=1)
            return item

        results = await execute_indexed(
            ["first", "second", "third"],
            operation,
            sequential=False,
        )

        self.assertEqual(set(started), {0, 1, 2})
        self.assertEqual(results, ["first", "second", "third"])


if __name__ == "__main__":
    unittest.main()
