import asyncio
import threading
import time
import unittest

import numpy as np

from app.core.v4_correction import Request, Snapshot, SerialRunner, V4CorrectionScheduler, V4Punctuation


class BlockingModel:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.running = 0
        self.peak = 0

    def generate(self, **kwargs):
        self.running += 1
        self.peak = max(self.peak, self.running)
        self.started.set()
        self.release.wait(2)
        self.running -= 1
        return [{'text': '我们今天讨论历史发展'}]


def request(cid, revision, kind='intermediate', generation=1, frozen=None):
    def freeze():
        if frozen is not None:
            frozen.append(revision)
        return Snapshot(cid, generation, revision, 0, revision * 16000,
                        '我们今天讨论历史发展', revision,
                        np.zeros(revision * 16000, dtype=np.float32), kind)
    return Request(generation, kind, freeze)


async def eventually(predicate):
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(.005)
    raise AssertionError('condition did not become true')


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_itn_runs_before_punctuation_and_keeps_alignment_candidate(self):
        class Model:
            def generate(self, **kwargs):
                return [{'text': '百分之三点五增长'}]

        class ITN:
            async def apply(self, text):
                self.input = text
                return '3.5%增长', 1.0, 'applied', None

        itn = ITN()
        punctuation_inputs = []

        def punctuation(text, cache):
            punctuation_inputs.append(text)
            return {'text': text + '。'}

        scheduler = V4CorrectionScheduler(
            [Model()], V4Punctuation(punctuation), itn=itn,
        )
        await scheduler.start()
        result_queue = scheduler.register('a')

        def freeze():
            return Snapshot(
                'a', 1, 1, 0, 64000, '百分之三点五增长', 1,
                np.zeros(64000, dtype=np.float32), 'intermediate',
            )

        try:
            scheduler.submit('a', Request(1, 'intermediate', freeze))
            result = await asyncio.wait_for(result_queue.get(), 1)
            self.assertEqual(itn.input, '百分之三点五增长')
            self.assertEqual(punctuation_inputs, ['3.5%增长'])
            self.assertEqual(result.text, '3.5%增长。')
            self.assertEqual(result.alignment_text, '百分之三点五增长')
            self.assertEqual(result.itn_status, 'applied')
        finally:
            await scheduler.close()

    async def test_pending_global_bound_final_priority_and_single_inflight(self):
        model = BlockingModel()
        punc = V4Punctuation(lambda text, cache: [{'text':text+'。'}])
        scheduler = V4CorrectionScheduler([model], punc, capacity=2)
        await scheduler.start()
        a = scheduler.register('a')
        b = scheduler.register('b')
        scheduler.register('c')
        frozen = []
        try:
            self.assertTrue(scheduler.submit('a', request('a', 1, frozen=frozen)))
            await eventually(model.started.is_set)
            self.assertTrue(scheduler.submit('a', request('a', 2, frozen=frozen)))
            self.assertTrue(scheduler.submit('a', request('a', 3, frozen=frozen)))
            self.assertTrue(scheduler.submit('a', request('a', 4, 'final', frozen=frozen)))
            self.assertTrue(scheduler.submit('b', request('b', 1)))
            self.assertFalse(scheduler.submit('c', request('c', 1)))
            self.assertEqual(frozen, [1])
            model.release.set()
            await asyncio.wait_for(a.get(), 1)
            second = await asyncio.wait_for(a.get(), 1)
            self.assertEqual(second.snapshot.revision, 4)
            self.assertEqual(second.snapshot.kind, 'final')
            await asyncio.wait_for(b.get(), 1)
            self.assertEqual(frozen, [1,4])
            self.assertEqual(model.peak, 1)
        finally:
            model.release.set()
            await scheduler.close()

    async def test_timeout_disconnect_and_shutdown_keep_native_ownership(self):
        model = BlockingModel()
        scheduler = V4CorrectionScheduler([model], V4Punctuation(lambda text, cache: {'text':text}))
        await scheduler.start()
        results = scheduler.register('a')
        scheduler.submit('a', request('a', 1))
        await eventually(model.started.is_set)
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(results.get(), .01)
        self.assertIn('a', scheduler.active)
        scheduler.unregister('a')
        closing = asyncio.create_task(scheduler.close())
        await asyncio.sleep(.02)
        self.assertFalse(closing.done())
        model.release.set()
        await asyncio.wait_for(closing, 1)
        self.assertTrue(results.empty())
        self.assertFalse(scheduler.active)

    async def test_runner_cancellation_does_not_unlock_native_call(self):
        model = BlockingModel()
        runner = SerialRunner()
        first = asyncio.create_task(runner.run(model.generate))
        await eventually(model.started.is_set)
        first.cancel()
        second = asyncio.create_task(runner.run(model.generate))
        await asyncio.sleep(.02)
        self.assertEqual(model.peak, 1)
        self.assertFalse(first.done())
        model.release.set()
        await asyncio.gather(first, second, return_exceptions=True)
        self.assertEqual(model.peak, 1)

    async def test_bad_punctuation_cannot_remove_text(self):
        punc = V4Punctuation(lambda text, cache: {'text':'。'})
        text, _ = await punc.apply('正常语音文字')
        self.assertEqual(text, '正常语音文字')

    async def test_repunctuation_removes_old_marks_but_keeps_numbers(self):
        sources = []
        def model(text, cache):
            sources.append(text)
            return {'text':text+'。'}
        punc = V4Punctuation(model)
        text, _ = await punc.apply('先讲课程。，再讲3.14，时间12:30。')
        self.assertEqual(sources, ['先讲课程再讲3.14时间12:30'])
        self.assertNotIn('。，', text)

    async def test_worker_cancel_keeps_native_call_owned(self):
        model = BlockingModel()
        scheduler = V4CorrectionScheduler([model], V4Punctuation(lambda text, cache: {'text':text}))
        await scheduler.start()
        scheduler.register('a')
        scheduler.submit('a', request('a',1))
        await eventually(model.started.is_set)
        worker = scheduler.workers[0]
        worker.cancel()
        await asyncio.sleep(.02)
        self.assertIn('a', scheduler.active)
        self.assertFalse(worker.done())
        model.release.set()
        await asyncio.gather(worker, return_exceptions=True)
        self.assertFalse(scheduler.active)
        scheduler.workers.clear()
        await scheduler.close()

    async def test_failure_does_not_kill_worker_or_future_opportunities(self):
        class Model:
            calls = 0
            def generate(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise ValueError('failure')
                return [{'text':'我们今天讨论历史发展'}]
        scheduler = V4CorrectionScheduler([Model()], V4Punctuation(lambda text, cache: {'text':text}))
        await scheduler.start()
        q = scheduler.register('a')
        try:
            scheduler.submit('a', request('a', 1))
            self.assertEqual((await asyncio.wait_for(q.get(), 1)).error, 'ValueError')
            scheduler.submit('a', request('a', 2))
            self.assertIsNone((await asyncio.wait_for(q.get(), 1)).error)
        finally:
            await scheduler.close()
