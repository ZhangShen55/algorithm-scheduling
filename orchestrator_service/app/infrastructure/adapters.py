"""Consolidated imports for existing algorithm and media adapters."""

from .asr import OfflineAsrAdapter
from .audio import FFmpegAudioExtractor
from .ppt_slice import PptSliceAdapter
from .ppt_text import OcrAdapter

__all__ = [
    "FFmpegAudioExtractor",
    "OcrAdapter",
    "OfflineAsrAdapter",
    "PptSliceAdapter",
]
