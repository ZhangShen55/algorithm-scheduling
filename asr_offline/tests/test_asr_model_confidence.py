import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import tomli
from funasr import AutoModel


class AsrModelConfidenceOutputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[1]
        cls.project_root = project_root
        with (project_root / "config.toml").open("rb") as f:
            cls.config = tomli.load(f)

    def test_base_asr_punc_model_output_confidence_fields(self):
        model_paths = self.config["model_paths"]
        audio_path = self.project_root / "test_wav" / "教师1_16k.wav"
        self.assertTrue(audio_path.exists(), f"missing audio file: {audio_path}")

        clip_path = self._extract_audio_clip(audio_path, start_seconds=95, seconds=30)
        try:
            model = AutoModel(
                model=model_paths["asr_model_dir"],
                punc_model=model_paths["punc_model_dir"],
                device=self.config.get("device", "cuda:0"),
                ngpu=0 if self.config.get("device", "cuda:0") == "cpu" else 1,
                sentence_timestamp=True,
                disable_update=True,
                disable_pbar=True,
            )

            result = model.generate(
                input=str(clip_path),
                is_final=True,
                batch_size_s=300,
            )
        finally:
            try:
                os.remove(clip_path)
            except FileNotFoundError:
                pass

        self.assertIsInstance(result, list)
        self.assertTrue(result, "ASR model returned an empty result list")
        first = result[0]
        self.assertIsInstance(first, dict)

        confidence_like_fields = self._collect_confidence_like_fields(first)
        sentence_info = first.get("sentence_info") or []
        first_sentence = sentence_info[0] if sentence_info else {}

        print("\nASR_SOURCE_AUDIO=")
        print(str(audio_path))
        print("ASR_TEST_CLIP_START_SECONDS=95")
        print("ASR_TEST_CLIP_SECONDS=30")
        print("ASR_RESULT_TOP_LEVEL_KEYS=")
        print(json.dumps(sorted(first.keys()), ensure_ascii=False, indent=2))
        print("ASR_FIRST_SENTENCE_KEYS=")
        print(json.dumps(sorted(first_sentence.keys()), ensure_ascii=False, indent=2))
        print("ASR_CONFIDENCE_LIKE_FIELDS=")
        print(json.dumps(confidence_like_fields, ensure_ascii=False, indent=2))
        print("ASR_TEXT_PREVIEW=")
        print((first.get("text") or "")[:200])

    @staticmethod
    def _extract_audio_clip(audio_path: Path, start_seconds: int, seconds: int) -> str:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            clip_path = tmp.name
        subprocess.run([
            "ffmpeg", "-y",
            "-ss", str(start_seconds),
            "-i", str(audio_path),
            "-t", str(seconds),
            "-ar", "16000",
            "-ac", "1",
            "-loglevel", "error",
            clip_path,
        ], check=True)
        return clip_path

    @classmethod
    def _collect_confidence_like_fields(cls, value, path="root"):
        matched_names = ("confidence", "conf", "prob", "score", "logit")
        matches = []

        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                lowered = str(key).lower()
                if any(name in lowered for name in matched_names):
                    matches.append({
                        "path": child_path,
                        "type": type(child).__name__,
                        "sample": cls._sample_value(child),
                    })
                matches.extend(cls._collect_confidence_like_fields(child, child_path))
        elif isinstance(value, list):
            for index, child in enumerate(value[:5]):
                matches.extend(cls._collect_confidence_like_fields(child, f"{path}[{index}]"))

        return matches

    @staticmethod
    def _sample_value(value):
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, list):
            return value[:5]
        if isinstance(value, dict):
            return {key: value[key] for key in list(value)[:5]}
        return repr(value)
