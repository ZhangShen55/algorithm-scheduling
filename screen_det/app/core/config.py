from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import toml

from app.core.model_protection import ModelProtectionConfig
from packages.operator_registry_client import (
    OperatorDeploymentSettings,
    load_operator_deployment_settings,
)

BASE_DIR = Path(__file__).resolve().parents[2]


def _resolve_config_path() -> Path:
    configured_path = Path(os.environ.get("CONFIG_PATH", "config.toml"))
    if configured_path.is_absolute():
        return configured_path
    return BASE_DIR / configured_path


CONFIG_PATH = _resolve_config_path()


@dataclass(frozen=True)
class AppConfig:
    name: str = "tilt-detection-service"
    version: str = "1.0.0"
    debug: bool = False


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8880
    workers: int = 1


@dataclass(frozen=True)
class YoloConfig:
    device: str = "cpu"


@dataclass(frozen=True)
class DetectionConfig:
    tilt_threshold: float = 1.5
    min_line_length_ratio: float = 0.1
    min_valid_lines: int = 5
    trim_start_ratio: float = 0.2
    trim_end_ratio: float = 0.8
    gaussian_kernel_size: int = 5
    canny_threshold1: int = 50
    canny_threshold2: int = 150
    horizontal_angle_min: float = -30
    horizontal_angle_max: float = 30
    vertical_angle_min: float = 60
    vertical_angle_max: float = 120


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    directory: str = "logs"
    file_name: str = "application.log"
    max_file_size_mib: int = 100
    retention_days: int = 7
    stdout_enabled: bool = True
    file_enabled: bool = True


@dataclass(frozen=True)
class RuntimeConfig:
    require_gpu: bool = False
    max_image_bytes: int = 10 * 1024 * 1024


@dataclass(frozen=True)
class ScreenDetectionConfig:
    weights_path: str = "model/screen.pt"
    conf: float = 0.25
    iou: float = 0.45
    allowed_class_ids: tuple[int, ...] = (0, 1, 2, 3)
    max_batch_size: int = 16


@dataclass(frozen=True)
class QualityAbnormalDetectionConfig:
    enabled: bool = True
    analyze_max_side: int = 960
    overlay_top_ratio: float = 0.08
    overlay_bottom_ratio: float = 0.05
    color_cast_lab_threshold: float = 18.0
    color_cast_imbalance_threshold: float = 0.25
    snow_noise_threshold: float = 14.0
    snow_edge_density_threshold: float = 0.18
    blur_laplacian_threshold: float = 450.0
    blur_edge_density_threshold: float = 0.06
    glitch_band_laplacian_threshold: float = 450.0
    glitch_band_saturation_threshold: float = 28.0
    glitch_min_area_ratio: float = 0.18
    glitch_grid_rows: int = 16
    glitch_grid_cols: int = 24


@dataclass(frozen=True)
class OcclusionDetectionConfig:
    enabled: bool = True
    analyze_max_side: int = 960
    threshold: float = 0.25
    area_ratio: float = 0.2
    yolo_seg_weights_path: str = "model/occlusion.pt"
    yolo_imgsz: int = 960
    yolo_retina_masks: bool = True


@dataclass(frozen=True)
class AggregateDetectionConfig:
    enabled: bool = True
    default_modules: tuple[str, ...] = ("tilt", "screen", "quality_abnormal", "occlusion")
    tilt_threshold: float = 1.5
    screen_conf: float = 0.25
    screen_iou: float = 0.45
    occlusion_threshold: float = 0.25
    occlusion_area_ratio: float = 0.2


@dataclass(frozen=True)
class Settings:
    app: AppConfig
    server: ServerConfig
    yolo: YoloConfig
    model_protection: ModelProtectionConfig
    detection: DetectionConfig
    screen_detection: ScreenDetectionConfig
    quality_abnormal_detection: QualityAbnormalDetectionConfig
    occlusion_detection: OcclusionDetectionConfig
    aggregate_detection: AggregateDetectionConfig
    logging: LoggingConfig
    runtime: RuntimeConfig
    operator_deployment: OperatorDeploymentSettings


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    return value if isinstance(value, dict) else {}


def _normalize_kernel_size(value: int) -> int:
    if value < 3:
        return 3
    return value if value % 2 == 1 else value + 1


def _normalize_aggregate_data(data: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    if "default_modules" in result:
        result["default_modules"] = tuple(str(x) for x in result["default_modules"])
    return result


def _load_settings() -> Settings:
    raw = toml.load(CONFIG_PATH) if CONFIG_PATH.exists() else {}

    detection_data = _section(raw, "detection")
    if "gaussian_kernel_size" in detection_data:
        detection_data["gaussian_kernel_size"] = _normalize_kernel_size(
            int(detection_data["gaussian_kernel_size"])
        )

    screen_data = _section(raw, "screen_detection")
    if "allowed_class_ids" in screen_data:
        screen_data["allowed_class_ids"] = tuple(int(x) for x in screen_data["allowed_class_ids"])

    return Settings(
        app=AppConfig(**_section(raw, "app")),
        server=ServerConfig(**_section(raw, "server")),
        yolo=YoloConfig(**_section(raw, "yolo")),
        model_protection=ModelProtectionConfig(**_section(raw, "model_protection")),
        detection=DetectionConfig(**detection_data),
        screen_detection=ScreenDetectionConfig(**screen_data),
        quality_abnormal_detection=QualityAbnormalDetectionConfig(
            **_section(raw, "quality_abnormal_detection")
        ),
        occlusion_detection=OcclusionDetectionConfig(
            **_section(raw, "occlusion_detection")
        ),
        aggregate_detection=AggregateDetectionConfig(
            **_normalize_aggregate_data(_section(raw, "aggregate_detection"))
        ),
        logging=LoggingConfig(**_section(raw, "logging")),
        runtime=RuntimeConfig(**_section(raw, "runtime")),
        operator_deployment=load_operator_deployment_settings(
            CONFIG_PATH,
            default_capacity=128,
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return _load_settings()


class StartupConfigChangedError(ValueError):
    pass


def reload_settings() -> Settings:
    current = get_settings()
    candidate = _load_settings()
    startup_changes: list[str] = []
    if candidate.yolo != current.yolo:
        startup_changes.append("yolo")
    if candidate.model_protection != current.model_protection:
        startup_changes.append("model_protection")
    if candidate.screen_detection.weights_path != current.screen_detection.weights_path:
        startup_changes.append("screen_detection.weights_path")
    if (
        candidate.occlusion_detection.yolo_seg_weights_path
        != current.occlusion_detection.yolo_seg_weights_path
    ):
        startup_changes.append("occlusion_detection.yolo_seg_weights_path")
    if startup_changes:
        raise StartupConfigChangedError(
            "启动级配置已变化，必须重启服务: " + ", ".join(startup_changes)
        )
    get_settings.cache_clear()
    return get_settings()
