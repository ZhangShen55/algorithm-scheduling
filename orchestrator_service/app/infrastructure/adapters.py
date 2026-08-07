"""Consolidated imports for existing algorithm and media adapters."""

from .asr import OfflineAsrAdapter
from .audio import FFmpegAudioExtractor
from .course_overview import CourseOverviewAdapter
from .ppt_slice import PptSliceAdapter
from .ppt_text import KeywordAdapter, OcrAdapter

__all__ = [
    "CourseOverviewAdapter",
    "FFmpegAudioExtractor",
    "KeywordAdapter",
    "OfflineAsrAdapter",
    "OcrAdapter",
    "PptSliceAdapter",
]
