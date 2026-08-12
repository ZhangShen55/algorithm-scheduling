import os
import time
import shutil
import tempfile
import logging
from typing import Annotated
from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from app.utils.audio_analyze import analyze_audio_auto
from app.utils.asr_stats import update_stat

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/audio/db_snr")
async def audio_analyze(
    audioFile: Annotated[UploadFile, File(..., description="音频文件(wav/pcm)")],
    time_size: Annotated[int, Form(description="检测粒度，单位秒")] = 10,
):
    if time_size <= 0:
        raise HTTPException(status_code=400, detail="time_size must be positive")

    filename = audioFile.filename or "audio"
    suffix = os.path.splitext(filename)[-1]
    tmp_path = None
    start_time = time.time()

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(audioFile.file, tmp)
            tmp_path = tmp.name

        result = analyze_audio_auto(tmp_path, window_size_sec=time_size)
        end_time = time.time()
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                logger.warning(f"临时文件已不存在：{tmp_path}")
            except Exception as e:
                logger.error(f"删除临时文件失败：{tmp_path}，错误：{e}")

    update_stat("offline")
    return {
        "result": result,
        "task_id": f"task_{filename}",
        "process_time_ms": int((end_time - start_time) * 1000),
        "timestamp": int(time.time()),
    }
