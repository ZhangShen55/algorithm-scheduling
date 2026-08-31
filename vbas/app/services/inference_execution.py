import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar


ItemT = TypeVar("ItemT")
ResultT = TypeVar("ResultT")


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
