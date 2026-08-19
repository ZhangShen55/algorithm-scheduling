from pathlib import Path
import os
import re
from typing import Annotated

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.core.exceptions import ConfigurationError
from packages.operator_registry_client import (
    OperatorDeploymentSettings,
    load_operator_deployment_settings,
)

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


DEVICE_PATTERN = re.compile(r"^(cpu|cuda:(\d+)|npu:(\d+))$")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_device(raw: str) -> tuple[str, int | None]:
    match = DEVICE_PATTERN.fullmatch(raw)
    if not match:
        raise ConfigurationError(
            "ocr.device 必须是 cpu、cuda:<非负整数> 或 npu:<非负整数>"
        )
    if raw == "cpu":
        return "cpu", None
    kind, index = raw.split(":", maxsplit=1)
    return kind, int(index)


def to_paddle_device(raw: str) -> str:
    kind, index = parse_device(raw)
    if kind == "cuda":
        return f"gpu:{index}"
    return raw


class ApplicationSettings(BaseModel):
    name: str
    version: str


class ServerSettings(BaseModel):
    host: str = "0.0.0.0"
    port: Annotated[int, Field(ge=1, le=65535)] = 8866
    workers: Annotated[int, Field(ge=1)] = 1


class DetectionSettings(BaseModel):
    limit_side_len: Annotated[int, Field(ge=32)] = 960
    threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.3
    box_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.6
    unclip_ratio: Annotated[float, Field(gt=0.0)] = 1.5


class OCRSettings(BaseModel):
    device: str = "cpu"
    detection_model_dir: Path
    recognition_model_dir: Path
    recognition_batch_size: Annotated[int, Field(ge=1)] = 1
    cpu_threads: Annotated[int, Field(ge=1)] = 8
    enable_mkldnn: bool = True
    enable_hpi: bool = False
    max_concurrency: Annotated[int, Field(ge=1)] = 1
    image_max_bytes: Annotated[int, Field(ge=1)] = 50 * 1024 * 1024
    detection: DetectionSettings = Field(default_factory=DetectionSettings)

    @field_validator("device")
    @classmethod
    def validate_device(cls, value: str) -> str:
        parse_device(value)
        return value


class FormulaSettings(BaseModel):
    enabled: bool = False
    layout_model_dir: Path = Path("models/PP-DocLayout_plus-L")
    recognition_model_dir: Path = Path("models/PP-FormulaNet_plus-M")
    recognition_batch_size: Annotated[int, Field(ge=1)] = 1
    layout_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5


class LoggingSettings(BaseModel):
    level: str = "INFO"
    directory: Path = Path("logs")
    max_size_mb: Annotated[int, Field(ge=1)] = 100
    backup_count: Annotated[int, Field(ge=1)] = 3

    @field_validator("level")
    @classmethod
    def normalize_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("logging.level 无效")
        return normalized


class Settings(BaseModel):
    application: ApplicationSettings
    server: ServerSettings
    ocr: OCRSettings
    formula: FormulaSettings = Field(default_factory=FormulaSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    config_path: Path
    operator_deployment: OperatorDeploymentSettings


def _resolve_path(base: Path, raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base / path
    return Path(os.path.abspath(path))


def load_settings(config_path: str | Path | None = None) -> Settings:
    selected = config_path or os.environ.get("CONFIG_PATH") or PROJECT_ROOT / "config.toml"
    path = Path(selected).expanduser().resolve()
    if not path.is_file():
        raise ConfigurationError(f"配置文件不存在：{path}")

    try:
        with path.open("rb") as source:
            data = tomllib.load(source)
        base = path.parent
        data["ocr"]["detection_model_dir"] = _resolve_path(
            base, data["ocr"]["detection_model_dir"]
        )
        data["ocr"]["recognition_model_dir"] = _resolve_path(
            base, data["ocr"]["recognition_model_dir"]
        )
        formula_data = data.setdefault("formula", {})
        formula_data["layout_model_dir"] = _resolve_path(
            base,
            formula_data.get("layout_model_dir", "models/PP-DocLayout_plus-L"),
        )
        formula_data["recognition_model_dir"] = _resolve_path(
            base,
            formula_data.get(
                "recognition_model_dir", "models/PP-FormulaNet_plus-M"
            ),
        )
        logging_data = data.setdefault("logging", {})
        logging_data["directory"] = _resolve_path(
            base, logging_data.get("directory", "logs")
        )
        data["config_path"] = path
        data["operator_deployment"] = load_operator_deployment_settings(
            path,
            default_capacity=256,
        )
        settings = Settings.model_validate(data)
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        raise ConfigurationError(f"配置内容无效：{error}") from error

    if not settings.ocr.detection_model_dir.is_dir():
        raise ConfigurationError(
            f"检测模型目录不存在：{settings.ocr.detection_model_dir}"
        )
    if not settings.ocr.recognition_model_dir.is_dir():
        raise ConfigurationError(
            f"识别模型目录不存在：{settings.ocr.recognition_model_dir}"
        )
    if settings.formula.enabled and not settings.formula.layout_model_dir.is_dir():
        raise ConfigurationError(
            f"公式版面模型目录不存在：{settings.formula.layout_model_dir}"
        )
    if settings.formula.enabled and not settings.formula.recognition_model_dir.is_dir():
        raise ConfigurationError(
            "公式识别模型目录不存在："
            f"{settings.formula.recognition_model_dir}"
        )
    return settings
