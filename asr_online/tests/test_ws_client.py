import asyncio
import contextlib
import importlib.util
import io
import json
import tempfile
import time
import unittest
import wave
from pathlib import Path

import websockets
from websockets.exceptions import ConnectionClosed


spec = importlib.util.spec_from_file_location(
    "ws_client", Path(__file__).resolve().parents[1] / "test/test_api_ws_client.py"
)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wav = Path(self.tmp.name) / "audio.wav"
        self.output = Path(self.tmp.name) / "results.jsonl"
        with wave.open(str(self.wav), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0" * client.CHUNK_SAMPLES * 2 * 2)

    def summary(self):
        return json.loads(self.output.with_suffix(".summary.json").read_text())

    async def execute(self, handler, **kwargs):
        kwargs.setdefault("send_mode", "realtime")
        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with contextlib.redirect_stdout(io.StringIO()):
                await asyncio.wait_for(client.run(
                    self.wav, f"ws://127.0.0.1:{port}", self.output,
                    tail_chunks=1, drain_seconds=0.05, **kwargs,
                ), timeout=5)

    async def test_extra_corrections_do_not_accelerate_audio(self):
        arrivals = []

        async def handler(ws):
            async for _ in ws:
                arrivals.append(time.monotonic())
                response = dict(key="segment-1", text="test", finished=False,
                                bg=0, ed=round(len(arrivals) * client.CHUNK_SECONDS, 2))
                await ws.send(json.dumps(response))
                await ws.send(json.dumps(response))

        await self.execute(handler)
        self.assertEqual(len(arrivals), 3)
        self.assertTrue(all(b - a >= 0.44 for a, b in zip(arrivals, arrivals[1:])))
        self.assertEqual(self.summary()["responses"], 6)
        self.assertEqual(self.summary()["status"], "completed")
        self.assertEqual(self.summary()["last_response_ed"], 1.44)

    async def test_disconnect_reports_code_and_saves_partial_progress(self):
        async def handler(ws):
            await ws.recv()
            await ws.close(code=1013, reason="input_backlog_limit")

        with self.assertRaises(ConnectionClosed):
            await self.execute(handler)
        self.assertEqual(self.summary()["status"], "failed")
        self.assertEqual(self.summary()["close_code"], 1013)
        self.assertEqual(self.summary()["close_reason"], "input_backlog_limit")
        self.assertEqual(self.summary()["source_chunks"], 1)

    async def test_silent_server_times_out(self):
        async def handler(ws):
            await ws.wait_closed()

        with self.assertRaises(TimeoutError):
            await self.execute(handler, response_timeout=0.15)
        self.assertEqual(self.summary()["status"], "failed")
        self.assertIn("TimeoutError", self.summary()["error"])

    async def test_repeated_ed_does_not_hide_stall(self):
        async def handler(ws):
            await ws.recv()
            try:
                while True:
                    await ws.send(json.dumps(dict(key="segment-1", text="test",
                                                  finished=False, bg=0, ed=0)))
                    await asyncio.sleep(0.02)
            except ConnectionClosed:
                pass

        with self.assertRaises(TimeoutError):
            await self.execute(handler, response_timeout=0.15)
        self.assertGreater(self.summary()["responses"], 1)
        self.assertEqual(self.summary()["status"], "failed")

    async def test_interruption_preserves_summary(self):
        received = asyncio.Event()

        async def handler(ws):
            await ws.recv()
            received.set()
            await ws.wait_closed()

        task = asyncio.create_task(self.execute(handler))
        await asyncio.wait_for(received.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.summary()["status"], "interrupted")
        self.assertEqual(self.summary()["source_chunks"], 1)

    async def test_fast_window_bounds_backlog_despite_extra_responses(self):
        with wave.open(str(self.wav), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0" * client.CHUNK_SAMPLES * 2 * 12)
        backlog = []
        arrivals = []

        async def handler(ws):
            queue = asyncio.Queue()
            processed = 0

            async def receive():
                async for chunk in ws:
                    arrivals.append(time.monotonic())
                    backlog.append(len(arrivals) - processed)
                    await queue.put(chunk)

            receiver = asyncio.create_task(receive())
            try:
                for _ in range(13):
                    await queue.get()
                    await asyncio.sleep(0.02)
                    processed += 1
                    response = dict(key="segment-1", text="test", finished=False,
                                    bg=0, ed=round(processed * client.CHUNK_SECONDS, 2))
                    for _ in range(4):
                        await ws.send(json.dumps(response))
                await ws.wait_closed()
            finally:
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)

        await self.execute(handler, send_mode="fast", window_seconds=0.96)
        self.assertEqual(len(arrivals), 13)
        self.assertLessEqual(max(backlog), 2)
        self.assertLess(arrivals[-1] - arrivals[0], 2)
        self.assertEqual(self.summary()["responses"], 52)
        self.assertEqual(self.summary()["status"], "completed")

    async def test_fast_stalled_window_does_not_send_more_audio(self):
        arrivals = []

        async def handler(ws):
            async for chunk in ws:
                arrivals.append(chunk)
                await ws.send(json.dumps(dict(key="segment-1", text="test",
                                              finished=False, bg=0, ed=0)))

        with self.assertRaises(TimeoutError):
            await self.execute(handler, send_mode="fast", window_seconds=0.48,
                               response_timeout=0.15)
        self.assertEqual(len(arrivals), 1)
        self.assertEqual(self.summary()["status"], "failed")


if __name__ == "__main__":
    unittest.main()
