# app/services/normalizers.py
from typing import Dict, Any, Tuple, List, Optional
import re

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.setLevel(logging.INFO)
except Exception:
    import logging
    log = logging.getLogger(__name__)

_TIME_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")

def _parse_time_relaxed(t: str) -> Optional[Tuple[int, int]]:
    if not isinstance(t, str):
        return None
    m = _TIME_RE.match(t)
    if not m:
        return None
    s, e = int(m.group(1)), int(m.group(2))
    return s, e

def _fmt(s: int, e: int) -> str:
    return f"{int(s)}-{int(e)}"

def normalize_node_times(
    data: Dict[str, Any],
    *,
    depth_min: int = 2,              # 反转修复从“孙节点”起
    clamp_to_parent: bool = True,    # 开启：夹到父区间
    clamp_depth_min: int = 1,        # 子节点及以下都夹
    resort_siblings: bool = False,   # 可选：按开始时间重排兄弟
    fix_third_grandchild: bool = True,  # ✅ 新增：修正第3个孙节点
) -> Dict[str, Any]:
    stats = {"fixed": 0, "clamped": 0, "collapsed": 0, "touched": [], "gc3_fixed": 0}

    def walk(node: Dict[str, Any], depth: int, parent_range: Optional[Tuple[int, int]], path: str):
        t = node.get("time", "")
        parsed = _parse_time_relaxed(t)
        if parsed is None:
            cur_range = parent_range
        else:
            s, e = parsed

            # 1) 反转修复（仅 depth >= depth_min）
            if e < s and depth >= depth_min:
                log.debug(f"[normalize] 修正倒置 | path={path} id={node.get('id','?')} {t} -> {_fmt(e,s)}")
                node["time"] = _fmt(e, s)
                s, e = e, s
                stats["fixed"] += 1
                stats["touched"].append(node.get("id"))

            # 2) 夹到父区间（depth >= clamp_depth_min）
            if clamp_to_parent and parent_range is not None and depth >= clamp_depth_min:
                ps, pe = parent_range
                ns, ne = max(s, ps), min(e, pe)
                if ns > ne:
                    # 完全落在父区间外：折叠到最近父边界（默认用父结束；如要父开始改成 ps）
                    ns = ne = pe if s > pe else ps
                    log.debug(f"[normalize] 折叠到父边界 | path={path} id={node.get('id','?')} -> {ns}-{ne}")
                    node["time"] = _fmt(ns, ne)
                    stats["collapsed"] += 1
                elif (ns, ne) != (s, e):
                    log.debug(f"[normalize] 夹到父区间 | path={path} id={node.get('id','?')} {s}-{e} -> {ns}-{ne}")
                    node["time"] = _fmt(ns, ne)
                    stats["clamped"] += 1
                s, e = ns, ne

            cur_range = (s, e)

        # 递归处理 children
        children = node.get("children") or []
        for idx, c in enumerate(children):
            walk(c, depth + 1, cur_range, f"{path}.children[{idx}]")

         # 3) 第3个孙节点特殊修复
        # depth == 1 说明当前 node 是“子节点”，它的 children 是“孙节点”们
        if fix_third_grandchild and depth == 1 and len(children) >= 3 and cur_range is not None:
            ps, pe = cur_range  # 当前子节点区间
            p0 = _parse_time_relaxed(children[0].get("time", ""))
            p1 = _parse_time_relaxed(children[1].get("time", ""))  # 上一个孙节点
            p2 = _parse_time_relaxed(children[2].get("time", ""))  # 第三个孙节点（目标）

            # 仅在第3个孙节点修正后仍为 0 秒时启动切分策略
            if p1 and p2:
                s1, e1 = p1
                s2, e2 = p2
                dur2 = max(0, e2 - s2)

                if dur2 == 0:
                    # 计算上一个孙节点的时长
                    d1 = max(0, e1 - s1)
                    prev_min_end = max(s1, (p0[1] if p0 else s1))  # 不要压到第1个孙节点的末尾之前

                    if d1 >= 2:
                        # 正常切半：前半给上一个，后半给第三个
                        split_prev = d1 // 2                      # 上一个保留的时长（向下取整）
                        new_prev_end = max(s1 + split_prev, prev_min_end)
                        new_prev_end = min(new_prev_end, e1)      # 不能超过原来的结束
                    elif d1 == 1:
                        # 边界：只有 1 秒。把 1 秒让给第三个，上一个变 0 秒（可接受）
                        new_prev_end = max(e1 - 1, prev_min_end, s1)
                    else:
                        # 上一个本身就 0 秒，没得切，放弃本轮调整
                        log.debug(f"[normalize] 无法为第三孙节点分配，上一孙节点时长=0 | path={path}")
                        new_prev_end = e1  # 维持原状

                    # 建立第三个孙节点区间：紧接 new_prev_end 到 e1
                    new_third_start = new_prev_end
                    new_third_end = e1

                    # 全量再夹一次到父子区间，避免越界
                    new_prev_end = min(max(new_prev_end, ps), pe)
                    new_third_start = min(max(new_third_start, ps), pe)
                    new_third_end = min(max(new_third_end, new_third_start), pe)

                    # 回填
                    old_prev = children[1].get("time", "")
                    old_third = children[2].get("time", "")
                    children[1]["time"] = _fmt(s1, new_prev_end)
                    children[2]["time"] = _fmt(new_third_start, new_third_end)

                    log.debug(
                        f"[normalize] 第3孙节点零时长拆分 | path={path} "
                        f"id_prev={children[1].get('id','?')} {old_prev} -> {children[1]['time']} | "
                        f"id_third={children[2].get('id','?')} {old_third} -> {children[2]['time']} "
                        f"(prev_dur={d1})"
                    )
                    stats["gc3_fixed"] += 1
                    stats["touched"].extend([children[1].get("id"), children[2].get("id")])


        # 4) 可选：重排兄弟（按开始时间）
        if resort_siblings and children:
            try:
                node["children"] = sorted(
                    children,
                    key=lambda c: (_parse_time_relaxed(c.get("time","")) or (10**12, 10**12))[0]
                )
            except Exception:
                pass

    root = data.get("nodes")
    if isinstance(root, dict):
        walk(root, depth=0, parent_range=None, path="nodes")
    return stats

