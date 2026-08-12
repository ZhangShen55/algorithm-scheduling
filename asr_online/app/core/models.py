import asyncio
import os

import torch
from funasr import AutoModel
from modelscope.pipelines import pipeline
from modelscope.utils.constant import Tasks

from app.core.config import settings
from app.core.model_assets import prepare_decrypted_model_dir

_model_online = None
_punct_pipeline = None
_model_lock = asyncio.Lock()


def require_gpu_enabled() -> bool:
    return os.getenv("REQUIRE_GPU", "false").strip().lower() in {"1", "true", "yes"}


def resolve_runtime_device() -> torch.device:
    configured = settings.device.strip().lower()
    if configured == "cpu":
        if require_gpu_enabled():
            raise RuntimeError("部署要求使用 GPU，但算子配置不是 cuda:<index>")
        return torch.device("cpu")
    if not configured.startswith("cuda:") or not configured[5:].isdigit():
        raise RuntimeError("在线 ASR device 必须是 cpu 或 cuda:<index>")

    index = int(configured[5:])
    if settings.ngpu != 1:
        raise RuntimeError("在线 ASR 使用 CUDA 时 ngpu 必须为 1")
    if not torch.cuda.is_available():
        raise RuntimeError(f"算子要求使用 GPU {configured}，但 CUDA 不可用")
    visible_count = torch.cuda.device_count()
    if index >= visible_count:
        raise RuntimeError(
            f"GPU 设备 {configured} 索引越界，可见 CUDA 设备数量为 {visible_count}"
        )
    return torch.device(configured)


def device() -> torch.device:
    return resolve_runtime_device()


async def load_models_if_needed():
    global _model_online, _punct_pipeline

    async with _model_lock:
        resolve_runtime_device()

        if _model_online is None:
            _model_online = AutoModel(
                model=prepare_decrypted_model_dir(settings.asr_online_model_dir),
                device=settings.device,
                ngpu=settings.ngpu,
                disable_update=True,
                disable_pbar=True,
            )

        if _punct_pipeline is None:
            _punct_pipeline = pipeline(
                task=Tasks.punctuation,
                model=prepare_decrypted_model_dir(settings.asr_online_punc_model_dir),
                disable_update=True,
                device=settings.device,
            )


def get_online_model():
    return _model_online


def get_punct_pipeline():
    return _punct_pipeline
