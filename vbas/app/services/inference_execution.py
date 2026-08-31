import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar


ItemT = TypeVar("ItemT")
ResultT = TypeVar("ResultT")


def cuda_memory_snapshot(torch_module: Any = None) -> dict[str, int] | None:
    """读取当前服务进程的 CUDA allocator 指标，CPU 环境返回空。"""
    if torch_module is None:
        import torch as torch_module
    if not torch_module.cuda.is_available():
        return None
    return {
        "allocated_bytes": int(torch_module.cuda.memory_allocated()),
        "reserved_bytes": int(torch_module.cuda.memory_reserved()),
        "max_allocated_bytes": int(torch_module.cuda.max_memory_allocated()),
        "max_reserved_bytes": int(torch_module.cuda.max_memory_reserved()),
    }


def log_cuda_memory(logger: Any, operation: str) -> None:
    snapshot = cuda_memory_snapshot()
    if snapshot is None:
        return
    mib = 1024 * 1024
    logger.info(
        "CUDA allocator operation=%s allocated_mib=%.2f reserved_mib=%.2f "
        "max_allocated_mib=%.2f max_reserved_mib=%.2f",
        operation,
        snapshot["allocated_bytes"] / mib,
        snapshot["reserved_bytes"] / mib,
        snapshot["max_allocated_bytes"] / mib,
        snapshot["max_reserved_bytes"] / mib,
    )


async def execute_indexed(
    items: Sequence[ItemT],
    operation: Callable[[int, ItemT], Awaitable[ResultT]],
    *,
    sequential: bool,
) -> list[ResultT]:
    """按配置顺序等待，或保留现有 gather 兼容路径。"""
    if sequential:
        results: list[ResultT] = []
        for index, item in enumerate(items):
            results.append(await operation(index, item))
        return results
    return list(await asyncio.gather(
        *(operation(index, item) for index, item in enumerate(items))
    ))
