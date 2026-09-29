import unittest
from contextlib import ExitStack
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import ws_online


class FakeOnlineModel:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("is_final"):
            return [{"text": "在线最终"}]
        return [{"text": "公能" if len(self.calls) == 1 else "调度"}]


class FakeVadModel:
    def __init__(self):
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        events = [[0, -1]] if self.calls == 1 else [[-1, 400]]
        return [{"value": events}]


class FakeCorrectionModel:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return [{"text": "功能调度"}]


class FakeSenseVoiceModel(FakeCorrectionModel):
    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return [{"text": "<|zh|><|NEUTRAL|><|Speech|><|withitn|>功能调度。"}]


class V1_0_2ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(ws_online.router)

    def test_routes_include_v1_v2_and_v3(self):
        paths = {route.path for route in ws_online.router.routes}
        self.assertIn("/v1.0.1/seacraft_asr_online", paths)
        self.assertIn("/v1.0.2/seacraft_asr_online", paths)
        self.assertIn("/v1.0.3/seacraft_asr_online", paths)

    def test_merge_streaming_text_removes_lookback_overlap(self):
        self.assertEqual(
            ws_online._merge_streaming_text("我本说", "我本说是"),
            "我本说是",
        )
        self.assertEqual(
            ws_online._merge_streaming_text("功能", "调度"),
            "功能调度",
        )

    def test_v2_uses_same_key_for_partial_and_final_revision(self):
        online = FakeOnlineModel()
        vad = FakeVadModel()
        correction = FakeCorrectionModel()
        identity_punctuation = lambda text, cache: [{"text": text}]

        with ExitStack() as stack:
            stack.enter_context(patch.object(ws_online, "get_online_model", return_value=online))
            stack.enter_context(patch.object(ws_online, "get_vad_model", return_value=vad))
            stack.enter_context(
                patch.object(ws_online, "get_correction_model", return_value=correction)
            )
            stack.enter_context(
                patch.object(
                    ws_online,
                    "get_punct_pipeline",
                    return_value=identity_punctuation,
                )
            )
            with TestClient(self.app) as client:
                with client.websocket_connect("/v1.0.2/seacraft_asr_online") as websocket:
                    websocket.send_bytes(b"\x01\x00" * 7680)
                    first = websocket.receive_json()
                    websocket.send_bytes(b"\x01\x00" * 7680)
                    second = websocket.receive_json()
                    final = websocket.receive_json()

        self.assertFalse(first["finished"])
        self.assertFalse(second["finished"])
        self.assertTrue(final["finished"])
        self.assertEqual(first["key"], second["key"])
        self.assertEqual(second["key"], final["key"])
        self.assertEqual(final["text"], "功能调度")
        self.assertEqual(len(correction.calls), 1)
        self.assertTrue(correction.calls[0]["is_final"])

    def test_v3_uses_sensevoice_without_punctuation_model(self):
        online = FakeOnlineModel()
        vad = FakeVadModel()
        sensevoice = FakeSenseVoiceModel()

        with ExitStack() as stack:
            stack.enter_context(patch.object(ws_online, "get_online_model", return_value=online))
            stack.enter_context(patch.object(ws_online, "get_vad_model", return_value=vad))
            stack.enter_context(
                patch.object(ws_online, "get_sensevoice_model", return_value=sensevoice)
            )
            punctuation = stack.enter_context(
                patch.object(ws_online, "get_punct_pipeline")
            )
            with TestClient(self.app) as client:
                with client.websocket_connect("/v1.0.3/seacraft_asr_online") as websocket:
                    websocket.send_bytes(b"\x01\x00" * 7680)
                    first = websocket.receive_json()
                    websocket.send_bytes(b"\x01\x00" * 7680)
                    second = websocket.receive_json()
                    final = websocket.receive_json()

        punctuation.assert_not_called()
        self.assertFalse(first["finished"])
        self.assertFalse(second["finished"])
        self.assertTrue(final["finished"])
        self.assertEqual(first["key"], second["key"])
        self.assertEqual(second["key"], final["key"])
        self.assertEqual(final["text"], "功能调度。")
        self.assertEqual(len(sensevoice.calls), 1)
        self.assertEqual(sensevoice.calls[0]["language"], "zh")
        self.assertTrue(sensevoice.calls[0]["use_itn"])
        self.assertNotIn("is_final", sensevoice.calls[0])


if __name__ == "__main__":
    unittest.main()
