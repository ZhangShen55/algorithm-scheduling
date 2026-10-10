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


class EmptyOnlineModel(FakeOnlineModel):
    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return [{"text": ""}]


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


class NoEndVadModel:
    def __init__(self):
        self.cache_ids = []
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        self.cache_ids.append(id(kwargs["cache"]))
        return [{"value": []}]


class ScriptedVadModel:
    def __init__(self, events):
        self.events = list(events)
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        index = len(self.calls) - 1
        return [{"value": self.events[index] if index < len(self.events) else []}]


class RecordingCorrectionModel(FakeCorrectionModel):
    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return [{"text": f"纠正{len(self.calls)}"}]


class V1_0_2ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(ws_online.router)

    def test_routes_include_v1_v2_and_v3(self):
        paths = {route.path for route in ws_online.router.routes}
        self.assertIn("/v1.0.1/seacraft_asr_online", paths)
        self.assertIn("/v1.0.2/seacraft_asr_online", paths)
        self.assertIn("/v1.0.3/seacraft_asr_online", paths)

    def test_v1_0_1_keeps_legacy_response_shape(self):
        online = FakeOnlineModel()
        identity_punctuation = lambda text, cache: [{"text": text}]

        with ExitStack() as stack:
            stack.enter_context(patch.object(ws_online, "get_online_model", return_value=online))
            stack.enter_context(
                patch.object(ws_online, "get_punct_pipeline", return_value=identity_punctuation)
            )
            with TestClient(self.app) as client:
                with client.websocket_connect("/v1.0.1/seacraft_asr_online") as websocket:
                    websocket.send_bytes(b"\x01\x00" * 7680)
                    response = websocket.receive_json()

        self.assertEqual(
            set(response), {"key", "text", "finished", "bg", "ed"}
        )
        self.assertIsInstance(response["key"], str)
        self.assertIsInstance(response["text"], str)
        self.assertIsInstance(response["finished"], bool)

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
                    websocket.send_bytes(b"\x01\x00" * 7680)
                    final = websocket.receive_json()
                    third = websocket.receive_json()

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
                    websocket.send_bytes(b"\x01\x00" * 7680)
                    final = websocket.receive_json()
                    third = websocket.receive_json()

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

    def test_v2_keeps_one_vad_cache_and_uses_configured_silence(self):
        online = FakeOnlineModel()
        vad = NoEndVadModel()
        correction = FakeCorrectionModel()
        identity_punctuation = lambda text, cache: [{"text": text}]

        with ExitStack() as stack:
            stack.enter_context(patch.object(ws_online, "get_online_model", return_value=online))
            stack.enter_context(patch.object(ws_online, "get_vad_model", return_value=vad))
            stack.enter_context(
                patch.object(ws_online, "get_correction_model", return_value=correction)
            )
            stack.enter_context(
                patch.object(ws_online, "get_punct_pipeline", return_value=identity_punctuation)
            )
            with TestClient(self.app) as client:
                with client.websocket_connect("/v1.0.2/seacraft_asr_online") as websocket:
                    for _ in range(8):
                        websocket.send_bytes(b"\x01\x00" * 7680)
                        websocket.receive_json()

        self.assertEqual(vad.cache_ids, [vad.cache_ids[0]] * 8)
        self.assertTrue(
            all(
                call["max_end_silence_time"] == ws_online._VAD_MAX_END_SILENCE_MS
                for call in vad.calls
            )
        )

    def test_idle_vad_cache_resets_once_per_bounded_silence_window(self):
        online = EmptyOnlineModel()
        vad = NoEndVadModel()
        correction = FakeCorrectionModel()
        identity_punctuation = lambda text, cache: [{"text": text}]

        with ExitStack() as stack:
            stack.enter_context(patch.object(ws_online, "get_online_model", return_value=online))
            stack.enter_context(patch.object(ws_online, "get_vad_model", return_value=vad))
            stack.enter_context(
                patch.object(ws_online, "get_correction_model", return_value=correction)
            )
            stack.enter_context(
                patch.object(ws_online, "get_punct_pipeline", return_value=identity_punctuation)
            )
            with TestClient(self.app) as client:
                with client.websocket_connect("/v1.0.2/seacraft_asr_online") as websocket:
                    for _ in range(15):
                        websocket.send_bytes(b"\x00\x00" * 7680)
                        websocket.receive_json()

        self.assertEqual(vad.cache_ids[:13], [vad.cache_ids[0]] * 13)
        self.assertEqual(vad.cache_ids[13:], [vad.cache_ids[13]] * 2)
        self.assertNotEqual(vad.cache_ids[12], vad.cache_ids[13])

    def test_delayed_vad_start_replaces_online_fallback_timestamp(self):
        online = FakeOnlineModel()
        vad = ScriptedVadModel([[], [[700, -1]], [[-1, 1000]]])
        correction = RecordingCorrectionModel()
        identity_punctuation = lambda text, cache: [{"text": text}]

        with ExitStack() as stack:
            stack.enter_context(patch.object(ws_online, "get_online_model", return_value=online))
            stack.enter_context(patch.object(ws_online, "get_vad_model", return_value=vad))
            stack.enter_context(
                patch.object(ws_online, "get_correction_model", return_value=correction)
            )
            stack.enter_context(
                patch.object(ws_online, "get_punct_pipeline", return_value=identity_punctuation)
            )
            with TestClient(self.app) as client:
                with client.websocket_connect("/v1.0.2/seacraft_asr_online") as websocket:
                    for index in range(4):
                        websocket.send_bytes(b"\x01\x00" * 7680)
                        response = websocket.receive_json()
                        if index == 3:
                            final = response
                            websocket.receive_json()

        self.assertEqual(final["bg"], 0.7)
        self.assertEqual(final["ed"], 1.0)
        self.assertEqual(correction.calls[0]["input"].size, int(0.45 * 16000))

    def test_delayed_vad_start_rejects_old_end_at_same_boundary(self):
        online = FakeOnlineModel()
        vad = ScriptedVadModel(
            [
                [],
                [],
                [[960, -1], [-1, 960]],
                [],
                [],
            ]
        )
        correction = RecordingCorrectionModel()
        identity_punctuation = lambda text, cache: [{"text": text}]

        with ExitStack() as stack:
            stack.enter_context(patch.object(ws_online, "get_online_model", return_value=online))
            stack.enter_context(patch.object(ws_online, "get_vad_model", return_value=vad))
            stack.enter_context(
                patch.object(ws_online, "get_correction_model", return_value=correction)
            )
            stack.enter_context(
                patch.object(ws_online, "get_punct_pipeline", return_value=identity_punctuation)
            )
            with TestClient(self.app) as client:
                with client.websocket_connect("/v1.0.2/seacraft_asr_online") as websocket:
                    responses = []
                    for _ in range(5):
                        websocket.send_bytes(b"\x01\x00" * 7680)
                        responses.append(websocket.receive_json())

        self.assertEqual(responses[2]["bg"], 0.96)
        self.assertFalse(any(item["finished"] for item in responses))
        self.assertEqual(correction.calls, [])

    def test_vad_events_keep_exact_cross_chunk_sample_positions(self):
        starts, ends = ws_online._vad_event_samples(
            [[0, -1], [-1, 2310], [2720, 3200]],
            cache_origin_sample=0,
            lower_bound_sample=0,
            upper_bound_sample=7 * 7680,
        )
        self.assertEqual(starts, [0, 43520])
        self.assertEqual(ends, [36960, 51200])

    def test_vad_events_ignore_zero_length_silence_segments(self):
        starts, ends = ws_online._vad_event_samples(
            [[0, 0], [480, 480], [-1, 900]],
            cache_origin_sample=0,
            lower_bound_sample=0,
            upper_bound_sample=16000,
        )
        self.assertEqual(starts, [])
        self.assertEqual(ends, [14400])

    def test_v2_keeps_end_tail_and_uses_preroll_for_next_sentence(self):
        online = FakeOnlineModel()
        vad = ScriptedVadModel(
            [
                [[0, -1]],
                [],
                [],
                [],
                [[-1, 2310]],
                [[2720, -1]],
                [[-1, 3200]],
            ]
        )
        correction = RecordingCorrectionModel()
        identity_punctuation = lambda text, cache: [{"text": text}]

        with ExitStack() as stack:
            stack.enter_context(patch.object(ws_online, "get_online_model", return_value=online))
            stack.enter_context(patch.object(ws_online, "get_vad_model", return_value=vad))
            stack.enter_context(
                patch.object(ws_online, "get_correction_model", return_value=correction)
            )
            stack.enter_context(
                patch.object(ws_online, "get_punct_pipeline", return_value=identity_punctuation)
            )
            with TestClient(self.app) as client:
                with client.websocket_connect("/v1.0.2/seacraft_asr_online") as websocket:
                    responses = []
                    for index in range(9):
                        websocket.send_bytes(b"\x01\x00" * 7680)
                        response = websocket.receive_json()
                        self.assertEqual(set(response), {"key", "text", "finished", "bg", "ed"})
                        responses.append(response)
                        if response["finished"]:
                            break

        finished = [item for item in responses if item["finished"]]
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["bg"], 0.0)
        self.assertEqual(finished[0]["ed"], 3.2)
        self.assertEqual(len(correction.calls), 1)
        # 2.72s start is within the 720ms merge window after 2.31s end;
        # the 0.09s tail and the following speech stay in one correction input.
        self.assertEqual(correction.calls[0]["input"].size, int(3.2 * 16000))

    def test_v2_merges_short_vad_gaps_and_finishes_once_after_timeout(self):
        online = FakeOnlineModel()
        vad = ScriptedVadModel(
            [
                [[0, -1]],
                [[-1, 400]],
                [[1000, -1]],
                [[-1, 1400]],
                [[2000, -1]],
                [[-1, 2400]],
                [],
                [],
            ]
        )
        correction = RecordingCorrectionModel()
        identity_punctuation = lambda text, cache: [{"text": text}]

        with ExitStack() as stack:
            stack.enter_context(patch.object(ws_online, "get_online_model", return_value=online))
            stack.enter_context(patch.object(ws_online, "get_vad_model", return_value=vad))
            stack.enter_context(
                patch.object(ws_online, "get_correction_model", return_value=correction)
            )
            stack.enter_context(
                patch.object(ws_online, "get_punct_pipeline", return_value=identity_punctuation)
            )
            with TestClient(self.app) as client:
                with client.websocket_connect("/v1.0.2/seacraft_asr_online") as websocket:
                    responses = []
                    for index in range(8):
                        websocket.send_bytes(b"\x01\x00" * 7680)
                        responses.append(websocket.receive_json())
                        if index == 7:
                            responses.append(websocket.receive_json())

        finished = [item for item in responses if item["finished"]]
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["bg"], 0.0)
        self.assertEqual(finished[0]["ed"], 2.4)
        self.assertEqual(len({item["key"] for item in finished}), 1)
        self.assertEqual(len(correction.calls), 1)

    def test_v2_finishes_after_gap_exceeds_merge_window_and_starts_new_key(self):
        online = FakeOnlineModel()
        vad = ScriptedVadModel(
            [
                [[0, -1]],
                [[-1, 400]],
                [],
                [[2000, -1]],
                [[-1, 2400]],
            ]
        )
        correction = RecordingCorrectionModel()
        identity_punctuation = lambda text, cache: [{"text": text}]

        with ExitStack() as stack:
            stack.enter_context(patch.object(ws_online, "get_online_model", return_value=online))
            stack.enter_context(patch.object(ws_online, "get_vad_model", return_value=vad))
            stack.enter_context(
                patch.object(ws_online, "get_correction_model", return_value=correction)
            )
            stack.enter_context(
                patch.object(ws_online, "get_punct_pipeline", return_value=identity_punctuation)
            )
            with TestClient(self.app) as client:
                with client.websocket_connect("/v1.0.2/seacraft_asr_online") as websocket:
                    outputs = []
                    for index in range(5):
                        websocket.send_bytes(b"\x01\x00" * 7680)
                        outputs.append(websocket.receive_json())
                        if index == 3:
                            outputs.append(websocket.receive_json())

        finished = [item for item in outputs if item["finished"]]
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["ed"], 0.4)
        self.assertGreaterEqual(len({item["key"] for item in outputs}), 2)
        self.assertEqual(len(correction.calls), 1)

    def test_v2_does_not_reuse_old_end_for_far_new_start(self):
        online = FakeOnlineModel()
        vad = ScriptedVadModel(
            [
                [[0, -1]],
                [[-1, 400]],
                [[1200, 1400]],
                [],
                [],
                [],
                [],
            ]
        )
        correction = RecordingCorrectionModel()
        identity_punctuation = lambda text, cache: [{"text": text}]

        with ExitStack() as stack:
            stack.enter_context(patch.object(ws_online, "get_online_model", return_value=online))
            stack.enter_context(patch.object(ws_online, "get_vad_model", return_value=vad))
            stack.enter_context(
                patch.object(ws_online, "get_correction_model", return_value=correction)
            )
            stack.enter_context(
                patch.object(ws_online, "get_punct_pipeline", return_value=identity_punctuation)
            )
            with TestClient(self.app) as client:
                with client.websocket_connect("/v1.0.2/seacraft_asr_online") as websocket:
                    outputs = []
                    for _ in range(8):
                        websocket.send_bytes(b"\x01\x00" * 7680)
                        outputs.append(websocket.receive_json())

        finished = [item for item in outputs if item["finished"]]
        self.assertEqual([item["ed"] for item in finished], [0.4, 1.4])
        self.assertEqual(len(correction.calls), 2)

    def test_v2_does_not_correct_or_finish_all_zero_silence(self):
        online = FakeOnlineModel()
        vad = ScriptedVadModel([[[0, -1]], [[-1, 480]]])
        correction = RecordingCorrectionModel()
        identity_punctuation = lambda text, cache: [{"text": text}]

        with ExitStack() as stack:
            stack.enter_context(patch.object(ws_online, "get_online_model", return_value=online))
            stack.enter_context(patch.object(ws_online, "get_vad_model", return_value=vad))
            stack.enter_context(
                patch.object(ws_online, "get_correction_model", return_value=correction)
            )
            stack.enter_context(
                patch.object(ws_online, "get_punct_pipeline", return_value=identity_punctuation)
            )
            with TestClient(self.app) as client:
                with client.websocket_connect("/v1.0.2/seacraft_asr_online") as websocket:
                    websocket.send_bytes(b"\x00\x00" * 7680)
                    first = websocket.receive_json()
                    websocket.send_bytes(b"\x00\x00" * 7680)
                    second = websocket.receive_json()

        self.assertFalse(first["finished"])
        self.assertFalse(second["finished"])
        self.assertEqual(correction.calls, [])

    def test_v3_forces_a_final_result_at_segment_limit(self):
        online = FakeOnlineModel()
        vad = NoEndVadModel()
        sensevoice = FakeSenseVoiceModel()

        with ExitStack() as stack:
            stack.enter_context(patch.object(ws_online, "get_online_model", return_value=online))
            stack.enter_context(patch.object(ws_online, "get_vad_model", return_value=vad))
            stack.enter_context(
                patch.object(ws_online, "get_sensevoice_model", return_value=sensevoice)
            )
            stack.enter_context(
                patch.object(ws_online, "_MAX_REVISION_SEGMENT_CHUNKS", 3)
            )
            with TestClient(self.app) as client:
                with client.websocket_connect("/v1.0.3/seacraft_asr_online") as websocket:
                    websocket.send_bytes(b"\x01\x00" * 7680)
                    websocket.receive_json()
                    websocket.send_bytes(b"\x01\x00" * 7680)
                    websocket.receive_json()
                    websocket.send_bytes(b"\x01\x00" * 7680)
                    websocket.receive_json()
                    final = websocket.receive_json()

        self.assertTrue(final["finished"])
        self.assertEqual(final["text"], "功能调度。")
        self.assertEqual(len(sensevoice.calls), 1)


if __name__ == "__main__":
    unittest.main()
