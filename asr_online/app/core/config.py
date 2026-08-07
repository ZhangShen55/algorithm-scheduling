import os
import tomli
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_BASE_DIR = os.getenv("MODEL_BASE_DIR", str(PROJECT_ROOT / "model"))
DEFAULT_ASR_MODEL_NAME = "speech_paraformer_asr_nat-zh-cn-16k-common-vocab8404-online"
DEFAULT_PUNC_MODEL_NAME = "punc_ct-transformer_zh-cn-common-vad_realtime-vocab272727"


def _load_toml(path: str) -> dict:
    with open(path, "rb") as f:
        return tomli.load(f)


@dataclass
class Settings:
    config_path: str = os.getenv("CONFIG_PATH", str(PROJECT_ROOT / "config.toml"))

    @property
    def _cfg(self) -> dict:
        return _load_toml(self.config_path)

    @property
    def id_engine(self) -> str:
        return self._cfg.get("id_engine", "online-1")

    @property
    def version(self) -> str:
        return self._cfg.get("version", "")

    @property
    def log_path(self) -> str:
        return self._cfg.get("log_path", "./asr_online_service.log")

    @property
    def device(self) -> str:
        return self._cfg.get("device", "cuda:0")

    @property
    def ngpu(self) -> int:
        return int(self._cfg.get("ngpu", 1))

    # 在线模型路径
    @property
    def asr_online_model_dir(self) -> str:
        return self._cfg.get("model_paths", {}).get(
            "asr_online_model_dir",
            os.path.join(DEFAULT_MODEL_BASE_DIR, DEFAULT_ASR_MODEL_NAME),
        )

    @property
    def asr_online_punc_model_dir(self) -> str:
        return self._cfg.get("model_paths", {}).get(
            "asr_online_punc_model_dir",
            os.path.join(DEFAULT_MODEL_BASE_DIR, DEFAULT_PUNC_MODEL_NAME),
        )


settings = Settings()
