import time
import logging
from fastapi import APIRouter
from app.entity.data import SegmentRequestBody
from app.core.config import settings
from app.core.models import predict_fivewh

logger = logging.getLogger(__name__)
from app.utils.feature_utils import (
    extract_features_segments, merge_segments, format_result, reformat_result
)

router = APIRouter()


@router.post("/text/question")
async def fivewh(request: SegmentRequestBody):
    if not settings.open_fivewh:
        logger.error(f"open_fivewh={settings.open_fivewh} 未开启五何分类")
        return {"msg": f"open_fivewh={settings.open_fivewh} 未开启五何分类", "code": 4003}

    start_time = time.time()
    segments = request.segments

    # 课程总时长
    course_time_s = float(segments[-1].ed) - float(segments[0].bg)

    # 说话人特征
    spk_features = extract_features_segments(segments)

    # 所有 role（保持输入顺序去重）
    roles = []
    seen = set()
    for seg in segments:
        if seg.role not in seen:
            seen.add(seg.role)
            roles.append(seg.role)

    merged = merge_segments(segments)

    predictions = []
    for item in merged:
        label, _, conf = predict_fivewh(item["text"])
        if conf >= request.confidence:
            item.update({"label": label, "confidence": conf})
            predictions.append(item)

    result_list = []
    for role in roles:
        speak_time = spk_features[role]["speech_time"] if role in spk_features else 0
        single_result = format_result(
            data=predictions,
            target_ids=role,
            speak_time=speak_time,
            min_len=request.min_len
        )
        result_list.append(reformat_result(single_result))

    return {
        "result": result_list,
        "course_time": course_time_s,
        "timestamp": int(time.time()),
        "process_time_ms": int((time.time() - start_time) * 1000),
        "task_id": request.task_id or f"task_{int(time.time())}",
    }
