import asyncio
import torch
from funasr import AutoModel
from modelscope.pipelines import pipeline
from modelscope.utils.constant import Tasks

from app.core.config import operator_deployment, settings
from app.core.correction_scheduler import CorrectionScheduler
from app.core.model_assets import cleanup_prepared_model_dirs, prepare_decrypted_model_dir
from app.core.v4_correction import V4CorrectionScheduler, V4Punctuation
from app.core.v4_itn import V4ITN

_model_online = None
_punct_pipeline = None
_model_vad = None
_model_correction = None
_model_sensevoice = None
_sensevoice_models = []
_correction_scheduler = None
_v4_scheduler = None
_v4_punctuation = None
_model_lock = asyncio.Lock()


def require_gpu_enabled() -> bool:
    return operator_deployment.runtime.require_gpu


def _resolve_device(
    configured: str,
    ngpu: int,
    *,
    label: str,
    require_gpu: bool,
) -> torch.device:
    configured = configured.strip().lower()
    if configured == "cpu":
        if require_gpu:
            raise RuntimeError(f"部署要求使用 GPU，但{label}配置不是 cuda:<index>")
        return torch.device("cpu")
    if not configured.startswith("cuda:") or not configured[5:].isdigit():
        raise RuntimeError(f"{label} device 必须是 cpu 或 cuda:<index>")

    index = int(configured[5:])
    if ngpu != 1:
        raise RuntimeError(f"{label}使用 CUDA 时 ngpu 必须为 1")
    if not torch.cuda.is_available():
        raise RuntimeError(f"{label}要求使用 GPU {configured}，但 CUDA 不可用")
    visible_count = torch.cuda.device_count()
    if index >= visible_count:
        raise RuntimeError(
            f"{label}设备 {configured} 索引越界，可见 CUDA 设备数量为 {visible_count}"
        )
    return torch.device(configured)


def resolve_runtime_device() -> torch.device:
    return _resolve_device(
        settings.device,
        settings.ngpu,
        label="在线 ASR",
        require_gpu=require_gpu_enabled(),
    )


def resolve_sensevoice_device() -> torch.device:
    configured = settings.sensevoice_correction_device
    correction_ngpu = 0 if configured == "cpu" else 1
    return _resolve_device(
        configured,
        correction_ngpu,
        label="SenseVoice 纠正",
        require_gpu=require_gpu_enabled(),
    )


def device() -> torch.device:
    return resolve_runtime_device()


async def load_models_if_needed():
    global _model_online, _punct_pipeline, _model_vad, _model_correction
    global _model_sensevoice, _sensevoice_models, _correction_scheduler
    global _v4_scheduler, _v4_punctuation

    async with _model_lock:
        try:
            if settings.qwen_stream_only_mode:
                if not settings.qwen_stream_enabled:
                    raise RuntimeError("qwen_stream_only_mode 要求启用 qwen_stream")
                return
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
                correction_device = str(resolve_sensevoice_device())
                correction_ngpu = 0 if correction_device == "cpu" else 1
                model_dir = prepare_decrypted_model_dir(settings.sensevoice_model_dir)
                loaded_models = []
                for _ in range(settings.sensevoice_correction_workers):
                    loaded_models.append(
                        AutoModel(
                            model=model_dir,
                            device=correction_device,
                            ngpu=correction_ngpu,
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
                _sensevoice_models = loaded_models
                _model_sensevoice = loaded_models[0]
                _correction_scheduler = scheduler
            if settings.v4_correction_model_dir and _v4_scheduler is None:
                from pathlib import Path

                model_path = settings.v4_correction_model_dir
                if not Path(model_path).is_dir():
                    raise RuntimeError("v4 Paraformer model directory is missing")
                asr_device, punc_device = settings.v4_correction_device, settings.v4_punctuation_device
                for label, configured in (("v4 correction", asr_device), ("v4 punctuation", punc_device)):
                    _resolve_device(configured, 0 if configured == "cpu" else 1,
                                    label=label, require_gpu=require_gpu_enabled())
                settings.v4_correction_interval_ms
                correction_models = [AutoModel(
                    model=prepare_decrypted_model_dir(model_path), device=asr_device,
                    ngpu=0 if asr_device == "cpu" else 1,
                    disable_update=True, disable_pbar=True,
                ) for _ in range(settings.v4_correction_workers)]
                _v4_punctuation = V4Punctuation(pipeline(
                    task=Tasks.punctuation,
                    model=prepare_decrypted_model_dir(settings.asr_online_punc_model_dir),
                    device=punc_device, disable_update=True,
                ))
                v4_itn = V4ITN(
                    enabled=settings.v4_itn_enabled,
                    failure_mode=settings.v4_itn_failure_mode,
                    remove_interjections=settings.v4_itn_remove_interjections,
                    enable_standalone_number=settings.v4_itn_enable_standalone_number,
                    enable_0_to_9=settings.v4_itn_enable_0_to_9,
                    enable_million=settings.v4_itn_enable_million,
                )
                await v4_itn.warmup()
                _v4_scheduler = V4CorrectionScheduler(
                    correction_models, _v4_punctuation,
                    capacity=settings.v4_correction_queue_capacity,
                    max_samples=(settings.v4_max_segment_seconds + 20) * 16000,
                    itn=v4_itn,
                )
                await _v4_scheduler.start()
        except Exception:
            await _release_models()
            raise


async def _release_models():
    global _model_online, _punct_pipeline, _model_vad, _model_correction
    global _model_sensevoice, _sensevoice_models, _correction_scheduler
    global _v4_scheduler, _v4_punctuation
    if _v4_scheduler is not None:
        await _v4_scheduler.close()
    _v4_scheduler = None
    _v4_punctuation = None
    if _correction_scheduler is not None:
        await _correction_scheduler.close()
    _correction_scheduler = None
    _model_sensevoice = None
    _sensevoice_models = []
    _model_online = None
    _punct_pipeline = None
    _model_vad = None
    _model_correction = None
    cleanup_prepared_model_dirs()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


async def shutdown_models():
    async with _model_lock:
        await _release_models()


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


def get_v4_scheduler():
    return _v4_scheduler


def get_v4_punctuation():
    return _v4_punctuation
