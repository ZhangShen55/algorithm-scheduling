import asyncio
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
from uuid import NAMESPACE_URL, uuid5

ResultT = TypeVar("ResultT")


def make_ppt_image_id(task_id: str, *, frame_seq: int, snap_time: int) -> str:
    identity = f"algorithm-platform:ppt:{task_id}:{frame_seq}:{snap_time}"
    return f"ppt-{uuid5(NAMESPACE_URL, identity).hex}"


@dataclass(frozen=True, slots=True)
class PptImageWork:
    ppt_image_id: str
    image_path: Path
    ordinal: int


@dataclass(frozen=True, slots=True)
class PptWorkLimits:
    batch_size: int = 8
    max_concurrency: int = 2

    def __post_init__(self) -> None:
        if self.batch_size <= 0:
            raise ValueError("PPT 子任务批次大小必须大于 0")
        if self.max_concurrency <= 0:
            raise ValueError("PPT 子任务并发上限必须大于 0")


def iter_work_batches(
    work: Iterable[PptImageWork],
    limits: PptWorkLimits,
) -> Iterator[list[PptImageWork]]:
    batch: list[PptImageWork] = []
    for item in work:
        batch.append(item)
        if len(batch) == limits.batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


async def run_bounded_work(
    work: Iterable[PptImageWork],
    limits: PptWorkLimits,
    worker: Callable[[PptImageWork], Awaitable[ResultT]],
) -> list[ResultT]:
    semaphore = asyncio.Semaphore(limits.max_concurrency)

    async def run_one(item: PptImageWork) -> ResultT:
        async with semaphore:
            return await worker(item)

    results: list[ResultT] = []
    for batch in iter_work_batches(work, limits):
        results.extend(await asyncio.gather(*(run_one(item) for item in batch)))
    return results
