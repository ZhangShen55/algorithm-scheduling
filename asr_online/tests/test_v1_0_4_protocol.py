import asyncio
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import ws_online
from app.api.routes import ws_online_v4 as v4
from app.core.correction_scheduler import CorrectionResult


PCM = b"\x01\x00" * 7680


class ScriptedOnlineModel:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        index = min(len(self.calls) - 1, len(self.texts) - 1)
        return [{"text": self.texts[index]}]


class RecordingPunctuation:
    def __init__(self, suffix="。"):
        self.suffix = suffix
        self.calls = []

    def __call__(self, text, cache):
        self.calls.append((text, cache))
        return [{"text": f"{text}{self.suffix}"}]


class FakeScheduler:
    def __init__(
        self,
        text="整句纠正。",
        immediate=True,
        queue_depth=0,
        submit_allowed=True,
    ):
        self.text = text
        self.immediate = immediate
        self.queue_depth = queue_depth
        self.submit_allowed = submit_allowed
        self.submissions = []
        self.queues = {}
        self.unregistered = []

    async def register_session(self, connection_id):
        queue = asyncio.Queue()
        self.queues[connection_id] = queue
        return queue

    async def unregister_session(self, connection_id):
        self.unregistered.append(connection_id)
        self.queues.pop(connection_id, None)

    async def submit(self, snapshot):
        self.submissions.append(snapshot)
        if not self.submit_allowed:
            return False
        if self.immediate:
            self.queues[snapshot.connection_id].put_nowait(
                CorrectionResult(
                    snapshot=snapshot,
                    text=self.text,
                    queue_seconds=0.001,
                    inference_seconds=0.01,
                )
            )
        return True

    def metrics_snapshot(self):
        return {
            "queue_depth": self.queue_depth,
            "inflight": 0,
            "sessions": len(self.queues),
            "workers": 1,
        }

    def deliver(self, index=0, text=None):
        snapshot = self.submissions[index]
        self.queues[snapshot.connection_id].put_nowait(
            CorrectionResult(
                snapshot=snapshot,
                text=text or self.text,
                queue_seconds=0.001,
                inference_seconds=0.01,
            )
        )


