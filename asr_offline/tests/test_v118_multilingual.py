from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.entity.data import AsrRequestParams


SUPPORTED_PARA_LANGUAGES = ("auto", "zh", "en")
SUCCESS_KEYS = {
    "language",
    "segments",
    "text",
    "speed_info",
    "load_audio_time_ms",
    "gpu_time_ms",
}


@dataclass
class FakeWord:
    start: float
    end: float
    word: str


@dataclass
class FakeSegment:
    start: float
    end: float
    text: str
    words: list[FakeWord] | None = None


class FakeWhisper:
    def __init__(self, segments):
        self._segments = list(segments)
        self.calls = []

    def transcribe(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return iter(self._segments), SimpleNamespace(language="fr")


def make_request(
    *,
    language="fr",
    word_timestamps=False,
    show_spk=False,
    show_emotion=False,
    show_role_identify=False,
):
    return AsrRequestParams(
        audioFile=object(),
        language=language,
        wordTimestamps=word_timestamps,
        hotWords=[],
        showSpk=show_spk,
        openPanel=False,
        showEmotion=show_emotion,
        showSpeed=False,
        showRoleIdentify=show_role_identify,
    )


def make_context(request, *, duration=60.0):
    return SimpleNamespace(
        request=request,
        tmp_path="/tmp/fake-french-audio.wav",
        tmp_paths=[],
        audio_total_s=duration,
        task_id="fake-french-task",
        load_audio_time_ms=12.345,
    )


class RouteAssemblyContractTests(unittest.TestCase):
    def test_retired_routes_are_absent_from_openapi(self):
        from app.main import create_app

        paths = create_app().openapi()["paths"]

        self.assertNotIn("/v1.1.7/seacraft_asr", paths)
        self.assertNotIn("/audio/detect_mandarin", paths)
        self.assertIn("/v1.1.8/seacraft_asr", paths)
        self.assertIn("/audio/db_snr", paths)
        self.assertIn("/text/question", paths)


class HttpLanguageFormContractTests(unittest.TestCase):
    @staticmethod
    def _build_client():
        import app.api.routes.asr_v18 as route

        app = FastAPI()
        app.include_router(route.router)
        return route, TestClient(app)

    def test_explicit_empty_language_is_rejected_before_audio_preparation(self):
        route, client = self._build_client()
        prepare = AsyncMock(return_value=({"msg": "audio read", "code": 4002}, None))

        with patch.object(route, "prepare_asr_context", prepare):
            response = client.post(
                "/v1.1.8/seacraft_asr",
                data={"language": ""},
                files={"audioFile": ("dummy.wav", b"not-read", "audio/wav")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"msg": "不支持的语言: ", "code": 4009})
        prepare.assert_not_awaited()

    def test_omitted_language_keeps_auto_default(self):
        route, client = self._build_client()
        prepare = AsyncMock(
            side_effect=lambda request: (None, make_context(request)),
        )
        paraformer = AsyncMock(
            side_effect=lambda context: {"language": context.request.language},
        )

        with (
            patch.object(route, "prepare_asr_context", prepare),
            patch.object(route, "run_paraformer_asr", paraformer),
        ):
            response = client.post(
                "/v1.1.8/seacraft_asr",
                files={"audioFile": ("dummy.wav", b"not-read", "audio/wav")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"language": "auto"})
        request = prepare.await_args.args[0]
        self.assertEqual(request.language, "auto")
        paraformer.assert_awaited_once()

    def test_non_string_language_keeps_fastapi_validation(self):
        route, client = self._build_client()
        prepare = AsyncMock()

        with patch.object(route, "prepare_asr_context", prepare):
            response = client.post(
                "/v1.1.8/seacraft_asr",
                files={
                    "audioFile": ("dummy.wav", b"not-read", "audio/wav"),
                    "language": (
                        "language.bin",
                        b"fr",
                        "application/octet-stream",
                    ),
                },
            )

        self.assertEqual(response.status_code, 422)
        prepare.assert_not_awaited()


class LanguageDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_paraformer_languages_are_normalized_before_dispatch(self):
        import app.api.routes.asr_v18 as route

        for supplied, expected in ((" Auto ", "auto"), ("ZH", "zh"), ("EN", "en")):
            with self.subTest(language=supplied):
                request = make_request(language=supplied)
                ctx = make_context(request)
                prepare = AsyncMock(return_value=(None, ctx))
                paraformer = AsyncMock(
                    side_effect=lambda prepared: {"language": prepared.request.language}
                )
                whisper_getter = Mock(return_value=FakeWhisper([]))

                with (
                    patch.object(route, "prepare_asr_context", prepare),
                    patch.object(route, "run_paraformer_asr", paraformer),
                    patch.object(route, "get_whisper_model", whisper_getter, create=True),
                ):
                    result = await route.api_asr_v18(request)

                self.assertEqual(result, {"language": expected})
                self.assertEqual(request.language, expected)
                prepare.assert_awaited_once_with(request)
                paraformer.assert_awaited_once_with(ctx)
                whisper_getter.assert_not_called()

    async def test_unknown_and_empty_languages_are_rejected_before_audio_preparation(self):
        import app.api.routes.asr_v18 as route

        for supplied, normalized in ((" Klingon ", "klingon"), ("", "")):
            with self.subTest(language=supplied):
                request = make_request(language=supplied)
                prepare = AsyncMock(return_value=(None, make_context(request)))
                paraformer = AsyncMock(return_value={"unexpected": True})

                with (
                    patch.object(route, "prepare_asr_context", prepare),
                    patch.object(route, "run_paraformer_asr", paraformer),
                ):
                    result = await route.api_asr_v18(request)

                self.assertEqual(
                    result,
                    {"msg": f"不支持的语言: {normalized}", "code": 4009},
                )
                prepare.assert_not_awaited()
                paraformer.assert_not_awaited()

    async def test_disabled_french_is_rejected_before_audio_preparation(self):
        import app.api.routes.asr_v18 as route

        request = make_request(language=" FR ")
        prepare = AsyncMock(return_value=(None, make_context(request)))
        paraformer = AsyncMock(return_value={"path": "paraformer"})
        whisper_getter = Mock(return_value=FakeWhisper([]))

        with (
            patch.object(route, "settings", SimpleNamespace(open_mul_lang=False), create=True),
            patch.object(route, "prepare_asr_context", prepare),
            patch.object(route, "run_paraformer_asr", paraformer),
            patch.object(route, "get_whisper_model", whisper_getter, create=True),
        ):
            result = await route.api_asr_v18(request)

        self.assertEqual(
            result,
            {"msg": "未开启小语种识别或模型未就绪", "code": 4003},
        )
        self.assertEqual(request.language, "fr")
        prepare.assert_not_awaited()
        whisper_getter.assert_not_called()

    async def test_unready_french_is_rejected_before_audio_preparation(self):
        import app.api.routes.asr_v18 as route

        request = make_request(language="fr")
        prepare = AsyncMock(return_value=(None, make_context(request)))
        paraformer = AsyncMock(return_value={"path": "paraformer"})
        whisper_getter = Mock(return_value=None)

        with (
            patch.object(route, "settings", SimpleNamespace(open_mul_lang=True), create=True),
            patch.object(route, "prepare_asr_context", prepare),
            patch.object(route, "run_paraformer_asr", paraformer),
            patch.object(route, "get_whisper_model", whisper_getter, create=True),
        ):
            result = await route.api_asr_v18(request)

        self.assertEqual(
            result,
            {"msg": "未开启小语种识别或模型未就绪", "code": 4003},
        )
        prepare.assert_not_awaited()
        whisper_getter.assert_called_once_with()


class WhisperResponseContractTests(unittest.IsolatedAsyncioTestCase):
    async def _call_french(self, request, segments, *, duration=60.0, extra_patches=()):
        import app.api.routes.asr_v18 as route

        ctx = make_context(request, duration=duration)
        model = FakeWhisper(segments)
        prepare = AsyncMock(return_value=(None, ctx))
        paraformer = AsyncMock(return_value={"path": "paraformer"})

        patches = [
            patch.object(route, "settings", SimpleNamespace(open_mul_lang=True), create=True),
            patch.object(route, "prepare_asr_context", prepare),
            patch.object(route, "run_paraformer_asr", paraformer),
            patch.object(route, "get_whisper_model", Mock(return_value=model), create=True),
        ]
        patches.extend(extra_patches)

        entered = []
        try:
            for patcher in patches:
                entered.append(patcher)
                patcher.start()
            result = await route.api_asr_v18(request)
        finally:
            for patcher in reversed(entered):
                patcher.stop()

        return result, model, prepare, paraformer

    async def test_french_uses_whisper_and_preserves_success_top_level_fields(self):
        request = make_request(language=" FR ")
        segment = FakeSegment(0.0, 2.0, " bonjour le monde ")

        result, model, prepare, paraformer = await self._call_french(request, [segment])

        self.assertEqual(set(result), SUCCESS_KEYS)
        self.assertEqual(result["language"], "fr")
        self.assertEqual(result["text"], "bonjour le monde")
        self.assertNotIn("code", result)
        prepare.assert_awaited_once_with(request)
        paraformer.assert_not_awaited()
        self.assertEqual(len(model.calls), 1)
        args, kwargs = model.calls[0]
        self.assertEqual(args, ("/tmp/fake-french-audio.wav",))
        self.assertEqual(
            kwargs,
            {"language": "fr", "beam_size": 5, "word_timestamps": False},
        )

    async def test_requested_role_and_emotion_are_null_without_enhancement_models(self):
        import app.api.routes.asr_common as common
        import app.core.models as models

        request = make_request(
            show_spk=True,
            show_emotion=True,
            show_role_identify=True,
        )
        emotion_getter = Mock()
        speaker_getter = Mock()
        extract = Mock()
        identify = Mock()

        result, _, _, _ = await self._call_french(
            request,
            [FakeSegment(0.0, 1.0, "bonjour")],
            extra_patches=(
                patch.object(common, "get_emotion_model", emotion_getter),
                patch.object(common, "extract_features", extract),
                patch.object(common, "identify_teacher", identify),
                patch.object(models, "get_speaker_model", speaker_getter, create=True),
            ),
        )

        self.assertIn("segments", result)
        self.assertIsNone(result["segments"][0]["role"])
        self.assertIsNone(result["segments"][0]["emotion"])
        emotion_getter.assert_not_called()
        speaker_getter.assert_not_called()
        extract.assert_not_called()
        identify.assert_not_called()

    async def test_role_and_emotion_are_absent_when_not_requested(self):
        request = make_request(
            show_spk=False,
            show_emotion=False,
            show_role_identify=False,
        )

        result, _, _, _ = await self._call_french(
            request,
            [FakeSegment(0.0, 1.0, "bonjour")],
        )

        self.assertIn("segments", result)
        item = result["segments"][0]
        self.assertNotIn("role", item)
        self.assertNotIn("emotion", item)

    async def test_role_identification_request_alone_emits_null_role(self):
        request = make_request(show_spk=False, show_role_identify=True)

        result, _, _, _ = await self._call_french(
            request,
            [FakeSegment(0.0, 1.0, "bonjour")],
        )

        self.assertIn("segments", result)
        self.assertIn("role", result["segments"][0])
        self.assertIsNone(result["segments"][0]["role"])

    async def test_word_timestamps_false_returns_an_empty_array(self):
        request = make_request(word_timestamps=False)
        words = [FakeWord(0.1, 0.4, " bonjour ")]

        result, _, _, _ = await self._call_french(
            request,
            [FakeSegment(0.0, 1.0, "bonjour", words)],
        )

        self.assertIn("segments", result)
        self.assertEqual(result["segments"][0]["segment_words"], [])

    async def test_word_timestamps_true_maps_real_whisper_words(self):
        request = make_request(word_timestamps=True)
        words = [
            FakeWord(0.123, 0.456, " bonjour "),
            FakeWord(0.5, 0.9, " élève "),
        ]

        result, _, _, _ = await self._call_french(
            request,
            [FakeSegment(0.0, 1.0, "bonjour élève", words)],
        )

        self.assertIn("segments", result)
        self.assertEqual(
            result["segments"][0]["segment_words"],
            [
                {"bg": "0.12", "ed": "0.46", "word_text": "bonjour"},
                {"bg": "0.50", "ed": "0.90", "word_text": "élève"},
            ],
        )

    async def test_segment_speed_uses_raw_times_and_configured_factor(self):
        request = make_request()
        segment = FakeSegment(0.004, 0.499, "L’école française aujourd’hui")

        result, _, _, _ = await self._call_french(request, [segment])

        self.assertIn("segments", result)
        item = result["segments"][0]
        self.assertEqual(item["bg"], "0.00")
        self.assertEqual(item["ed"], "0.50")
        self.assertEqual(item["speed"], int(3 * 60 / (0.499 - 0.004) * 0.4))
        self.assertIsInstance(item["speed"], int)
        self.assertGreaterEqual(item["speed"], 0)

    async def test_speed_info_does_not_apply_the_segment_rate_factor(self):
        request = make_request()
        ten_words = "un deux trois quatre cinq six sept huit neuf dix"

        result, _, _, _ = await self._call_french(
            request,
            [FakeSegment(0.0, 60.0, ten_words)],
            duration=60.0,
        )

        self.assertIn("segments", result)
        self.assertEqual(result["segments"][0]["speed"], 4)
        self.assertIn("speed_info", result)
        by_unit = {item["unit"]: item["segment_info"] for item in result["speed_info"]}
        self.assertEqual(by_unit[1], {"segment_count": 1, "speed": [10]})
        self.assertEqual(by_unit[5], {"segment_count": 1, "speed": [10]})
        self.assertEqual(by_unit[10], {"segment_count": 1, "speed": [10]})

    async def test_speed_info_keeps_one_five_and_ten_minute_windows(self):
        request = make_request()

        result, _, _, _ = await self._call_french(
            request,
            [FakeSegment(0.0, 1.0, "bonjour")],
            duration=442.85,
        )

        self.assertIn("speed_info", result)
        by_unit = {item["unit"]: item["segment_info"] for item in result["speed_info"]}
        self.assertEqual(by_unit[1]["segment_count"], 8)
        self.assertEqual(by_unit[5]["segment_count"], 2)
        self.assertEqual(by_unit[10]["segment_count"], 1)

    async def test_whisper_empty_result_returns_business_4008(self):
        request = make_request()

        result, _, _, _ = await self._call_french(request, [])

        self.assertEqual(
            result,
            {"msg": "音频文件为空或未检测到任何人声", "code": 4008},
        )

    async def test_whisper_generator_is_consumed_before_gpu_slot_release(self):
        import app.api.routes.asr_common as common
        import app.api.routes.asr_v18 as route

        slot_active = False
        yield_states = []

        @asynccontextmanager
        async def tracked_gpu_slot(*, task_id):
            nonlocal slot_active
            slot_active = True
            try:
                yield
            finally:
                slot_active = False

        class SlotCheckingWhisper(FakeWhisper):
            def transcribe(self, *args, **kwargs):
                self.calls.append((args, kwargs))

                def segments():
                    yield_states.append(slot_active)
                    yield FakeSegment(0.0, 1.0, "bonjour")

                return segments(), SimpleNamespace(language="fr")

        request = make_request()
        model = SlotCheckingWhisper([])
        ctx = make_context(request)
        prepare = AsyncMock(return_value=(None, ctx))

        with (
            patch.object(route, "settings", SimpleNamespace(open_mul_lang=True), create=True),
            patch.object(route, "prepare_asr_context", prepare),
            patch.object(route, "run_paraformer_asr", AsyncMock(return_value={})),
            patch.object(route, "get_whisper_model", Mock(return_value=model), create=True),
            patch.object(common, "acquire_gpu_slot", tracked_gpu_slot),
        ):
            result = await route.api_asr_v18(request)

        self.assertIn("text", result)
        self.assertEqual(result["text"], "bonjour")
        self.assertEqual(yield_states, [True])
        self.assertFalse(slot_active)


class UnicodeSpeechRateTests(unittest.TestCase):
    def test_french_unicode_words_keep_accents_and_internal_apostrophes(self):
        from app.utils.feature_utils import count_content_words

        self.assertEqual(count_content_words("L’école française aujourd’hui"), 3)

    def test_internal_hyphens_and_apostrophes_do_not_create_extra_words(self):
        from app.utils.feature_utils import count_content_words

        self.assertEqual(count_content_words("l'école porte-monnaie - ' français"), 3)

    def test_chinese_characters_and_unicode_words_keep_the_existing_mixed_count(self):
        from app.utils.feature_utils import count_content_words

        self.assertEqual(count_content_words("中文 e\u0301le\u0300ve 123"), 4)
