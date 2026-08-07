from __future__ import annotations

import asyncio
import copy
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]
InferenceFactory = Callable[[], Awaitable[JsonObject]]


class VisionStream(StrEnum):
    TEACHER = "T"
    STUDENT = "S"


@dataclass(frozen=True, slots=True)
class FrameCacheKey:
    task_id: str
    stream: VisionStream
    timestamp_ms: int

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("帧缓存 task_id 不能为空")
        if self.timestamp_ms < 0:
            raise ValueError("帧缓存 timestamp_ms 不能小于 0")


@dataclass(frozen=True, slots=True)
class InferenceCacheKey:
    frame: FrameCacheKey
    capability: str
    model_version: str
    roi_version: str

    def __post_init__(self) -> None:
        if not self.capability or not self.model_version or not self.roi_version:
            raise ValueError("推理缓存能力、模型版本和 ROI 版本不能为空")


class VisionAnalysisCache:
    def __init__(self, *, max_inference_entries: int) -> None:
        if max_inference_entries <= 0:
            raise ValueError("视觉推理缓存上限必须大于 0")
        self._max_inference_entries = max_inference_entries
        self._frames: dict[FrameCacheKey, Path] = {}
        self._inferences: OrderedDict[InferenceCacheKey, JsonObject] = OrderedDict()
        self._locks: dict[InferenceCacheKey, asyncio.Lock] = {}

    def remember_frame(self, key: FrameCacheKey, path: Path) -> None:
        self._frames[key] = path

    def get_frame(self, key: FrameCacheKey) -> Path | None:
        return self._frames.get(key)

    async def get_or_compute_inference(
        self,
        key: InferenceCacheKey,
        compute: InferenceFactory,
    ) -> JsonObject:
        cached = self._cached_inference(key)
        if cached is not None:
            return cached

        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                cached = self._cached_inference(key)
                if cached is not None:
                    return cached
                result = await compute()
                stored = copy.deepcopy(result)
                self._inferences[key] = stored
                self._inferences.move_to_end(key)
                while len(self._inferences) > self._max_inference_entries:
                    self._inferences.popitem(last=False)
                return copy.deepcopy(stored)
        finally:
            self._locks.pop(key, None)

    def clear_task(self, task_id: str) -> None:
        self._frames = {
            key: value for key, value in self._frames.items() if key.task_id != task_id
        }
        for key in [key for key in self._inferences if key.frame.task_id == task_id]:
            self._inferences.pop(key, None)

    def _cached_inference(self, key: InferenceCacheKey) -> JsonObject | None:
        cached = self._inferences.get(key)
        if cached is None:
            return None
        self._inferences.move_to_end(key)
        return copy.deepcopy(cached)