class V1_0_4ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(ws_online.router)

    def run_session(self, texts, *, scheduler=None, punctuation=None, patches=()):
        online = ScriptedOnlineModel(texts)
        scheduler = scheduler or FakeScheduler()
        punctuation = punctuation or RecordingPunctuation()
        responses = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(v4, "get_online_model", return_value=online))
            stack.enter_context(
                patch.object(v4, "get_punct_pipeline", return_value=punctuation)
            )
            stack.enter_context(
                patch.object(v4, "get_correction_scheduler", return_value=scheduler)
            )
            for target, value in patches:
                stack.enter_context(patch.object(v4, target, value))
            with TestClient(self.app) as client:
                with client.websocket_connect("/v1.0.4/seacraft_asr_online") as websocket:
                    for _ in texts:
                        websocket.send_bytes(PCM)
                        responses.append(websocket.receive_json())
        return responses, online, scheduler, punctuation

    def test_candidate_correction_uses_stable_key_and_finishes_once(self):
        responses, online, scheduler, punctuation = self.run_session(
            ["功能", "调度", "", ""]
        )

        self.assertEqual(len(online.calls), 4)
        self.assertEqual(len(scheduler.submissions), 1)
        self.assertEqual(scheduler.submissions[0].trigger, "first_empty_result")
        self.assertEqual(scheduler.submissions[0].audio.size, 2 * 7680)
        self.assertEqual({item["key"] for item in responses}, {"segment-000001"})
        self.assertEqual([item["finished"] for item in responses], [False, False, False, True])
        self.assertEqual(responses[-1]["text"], "整句纠正。")
        self.assertGreaterEqual(responses[-1]["ed"], responses[-2]["ed"])
        self.assertEqual(punctuation.calls, [])
        self.assertEqual(len(scheduler.unregistered), 1)

    def test_not_ready_correction_falls_back_within_budget(self):
        scheduler = FakeScheduler(immediate=False)
        punctuation = RecordingPunctuation()
        started = time.monotonic()
        responses, _, scheduler, punctuation = self.run_session(
            ["功能", "调度", "", ""],
            scheduler=scheduler,
            punctuation=punctuation,
            patches=(("_FINAL_BUDGET_SECONDS", 0.01),),
        )

        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(responses[-1]["text"], "功能调度。")
        self.assertTrue(responses[-1]["finished"])
        self.assertEqual(len(scheduler.submissions), 1)
        self.assertEqual([item[0] for item in punctuation.calls], ["功能调度"])

    def test_speech_resume_invalidates_candidate_without_resubmitting(self):
        scheduler = FakeScheduler(text="过期纠正。", immediate=True)
        punctuation = RecordingPunctuation()
        responses, _, scheduler, punctuation = self.run_session(
            ["功能", "", "继续", "", ""],
            scheduler=scheduler,
            punctuation=punctuation,
            patches=(("_FINAL_BUDGET_SECONDS", 0.001),),
        )

        finished = [item for item in responses if item["finished"]]
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["text"], "功能继续。")
        self.assertEqual(len(scheduler.submissions), 1)

    def test_segment_limit_bounds_pcm_and_forces_completion(self):
        responses, _, scheduler, _ = self.run_session(
            ["功能", "继续"],
            patches=(("_MAX_SEGMENT_SAMPLES", 2 * 7680),),
        )

        self.assertFalse(responses[0]["finished"])
        self.assertTrue(responses[1]["finished"])
        self.assertEqual(len(scheduler.submissions), 1)
        self.assertEqual(scheduler.submissions[0].audio.size, 2 * 7680)

    def test_partial_punctuation_is_throttled(self):
        punctuation = RecordingPunctuation(suffix="，")
        responses, _, _, punctuation = self.run_session(
            ["功能", "调度", "继续"],
            punctuation=punctuation,
            patches=(
                ("_PARTIAL_PUNCTUATION_UNITS", 4),
                ("_PARTIAL_PUNCTUATION_SAMPLES", 999999),
            ),
        )

        self.assertEqual(len(punctuation.calls), 1)
        self.assertEqual(responses[1]["text"], "功能调度，")
        self.assertEqual(responses[2]["text"], "功能调度，继续")

    def test_initial_silence_never_finishes_or_submits_correction(self):
        responses, _, scheduler, _ = self.run_session(["", "", ""])

        self.assertFalse(any(item["finished"] for item in responses))
        self.assertEqual(scheduler.submissions, [])
        self.assertEqual(
            set(responses[0]), {"key", "text", "finished", "bg", "ed"}
        )

    def test_pcm_before_first_nonempty_result_is_retained_for_correction(self):
        _, _, scheduler, _ = self.run_session(["", "功能", "", ""])

        self.assertEqual(len(scheduler.submissions), 1)
        self.assertEqual(scheduler.submissions[0].audio.size, 2 * 7680)
        self.assertEqual(scheduler.submissions[0].start_sample, 0)

    def test_queue_overload_skips_correction_and_finishes_with_online_text(self):
        scheduler = FakeScheduler(queue_depth=v4._CORRECTION_QUEUE_CAPACITY)
        responses, _, scheduler, punctuation = self.run_session(
            ["功能", "", ""], scheduler=scheduler
        )

        self.assertEqual(scheduler.submissions, [])
        self.assertTrue(responses[-1]["finished"])
        self.assertEqual(responses[-1]["text"], "功能。")
        self.assertEqual(len(punctuation.calls), 1)

    def test_late_correction_cannot_rewrite_a_finished_segment(self):
        online = ScriptedOnlineModel(["功能", "", "", "继续"])
        scheduler = FakeScheduler(immediate=False)
        punctuation = RecordingPunctuation()
        with (
            patch.object(v4, "get_online_model", return_value=online),
            patch.object(v4, "get_punct_pipeline", return_value=punctuation),
            patch.object(v4, "get_correction_scheduler", return_value=scheduler),
            patch.object(v4, "_FINAL_BUDGET_SECONDS", 0.001),
            TestClient(self.app) as client,
        ):
            with client.websocket_connect(
                "/v1.0.4/seacraft_asr_online"
            ) as websocket:
                responses = []
                for _ in range(3):
                    websocket.send_bytes(PCM)
                    responses.append(websocket.receive_json())
                scheduler.deliver(text="迟到纠正。")
                websocket.send_bytes(PCM)
                responses.append(websocket.receive_json())

        finished = [item for item in responses if item["finished"]]
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["text"], "功能。")
        self.assertEqual(responses[-1]["key"], "segment-000002")
        self.assertFalse(responses[-1]["finished"])

    def test_two_finished_segments_have_unique_keys_and_nonoverlapping_timestamps(self):
        responses, _, _, _ = self.run_session(["第一", "", "", "第二", "", ""])

        finished = [item for item in responses if item["finished"]]
        self.assertEqual(len(finished), 2)
        self.assertEqual(
            [item["key"] for item in finished],
            ["segment-000001", "segment-000002"],
        )
        self.assertGreaterEqual(finished[1]["bg"], finished[0]["ed"])


if __name__ == "__main__":
    unittest.main()
