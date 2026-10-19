import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import WebSocketDisconnect

from app.api.routes import ws_online_v4 as v4
from app.core.v4_correction import V4CorrectionScheduler, V4Punctuation


PCM = b'\x01\x00' * 7680


class Socket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.rows = []
        self.client_state = SimpleNamespace(name='CONNECTED')
        self.application_state = SimpleNamespace(name='CONNECTED')

    async def accept(self):
        pass

    async def receive_bytes(self):
        data = await self.incoming.get()
        if data is None:
            self.client_state.name = 'DISCONNECTED'
            raise WebSocketDisconnect()
        return data

    async def send_json(self, value):
        self.rows.append(value)
        self.outgoing.put_nowait(value)

    async def close(self, **kwargs):
        self.application_state.name = 'DISCONNECTED'


class Online:
    def __init__(self, texts, tail=''):
        self.texts = iter(texts)
        self.calls = []
        self.tail = tail

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return [{'text': self.tail if kwargs['is_final'] else next(self.texts, '')}]


class Offline:
    def __init__(self, texts=None, delay=0):
        self.texts = texts or {}
        self.delay = delay
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        time.sleep(self.delay)
        return [{'text': self.texts.get(len(kwargs['input']), '。')}]


class V4ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, texts, offline=None, tail='', patches=(), pause=.01):
        ws = Socket()
        online = Online(texts, tail)
        offline = offline or Offline()
        punct = V4Punctuation(lambda text, cache: {'text':text})
        scheduler = V4CorrectionScheduler([offline], punct)
        await scheduler.start()
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(v4, 'get_online_model', return_value=online))
            stack.enter_context(patch.object(v4, 'get_v4_scheduler', return_value=scheduler))
            stack.enter_context(patch.object(v4, 'get_v4_punctuation', return_value=punct))
            for target, value in patches:
                stack.enter_context(patch.object(v4, target, value))
            task = asyncio.create_task(v4.run_v4_session(ws))
            try:
                for index in range(len(texts)):
                    await ws.incoming.put(PCM)
                    while True:
                        row = await asyncio.wait_for(ws.outgoing.get(), 2)
                        if row['ed'] >= round((index+1)*.48,2):
                            break
                    await asyncio.sleep(pause)
                await ws.incoming.put(None)
                await asyncio.wait_for(task, 2)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await scheduler.close()
        return ws.rows, online, offline

    async def test_two_cumulative_corrections_before_final_and_unchanged_contract(self):
        increments = ['我们','今天','研究','文华','历史','以及','社会','变化','发展',
                      '然后','讨论','新的','问题','还有','不同','人物','行为','特点']
        first = ''.join(increments[:9]).replace('文华','文化')
        second = ''.join(increments).replace('文华','文化')
        offline = Offline({9*7680:first+'。',18*7680:second+'。',20*7680:second+'。'})
        rows, online, offline = await self.run_case(increments+['',''], offline)
        updates = [r for r in rows if not r['finished'] and '文化' in r['text']]
        # This fixture's punctuation model is an identity function.
        self.assertTrue(any(r['text']==first for r in updates))
        self.assertTrue(any(r['text']==second for r in updates))
        first_applied = next(i for i,r in enumerate(rows) if '文化' in r['text'])
        self.assertTrue(all('文华' not in r['text'] for r in rows[first_applied:]))
        self.assertEqual(sum(r['finished'] for r in rows), 1)
        self.assertEqual([len(c['input']) for c in offline.calls], [9*7680,18*7680,20*7680])
        self.assertEqual(sum(c['is_final'] for c in online.calls), 1)
        for r in rows:
            self.assertEqual(set(r), {'key','text','finished','bg','ed'})
        self.assertEqual([r['ed'] for r in rows], sorted(r['ed'] for r in rows))

    async def test_empty_result_then_speech_does_not_consume_correction_opportunity(self):
        texts=['今天','我们','','讨论','中国','文化','历史','社会','发展','还有','','']
        offline=Offline({9*7680:'今天我们讨论中国文化历史社会发展'})
        rows, _, offline=await self.run_case(texts, offline)
        self.assertGreaterEqual(len(offline.calls),2)
        self.assertTrue(any('文化历史社会发展' in r['text'] for r in rows))

    async def test_final_budget_retains_corrected_prefix_tail_and_flush(self):
        increments=['我们','今天','研究','文华','历史','以及','社会','变化','发展']
        first=''.join(increments).replace('文华','文化')
        class SlowFinal(Offline):
            def generate(self, **kwargs):
                if len(kwargs['input'])>9*7680:
                    time.sleep(.15)
                return super().generate(**kwargs)
        rows, _, _=await self.run_case(increments+['继续','',''],
                                     SlowFinal({9*7680:first+'。'}), tail='尾字',
                                     patches=(('_FINAL_BUDGET_SECONDS',.02),))
        final=next(r for r in rows if r['finished'])
        self.assertEqual(final['text'],first+'继续尾字')

    async def test_late_results_never_cross_finished_barrier(self):
        rows, _, _=await self.run_case(['第一句话','','','第二句话','',''],
                                     Offline({3*7680:'迟到结果'},delay=.08),
                                     patches=(('_FINAL_BUDGET_SECONDS',.005),))
        finished=[]
        active=[]
        for row in rows:
            active.append(row['text'])
            if row['finished']:
                finished.append(active)
                active=[]
        self.assertEqual(len(finished),2)
        self.assertEqual(finished[0][-1],'第一句话')
        self.assertEqual(finished[1][-1],'第二句话')
        self.assertFalse(any('迟到' in r['text'] for r in rows))

    async def test_limit_resets_audio_range_and_silence_does_not_finish(self):
        rows, _, offline=await self.run_case(['一','二','三','四'],
                       patches=(('_MAX_SEGMENT_SAMPLES',2*7680),))
        self.assertEqual(sum(r['finished'] for r in rows),2)
        self.assertTrue(all(len(c['input'])==2*7680 for c in offline.calls))
        ends=[r for r in rows if r['finished']]
        self.assertGreaterEqual(ends[1]['bg'],ends[0]['ed'])
        rows, _, offline=await self.run_case(['','',''])
        self.assertFalse(any(r['finished'] for r in rows))
        self.assertFalse(offline.calls)

    async def test_initial_pcm_is_kept_and_final_is_independent(self):
        _, _, offline=await self.run_case(['','正常语音','',''])
        self.assertEqual(len(offline.calls[0]['input']),4*7680)
