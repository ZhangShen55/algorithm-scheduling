import os
import tomli
from dataclasses import dataclass, field
from pathlib import Path

from packages.operator_registry_client import load_operator_deployment_settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_BASE_DIR = os.getenv("MODEL_BASE_DIR", str(PROJECT_ROOT / "model"))
DEFAULT_ASR_MODEL_NAME = "speech_paraformer_asr_nat-zh-cn-16k-common-vocab8404-online"
DEFAULT_PUNC_MODEL_NAME = "punc_ct-transformer_zh-cn-common-vad_realtime-vocab272727"


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
    config_path: str = field(default_factory=lambda: str(resolve_config_path()))

    def __post_init__(self) -> None:
        self.config_path = str(resolve_config_path(self.config_path))

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
    def logging_config(self) -> dict:
        configured = dict(self._cfg.get("logging", {}))
        if not configured and self.log_path:
            legacy_path = Path(self.log_path)
            configured = {
                "directory": str(legacy_path.parent),
                "file_name": legacy_path.name,
            }
        return configured

    @property
    def device(self) -> str:
        return self._cfg.get("device", "cuda:0")

    @property
    def ngpu(self) -> int:
        return int(self._cfg.get("ngpu", 1))

    @property
    def vad_max_end_silence_ms(self) -> int:
        value = int(self._cfg.get("vad_max_end_silence_ms", 1000))
        if value <= 0:
            raise ValueError("vad_max_end_silence_ms 必须为正整数")
        return value

    @property
    def vad_merge_max_gap_ms(self) -> int:
        value = int(self._cfg.get("vad_merge_max_gap_ms", 720))
        if value <= 0:
            raise ValueError("vad_merge_max_gap_ms 必须为正整数")
        return value

    @property
    def progressive_correction_enabled(self) -> bool:
        value = self._cfg.get("progressive_correction_enabled", True)
        if not isinstance(value, bool):
            raise ValueError("progressive_correction_enabled 必须为布尔值")
        return value

    def _positive_int(self, key: str, default: int) -> int:
        value = int(self._cfg.get(key, default))
        if value <= 0:
            raise ValueError(f"{key} 必须为正整数")
        return value

    @property
    def progressive_correction_chars(self) -> int:
        return self._positive_int("progressive_correction_chars", 8)

    @property
    def progressive_correction_audio_ms(self) -> int:
        return self._positive_int("progressive_correction_audio_ms", 4800)

    @property
    def progressive_correction_cooldown_ms(self) -> int:
        return self._positive_int("progressive_correction_cooldown_ms", 2000)

    @property
    def sensevoice_correction_workers(self) -> int:
        value = self._positive_int("sensevoice_correction_workers", 1)
        if value > 64:
            raise ValueError("sensevoice_correction_workers 不能大于 64")
        return value

    @property
    def correction_queue_capacity(self) -> int:
        value = self._positive_int("correction_queue_capacity", 16)
        if value < self.sensevoice_correction_workers:
            raise ValueError("correction_queue_capacity 不能小于 correction worker 数")
        return value

    @property
    def correction_task_timeout_ms(self) -> int:
        return self._positive_int("correction_task_timeout_ms", 5000)

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

    def _optional_model_path(self, config_key: str) -> str | None:
        configured = self._cfg.get("model_paths", {}).get(config_key)
        if not configured:
            return None
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return str(path.resolve())

    @property
    def vad_model_dir(self) -> str | None:
        return self._optional_model_path("vad_model_dir")

    @property
    def correction_model_dir(self) -> str | None:
        return self._optional_model_path("asr_online_correction_model_dir")

    @property
    def sensevoice_model_dir(self) -> str | None:
        return self._optional_model_path("sensevoice_model_dir")


settings = Settings()
operator_deployment = load_operator_deployment_settings(
    settings.config_path,
    default_capacity=10,
)
