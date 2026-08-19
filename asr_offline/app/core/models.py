import asyncio
import ctranslate2
import torch
from faster_whisper import WhisperModel
from funasr import AutoModel

from app.core.config import operator_deployment, settings

# 单例缓存
_model_asr = None
_model_emotion = None
_model_whisper = None

# 线程锁
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
        raise RuntimeError("ASR device 必须是 cpu 或 cuda:<index>")

    index = int(configured[5:])
    if not torch.cuda.is_available():
        raise RuntimeError(f"算子要求使用 GPU {configured}，但 CUDA 不可用")
    visible_count = torch.cuda.device_count()
    if index >= visible_count:
        raise RuntimeError(
            f"GPU 设备 {configured} 索引越界，可见 CUDA 设备数量为 {visible_count}"
        )
    return torch.device(configured)


def _validate_ctranslate2_cuda(runtime_device: torch.device) -> None:
    if runtime_device.type != "cuda":
        return
    index = runtime_device.index or 0
    available_count = ctranslate2.get_cuda_device_count()
    if index >= available_count:
        raise RuntimeError(
            "Faster Whisper 要求使用 CTranslate2 CUDA "
            f"设备 cuda:{index}，但可见设备数量为 {available_count}"
        )


async def load_models_if_needed():
    """
    根据配置开关懒加载模型。
    """
    global _model_asr, _model_emotion, _model_whisper

    async with _model_lock:
        runtime_device = resolve_runtime_device()
        model_ngpu = 1 if runtime_device.type == "cuda" else 0
        if settings.open_mul_lang:
            _validate_ctranslate2_cuda(runtime_device)

        if settings.open_spk and _model_asr is None:
            _model_asr = AutoModel(
                model=settings.asr_model_dir,
                device=str(runtime_device),
                ngpu=model_ngpu,
                punc_model=settings.punc_model_dir,
                vad_model=settings.vad_model_dir,
                spk_model=settings.spk_model_dir,
                vad_kwargs={"max_single_segment_time": 30000, "max_end_silence_time": 800},
                sentence_timestamp=True,
                disable_update=True,
                disable_pbar=True
            )

        if settings.open_emotion and settings.open_spk and _model_emotion is None:
            _model_emotion = AutoModel(
                model=settings.emotion_model_dir,
                device=str(runtime_device),
                ngpu=model_ngpu,
                disable_update=True,
                disable_pbar=True
            )

        if settings.open_mul_lang and _model_whisper is None:
            _model_whisper = WhisperModel(
                settings.whisper_model_dir,
                compute_type="int8",
                device=runtime_device.type,
                device_index=runtime_device.index or 0,
            )


def get_asr_model():
    return _model_asr


def get_emotion_model():
    return _model_emotion


def get_whisper_model():
    return _model_whisper
