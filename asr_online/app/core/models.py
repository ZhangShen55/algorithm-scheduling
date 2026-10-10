import asyncio
import torch
from funasr import AutoModel
from modelscope.pipelines import pipeline
from modelscope.utils.constant import Tasks

from app.core.config import operator_deployment, settings
from app.core.correction_scheduler import CorrectionScheduler
from app.core.model_assets import prepare_decrypted_model_dir

_model_online = None
_punct_pipeline = None
_model_vad = None
_model_correction = None
_model_sensevoice = None
_sensevoice_models = []
_correction_scheduler = None
_model_lock = asyncio.Lock()


def require_gpu_enabled() -> bool:
    return operator_deployment.runtime.require_gpu


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
    global _model_online, _punct_pipeline, _model_vad, _model_correction
    global _model_sensevoice, _sensevoice_models, _correction_scheduler

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

        if settings.vad_model_dir and _model_vad is None:
            _model_vad = AutoModel(
                model=prepare_decrypted_model_dir(settings.vad_model_dir),
                device=settings.device,
                ngpu=settings.ngpu,
                disable_update=True,
                disable_pbar=True,
            )

        if settings.correction_model_dir and _model_correction is None:
            _model_correction = AutoModel(
                model=prepare_decrypted_model_dir(settings.correction_model_dir),
                device=settings.device,
                ngpu=settings.ngpu,
                disable_update=True,
                disable_pbar=True,
            )

        if settings.sensevoice_model_dir and _correction_scheduler is None:
            model_dir = prepare_decrypted_model_dir(settings.sensevoice_model_dir)
            loaded_models = []
            try:
                for _ in range(settings.sensevoice_correction_workers):
                    loaded_models.append(
                        AutoModel(
                            model=model_dir,
                            device=settings.device,
                            ngpu=settings.ngpu,
                            disable_update=True,
                            disable_pbar=True,
                        )
                    )
                scheduler = CorrectionScheduler(
                    loaded_models,
                    queue_capacity=settings.correction_queue_capacity,
                    submit_timeout_seconds=settings.correction_task_timeout_ms / 1000,
                )
                await scheduler.start()
            except Exception:
                loaded_models.clear()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                raise
            _sensevoice_models = loaded_models
            _model_sensevoice = loaded_models[0]
            _correction_scheduler = scheduler


async def shutdown_models():
    global _model_sensevoice, _sensevoice_models, _correction_scheduler
    if _correction_scheduler is not None:
        await _correction_scheduler.close()
    _correction_scheduler = None
    _model_sensevoice = None
    _sensevoice_models = []
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def get_online_model():
    return _model_online


def get_punct_pipeline():
    return _punct_pipeline


def get_vad_model():
    return _model_vad


def get_correction_model():
    return _model_correction


def get_sensevoice_model():
    return _model_sensevoice


def get_correction_scheduler():
    return _correction_scheduler
