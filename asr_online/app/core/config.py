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
operator_deployment = load_operator_deployment_settings(
    settings.config_path,
    default_capacity=10,
)
