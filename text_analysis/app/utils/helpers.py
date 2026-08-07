# app/utils/helpers.py
import json
import logging
import requests
import json_repair
import re
import random
from fastapi import HTTPException
from app.models.entities import TextSegment
from typing import List, Optional, Union, Dict, Tuple, Any, Iterable

from app.models.entities import (
    MindMapNode, MindMap,
    ContentRequestObject,UsageInfo
)

logger = logging.getLogger(__name__)

# -----------------------------
# small helpers
# -----------------------------
def extract_content(input_str: str) -> str:
    start_index = input_str.find('{')
    end_index = input_str.rfind('}')
    if start_index != -1 and end_index != -1 and start_index < end_index:
        return input_str[start_index:end_index + 1]
    return "输入的字符串格式不正确"


def build_gen_params(request: ContentRequestObject) -> Dict:
    return {
        "messages": None,
        "temperature": request.temperature,
        "top_p": request.top_p,
        "max_tokens": request.max_tokens or 1024,
        "echo": False,
        "stream": request.stream,
        "repetition_penalty": request.repetition_penalty,
        "tools": request.tools,
        "tool_choice": request.tool_choice,
    }

# -----------------------------
# message tool formatting
# -----------------------------
def process_messages(messages, tools=None, tool_choice: Union[str, dict] = "none"):
    _messages = messages
    processed_messages = []
    msg_has_sys = False

    def filter_tools(_tool_choice, _tools):
        function_name = _tool_choice.get('function', {}).get('name', None)
        if not function_name:
            return []
        return [t for t in _tools if t.get('function', {}).get('name') == function_name]

    if tool_choice != "none":
        if isinstance(tool_choice, dict):
            tools = filter_tools(tool_choice, tools or [])
        if tools:
            processed_messages.append({"role": "system", "content": None, "tools": tools})
            msg_has_sys = True

    if isinstance(tool_choice, dict) and tools:
        processed_messages.append({"role": "assistant", "metadata": tool_choice["function"]["name"], "content": ""})

    for m in _messages:
        role, content = m.role, m.content
        tool_calls = getattr(m, 'tool_calls', None)

        if role == "function":
            processed_messages.append({"role": "observation", "content": content})
        elif role == "tool":
            processed_messages.append({"role": "observation", "content": content, "function_call": True})
        elif role == "assistant":
            if tool_calls:
                for tool_call in tool_calls:
                    processed_messages.append({
                        "role": "assistant",
                        "metadata": tool_call.function.name,
                        "content": tool_call.function.arguments
                    })
            else:
                for response in (content or "").split("\n"):
                    if "\n" in response:
                        metadata, sub_content = response.split("\n", maxsplit=1)
                    else:
                        metadata, sub_content = "", response
                    processed_messages.append({"role": role, "metadata": metadata, "content": sub_content.strip()})
        else:
            if role == "system" and msg_has_sys:
                msg_has_sys = False
                continue
            processed_messages.append({"role": role, "content": content})

    if not tools or tool_choice == "none":
        for m in _messages:
            if m.role == 'system':
                processed_messages.insert(0, {"role": m.role, "content": m.content})
                break
    return processed_messages

# -----------------------------
# mindmap helpers
# -----------------------------
def wrap_mindmap(mindmap_json_str: str) -> Optional[MindMap]:
    mindmap_data = json.loads(mindmap_json_str)

    def process_node(node: Dict) -> MindMapNode:
        if 'children' not in node:
            node['children'] = []
        return MindMapNode(**node)

    def process_children(children: Optional[List[Dict]]):
        if children:
            return [process_node(child) for child in children]
        return None

    def process_nodes(nodes: List[Dict]):
        processed_nodes: List[MindMapNode] = []
        for node in nodes:
            processed_children = process_children(node.get('children', []))
            if processed_children is not None:
                processed_nodes.append(process_node({**node, 'children': processed_children}))
        return processed_nodes

    processed_nodes = process_nodes(mindmap_data['mindmap']['nodes'])
    if processed_nodes:
        return MindMap(nodes=processed_nodes)
    return None

# -----------------------------
# JSON fences helpers
# -----------------------------
def extract_json_content(content: str) -> Optional[str]:
    start_tag = "```json"
    end_tag = "```"
    start_index = content.find(start_tag)
    if start_index == -1:
        return None
    end_index = content.find(end_tag, start_index + len(start_tag))
    if end_index != -1:
        return content[start_index + len(start_tag):end_index].strip()
    return content[start_index + len(start_tag):].strip()


