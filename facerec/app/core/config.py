import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import tomli
from pydantic import BaseModel, ConfigDict, computed_field, field_validator

logger = logging.getLogger(__name__)
logger.level = logging.DEBUG

PROJECT_ROOT = Path(__file__).resolve().parents[2]
config_path = Path(
    os.environ.get("CONFIG_PATH", str(PROJECT_ROOT / "config.toml"))
).expanduser().resolve()

class DBSettings(BaseModel):
    username: str
    password: str
    host: str
    port: str
    database: str
    auth_source: str
    limit: int = 10000

    @computed_field
    def url(self) -> str:
        # 构建 URL mongodb://user:pass@host:port/db?authSource=admin
        return (
            f"mongodb://{quote(self.username, safe='')}:{quote(self.password, safe='')}@"
            f"{self.host}:{self.port}/{self.database}"
            f"?authSource={self.auth_source}"
        )


def apply_db_environment(config: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(config)
    environment_fields = {
        "username": "FACEREC_MONGO_USERNAME",
        "password": "FACEREC_MONGO_PASSWORD",
    }
    for field, environment_name in environment_fields.items():
        value = os.getenv(environment_name)
        if value is not None:
            resolved[field] = value
    return resolved


class FaceSettings(BaseModel):
    threshold: float
    candidate_threshold: float
    rec_min_face_hw: int

class ThreadSettings(BaseModel):
    max_workers: int

class GpuSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device: str

    @field_validator("device")
    @classmethod
    def validate_device(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized == "cpu":
            return normalized
        if normalized.startswith("cuda:") and normalized[5:].isdigit():
            return normalized
        raise ValueError('device must be "cpu" or "cuda:N"')

class FrontLoginSettings(BaseModel):
    username: str
    password: str

class LoggerSettings(BaseModel):
    level: str = "INFO"
    log_path: str = "/app/logs/facerec.log"

class FeatureImageSettings(BaseModel):
    max_feature_image_width_px: int = 720
    max_feature_image_height_px: int = 1280
    min_feature_image_width_px: int = 80
    min_feature_image_height_px: int= 80
    max_feature_image_size_m : int = 10
    max_face_hw: int = 300
    min_face_hw: int = 40
    save_person_photo: bool = False

class StatsSettings(BaseModel):
    retention_days: int = 7  # 详细日志保留天数
    hourly_retention_days: int = 30  # 按小时聚合数据保留天数

class Settings(BaseModel):
    db: DBSettings
    face: FaceSettings
    thread: ThreadSettings
    gpu: GpuSettings
    frontlogin: FrontLoginSettings
    feature_image: FeatureImageSettings
    logger: LoggerSettings
    stats: StatsSettings

def load_config():
    logger.debug(f"Loading config from {config_path}")
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "rb") as f:
        config_data = tomli.load(f)

    logger.debug("Config loaded successfully from %s", config_path)

    return Settings(
        db=DBSettings(**apply_db_environment(config_data["db"])),
        face=FaceSettings(**config_data["face"]),
        thread=ThreadSettings(**config_data["threading"]),
        gpu=GpuSettings(**config_data["gpu"]),
        frontlogin=FrontLoginSettings(**config_data["frontlogin"]),
        feature_image=FeatureImageSettings(**config_data["image"]),
        logger=LoggerSettings(**config_data["logger"]),
        stats=StatsSettings(**config_data.get("stats", {}))  # 兼容旧配置
    )


settings = load_config()
