import struct
import unittest

import httpx

from app.core.qwen_stream import QwenBackendPool, QwenStreamClient


class QwenStreamClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_stateful_start_chunk_and_finish_use_official_protocol(self):
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/api/start":
                return httpx.Response(200, json={"session_id": "session-1"})
            if request.url.path == "/api/chunk":
                return httpx.Response(
                    200,
                    json={
                        "language": "Chinese",
                        "text": "language Chinese<asr_text>测试文本。",
                    },
                )
            if request.url.path == "/api/finish":
                return httpx.Response(
                    200,
                    json={"language": "Chinese", "text": "测试文本。"},
                )
            return httpx.Response(404)

        client = QwenStreamClient(
            "http://qwen.test",
            connect_timeout_seconds=1,
            chunk_timeout_seconds=1,
            transport=httpx.MockTransport(handler),
        )
        try:
            await client.start()
            update = await client.chunk(b"\0\0\0\0")
            final = await client.finish()
        finally:
            await client.close()

        self.assertEqual([request.url.path for request in requests], [
            "/api/start",
            "/api/chunk",
            "/api/finish",
        ])
        self.assertEqual(requests[1].url.params["session_id"], "session-1")
        self.assertEqual(requests[1].headers["content-type"], "application/octet-stream")
        self.assertEqual(update.text, "测试文本。")
        self.assertEqual(update.language, "Chinese")
        self.assertEqual(final.text, "测试文本。")


class QwenBackendPoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_lease_per_backend_and_round_robin_distribution(self):
        pool = QwenBackendPool(
            ["http://qwen-1", "http://qwen-2"],
            capacity_per_backend=1,
            total_capacity=2,
        )

        first = await pool.try_acquire()
        second = await pool.try_acquire()
        rejected = await pool.try_acquire()

        self.assertEqual(first.base_url, "http://qwen-1")
        self.assertEqual(second.base_url, "http://qwen-2")
        self.assertIsNone(rejected)
        self.assertEqual(pool.active, [1, 1])

        await first.release()
        third = await pool.try_acquire()
        self.assertEqual(third.base_url, "http://qwen-1")
        await second.release()
        await third.release()
        self.assertEqual(pool.active, [0, 0])


class QwenStreamProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.api.routes import ws_online_v5 as v5
        from app.core.qwen_stream import QwenBackendPool

        self.v5 = v5
        self.original_pool = v5._BACKEND_POOL
        v5._BACKEND_POOL = QwenBackendPool(
            ["http://qwen.test"],
            capacity_per_backend=1,
            total_capacity=1,
        )

    async def asyncTearDown(self):
        self.v5._BACKEND_POOL = self.original_pool

    async def run_case(self, updates, chunks):
        from types import SimpleNamespace
        from unittest.mock import patch

        class Socket:
            def __init__(self):
                self.incoming = __import__("asyncio").Queue()
                self.rows = []
                self.client_state = SimpleNamespace(name="CONNECTED")
                self.application_state = SimpleNamespace(name="CONNECTED")
                self.close_args = None

            async def accept(self):
                pass

            async def receive_bytes(self):
                from fastapi import WebSocketDisconnect

                chunk = await self.incoming.get()
                if chunk is None:
                    self.client_state.name = "DISCONNECTED"
                    raise WebSocketDisconnect()
                return chunk

            async def send_json(self, value):
                self.rows.append(value)

            async def close(self, **kwargs):
                self.close_args = kwargs
                self.application_state.name = "DISCONNECTED"

        class FakeClient:
            instance = None

            def __init__(self, *args, **kwargs):
                self.updates = iter(updates)
                self.chunks = []
                self.started = 0
                self.finished = 0
                self.closed = 0
                FakeClient.instance = self

            async def start(self):
                self.started += 1
                return f"session-test-{self.started}"

            async def chunk(self, pcm):
                self.chunks.append(pcm)
                from app.core.qwen_stream import QwenStreamUpdate

                return QwenStreamUpdate(next(self.updates, ""), "Chinese")

            async def finish(self):
                from app.core.qwen_stream import QwenStreamUpdate

                self.finished += 1
                return QwenStreamUpdate("", "Chinese")

            async def close(self):
                self.closed += 1

        ws = Socket()
        with patch.object(self.v5, "QwenStreamClient", FakeClient):
            import asyncio

            task = asyncio.create_task(self.v5.run_v5_session(ws))
            for chunk in chunks:
                await ws.incoming.put(chunk)
            await ws.incoming.put(None)
            await asyncio.wait_for(task, 2)
        return ws, FakeClient.instance

    async def test_v1_0_5_preserves_v4_response_shape_and_finishes_on_silence(self):
        updates = ["第一句话", "第一句话。", "第一句话。", "第一句话。",
                   "第一句话。", "第一句话。"]
        chunks = [b"\x00\x10" * 7680] * 3 + [b"\0" * 15360] * 3
        ws, client = await self.run_case(updates, chunks)

        self.assertEqual(len(ws.rows), 6)
        self.assertEqual(sum(row["finished"] for row in ws.rows), 1)
        self.assertEqual(ws.rows[-1]["text"], "第一句话。")
        self.assertTrue(ws.rows[-1]["finished"])
        for row in ws.rows:
            self.assertEqual(set(row), {"key", "text", "finished", "bg", "ed"})
            self.assertEqual(row["key"], "segment-000001")
        self.assertEqual(
            [row["ed"] for row in ws.rows],
            [round(index * 0.48, 2) for index in range(1, 7)],
        )
        self.assertEqual(len(client.chunks), 6)
        self.assertTrue(all(len(pcm) == 7680 * struct.calcsize("<f") for pcm in client.chunks))
        self.assertEqual(client.started, 2)
        self.assertEqual(client.finished, 2)
        self.assertEqual(client.closed, 1)

    async def test_new_speech_after_finished_uses_only_cumulative_suffix(self):
        updates = [
            "第一句话",
            "第一句话。",
            "第一句话。",
            "第一句话。",
            "第一句话。",
            "第一句话。",
            "第二句话",
            "第二句话。",
        ]
        chunks = (
            [b"\x00\x10" * 7680] * 3
            + [b"\0" * 15360] * 3
            + [b"\x00\x10" * 7680, b"\0" * 15360]
        )
        ws, _ = await self.run_case(updates, chunks)
        rows = ws.rows
        self.assertEqual(rows[6]["key"], "segment-000002")
        self.assertEqual(rows[6]["text"], "第二句话")
        self.assertEqual(rows[7]["text"], "第二句话。")

    async def test_first_text_clears_lead_in_silence(self):
        updates = ["", "", "", "嗯。"]
        chunks = [b"\0" * 15360] * 3 + [b"\x00\x10" * 7680]
        ws, _ = await self.run_case(updates, chunks)

        self.assertEqual(len(ws.rows), 4)
        self.assertFalse(any(row["finished"] for row in ws.rows))
        self.assertEqual(ws.rows[-1]["text"], "嗯。")

    async def test_session_restarts_at_limit_without_decoded_text(self):
        from unittest.mock import patch

        with patch.object(self.v5, "_MAX_SEGMENT_SAMPLES", 7680 * 2):
            ws, client = await self.run_case(["", ""], [b"\0" * 15360] * 2)

        self.assertEqual(len(ws.rows), 1)
        self.assertFalse(any(row["finished"] for row in ws.rows))
        self.assertEqual(client.started, 2)
        self.assertEqual(client.finished, 2)


if __name__ == "__main__":
    unittest.main()