def remove_json_markdown_fences(text: str) -> str:
    if "```json" in text:
        text = text.replace("```json", "")
    if "```" in text:
        text = text.replace("```", "")
    return text

# -----------------------------
# LLM response processing
# -----------------------------
def process_response(response, interface_tag: str, add_square: bool = False, remove_json_tag: bool = False):
    try:
        content = response.choices[0].message.content
        logger.debug(f"[{interface_tag}] response content: \n{content}")

        content = (content or "").replace("\n", "").replace(r'\"', '"').replace(r'\\', '')

        if "</think>" in content:
            content = content.split("</think>")[-1]
            logger.debug(f"[{interface_tag}] remove </think>")

        if remove_json_tag:
            content = remove_json_markdown_fences(content)
            if content:
                logger.debug(f"[{interface_tag}] extracted JSON part")

        if add_square and (not content.startswith('[') or not content.endswith(']')):
            content = '[' + content + ']'
            logger.debug(f"[{interface_tag}] patched [] around JSON")

        if interface_tag in {"translate", "extract_keywords", "course_overviews", "extract_knowledge", "course_evaluation"}:
            try:
                if interface_tag in {"translate", "extract_keywords", "extract_knowledge"}:
                    content = json_repair.repair_json(content)
                content = json.loads(content)
            except json.JSONDecodeError:
                logger.error(f"[{interface_tag}] Failed to parse response as JSON: {content}")
                raise HTTPException(status_code=400, detail="Invalid JSON response format")

        logger.debug(f"[{interface_tag}] JSON 解析结果: {type(content)}")
        return content
    except KeyError:
        logger.error(f"[{interface_tag}] response not contains 'message' key")
        raise HTTPException(status_code=400, detail="Invalid response, 'message' key not found")
    except json.JSONDecodeError:
        logger.error(f"[{interface_tag}] Failed to decode JSON")
        raise HTTPException(status_code=400, detail="Failed to parse JSON")
    except Exception as e:
        logger.error(f"[{interface_tag}] Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

def llm_json_response_repair(content: str):
    return json_repair.repair_json(content)

def load_prompt_content(file_path: str) -> str:
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"文件 {file_path} 未找到。")
    except PermissionError:
        raise PermissionError(f"没有权限读取文件 {file_path}。")
    except Exception as e:
        raise Exception(f"读取文件 {file_path} 时发生未知错误：{e}")

# -----------------------------
# HTTP helpers
# -----------------------------
def send_request(pyload: Dict, base_url: str):
    response = requests.post(f"{base_url}/api/chat", json=pyload)
    if response.status_code == 200:
        data = response.json()
        logger.debug(f"deepseek response: \n{data}")
        return data
    logger.error(f"deepseek request failed, status code: {response.status_code}")
    raise HTTPException(status_code=500, detail=f"Request failed, status code: {response.status_code}")


# -----------------------------
# segmentation helper
# -----------------------------
def concatenate_segments(data) -> List[str]:
    """把逐字片段合成完整句，输出如 'start-end:text' 的行列表。"""
    result: List[str] = []
    temp_text = ""
    start_time = None

    ed = 0
    for seg in data:
        segment_text = getattr(seg, 'text', '')
        bg = float(getattr(seg, 'bg', 0))
        ed = float(getattr(seg, 'ed', 0))

        if start_time is None:
            start_time = bg

        temp_text += segment_text

        if segment_text.endswith(("。", "？", ".", "?")):
            result.append(f"{int(start_time)}-{int(ed)}:{temp_text}")
            temp_text = ""
            start_time = None

    # 如果最后一段没有句号，兜底也输出
    if temp_text and start_time is not None:
        result.append(f"{int(start_time)}-{int(ed)}:{temp_text}")

    return result

def build_part_lines(part_segments: List[Any]) -> List[str]:
    """拼接成 start-end:text (复用逻辑)"""
    return concatenate_segments(part_segments)

# 校验
def is_valid_result(result: Dict) -> bool:
    try:
        if not isinstance(result, dict) or 'knowledge' not in result:
            logger.info("[is_valid_result] 缺少knowledge字段")
            return False
        knowledge = result['knowledge']
        if len(knowledge) < 4:
            logger.info(f"[is_valid_result] 分类数量不足: {len(knowledge)} (要求至少4个)")
            return False
        for category_name, items in knowledge.items():
            if len(items) < 4:
                logger.info(f"[is_valid_result] 分类 '{category_name}' 的知识点数量不足: {len(items)} (要求至少4个)")
                return False
        return True
    except Exception as e:
        logger.error(f"[is_valid_result] 验证过程发生异常: {str(e)}")
        return False

