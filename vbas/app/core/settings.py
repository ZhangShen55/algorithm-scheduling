# app/core/settings.py
import os
from pathlib import Path
from typing import Any, Dict, Union

import torch
from pydantic import Field
from pydantic_settings import BaseSettings
from ultralytics import YOLO
from packages.operator_registry_client import load_operator_deployment_settings

from .config_loader import load_config, resolve_config_path
from .model_protection import ModelPathResolver, ModelProtectionConfig
from .runtime_device import resolve_runtime_device

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = str(resolve_config_path())
_cfg = load_config(CONFIG_PATH)
LOGGING_CONFIG = dict(_cfg.get("logging", {}))

class Settings(BaseSettings):
    IMAGE_ROOT: str = "/mnt/ias-images"
    RESULT_IMAGE_ROOT: str = "/data/result_images"
    SAVE_RESULT_IMAGE: int = 0
    Port: int = 8881
    GPU_ID: Union[int, str]
    Person_Thresd: Dict[str, float]
    Face_Thresd: Dict[str, float]
    Student_Thresd: Dict[str, float]  # 新增学生行为阈值
    Teacher_Behavior_Thresd: Dict[str, Any] = Field(default_factory=lambda: {
        "MergeIoU": 0.8,
        "ImageSize": 640,
        "sit": 0.4,
        "stand": 0.4,
        "bbwriting": 0.25,
        "teach": 0.25,
        "KeepOnlyMainSubject": True,
        "MainSubjectStrategy": "posture_confidence",
        "SubjectClusterIoU": 0.45,
        "PostureConflictRatio": 0.10,
        "PostureConflictDefault": "stand",
        "ForcePostureWhenMissing": True
    })
    Teacher_Head_Pose: Dict[str, Any] = Field(default_factory=lambda: {
        "Enabled": False,
        "DirectMHPRoot": "app/vendor/DirectMHP",
        "DirectMHPWeights": "models/cmu_m_1280_e200_t40_lw010_best.pt",
        "DirectMHPData": "models/cmu_panoptic_coco.yaml",
        "Device": "cpu",
        "ImageSize": 1280,
        "ConfThres": 0.35,
        "IouThres": 0.45,
        "CropScale": 1.35,
        "SideYawThreshold": 25.0,
        "DownPitchThreshold": 25.0
    })
    INSTANCE_COUNT: int = 1  # nginx实例个数
    WORKERS_PER_INSTANCE: int = 1 # 每实例workers数量，默认1
    TIAS: Dict[str, Any] = Field(default_factory=dict)
    TiasExposeLegacySyncTasks: bool = False
    InstanceId: str = "tias-8981"
    BaseUrl: str = "http://127.0.0.1:8981"
    AiQualityBaseUrl: str = "http://127.0.0.1:9000"
    MaxConcurrentBatches: int = 1
    MaxQueueSize: int = 0
    HeartbeatIntervalSeconds: int = 5
    HeartbeatTimeoutSeconds: int = 15
    RegisterRetryIntervalSeconds: int = 5
    ModelProtection: Dict[str, Any] = Field(default_factory=lambda: {
        "Enabled": False,
        "EncryptedModelRoot": "models-encrypted",
        "DecryptedTempRoot": "/dev/shm/tias-models",
        "KeyFile": "/dev/shm/tias_model_key",
        "CleanupAfterLoad": True
    })

    model_config = {"env_file": None, "extra": "ignore"}

_tias_config = _cfg.get("TIAS", {})
if not isinstance(_tias_config, dict):
    _tias_config = {}
_cfg = {
    **_cfg,
    "TiasExposeLegacySyncTasks": bool(_tias_config.get("TiasExposeLegacySyncTasks", False)),
    "InstanceId": str(_tias_config.get("InstanceId", "tias-8981")),
    "BaseUrl": str(_tias_config.get("BaseUrl", "http://127.0.0.1:8981")),
    "AiQualityBaseUrl": str(_tias_config.get("AiQualityBaseUrl", "http://127.0.0.1:9000")),
    "MaxConcurrentBatches": int(_tias_config.get("MaxConcurrentBatches", 1)),
    "MaxQueueSize": int(_tias_config.get("MaxQueueSize", 0)),
    "HeartbeatIntervalSeconds": int(_tias_config.get("HeartbeatIntervalSeconds", 5)),
    "HeartbeatTimeoutSeconds": int(_tias_config.get("HeartbeatTimeoutSeconds", 15)),
    "RegisterRetryIntervalSeconds": int(_tias_config.get("RegisterRetryIntervalSeconds", 5)),
}
settings = Settings(**_cfg)
operator_deployment = load_operator_deployment_settings(
    CONFIG_PATH,
    default_capacity=128,
)

device = resolve_runtime_device(
    settings.GPU_ID,
    torch_module=torch,
    require_gpu=operator_deployment.runtime.require_gpu,
)
# use_half = device.type == "cuda"  # 仅在 CUDA 场景启用 FP16
use_half = False  # 优先精准度，所以开启 fp32。20251205

APP_VER = "V4.1_20251222"
ADP_VER = "V4.1_20251222"
ALG_VER = "person_count_20251222_1920p/face_count(20251212)/student(20250819)"
#已经处理任务总数
Total_HaveProcess_Tasks = {"val": 0}

PERSON_MODEL_PATH = PROJECT_ROOT / 'models' / 'person_count.pt'
FACE_MODEL_PATH = PROJECT_ROOT / 'models' / 'face_count.pt'
STUDENT_MODEL_PATH = PROJECT_ROOT / 'models' / 'student.pt'
TEACHER_BEHAVIOR_MODEL_PATH = PROJECT_ROOT / 'models' / 'teacher_behavior.pt'

model_protection_config = ModelProtectionConfig.from_mapping(
    getattr(settings, "ModelProtection", {})
)
model_path_resolver = ModelPathResolver(model_protection_config)

yolo_person_model = YOLO(
    str(model_path_resolver.prepare_model_path(PERSON_MODEL_PATH))
).to(device)
yolo_face_model = YOLO(
    str(model_path_resolver.prepare_model_path(FACE_MODEL_PATH))
).to(device)
yolo_student_model = YOLO(
    str(model_path_resolver.prepare_model_path(STUDENT_MODEL_PATH))
).to(device)
yolo_teacher_behavior_model = YOLO(
    str(model_path_resolver.prepare_model_path(TEACHER_BEHAVIOR_MODEL_PATH))
).to(device)
if model_protection_config.cleanup_after_load:
    model_path_resolver.cleanup()
