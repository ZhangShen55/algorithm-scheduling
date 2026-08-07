import json, asyncio, random
from typing import Dict, Any, List, Optional, Tuple

from fastapi import HTTPException
from app.services.llm_executor import chat_raw
from app.services.prompts import load_prompt
from app.models.entities import UsageInfo, CourseEvaluationRequestObject

from app.utils import (
    strip_think_blocks,
    llm_json_response_repair,
    coerce_usage,
    sum_usage,
)

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.setLevel(logging.DEBUG)
except Exception:
    import logging
    log = logging.getLogger(__name__)


_EVAL_KEYS = ("课程思政", "教学内容", "教学态度", "教学方法", "教学效果")


def _build_user_input_str(req: CourseEvaluationRequestObject) -> str:
    """严格按你给的模板拼接 user_input_str。"""
    knowledge_str = ", ".join(map(str, req.course_knowledge or []))
    return (
        f"课程名称：{req.course_name}\n"
        f"课堂内容：【{req.text}】\n"
        f"课堂模式：{req.course_model}\n"
        f"课堂知识点：[{knowledge_str}]\n"
        f"教师的板书示范次数：{req.blackboard_times}\n"
        f"教师的提问次数：{req.question_times}\n"
        f"师生互动次数：{req.interaction_times}\n"
        f"参与问答的学生人数：{req.question_stu_times}\n"
        f"学生发言总时长：{req.speak_stu_time}\n"
        f"教师课堂时间分布：{req.course_distribution}\n"
        f"学生举手站立互动次数：{req.standup_times}\n"
        f"学生抬头听讲行为次数：{req.raisehead_times}\n"
        f"学生抬头率：{req.raisehead_rate}\n"
        f"学生专注度：{req.concentration_rate}\n"
        f"学生互动率：{req.student_interaction_rate}\n"
        f"学生的迟到率：{req.late_rate}\n"
        f"学生的早退率：{req.leave_early_rate}\n"
        f"学生出勤率：{req.attendance_rate}\n"
        f"学生前排入座率：{req.frontrow_rate}\n"
        f"老师的普通话水平：{req.mandarin_level}\n"
        f"老师课堂站立讲台时长：{req.teacher_standup_time}\n"
        f"巡视时长：{req.patrol_time}\n"
        f"巡视次数：{req.patrol_times}"
    )


def _post_normalize(data: Dict[str, Any]) -> Dict[str, Any]:
    """把 score 统一为 0~10 的一位小数，reason 去首尾空白。"""
    for k in _EVAL_KEYS:
        item = data.get(k, {})
        if not isinstance(item, dict):
            continue
        sc = item.get("score", None)
        rs = item.get("reason", "")
        # 只要是数字就四舍五入到一位小数
        if isinstance(sc, (int, float)):
            sc = round(float(sc), 1)
            item["score"] = sc
        if isinstance(rs, str):
            item["reason"] = rs.strip()
    return data


def _validate_eval_result(data: Dict[str, Any]) -> Tuple[bool, str]:
    """
    目标 JSON:
    {
      "课程思政": {"score": float[0..10], "reason": str},
      "教学内容": {...},
      "教学态度": {...},
      "教学方法": {...},
      "教学效果": {...}
    }
    """
    if not isinstance(data, dict):
        return False, "返回不是对象"

    # 必备键
    for k in _EVAL_KEYS:
        if k not in data:
            return False, f"缺少维度：{k}"
        v = data[k]
        if not isinstance(v, dict):
            return False, f"维度 {k} 不是对象"
        if "score" not in v or "reason" not in v:
            return False, f"维度 {k} 缺少 score/reason"

        sc = v["score"]
        rs = v["reason"]

        # 分数必须为数字，范围 0..20，一位小数（已在 _post_normalize 四舍五入）
        if not isinstance(sc, (int, float)):
            return False, f"{k}.score 不是数字"
        if sc < 0 or sc > 20:
            return False, f"{k}.score 超出 0~10 范围"
        # 原因说明必须是非空字符串
        if not isinstance(rs, str) or not rs.strip():
            return False, f"{k}.reason 为空"

    return True, "ok"

def _validate_eval_scores(data: Dict[str, Any], base_score: float)->Dict[str, Any]:
    '''
    作用: 能够将评价结果中低于base_score的分数设置为base_score，减少模型调用次数
    '''
    for k in _EVAL_KEYS:
        score = data[k]["score"]
        if score < base_score:
            log.info(f"{k}.score < {base_score}，故 {score} -> {base_score}")
            data[k]["score"] = base_score
    return data

