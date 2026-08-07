import asyncio
import torch
from modelscope.pipelines import pipeline
from modelscope.utils.constant import Tasks
from funasr import AutoModel

from app.core.config import settings
from app.core.model_assets import prepare_decrypted_model_dir

_model_online = None
_punct_pipeline = None
_model_lock = asyncio.Lock()


def device() -> torch.device:
    return torch.device(settings.device if torch.cuda.is_available() else "cpu")


async def load_models_if_needed():
    global _model_online, _punct_pipeline

    async with _model_lock:
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
