import tomli
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from packages.operator_registry_client import load_operator_deployment_settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_config_path(config_path: str | Path | None = None) -> Path:
    selected = Path(
        config_path
        if config_path is not None
        else os.getenv("CONFIG_PATH", PROJECT_ROOT / "config.toml")
    ).expanduser()
    if not selected.is_absolute():
        selected = PROJECT_ROOT / selected
    return selected.resolve()


def _load_toml(path: str) -> dict:
    with open(path, "rb") as f:
        return tomli.load(f)


@dataclass
class Settings:
    # 从环境变量读取 config.toml 路径
    config_path: str = field(default_factory=lambda: str(resolve_config_path()))
    _cfg: dict = None  # 实际配置字典

    def __post_init__(self):
        self.config_path = str(resolve_config_path(self.config_path))
        self._cfg = _load_toml(self.config_path)
        model_paths = self._cfg.get("model_paths", {})
        for key, value in model_paths.items():
            path = Path(value).expanduser()
            if not path.is_absolute():
                model_paths[key] = str((PROJECT_ROOT / path).resolve())

    # 基础配置
    @property
    def id_engine(self) -> str:
        import uuid
        return self._cfg.get("id_engine", f"seacraft-asr-{uuid.uuid4()}")

    @property
    def version(self) -> str:
        return "asr:latest"

    # 设备与并发
    @property
    def device(self) -> str:
        return self._cfg.get("device", "cuda:0")

    @property
    def concurrency(self) -> int:
        return self._cfg.get("concurrency", 5)

    @property
    def gpu_slot_timeout_seconds(self) -> float:
        return float(self._cfg.get("gpu_slot_timeout_seconds", 90))

    @property
    def instance_count(self) -> int:
        return self._cfg.get("instance_count", 4)

    # 模型路径 - 从嵌套的 [model_paths] 部分读取
    @property
    def vad_model_dir(self) -> str:
        model_paths = self._cfg.get("model_paths", {})
        return model_paths.get("vad_model_dir", "/model/speech_fsmn_vad_zh-cn-16k-common-pytorch")

    @property
    def punc_model_dir(self) -> str:
        model_paths = self._cfg.get("model_paths", {})
        return model_paths.get("punc_model_dir", "/model/punc_ct-transformer_cn-en-common-vocab471067-large")

    @property
    def asr_model_dir(self) -> str:
        model_paths = self._cfg.get("model_paths", {})
        return model_paths.get("asr_model_dir", "/model/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch")

    @property
    def spk_model_dir(self) -> Optional[str]:
        model_paths = self._cfg.get("model_paths", {})
        return model_paths.get("spk_model_dir", "/model/speech_campplus_sv_zh_en_16k-common_advanced")

    @property
    def emotion_model_dir(self) -> str:
        model_paths = self._cfg.get("model_paths", {})
        # 注意：原 JSON 中 key 为 emotion_modek_dir（拼写错误），这里修正为 emotion_model_dir
        return model_paths.get("emotion_model_dir", "/model/emotion2vec_plus_seed")

    @property
    def whisper_model_dir(self) -> str:
        model_paths = self._cfg.get("model_paths", {})
        return model_paths.get("whisper_model_dir", "/model/faster-whisper-large-v3")

    # 语速计算配置 - 从嵌套的 [speech_rate] 部分读取
    @property
    def speech_rate_factor(self) -> float:
        speech_rate = self._cfg.get("speech_rate", {})
        return float(speech_rate.get("rate_factor", 1.0))

    # 功能开关 - 从嵌套的 [features] 部分读取
    @property
    def open_spk(self) -> bool:
        features = self._cfg.get("features", {})
        return features.get("open_spk", False)

    @property
    def open_mul_lang(self) -> bool:
        features = self._cfg.get("features", {})
        return features.get("open_mul_lang", False)

    @property
    def open_emotion(self) -> bool:
        features = self._cfg.get("features", {})
        return features.get("open_emotion", False)

    @property
    def ban_hotword(self) -> bool:
        features = self._cfg.get("features", {})
        return features.get("ban_hotword", False)

    # 长音频分块配置
    @property
    def chunk_threshold_minutes(self) -> float:
        return float(self._cfg.get("audio_chunk", {}).get("threshold_minutes", 90))

    @property
    def chunk_minutes(self) -> float:
        return float(self._cfg.get("audio_chunk", {}).get("chunk_minutes", 60))

    @property
    def min_last_chunk_minutes(self) -> float:
        return float(self._cfg.get("audio_chunk", {}).get("min_last_chunk_minutes", 15))

    @property
    def chunk_overlap_seconds(self) -> float:
        return float(self._cfg.get("audio_chunk", {}).get("overlap_seconds", 15))

    @property
    def chunk_retry_count(self) -> int:
        return int(self._cfg.get("audio_chunk", {}).get("chunk_retry_count", 2))


settings = Settings()
operator_deployment = load_operator_deployment_settings(
    settings.config_path,
    default_capacity=4,
)
