import re
from typing import Dict, Any, List

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
except Exception:
    import logging
    log = logging.getLogger(__name__)

# time 形如 "12-345"
TIME_RE = re.compile(r"^\d+-\d+$")
# 你的占位词规则（对子/孙节点做过滤；想连父节点一起管，就把 depth>=1 去掉）
PLACEHOLDER_LABEL_RE = re.compile(r"(子主题|孙主题)", re.I)

def _assert(cond: bool, msg: str, *, ctx: str = ""):
    if not cond:
        # 关键：断言失败时打出上下文 + 原因
        log.warning(f"[guard] 校验未通过 | ctx={ctx} | reason={msg}")
        raise AssertionError(f"{msg} | ctx={ctx}")

def _parse_time(t: str, *, ctx: str) -> tuple[int, int]:
    _assert(TIME_RE.match(t) is not None, f"time 非法: {t}", ctx=ctx)
    s, e = map(int, t.split("-", 1))
    _assert(s <= e, f"time 起止颠倒: {t}", ctx=ctx)
    return s, e

def _check_node(node: Dict[str, Any], depth: int = 0, path: str = "nodes"):
    nid = str(node.get("id", "?"))
    ctx = f"{path}>{nid}"

    _assert("id" in node and isinstance(node["id"], str) and node["id"].strip(), "nodes.id 缺失/空", ctx=ctx)
    _assert("label" in node and isinstance(node["label"], str) and node["label"].strip(), "nodes.label 缺失/空", ctx=ctx)
    _assert("time" in node and isinstance(node["time"], str), "nodes.time 缺失/类型不对", ctx=ctx)
    _parse_time(node["time"], ctx=f"{ctx}.time")

    if depth >= 1:
        lab = node["label"]
        _assert(PLACEHOLDER_LABEL_RE.search(lab) is None, f"检测到占位标签: {lab}", ctx=f"{ctx}.label")

    children = node.get("children", [])
    if children is None:
        children = []
    _assert(isinstance(children, list), "nodes.children 必须为 list 或省略", ctx=ctx)

    for c in children:
        _check_node(c, depth + 1, path=ctx)

def guard(data: Dict[str, Any]) -> Dict[str, Any]:
    # 顶层必备
    for k in ("key_points", "document_skims", "nodes"):
        _assert(k in data, f"缺少必填字段: {k}", ctx="root")

    # key_points
    kp = data["key_points"]
    _assert(isinstance(kp, str) and 4 <= len(kp.strip()) <= 120, "key_points 长度不在期望范围", ctx="root.key_points")

    # document_skims
    ds = data["document_skims"]
    _assert(isinstance(ds, dict), "document_skims 必须是对象", ctx="root.document_skims")
    for k in ("time", "overview", "content"):
        _assert(k in ds and isinstance(ds[k], str) and ds[k].strip(), f"document_skims.{k} 缺失/空", ctx="root.document_skims")
    total_s, total_e = _parse_time(ds["time"], ctx="root.document_skims.time")

    # nodes
    _assert(isinstance(data["nodes"], dict), "nodes 必须是对象", ctx="root.nodes")
    _check_node(data["nodes"], depth=0, path="nodes")

    # 父节点时间覆盖总时间
    ns, ne = _parse_time(data["nodes"]["time"], ctx="nodes.time")
    _assert(ns <= total_s and ne >= total_e, "nodes.time 未覆盖 document_skims.time", ctx="nodes.time_vs_total")

    return data

