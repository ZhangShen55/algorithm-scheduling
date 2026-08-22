import inspect
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.api.routes.asr_common import prepare_asr_context
from app.entity.data import AsrRequestParams, get_asr_params
from fastapi import UploadFile


class RequestDefaultsTests(unittest.TestCase):
    def test_v118_form_defaults_enable_full_result_without_role_identification(self) -> None:
        parameters = inspect.signature(get_asr_params).parameters

        self.assertIs(parameters["showSpk"].default, True)
        self.assertIs(parameters["showEmotion"].default, True)
        self.assertIs(parameters["showRoleIdentify"].default, False)
        self.assertIs(parameters["wordTimestamps"].default, False)

    def test_internal_request_defaults_match_v118_form_contract(self) -> None:
        parameters = inspect.signature(AsrRequestParams).parameters

        self.assertIs(parameters["showSpk"].default, True)
        self.assertIs(parameters["showEmotion"].default, True)
        self.assertIs(parameters["showRoleIdentify"].default, False)


class HotwordRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_enabled_hotwords_reach_paraformer_model_parameters(self) -> None:
        request = AsrRequestParams(
            audioFile=UploadFile(file=io.BytesIO(b"a" * 2048), filename="teacher.wav"),
            wordTimestamps=False,
            hotWords=["NOTAM,ATIS,TAF,WET,WET SNOW"],
            language="auto",
        )

        with (
            patch(
                "app.api.routes.asr_common.settings",
                SimpleNamespace(ban_hotword=False),
            ),
            patch(
                "app.api.routes.asr_common.preprocess_audio",
                new=AsyncMock(return_value=b"a" * 2048),
            ),
            patch(
                "app.api.routes.asr_common.write_audio_bytes_to_temp_file",
                return_value="/tmp/asr-hotword-test.wav",
            ),
            patch(
                "app.api.routes.asr_common.load_audio_tensor",
                return_value=(SimpleNamespace(shape=(1, 16000)), 16000),
            ),
            patch("app.api.routes.asr_common.generate_task_id", return_value="task-001"),
        ):
            error, context = await prepare_asr_context(request)

        self.assertIsNone(error)
        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(request.hotWords, ["NOTAM", "ATIS", "TAF", "WET", "WET SNOW"])
        self.assertEqual(context.local_param_dict["hotword"], "NOTAM ATIS TAF WET WET SNOW")