async def _llm_once(
    *, messages: List[dict], model: Optional[str], temperature: float,enable_thinking: bool = False
) -> Tuple[Dict[str, Any], Optional[UsageInfo]]:
    """单次调用：去 <think> → 尝试 JSON 解析 / 修复 → 归一化 usage。"""
    content, usage = await chat_raw(
        messages=messages,
        model=model,
        max_tokens=1024,
        temperature=temperature,
        top_p=0.9,
        presence_penalty=1.1,
        # response_format={"type": "json_object"},
        extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": enable_thinking}},
    )
    content = strip_think_blocks(content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = json.loads(llm_json_response_repair(content))

    data = _post_normalize(data)
    return data, coerce_usage(usage)


async def generate_course_evaluation_with_retry(
    req: CourseEvaluationRequestObject,
    *,
    eval_weight: Dict[str, float],
    retry_attempts: int = 3,
    enable_thinking: bool = False
) -> Tuple[Dict[str, Any], UsageInfo]:
    """
    - 组装 messages（或使用传入的）
    - LLM 调用 → 正规化 → 结构校验
    - 校验失败：指数退避重试
    - 返回 (data, usage_sum)
    """
    
    sys_prompt = load_prompt("课堂教学评价_20分.md") % (
        eval_weight.base_score,
        eval_weight.base_score,
        eval_weight.base_score,
        eval_weight.base_score,
        eval_weight.base_score,
        eval_weight.base_score,
        eval_weight.base_score,
        eval_weight.base_score,
        eval_weight.base_score,
        eval_weight.base_score,
        eval_weight.base_score
    )
    # log.info(f"[course_evaluation] 系统提示：{sys_prompt}")
    user_input_str = _build_user_input_str(req)
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_input_str},
    ]

    # messages = [m.model_dump() for m in req.messages] 将对象转换成字段 必须是pydantic的model

    usages: List[Optional[UsageInfo]] = []
    last_reason = "unknown"

    for attempt in range(1, max(1, retry_attempts) + 1):
        data, u = await _llm_once(messages=messages, model=req.model, temperature=req.temperature or 0.6, enable_thinking=enable_thinking)
        usages.append(u)
        log.debug(f"[course_evaluation] 第{attempt}次调用，返回结果：{data}")

        

        ok, reason = _validate_eval_result(data)


        # 权重配置
        _EVAL_WEIGHT = {
            # 5方面权重
            "课程思政": eval_weight.politics, 
            "教学内容": eval_weight.content, 
            "教学态度": eval_weight.attitude, 
            "教学方法": eval_weight.method, 
            "教学效果": eval_weight.effect,
            "knob": eval_weight.knob # 旋钮值
        }

        if ok:
            data = _validate_eval_scores(data, base_score=eval_weight.base_score)
            # log.info(f"[course_evaluation] 校验通过，返回结果：{data}")
            if attempt > 1:
                log.info(f"[course_evaluation] 校验通过，重试次数={attempt-1}")

            # ---------- 权重处理 ----------
            '''计算公式说明
            一、符号约定
            X = (x₁,…,x₅) 原始分
            W = (w₁,…,w₅) 权重，Σwᵢ = 1
            S = W·X = 9.02   例子： X = (8.9, 9.1, 9.2, 8.7, 9.0)
            α ∈ [0,1] “权重强调系数”，随时可调：
              α = 0 → 新分完全等于旧分（不强调权重）；
              α = 1 → 新分把权重差异拉到最大（仍守 base_score–10 边界）。
            二、构造 X₂ 的公式
            1. 
            先算“权重偏移量”
              dᵢ = (wᵢ − 1/5)  // 让最大权重项得正偏移，最小得负偏移
            2. 
            再把偏移量缩放到“安全幅度”
              Δ = 2(10 − S) = 2(10 − 9.02) = 1.96  // 离上界 10 的总余量
              δᵢ = α · dᵢ · Δ  // α 是你手里的旋钮
            3. 
            得到新分
              x₂ᵢ = xᵢ + δᵢ
            4. 
            边界保护（理论上只要 α ≤ 1 就不会越界，但写一步保险）
              x₂ᵢ = max(8, min(10, x₂ᵢ))
            '''
            scores = [data[k]["score"] for k in _EVAL_WEIGHT if k != "knob"]          # 原始分列表
            weights = [_EVAL_WEIGHT[k] for k in _EVAL_WEIGHT if k != "knob"]          # 对应权重
            S = sum(w * s for w, s in zip(weights, scores))                            # 原总分
            alpha = _EVAL_WEIGHT["knob"]                                               # 旋钮
            for k, w in ((k, _EVAL_WEIGHT[k]) for k in _EVAL_WEIGHT if k != "knob"):   # 逐条修正
                offset = alpha * (w - 0.2) * 2 * (20 - S)                              # 公式 δᵢ
                data[k]["score"] = float(round(max(eval_weight.base_score, min(20.0, data[k]["score"] + offset)))) # 四舍五入 小数位是0，例如8.12 -> 8.0 ,8.78 -> 9.0
                # data[k]["score"] = round(max(8.0, min(10.0, data[k]["score"] + offset)), 2) # 保留两位小数
                # data[k]["score"] = round(data[k]["score"] + offset, 2)
            
            return data, sum_usage(usages)

        last_reason = reason or "invalid structure"
        log.warning(f"[course_evaluation] 校验不通过，尝试 {attempt}/{retry_attempts}：{last_reason}")

        if attempt < retry_attempts:
            backoff = min(2 ** (attempt - 1), 4) + random.uniform(0, 0.3)
            await asyncio.sleep(backoff)

    raise HTTPException(status_code=400, detail=f"模型返回不符合课程评价结构：{last_reason}")
