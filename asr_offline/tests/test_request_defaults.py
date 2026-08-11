import inspect
import unittest

from app.entity.data import AsrRequestParams, get_asr_params


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
