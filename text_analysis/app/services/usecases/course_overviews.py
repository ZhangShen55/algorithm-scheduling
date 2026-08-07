import asyncio
import logging
from typing import List, Optional, Sequence, Dict, Any

from app.models.schemas import SegmentResult
from app.services.prompts import load_prompt
from app.services.llm_executor import chat_raw
from app.services.parsers import to_model
from app.services.guards import guard
from app.services.normalizers import normalize_node_times


try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
except Exception:
    log = logging.getLogger("app.services.usecases.course_overviews")

COURSE_OVERVIEW_PROMPT_FILE = "课程脑图生成部分.md"
COURSE_OVERVIEW_PROMPT_FILE_en = "课程脑图生成部分_en.md"

def split_into_parts(lst, part_count: int):
    part_count = max(1, int(part_count or 1))
    n = len(lst)
    k, m = divmod(n, part_count)
    result = []
    start = 0
    for i in range(part_count):
        end = start + k + (1 if i < m else 0)
        result.append(lst[start:end])
        start = end
    return result

def split_into_4_parts(lst):
    return split_into_parts(lst, 4)

def parts_from_segments_simple(segments, segment_count: int = 4):
    parts = split_into_parts(segments, segment_count)
    return [p for p in parts if p]

def build_user_prompts_from_parts(parts: Sequence[Sequence]) -> List[str]:
    user_prompts: List[str] = []
    for idx, segs in enumerate(parts):
        node_id = idx + 1
        lines, start, end = [], None, None
        for j, seg in enumerate(segs):
            bg, ed = int(float(seg.bg)), int(float(seg.ed))
            if j == 0:
                start = bg
            if j == len(segs) - 1:
                end = ed
            lines.append(f"{bg}-{ed}:{seg.text}")
        header = f"课程总的开始时间（秒):{start},结束时间（秒):{end},node_id:{node_id}"
        hints = "（请不要把 label 写成“子主题/孙主题”等占位词，必须是实际主题名称）"
        user_prompts.append(header + "\n" + "\n".join(lines) + "\n" + hints)
    return user_prompts

def build_user_prompts_from_parts_en(parts: Sequence[Sequence]) -> List[str]:
    user_prompts: List[str] = []
    for idx, segs in enumerate(parts):
        node_id = idx + 1
        lines, start, end = [], None, None
        for j, seg in enumerate(segs):
            bg, ed = int(float(seg.bg)), int(float(seg.ed))
            if j == 0:
                start = bg
            if j == len(segs) - 1:
                end = ed
            lines.append(f"{bg}-{ed}:{seg.text}")
        header = f"Total start time of the course (seconds):{start},End time (seconds):{end},node_id:{node_id}"
        hints = "(Please do not write the label as placeholder words such as' sub theme/sub theme ', it must be the actual theme name)"
        user_prompts.append(header + "\n" + "\n".join(lines) + "\n" + hints)
    return user_prompts

# async def _call_one_attempt(prompt: str, *, model: Optional[str]) -> tuple[Optional[SegmentResult], Dict[str,int]]:
#     """单次尝试：一定返回 usage；解析/guard 失败则返回 (None, usage)。"""
#     system_prompt = load_prompt(COURSE_OVERVIEW_PROMPT_FILE)
#     content, usage = await chat_raw(
#         user_prompt=prompt,
#         system_prompt=system_prompt,
#         model=model,
#         max_tokens=2048,
#         temperature=0.7,
#         top_p=0.8,
#         presence_penalty=1.5,
#         response_format={"type": "json_object"},
#         extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": False}},
#     )
#     try:
#         seg = to_model(content, SegmentResult)  # 内部自带 repair_json
#         guard(seg.model_dump())
#         return seg, usage
#     except Exception:
#         # 失败也要记 usage
#         return None, usage
async def _call_one_attempt(prompt: str, *, model: Optional[str], enable_thinking: bool = False, idx: int) -> tuple[Optional[SegmentResult], Dict[str,int]]:
    system_prompt = load_prompt(COURSE_OVERVIEW_PROMPT_FILE)
    content, usage = await chat_raw(
        user_prompt=prompt,
        system_prompt=system_prompt,
        model=model,
        max_tokens=2048,
        temperature=0.7,
        top_p=0.8,
        presence_penalty=1.5,
        response_format={"type": "json_object"},
        extra_body={"top_k": 10, "chat_template_kwargs": {"enable_thinking": enable_thinking}},
    )
    try:
        seg = to_model(content, SegmentResult)  # 先强类型化
        seg_dict = seg.model_dump()

        fix_stats = normalize_node_times(
            seg_dict,
            depth_min=2,               # 倒置修复从孙节点起
            clamp_to_parent=True,      # 夹到父区间
            clamp_depth_min=1,         # 子节点及以下都夹
            resort_siblings=False,     # 需要的话可开
            fix_third_grandchild=True  # 开启第3孙节点修复
        )
        if any(fix_stats.get(k,0) for k in ("fixed","clamped","collapsed","gc3_fixed")):
            log.debug(f"[course_overviews] part_idx={idx} 自动时间修正: {fix_stats}")

        # 用修正后的 dict 再校验
        guard(seg_dict)

        # 用修正后的 dict 重新构造 SegmentResult，后续统一使用
        seg = SegmentResult(**seg_dict)
        return seg, usage

    except Exception as e:
        preview = (content or "").replace("\n", " ")[:200]
        log.warning(f"[course_overviews] guard/normalize 失败 | part_idx={idx} | reason={e} | output_preview={preview}")
        return None, usage


async def run_until_all_pass(
    parts: Sequence[Sequence],
    *,
    model: Optional[str] = None,
    concurrency: int = 4,
    timeout_sec: float = 120.0,
    max_rounds: int = 5,
    enable_thinking: bool = False
) -> tuple[List[SegmentResult], List[Dict[str,int]]]:
    prompts = build_user_prompts_from_parts(parts)
    n = len(prompts)
    results: List[Optional[SegmentResult]] = [None] * n
    usage_accum: List[Dict[str,int]] = [{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0} for _ in range(n)]
    pending_idx = list(range(n))
    sem = asyncio.Semaphore(max(1, concurrency))

    async def runner(i: int):
        async with sem:
            seg, usage = await asyncio.wait_for(_call_one_attempt(prompts[i], model=model, enable_thinking=enable_thinking, idx=i), timeout=timeout_sec)
            # 成功与失败都添加
            for k in ("prompt_tokens","completion_tokens","total_tokens"):
                usage_accum[i][k] += int(usage.get(k, 0))
            return seg

    round_no = 0
    while pending_idx and round_no < max_rounds:
        round_no += 1
        tasks = [asyncio.create_task(runner(i)) for i in pending_idx]
        done = await asyncio.gather(*tasks, return_exceptions=False)
        new_pending: List[int] = []
        for j, i in enumerate(pending_idx):
            if done[j] is not None:
                results[i] = done[j]
            else:
                new_pending.append(i)
        pending_idx = new_pending

    if pending_idx:
        # 重试次数用尽都未通过
        failed = len(pending_idx)
        raise RuntimeError(f"仍有 {failed}/{n} 条未通过校验，放弃。")

    return [r for r in results if r is not None], usage_accum
