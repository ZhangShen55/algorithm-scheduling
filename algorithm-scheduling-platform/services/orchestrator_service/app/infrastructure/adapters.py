"""Consolidated imports for existing algorithm and media adapters."""

from services.orchestrator_service.asr import OfflineAsrAdapter
from services.orchestrator_service.audio import FFmpegAudioExtractor
from services.orchestrator_service.course_overview import CourseOverviewAdapter
from services.orchestrator_service.ppt_slice import PptSliceAdapter
from services.orchestrator_service.ppt_text import KeywordAdapter, OcrAdapter

__all__ = [
    "CourseOverviewAdapter",
    "FFmpegAudioExtractor",
    "KeywordAdapter",
    "OfflineAsrAdapter",
    "OcrAdapter",
    "PptSliceAdapter",
]
