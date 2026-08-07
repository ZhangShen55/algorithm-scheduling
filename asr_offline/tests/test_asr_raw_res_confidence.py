import unittest
from pathlib import Path

from funasr import AutoModel
from app.core.config import PROJECT_ROOT, settings


class AsrRawResultConfidenceTest(unittest.TestCase):
    def test_print_raw_res_for_confidence_inspection(self):
        _model_asr = AutoModel(
            model=settings.asr_model_dir,
            device=settings.device,
            ngpu=settings.ngpu,
            punc_model=settings.punc_model_dir,
            vad_model=settings.vad_model_dir,
            # spk_model='/var/model_zoo/model_asr/speech_campplus_sv_zh_en_16k-common_advanced',
            vad_kwargs={"max_single_segment_time": 30000, "max_end_silence_time": 800},
            sentence_timestamp=True,
            decoding_beamsize=5,
            decoding_mode="beam",
            disable_update=True,
            # disable_pbar=True
        )
        source_audio = (
            Path(settings.asr_model_dir) / "asr_example_hotword.wav"
        )
        if not source_audio.is_file():
            source_audio = PROJECT_ROOT / "test_wav/chinEng-16k.wav"
        res = _model_asr.generate(
            input=str(source_audio),
            batch_size_s=300,
        )
        print("\nRAW_FUNASR_RES_START")
        print(res)
        print("RAW_FUNASR_RES_END")
        self.assertIsInstance(res, list)
        self.assertTrue(res)
