import os
import re
import asyncio
import math
import time
import logging
import torch
from io import BytesIO
import soundfile as sf
from pydub import AudioSegment
from fastapi import File, Form
from faster_whisper import WhisperModel

import tempfile
import subprocess
import torchaudio


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("audio")

def load_audio_tensor(path: str):
    """
    加载音频为 tensor。优先 ffmpeg 后端；容器内 torchaudio 若未编译 ffmpeg 则回退 soundfile。
    """
    try:
        return torchaudio.load(path, backend="ffmpeg")
    except ValueError:
        return torchaudio.load(path)


def check_audio_format(audio_bytes: bytes) -> dict:
    try:
        with sf.SoundFile(BytesIO(audio_bytes)) as f:
            return {
                "samplerate": f.samplerate,
                "channels": f.channels,
                "subtype": f.subtype
            }
    except Exception:
        return {}


def standardize_audio(audio_bytes: bytes, suffix: str, force_resample: bool = False) -> bytes:
    info = check_audio_format(audio_bytes)
    need_resample = force_resample or not (
        info.get("samplerate") == 16000 and
        info.get("channels") == 1 and
        info.get("subtype") == "PCM_16"
    )

    if not need_resample:
        logger.info("[音频检查] 已符合要求，跳过重采样")
        return audio_bytes

    logger.info(f"[音频检查] 转换前参数：{info}，开始重采样")
    audio = AudioSegment.from_file(BytesIO(audio_bytes), format=suffix)
    audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)

    # 导出成 WAV 容器，不能是 raw_data
    buf = BytesIO()
    audio.export(buf, format="wav")          # 默认 PCM_16
    return buf.getvalue()

async def preprocess_audio(audio_bytes: bytes, suffix: str, force_resample: bool = False) -> bytes:
    start = time.perf_counter()
    result = await asyncio.to_thread(standardize_audio, audio_bytes, suffix, force_resample)
    duration = (time.perf_counter() - start) * 1000
    logger.info(f"[音频预处理] 耗时：{duration:.2f}ms")
    return result

# 分割音频
def crop_audio(audio_data:torch.Tensor, start_time, end_time, sample_rate):
    start_sample = int(start_time * sample_rate / 1000)  # 转换为样本数
    end_sample = int(end_time * sample_rate / 1000)  # 转换为样本数
    return audio_data[:,start_sample:end_sample]



def extract_audio_clip(input_path: str, start_time: float, duration: float, suffix=".wav") -> str:
    """
    用 ffmpeg 截取一段音频片段，返回新文件路径
    """
    output_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix).name
    command = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-ss", str(start_time),
        "-t", str(duration),
        "-ar", "16000",
        "-ac", "1",
        "-loglevel", "error",
        output_file
    ]
    subprocess.run(command, check=True)
    return output_file


logger = logging.getLogger(__name__)

def _normalize_temp_suffix(suffix: str) -> str:
    suffix = (suffix or "").strip().lower()
    suffix = os.path.basename(suffix)
    if suffix.startswith("."):
        suffix = suffix[1:]
    suffix = re.sub(r"[^a-z0-9]+", "", suffix)
    return f".{suffix or 'tmp'}"


def write_audio_bytes_to_temp_file(audio_bytes: bytes, file_name: str, suffix=".mp3") -> str:
    """

    file_name：上游传过来的文件名（仅用于保持调用签名，不参与临时路径拼接）

    将音频字节写入安全随机临时文件，若后缀为 .aac 则转换为 16kHz 单声道 WAV
    
    Args:
        audio_bytes: 音频字节数据
        suffix: 文件后缀，默认为 .mp3
    
    Returns:
        临时文件路径
    """
    safe_suffix = _normalize_temp_suffix(suffix)

    with tempfile.NamedTemporaryFile(delete=False, suffix=safe_suffix, dir="/tmp") as tmp:
        tmp.write(audio_bytes)
        original_file = tmp.name

    if safe_suffix == ".aac":
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav", dir="/tmp") as converted_tmp:
            converted_file = converted_tmp.name

        try:
            subprocess.run([
                "ffmpeg", "-y",
                "-i", original_file,
                "-ar", "16000",
                "-ac", "1",
                "-f", "wav",
                converted_file
            ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            logger.info(f"Converted AAC to WAV: {original_file} -> {converted_file}")
            os.remove(original_file)
            return converted_file

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to convert AAC to WAV: {e.stderr.decode()}")
            try:
                os.remove(converted_file)
            except FileNotFoundError:
                pass
            return original_file

    return original_file



def preprocess_audio2wav(input_file: str) -> str:
    # 转换为16k、单通道、wav格式
    audio = AudioSegment.from_file(input_file)
    audio = audio.set_frame_rate(16000).set_channels(1)
    temp_wav = tempfile.mktemp(suffix=".wav")
    audio.export(temp_wav, format="wav")
    return temp_wav


def split_audio(wav_file: str, chunk_size: int):
    audio = AudioSegment.from_file(wav_file)
    duration_ms = len(audio)
    duration_sec = math.ceil(duration_ms / 1000)
    chunks = []

    for i in range(0, duration_ms, chunk_size * 1000):
        chunk = audio[i:i + chunk_size * 1000]
        start_sec = i // 1000
        # 这里判断是否最后一段，end直接用duration_sec，否则正常加
        if i + chunk_size * 1000 >= duration_ms:
            end_sec = duration_sec
        else:
            end_sec = (i + len(chunk)) // 1000
        chunk_path = tempfile.mktemp(suffix=".wav")
        chunk.export(chunk_path, format="wav")
        chunks.append((chunk_path, start_sec, end_sec))
    return chunks


def plan_audio_chunks(
    duration_s: float,
    chunk_minutes: float = 60,
    min_last_minutes: float = 15,
    overlap_s: float = 15,
) -> list:
    """
    规划长音频分块方案，返回 [(clean_start_s, clean_end_s), ...] 列表。

    规则：
    - 按 chunk_minutes 等分，最后一块不足 min_last_minutes 则并入倒数第二块；
    - clean_start/clean_end 为该块对外"有效区间"，不含 overlap；
    - 实际送进模型的音频区间由调用方根据 overlap_s 扩展：
        actual_start = max(0, clean_start - overlap_s)   (非第一块)
        actual_end   = min(duration_s, clean_end + overlap_s) (非最后块)
    """
    chunk_s = chunk_minutes * 60
    min_last_s = min_last_minutes * 60

    chunks = []
    pos = 0.0
    while pos < duration_s:
        end = min(pos + chunk_s, duration_s)
        chunks.append((pos, end))
        if end >= duration_s:
            break
        pos = end

    # 最后一块过短则合并到倒数第二块
    if len(chunks) >= 2 and (chunks[-1][1] - chunks[-1][0]) < min_last_s:
        prev_start, _ = chunks[-2]
        _, last_end = chunks[-1]
        chunks[-2] = (prev_start, last_end)
        chunks.pop()

    return chunks


def detect_language(file_path: str, model: WhisperModel):
    # 语言检测
    seg,info = model.transcribe(file_path,language=None)
    # logging.info(f"detect info: {info}")
    lang = info.language
    prob = info.language_probability
    # text = seg.text.strip()
    return lang, prob