# 用于将课程分成4段
def split_into_4_parts(lst):
    n = len(lst)
    k, m = divmod(n, 4)
    result = []
    start = 0
    for i in range(4):
        end = start + k + (1 if i < m else 0)
        result.append(lst[start:end])
        start = end
    return result


def coerce_usage(u: Any) -> UsageInfo | None:
    """把 dict / pydantic / SDK 对象 统一转成 UsageInfo。"""
    if not u:
        return None
    if isinstance(u, UsageInfo):
        return u
    if isinstance(u, dict):
        d = u
    elif hasattr(u, "model_dump"):
        d = u.model_dump()
    elif hasattr(u, "dict"):
        d = u.dict()
    else:
        # 兜底：尝试用属性取
        d = {
            "prompt_tokens": getattr(u, "prompt_tokens", 0),
            "completion_tokens": getattr(u, "completion_tokens", 0),
            "total_tokens": getattr(u, "total_tokens", 0),
        }
    return UsageInfo(
        prompt_tokens=int(d.get("prompt_tokens") or 0),
        completion_tokens=int(d.get("completion_tokens") or 0),
        total_tokens=int(d.get("total_tokens") or 0),
    )



TIME_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")

def segments_to_plain_text(segments) -> str:
    """把 textSegments 的 text 串起来（不带时间）。"""
    return "\n".join(getattr(seg, "text", "") for seg in segments if getattr(seg, "text", ""))

def parse_time_pair(s: str) -> Tuple[int, int] | None:
    """把 'a-b' 转成 (a,b)，若 a>b 就交换；不合法返回 None。"""
    m = TIME_RE.match(s or "")
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if a > b:
        a, b = b, a
    return a, b


def sort_times(times: list[str]) -> list[str]:
    """去重并按 (start,end) 排序时间段字符串。"""
    parsed = []
    for t in times or []:
        p = parse_time_pair(str(t))
        if p is not None:
            parsed.append(p)
    parsed = sorted(set(parsed), key=lambda x: (x[0], x[1]))
    return [f"{s}-{e}" for s, e in parsed]


# def sum_usage(usages: list[UsageInfo | None]) -> UsageInfo:
#     """汇总多次 LLM 调用的 usage（真实 token 统计）。"""
#     pt = ct = tt = 0
#     for u in usages:
#         if not u:
#             continue
#         pt += int(u.prompt_tokens or 0)
#         ct += int(u.completion_tokens or 0)
#         tt += int(u.total_tokens or 0)
#     return UsageInfo(prompt_tokens=pt, completion_tokens=ct, total_tokens=tt)


#############

def sum_usage(
    usages: Iterable[Union[UsageInfo, Dict[str, int], Any]],
    *,
    as_dict: bool = False,
) -> UsageInfo | Dict[str, int]:
    """
    汇总多次 LLM 调用的 usage（真实 token 统计）。
    - 入参可混合 UsageInfo / dict / SDK对象
    - 默认返回 UsageInfo；需要 dict 则 as_dict=True
    """
    pt = ct = tt = 0
    for u in usages:
        u2 = coerce_usage(u)
        if not u2:
            continue
        pt += int(u2.prompt_tokens or 0)
        ct += int(u2.completion_tokens or 0)
        tt += int(u2.total_tokens or 0)

    if as_dict:
        return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}
    return UsageInfo(prompt_tokens=pt, completion_tokens=ct, total_tokens=tt)


#############


def strip_think_blocks(text: str) -> str:
    """
    移除 LLM 输出中的 <think> ... </think> 推理包裹，保留可解析内容。
    大小写不敏感，跨行匹配。
    """
    if not text:
        return text
    return re.sub(r"(?is)<\s*think\s*>.*?<\s*/\s*think\s*>", "", text).strip()


def shuffle_knowledge_modules(
    data: Dict[str, Any],
    *,
    seed: int | None = None,
    shuffle_modules: bool = False,   # 如果也想打乱“模块”的顺序，设 True
    shuffle_points: bool = True,     # 默认只打乱“知识点”顺序
) -> Dict[str, Any]:
    """
    对 extract_knowledge 的结果进行顺序随机化：
    - data 形如 {"course_name": "...", "knowledge": {"模块A": {"点1": true, ...}, ...}}
    - 默认仅打乱每个模块内部“知识点”的顺序；如需连模块顺序一起打乱，传 shuffle_modules=True
    - seed 不为 None 时得到可复现的随机顺序
    """
    if not isinstance(data, dict):
        return data
    k = data.get("knowledge")
    if not isinstance(k, dict):
        return data

    rnd = random.Random(seed)

    # 处理模块顺序
    module_items = list(k.items())
    if shuffle_modules:
        rnd.shuffle(module_items)

    new_k: Dict[str, Any] = {}
    for module, points in module_items:
        if isinstance(points, dict) and shuffle_points:
            items = list(points.items())
            rnd.shuffle(items)
            new_k[module] = dict(items)  # 以打乱后的插入顺序重建
        else:
            new_k[module] = points

    data["knowledge"] = new_k
    return data


