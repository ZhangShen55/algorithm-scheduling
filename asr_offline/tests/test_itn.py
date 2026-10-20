import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.utils import itn


class ItnAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import pynini  # noqa: F401
            from itn.chinese.inverse_normalizer import InverseNormalizer  # noqa: F401
        except Exception as exc:
            raise unittest.SkipTest(f"WeTextProcessing dependencies unavailable: {exc}")

    def setUp(self):
        itn.clear_normalizer_cache()

    def test_chinese_numbers_measures_dates_and_terms(self):
        config = itn.ItnConfig()
        segments, text, stats = itn.normalize_segments(
            [
                {"segment_text": "二十二摄氏度", "bg": "1.00", "ed": "2.00", "role": "teacher"},
                {"segment_text": "百分之五十", "bg": "2.00", "ed": "3.00", "role": "teacher"},
                {"segment_text": "二零二六年", "bg": "3.00", "ed": "4.00", "role": "teacher"},
                {"segment_text": "一氧化碳 二氧化碳 一阶导数 二维传热 三维空间", "bg": "4.00", "ed": "5.00", "role": "teacher"},
            ],
            "zh",
            config,
        )
        self.assertEqual(
            [item["segment_text"] for item in segments],
            ["22°C", "50%", "2026年", "一氧化碳 二氧化碳 一阶导数 二维传热 三维空间"],
        )
        self.assertEqual(text, "22°C 50% 2026年 一氧化碳 二氧化碳 一阶导数 二维传热 三维空间")
        self.assertEqual(segments[0]["bg"], "1.00")
        self.assertEqual(segments[0]["role"], "teacher")
        self.assertEqual(stats["failures"], 0)

    def test_empty_segments_are_not_filled(self):
        segments, text, _ = itn.normalize_segments(
            [{"segment_text": "", "bg": "1.00"}, {"segment_text": "二十二"}],
            "zh",
            itn.ItnConfig(),
        )
        self.assertEqual(segments[0]["segment_text"], "")
        self.assertEqual(text, "22")

    def test_disabled_itn_skips_normalizer(self):
        with patch.object(itn, "_get_normalizer") as get_normalizer:
            segments, text, stats = itn.normalize_segments(
                [{"segment_text": "二十二"}], "zh", itn.ItnConfig(enabled=False)
            )
        get_normalizer.assert_not_called()
        self.assertEqual(segments[0]["segment_text"], "二十二")
        self.assertEqual(text, "二十二")
        self.assertFalse(stats["enabled"])

    def test_initializer_failure_passes_through_without_text_logging(self):
        with patch.object(itn, "_get_normalizer", side_effect=RuntimeError("broken")):
            with self.assertLogs("app.utils.itn", level="ERROR") as captured:
                segments, text, stats = itn.normalize_segments(
                    [{"segment_text": "二十二"}], "zh", itn.ItnConfig()
                )
        self.assertEqual(segments[0]["segment_text"], "二十二")
        self.assertEqual(text, "二十二")
        self.assertEqual(stats["failure_stage"], "initialize")
        self.assertNotIn("二十二", " ".join(captured.output))

    def test_single_segment_failure_keeps_only_that_segment(self):
        class FakeNormalizer:
            def normalize(self, text):
                if text == "bad":
                    raise ValueError("bad segment")
                return "22" if text == "二十二" else text

        with patch.object(itn, "_get_normalizer", return_value=FakeNormalizer()):
            segments, text, stats = itn.normalize_segments(
                [{"segment_text": "二十二"}, {"segment_text": "bad"}],
                "zh",
                itn.ItnConfig(),
            )
        self.assertEqual([s["segment_text"] for s in segments], ["22", "bad"])
        self.assertEqual(text, "22 bad")
        self.assertEqual(stats["failures"], 1)

    def test_english_uses_english_graph(self):
        segments, text, _ = itn.normalize_segments(
            [{"segment_text": "twenty two degrees Celsius"}],
            "en",
            itn.ItnConfig(),
        )
        self.assertEqual(segments[0]["segment_text"], "22 degrees Celsius")
        self.assertEqual(text, "22 degrees Celsius")

    def test_unsupported_language_is_passed_through(self):
        with patch.object(itn, "_get_normalizer") as get_normalizer:
            segments, text, stats = itn.normalize_segments(
                [{"segment_text": "twenty two"}], "fr", itn.ItnConfig()
            )
        get_normalizer.assert_not_called()
        self.assertEqual(segments[0]["segment_text"], "twenty two")
        self.assertEqual(text, "twenty two")
        self.assertEqual(stats["skipped"], "unsupported_language")


if __name__ == "__main__":
    unittest.main()
