import unittest
from types import SimpleNamespace
from unittest.mock import patch


class ItnRouteAssemblyTests(unittest.IsolatedAsyncioTestCase):
    async def test_whisper_rebuilds_text_and_preserves_metadata(self):
        from app.api.routes import asr_common
        from tests.test_v118_multilingual import FakeSegment, make_context, make_request

        request = make_request(language="en")
        context = make_context(request, duration=3.0)
        model = SimpleNamespace(
            transcribe=lambda *args, **kwargs: (
                iter([FakeSegment(0.0, 2.0, "twenty two", [])]),
                SimpleNamespace(language="en"),
            )
        )

        with patch.object(
            asr_common,
            "normalize_segments",
            return_value=(
                [{
                    "segment_text": "22",
                    "bg": "0.00",
                    "ed": "2.00",
                    "speed": 1,
                    "segment_words": [],
                }],
                "22",
                {},
            ),
        ) as normalize:
            result = await asr_common.run_whisper_asr(context, model)

        self.assertEqual(result["text"], "22")
        self.assertEqual(result["segments"][0]["bg"], "0.00")
        self.assertEqual(result["segments"][0]["ed"], "2.00")
        self.assertEqual(result["speed_info"][0]["segment_info"]["speed"], [40])
        normalize.assert_called_once()


if __name__ == "__main__":
    unittest.main()
