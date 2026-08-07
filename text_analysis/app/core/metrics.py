import time
import asyncio
from typing import Dict, Any

TRACKED_PATHS = {
    "/v1/course_evaluation",
    "/v1/course_overviews",
    "/v1/course_time_analysis",
    "/v1/language_expression_analysis",
    "/v1/course_knowledge_corpus",
    "/v1/student_interaction_analysis",
    "/v1.1/extract_keywords",
    "/v1/extract_keywords",
    "/v1/extract_knowledge",
    "/v0.5/extract_knowledge",
    "/v1/ai_generated_evaluation",
    "/v1/course_ideopolitical",
}

def _now_ts() -> float:
    return time.time()

def _iso(ts: float) -> str:
    # 简单 ISO（不带时区偏移）
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))

class Metrics:
    def __init__(self) -> None:
        self.started_at = _now_ts()
        self._lock = asyncio.Lock()
        # 每个 path 的数据
        self._paths: Dict[str, Dict[str, float | int]] = {
            p: {"total": 0, "success": 0, "total_latency_ms": 0.0} for p in TRACKED_PATHS
        }
        # 汇总
        self._total: Dict[str, float | int] = {"total": 0, "success": 0, "total_latency_ms": 0.0}

    async def record(self, path: str, success: bool, latency_ms: float) -> None:
        if path not in self._paths:
            # 非跟踪接口直接忽略
            return
        async with self._lock:
            slot = self._paths[path]
            slot["total"] += 1
            if success:
                slot["success"] += 1
            slot["total_latency_ms"] += float(latency_ms)

            self._total["total"] += 1
            if success:
                self._total["success"] += 1
            self._total["total_latency_ms"] += float(latency_ms)

    def snapshot(self, *, version: str | None = None) -> Dict[str, Any]:
        # 计算平均耗时
        def _avg_ms(d: Dict[str, float | int]) -> float:
            total = int(d.get("total", 0) or 0)
            if total <= 0:
                return 0.0
            return round(float(d.get("total_latency_ms", 0.0)) / total, 2)

        paths_out: Dict[str, Any] = {}
        for p, d in self._paths.items():
            paths_out[p] = {
                "total": int(d["total"]),
                "success": int(d["success"]),
                "avg_latency_ms": _avg_ms(d),
            }

        total_out = {
            "total": int(self._total["total"]),
            "success": int(self._total["success"]),
            "avg_latency_ms": _avg_ms(self._total),
        }

        return {
            "started_at": _iso(self.started_at),
            "uptime_seconds": int(_now_ts() - self.started_at),
            "version": version or "",
            "paths": paths_out,
            "total": total_out,
        }

# 单例
metrics = Metrics()
