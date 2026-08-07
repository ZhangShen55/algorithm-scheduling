import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import HTTPException

from app.core.config import settings
from app.utils.asr_stats import (
    add_processing_task, remove_processing_task,
    add_queued_task, remove_queued_task
)

logger = logging.getLogger(__name__)

# 并发控制
gpu_semaphore = asyncio.Semaphore(settings.concurrency)
_processing_task_count = 0
_processing_task_lock = asyncio.Lock()
_queued_task_ids: list[str] = []
_queued_task_lock = asyncio.Lock()

_model_lock = asyncio.Lock()  # 模型临界区同一把锁


@asynccontextmanager
async def acquire_gpu_slot(timeout: float | None = None, task_id: str | None = None):
    acquired = False
    timeout = settings.gpu_slot_timeout_seconds if timeout is None else timeout

    if task_id:
        async with _queued_task_lock:
            if task_id not in _queued_task_ids:
                _queued_task_ids.append(task_id)
                add_queued_task(task_id)

    try:
        await asyncio.wait_for(gpu_semaphore.acquire(), timeout)
        acquired = True
        if task_id:
            async with _queued_task_lock:
                if task_id in _queued_task_ids:
                    _queued_task_ids.remove(task_id)
                remove_queued_task(task_id)
            add_processing_task(task_id)
        logger.info(f"任务 {task_id} 获得GPU资源")
        yield
    finally:
        if acquired:
            gpu_semaphore.release()
            if task_id:
                remove_processing_task(task_id)
        elif task_id:
            async with _queued_task_lock:
                if task_id in _queued_task_ids:
                    _queued_task_ids.remove(task_id)
                remove_queued_task(task_id)


async def generate_with_gpu_lock(model, *args, **kwargs):
    try:
        async with _model_lock:
            return await asyncio.wait_for(
                asyncio.to_thread(model.generate, *args, **kwargs),
                timeout=60 * 60
            )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=408, detail="请求超时，请稍后再试")


async def transcribe_with_gpu_lock(model, *args, **kwargs):
    try:
        async with _model_lock:
            return await asyncio.wait_for(
                asyncio.to_thread(model.transcribe, *args, **kwargs),
                timeout=60 * 60
            )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=408, detail="请求超时，请稍后再试")