def llm_json_response_repair(content: str) -> str:
    """
    粗暴的 JSON 修复器：
    - 去掉 markdown 代码块标记 ```json ... ```
    - 替换中文引号为英文引号
    - 尝试截取第一个 { 到最后一个 } 之间的内容
    """
    if not content:
        return "{}"

    # 去除代码块包裹
    content = re.sub(r"^```(?:json)?", "", content.strip(), flags=re.IGNORECASE|re.MULTILINE)
    content = content.replace("```", "")

    # 替换中文引号为英文引号
    content = content.replace("“", "\"").replace("”", "\"").replace("‘", "'").replace("’", "'")

    # 截取第一个 { 到最后一个 }
    if "{" in content and "}" in content:
        start = content.find("{")
        end = content.rfind("}") + 1
        content = content[start:end]

    # 尝试解析一遍，如果失败，返回 {}
    try:
        json.loads(content)
    except Exception:
        return "{}"

    return content


def parse_json_relaxed(s: str) -> Dict[str, Any]:
    """宽松 JSON 解析：先直接 load，失败再去掉 ``` 围栏再试。"""
    s = (s or "").strip()
    try:
        return json.loads(s)
    except Exception:
        s2 = s.replace("```json", "").replace("```", "").strip()
        return json.loads(s2)

def flatten_info_texts(info: List[Dict[str, Any]]) -> List[str]:
    """
    用于思政异常2次附件user_prompt构建
    把 info[].content[].text 拉平去重。
    """
    out, seen = [], set()
    for item in info or []:
        for c in item.get("content") or []:
            t = (c.get("text") or "").strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out


def parse_bool(s: str) -> Tuple[bool, bool]:
    """
    把 LLM 返回的 'True'/'False'（大小写不敏感、允许首尾空白）解析成布尔。
    返回 (is_valid, value)。解析失败 is_valid=False。
    """
    if s is None:
        return False, False
    v = s.strip().lower()
    if v == "true":
        return True, True
    if v == "false":
        return True, False
    return False, False


def any_text_match_in_part_lines(info: List[Dict[str, Any]], part_lines: List[str]) -> bool:
    """
    用于思政异常的分析阶段
    目的：防止text是llm无中生有的内容
    只要 info[].content[].text 在任意一行 part_lines 中出现（子串包含），就算命中。
    例：part_lines 形如 ["2492-2496:中国的反侵略战争是必然胜利的。", ...]
    """
    if not info or not part_lines:
        return False
    for item in info:
        for c in (item.get("content") or []):
            t = (c.get("text") or "").strip()
            if not t:
                continue
            if any(t in line for line in part_lines):
                return True
    return False

def clean_bad_words_simple(text: str) -> str:
    """移除指定脏词（不用正则），并简单收缩空白。"""
    _BAD_WORDS = ["妈的", "傻", "妹", "操"]
    _SB_VARIANTS = ["sb", "SB", "Sb", "sB"]

    if not text:
        return text
    s = text
    for w in _BAD_WORDS:
        s = s.replace(w, "")
    for w in _SB_VARIANTS:
        s = s.replace(w, "")
    s = " ".join(s.split()).strip()
    return s


def sanitize_text_segments_simple(segments: List[TextSegment]) -> List[TextSegment]:
    """返回清洗后的新数组，不该原对象"""
    return [
        TextSegment(text=clean_bad_words_simple(seg.text), bg=seg.bg, ed=seg.ed)
        for seg in segments
    ]


def sum_two_usage(a: Dict[str, int], b: Dict[str, int]) -> Dict[str, int]:
    # 用于思政异常2次复检token统计
    return {
        "prompt_tokens": int((a or {}).get("prompt_tokens", 0)) + int((b or {}).get("prompt_tokens", 0)),
        "completion_tokens": int((a or {}).get("completion_tokens", 0)) + int((b or {}).get("completion_tokens", 0)),
        "total_tokens": int((a or {}).get("total_tokens", 0)) + int((b or {}).get("total_tokens", 0)),
    }