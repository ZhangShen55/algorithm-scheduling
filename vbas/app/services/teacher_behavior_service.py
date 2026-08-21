# app/services/teacher_behavior_service.py
import os
import cv2
import base64
import time
import numpy as np
from typing import Any, List, Optional, Tuple, Dict
from ..schemas.stu_tea_behavior import (
    TeacherBehaviorRequest,
    TeacherBehaviorResponse,
    TeacherBehaviorImageResult,
    ResultItem,
    ObjectPosition
)
from ..core.settings import yolo_teacher_behavior_model, settings, use_half
from ..schemas.geometry import Point
import logging

logger = logging.getLogger(__name__)
verbose=True


def analyze_teacher_head_pose(img, position):
    from .teacher_head_pose_service import analyze_teacher_head_pose as analyze_teacher_head_pose_impl

    return analyze_teacher_head_pose_impl(img, position)


def failed_head_pose_result(message: str):
    from .teacher_head_pose_service import failed_head_pose_result as failed_head_pose_result_impl

    return failed_head_pose_result_impl(message)


def no_teacher_head_pose_result():
    from .teacher_head_pose_service import no_teacher_head_pose_result as no_teacher_head_pose_result_impl

    return no_teacher_head_pose_result_impl()


def get_teacher_head_pose_enabled() -> bool:
    head_pose_config = getattr(settings, "Teacher_Head_Pose", {})
    value = head_pose_config.get("Enabled", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
# 教师行为模型目标类别码定义：teacher_behavior.pt
TEACHER_BEHAVIOR_OBJECT_TYPES = {
    'platform_person': 100,  # 讲台是否有人
    'sitting': 201,  # sit
    'standing': 202,  # stand
    'writing': 203,  # bbwriting
    'teaching': 204,  # teach
}

TEACHER_BEHAVIOR_LABEL_TO_KEY = {
    'stand': 'standing',
    'sit': 'sitting',
    'bbwriting': 'writing',
    'teach': 'teaching',
}
TEACHER_BEHAVIOR_MERGE_IOU_KEY = "MergeIoU"
DEFAULT_TEACHER_BEHAVIOR_MERGE_IOU = 0.8
TEACHER_BEHAVIOR_SUBJECT_CLUSTER_IOU_KEY = "SubjectClusterIoU"
DEFAULT_TEACHER_BEHAVIOR_SUBJECT_CLUSTER_IOU = 0.45
TEACHER_BEHAVIOR_IMAGE_SIZE_KEY = "ImageSize"
DEFAULT_TEACHER_BEHAVIOR_IMAGE_SIZE = 640
DEFAULT_TEACHER_BEHAVIOR_CLASS_THRESHOLD = 0.25
TEACHER_BEHAVIOR_KEEP_ONLY_MAIN_SUBJECT_KEY = "KeepOnlyMainSubject"
DEFAULT_TEACHER_BEHAVIOR_KEEP_ONLY_MAIN_SUBJECT = True
TEACHER_BEHAVIOR_MAIN_SUBJECT_STRATEGY_KEY = "MainSubjectStrategy"
DEFAULT_TEACHER_BEHAVIOR_MAIN_SUBJECT_STRATEGY = "posture_confidence"
TEACHER_BEHAVIOR_POSTURE_LABELS = {"sit", "stand"}
TEACHER_BEHAVIOR_POSTURE_CONFLICT_RATIO_KEY = "PostureConflictRatio"
DEFAULT_TEACHER_BEHAVIOR_POSTURE_CONFLICT_RATIO = 0.10
TEACHER_BEHAVIOR_POSTURE_CONFLICT_DEFAULT_KEY = "PostureConflictDefault"
DEFAULT_TEACHER_BEHAVIOR_POSTURE_CONFLICT_DEFAULT = "stand"
TEACHER_BEHAVIOR_FORCE_POSTURE_FALLBACK_KEY = "ForcePostureWhenMissing"
DEFAULT_TEACHER_BEHAVIOR_FORCE_POSTURE_FALLBACK = True

def mask_polygon(img: np.ndarray, points: List[Point]) -> Tuple[np.ndarray, Tuple[int, int]]:
    """
    使用多边形区域对图像进行遮罩，仅保留多边形内区域
    """
    if not points:
        return img, (0, 0)

    pts = np.array([[p.X, p.Y] for p in points], dtype=np.int32)
    mask = np.zeros(img.shape[:2], dtype=np.uint8)
    cv2.fillPoly(mask, [pts], 255)
    masked_img = cv2.bitwise_and(img, img, mask=mask)

    x, y, w, h = cv2.boundingRect(pts)
    subimg = masked_img[y:y + h, x:x + w]
    return subimg, (x, y)


def get_teacher_behavior_config_value(key: str, default_value: float) -> float:
    threshold_config = getattr(settings, "Teacher_Behavior_Thresd", {})
    try:
        return float(threshold_config.get(
            key,
            default_value
        ))
    except (TypeError, ValueError):
        return default_value


def get_teacher_behavior_raw_config_value(key: str, default_value):
    threshold_config = getattr(settings, "Teacher_Behavior_Thresd", {})
    return threshold_config.get(key, default_value)


def get_teacher_behavior_merge_iou() -> float:
    return get_teacher_behavior_config_value(
        TEACHER_BEHAVIOR_MERGE_IOU_KEY,
        DEFAULT_TEACHER_BEHAVIOR_MERGE_IOU
    )


def get_teacher_behavior_subject_cluster_iou() -> float:
    return get_teacher_behavior_config_value(
        TEACHER_BEHAVIOR_SUBJECT_CLUSTER_IOU_KEY,
        DEFAULT_TEACHER_BEHAVIOR_SUBJECT_CLUSTER_IOU
    )


def get_teacher_behavior_image_size() -> int:
    return int(get_teacher_behavior_config_value(
        TEACHER_BEHAVIOR_IMAGE_SIZE_KEY,
        DEFAULT_TEACHER_BEHAVIOR_IMAGE_SIZE
    ))


def normalize_teacher_behavior_threshold_overrides(threshold_overrides: Optional[Any]) -> Dict[str, float]:
    if threshold_overrides is None:
        return {}
    if hasattr(threshold_overrides, "model_dump"):
        raw_values = threshold_overrides.model_dump(exclude_none=True)
    elif isinstance(threshold_overrides, dict):
        raw_values = threshold_overrides
    else:
        raw_values = {
            label: getattr(threshold_overrides, label)
            for label in TEACHER_BEHAVIOR_LABEL_TO_KEY
            if hasattr(threshold_overrides, label) and getattr(threshold_overrides, label) is not None
        }

    normalized = {}
    for label in TEACHER_BEHAVIOR_LABEL_TO_KEY:
        if label not in raw_values or raw_values[label] is None:
            continue
        try:
            normalized[label] = float(raw_values[label])
        except (TypeError, ValueError):
            continue
    return normalized


def get_teacher_behavior_label_threshold(label: str, threshold_overrides: Optional[Any] = None) -> float:
    normalized_overrides = normalize_teacher_behavior_threshold_overrides(threshold_overrides)
    if label in normalized_overrides:
        return normalized_overrides[label]
    return get_teacher_behavior_config_value(
        label,
        DEFAULT_TEACHER_BEHAVIOR_CLASS_THRESHOLD
    )


def get_teacher_behavior_predict_conf(threshold_overrides: Optional[Any] = None) -> float:
    thresholds = [
        get_teacher_behavior_label_threshold(label, threshold_overrides)
        for label in TEACHER_BEHAVIOR_LABEL_TO_KEY
    ]
    return min(thresholds) if thresholds else DEFAULT_TEACHER_BEHAVIOR_CLASS_THRESHOLD


def get_teacher_behavior_keep_only_main_subject() -> bool:
    value = get_teacher_behavior_raw_config_value(
        TEACHER_BEHAVIOR_KEEP_ONLY_MAIN_SUBJECT_KEY,
        DEFAULT_TEACHER_BEHAVIOR_KEEP_ONLY_MAIN_SUBJECT
    )
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def get_teacher_behavior_main_subject_strategy() -> str:
    value = get_teacher_behavior_raw_config_value(
        TEACHER_BEHAVIOR_MAIN_SUBJECT_STRATEGY_KEY,
        DEFAULT_TEACHER_BEHAVIOR_MAIN_SUBJECT_STRATEGY
    )
    return str(value).strip().lower() or DEFAULT_TEACHER_BEHAVIOR_MAIN_SUBJECT_STRATEGY


def get_teacher_behavior_posture_conflict_ratio() -> float:
    value = get_teacher_behavior_config_value(
        TEACHER_BEHAVIOR_POSTURE_CONFLICT_RATIO_KEY,
        DEFAULT_TEACHER_BEHAVIOR_POSTURE_CONFLICT_RATIO
    )
    return max(0.0, value)


def get_teacher_behavior_posture_conflict_default() -> str:
    value = get_teacher_behavior_raw_config_value(
        TEACHER_BEHAVIOR_POSTURE_CONFLICT_DEFAULT_KEY,
        DEFAULT_TEACHER_BEHAVIOR_POSTURE_CONFLICT_DEFAULT
    )
    normalized = str(value).strip().lower()
    if normalized not in TEACHER_BEHAVIOR_POSTURE_LABELS:
        return DEFAULT_TEACHER_BEHAVIOR_POSTURE_CONFLICT_DEFAULT
    return normalized


def get_teacher_behavior_force_posture_when_missing() -> bool:
    value = get_teacher_behavior_raw_config_value(
        TEACHER_BEHAVIOR_FORCE_POSTURE_FALLBACK_KEY,
        DEFAULT_TEACHER_BEHAVIOR_FORCE_POSTURE_FALLBACK
    )
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def bbox_area(box) -> float:
    x1, y1, x2, y2 = box[:4]
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def bbox_iou(box_a, box_b) -> float:
    ax1, ay1, ax2, ay2 = box_a[:4]
    bx1, by1, bx2, by2 = box_b[:4]
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_area = max(0.0, inter_x2 - inter_x1) * max(0.0, inter_y2 - inter_y1)
    union_area = bbox_area(box_a) + bbox_area(box_b) - inter_area
    if union_area <= 0:
        return 0.0
    return inter_area / union_area


def to_object_position(
        box,
        offset: Tuple[int, int],
        confidence: Optional[float] = None,
        suspected_sitting: Optional[bool] = None,
        posture_fallback: Optional[bool] = None) -> ObjectPosition:
    ox, oy = offset
    x1, y1, x2, y2 = map(int, box[:4])
    return ObjectPosition(
        LeftTopX=x1 + ox,
        LeftTopY=y1 + oy,
        RightBtmX=x2 + ox,
        RightBtmY=y2 + oy,
        Confidence=confidence,
        SuspectedSitting=suspected_sitting,
        PostureFallback=posture_fallback
    )


def normalize_detection_row(row):
    values = row.tolist() if hasattr(row, "tolist") else row
    return [float(v) for v in values]


def get_detection_label(row, names) -> str:
    return str(names.get(int(row[5]), int(row[5]))).lower()


def group_distinct_label_count(group, names) -> int:
    labels = set()
    for row in group:
        label = get_detection_label(row, names)
        if label in TEACHER_BEHAVIOR_LABEL_TO_KEY:
            labels.add(label)
    return len(labels)


def group_posture_confidence(group, names) -> float:
    posture_scores = []
    for row in group:
        label = get_detection_label(row, names)
        if label in TEACHER_BEHAVIOR_POSTURE_LABELS:
            posture_scores.append(row[4])
    return max(posture_scores) if posture_scores else 0.0


def group_top_y(group) -> float:
    return min(row[1] for row in group)


def group_max_confidence(group) -> float:
    return max(row[4] for row in group)


def select_main_subject_group(groups, names, strategy: str):
    if strategy == "topmost":
        return min(groups, key=lambda group: (
            group_top_y(group),
            -bbox_area(max(group, key=bbox_area)),
            -group_max_confidence(group)
        ))

    if strategy == "multi_label_topmost":
        return max(groups, key=lambda group: (
            group_distinct_label_count(group, names),
            -group_top_y(group),
            bbox_area(max(group, key=bbox_area)),
            group_max_confidence(group)
        ))

    return max(groups, key=lambda group: (
        1 if group_posture_confidence(group, names) > 0 else 0,
        group_posture_confidence(group, names),
        group_max_confidence(group),
        -group_top_y(group),
        group_distinct_label_count(group, names),
        bbox_area(max(group, key=bbox_area))
    ))


def collect_teacher_behavior_groups(detections, names, merge_iou: float, threshold_overrides: Optional[Any] = None):
    groups = []
    subject_cluster_iou = get_teacher_behavior_subject_cluster_iou()
    base_threshold = get_teacher_behavior_predict_conf(threshold_overrides)
    if subject_cluster_iou <= 0:
        subject_cluster_iou = merge_iou
    for raw_row in detections:
        row = normalize_detection_row(raw_row)
        if len(row) < 6:
            continue
        cls_id = int(row[5])
        label = str(names.get(cls_id, cls_id)).lower()
        if label not in TEACHER_BEHAVIOR_LABEL_TO_KEY:
            continue
        if row[4] < base_threshold:
            continue

        matched_group = None
        for group in groups:
            if any(bbox_iou(row, existing_row) >= subject_cluster_iou for existing_row in group):
                matched_group = group
                break
        if matched_group is None:
            groups.append([row])
        else:
            matched_group.append(row)

    if groups and get_teacher_behavior_keep_only_main_subject():
        groups = [select_main_subject_group(
            groups,
            names,
            get_teacher_behavior_main_subject_strategy()
        )]

    return groups


def best_rows_by_behavior(group, names, threshold_overrides: Optional[Any] = None):
    best_by_behavior = {}
    for row in group:
        label = get_detection_label(row, names)
        behavior_key = TEACHER_BEHAVIOR_LABEL_TO_KEY.get(label)
        if behavior_key is None:
            continue
        if row[4] < get_teacher_behavior_label_threshold(label, threshold_overrides):
            continue
        if behavior_key not in best_by_behavior or row[4] > best_by_behavior[behavior_key][4]:
            best_by_behavior[behavior_key] = row
    return best_by_behavior


def resolve_teacher_posture_outputs(accepted_by_behavior, subject_confidence: float):
    sitting_row = accepted_by_behavior.get("sitting")
    standing_row = accepted_by_behavior.get("standing")

    if sitting_row is not None and standing_row is not None:
        sitting_confidence = sitting_row[4]
        standing_confidence = standing_row[4]
        max_confidence = max(sitting_confidence, standing_confidence)
        diff_ratio = abs(sitting_confidence - standing_confidence) / max_confidence if max_confidence > 0 else 0.0
        if diff_ratio <= get_teacher_behavior_posture_conflict_ratio():
            default_label = get_teacher_behavior_posture_conflict_default()
            behavior_key = TEACHER_BEHAVIOR_LABEL_TO_KEY[default_label]
            default_row = standing_row if behavior_key == "standing" else sitting_row
            return {
                behavior_key: {
                    "row": default_row,
                    "confidence": default_row[4],
                    "suspected_sitting": behavior_key == "standing",
                    "posture_fallback": False,
                }
            }

        if standing_confidence >= sitting_confidence:
            return {
                "standing": {
                    "row": standing_row,
                    "confidence": standing_confidence,
                    "suspected_sitting": False,
                    "posture_fallback": False,
                }
            }
        return {
            "sitting": {
                "row": sitting_row,
                "confidence": sitting_confidence,
                "suspected_sitting": False,
                "posture_fallback": False,
            }
        }

    if standing_row is not None:
        return {
            "standing": {
                "row": standing_row,
                "confidence": standing_row[4],
                "suspected_sitting": False,
                "posture_fallback": False,
            }
        }

    if sitting_row is not None:
        return {
            "sitting": {
                "row": sitting_row,
                "confidence": sitting_row[4],
                "suspected_sitting": False,
                "posture_fallback": False,
            }
        }

    if not get_teacher_behavior_force_posture_when_missing():
        return {}

    default_label = get_teacher_behavior_posture_conflict_default()
    behavior_key = TEACHER_BEHAVIOR_LABEL_TO_KEY[default_label]
    return {
        behavior_key: {
            "row": None,
            "confidence": subject_confidence,
            "suspected_sitting": False,
            "posture_fallback": True,
        }
    }


def collect_teacher_behavior_group_details(
        detections,
        names,
        offset: Tuple[int, int],
        merge_iou: float,
        threshold_overrides: Optional[Any] = None):
    details = []
    groups = collect_teacher_behavior_groups(detections, names, merge_iou, threshold_overrides)
    for group in groups:
        representative_box = max(group, key=bbox_area)
        subject_confidence = group_max_confidence(group)
        accepted_by_behavior = best_rows_by_behavior(group, names, threshold_overrides)

        confidences = {"platform_person": subject_confidence}
        positions = {
            "platform_person": to_object_position(
                representative_box,
                offset,
                confidence=subject_confidence
            )
        }
        best_by_behavior = {}

        for behavior_key in ("writing", "teaching"):
            row = accepted_by_behavior.get(behavior_key)
            if row is None:
                continue
            confidence = row[4]
            confidences[behavior_key] = confidence
            positions[behavior_key] = to_object_position(
                representative_box,
                offset,
                confidence=confidence
            )
            best_by_behavior[behavior_key] = row

        posture_outputs = resolve_teacher_posture_outputs(
            accepted_by_behavior,
            subject_confidence
        )
        for behavior_key, output in posture_outputs.items():
            confidence = output["confidence"]
            confidences[behavior_key] = confidence
            positions[behavior_key] = to_object_position(
                representative_box,
                offset,
                confidence=confidence,
                suspected_sitting=output["suspected_sitting"],
                posture_fallback=output["posture_fallback"]
            )
            if output["row"] is not None:
                best_by_behavior[behavior_key] = output["row"]

        details.append({
            "position": positions["platform_person"],
            "positions": positions,
            "confidences": confidences,
            "best_by_behavior": best_by_behavior,
        })

    return details


def collect_teacher_behavior_results(
        detections,
        names,
        offset: Tuple[int, int],
        merge_iou: float,
        threshold_overrides: Optional[Any] = None):
    behavior_results = {
        'platform_person': [],
        'standing': [],
        'sitting': [],
        'writing': [],
        'teaching': []
    }

    details = collect_teacher_behavior_group_details(
        detections,
        names,
        offset,
        merge_iou,
        threshold_overrides
    )
    for detail in details:
        positions = detail["positions"]
        for behavior_key in behavior_results:
            position = positions.get(behavior_key)
            if position is not None:
                behavior_results[behavior_key].append(position)

    return behavior_results


def empty_teacher_behavior_results() -> Dict[str, List[ObjectPosition]]:
    return {
        'platform_person': [],
        'standing': [],
        'sitting': [],
        'writing': [],
        'teaching': []
    }


def process_teacher_behavior_model_detection_with_details(
        img: np.ndarray,
        offset: Tuple[int, int],
        img_size: Tuple[int, int],
        threshold_overrides: Optional[Any] = None):
    """
    使用 teacher_behavior.pt 检测老师行为。
    同一主体的高 IoU 多类别框会聚合为一个主体；姿态类和授课行为类按类别保留。
    """
    logger.info("========== 开始新老师行为模型检测 ==========")
    height, width = img_size
    results = yolo_teacher_behavior_model.predict(
        img,
        imgsz=get_teacher_behavior_image_size(),
        conf=get_teacher_behavior_predict_conf(threshold_overrides),
        half=use_half,
        verbose=verbose
    )
    if not results or len(results) == 0:
        return empty_teacher_behavior_results(), []

    result = results[0]
    detections = result.boxes.data.tolist() if result.boxes is not None else []
    names = getattr(result, "names", None) or getattr(yolo_teacher_behavior_model, "names", {})
    details = collect_teacher_behavior_group_details(
        detections,
        names,
        offset,
        get_teacher_behavior_merge_iou(),
        threshold_overrides
    )
    behavior_results = empty_teacher_behavior_results()
    for detail in details:
        positions = detail["positions"]
        for behavior_key in behavior_results:
            position = positions.get(behavior_key)
            if position is not None:
                behavior_results[behavior_key].append(position)
    logger.info(
        f"[新老师行为模型] 主体:{len(behavior_results['platform_person'])} "
        f"站立:{len(behavior_results['standing'])} 坐着:{len(behavior_results['sitting'])} "
        f"板书:{len(behavior_results['writing'])} 讲授:{len(behavior_results['teaching'])}")
    return behavior_results, details


def process_teacher_behavior_model_detection(
        img: np.ndarray,
        offset: Tuple[int, int],
        img_size: Tuple[int, int],
        threshold_overrides: Optional[Any] = None) -> Dict[str, List[ObjectPosition]]:
    behavior_results, _ = process_teacher_behavior_model_detection_with_details(
        img,
        offset,
        img_size,
        threshold_overrides,
    )
    return behavior_results


def load_behavior_image(image_item):
    if image_item.StoragePath.startswith('data:') or len(image_item.StoragePath) > 1000:
        if image_item.StoragePath.startswith('data:'):
            base64_data = image_item.StoragePath.split(',')[1]
        else:
            base64_data = image_item.StoragePath
        img_data = base64.b64decode(base64_data)
        img_array = np.frombuffer(img_data, np.uint8)
        return cv2.imdecode(img_array, cv2.IMREAD_COLOR)

    if image_item.StoragePath.startswith('http://') or image_item.StoragePath.startswith('https://'):
        import requests
        resp = requests.get(image_item.StoragePath, timeout=5)
        img_array = np.frombuffer(resp.content, np.uint8)
        return cv2.imdecode(img_array, cv2.IMREAD_COLOR)

    if os.path.isabs(image_item.StoragePath):
        img_path = image_item.StoragePath
    else:
        img_path = os.path.join(settings.IMAGE_ROOT, image_item.StoragePath.lstrip('/'))
    return cv2.imread(img_path)


def build_teacher_result_list(behavior_results: Dict[str, List[ObjectPosition]], object_types: Dict[str, int]):
    result_list = []
    behavior_keys = ['platform_person', 'sitting', 'standing', 'writing', 'teaching']

    for behavior_key in sorted(behavior_keys, key=lambda key: object_types[key]):
        positions = behavior_results[behavior_key]
        result_list.append(ResultItem(
            ObjectType=object_types[behavior_key],
            ObjectCount=len(positions),
            ObjectPostList=positions if positions else None
        ))
    return result_list


async def analyze_teacher_behavior_by_model(request: TeacherBehaviorRequest) -> TeacherBehaviorResponse:
    """
    老师行为分析主函数：使用 teacher_behavior.pt 输出站/坐/板书/讲授。
    当 Teacher_Head_Pose.Enabled=true 且 ReturnHeadPose=true 时，追加头部方向检测。
    """
    start_time = time.time()
    timestamp = int(time.time())
    logger.info(f"========== 开始老师行为模型分析 ========== 图片数量: {len(request.ImageList)}")
    from .capacity_service import increment_connection, increment_processed_images
    increment_connection()
    increment_processed_images(len(request.ImageList))

    processed_image_ids = []
    data_list = []
    total_stats = {
        'platform_person': 0,
        'standing': 0,
        'sitting': 0,
        'writing': 0,
        'teaching': 0
    }

    for image_item in request.ImageList:
        image_start_time = time.time()
        try:
            logger.debug(f"[老师行为模型] 开始处理图片 {image_item.ImageId}")
            img = load_behavior_image(image_item)
            if img is None:
                logger.error(f"[老师行为模型] 图片解码失败: {image_item.ImageId}")
                raise ValueError(f"无法读取图片: {image_item.ImageId}")

            original_height, original_width = img.shape[:2]
            img_size = (original_height, original_width)
            if image_item.Points:
                processed_img, offset = mask_polygon(img, image_item.Points)
            else:
                processed_img, offset = img, (0, 0)

            behavior_results, behavior_details = process_teacher_behavior_model_detection_with_details(
                processed_img,
                offset,
                img_size,
                request.Teacher_Behavior_Thresd
            )
            result_list = build_teacher_result_list(behavior_results, TEACHER_BEHAVIOR_OBJECT_TYPES)
            for stat_key in total_stats:
                total_stats[stat_key] += len(behavior_results[stat_key])

            head_pose_result = None
            return_head_pose = getattr(request, "ReturnHeadPose", False)
            head_pose_enabled = get_teacher_head_pose_enabled()
            if return_head_pose and not head_pose_enabled:
                logger.info(
                    "[老师行为模型] ReturnHeadPose=true，但 Teacher_Head_Pose.Enabled=false，跳过头部姿态检测"
                )
            if return_head_pose and head_pose_enabled:
                if behavior_details:
                    try:
                        head_pose_result = analyze_teacher_head_pose(
                            img,
                            behavior_details[0]["position"],
                        )
                    except Exception as head_pose_error:
                        logger.error(
                            "[老师行为模型] 头部方向检测失败 image_id=%s error_type=%s",
                            image_item.ImageId,
                            type(head_pose_error).__name__,
                            exc_info=True,
                        )
                        head_pose_result = failed_head_pose_result(str(head_pose_error))
                else:
                    head_pose_result = no_teacher_head_pose_result()

            image_use_time_ms = int((time.time() - image_start_time) * 1000)
            data_list.append(TeacherBehaviorImageResult(
                StatusObject={
                    "StatusString": "success",
                    "ImageId": image_item.ImageId,
                    "TimeStamp": timestamp,
                    "UseTimeMs": image_use_time_ms,
                    "StatusCode": 0
                },
                ResultList=result_list,
                HeadPoseResult=head_pose_result
            ))
            processed_image_ids.append(image_item.ImageId)
            logger.info(f"Successfully processed teacher behavior image {image_item.ImageId}")
        except Exception as e:
            logger.error(
                "[老师行为模型] 处理图片失败 image_id=%s error_type=%s",
                image_item.ImageId,
                type(e).__name__,
                exc_info=True,
            )
            data_list.append(TeacherBehaviorImageResult(
                StatusObject={"StatusString": "failed", "StatusCode": 500},
                ResultList=[]
            ))
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=str(e))

    use_time_ms = int((time.time() - start_time) * 1000)
    response = TeacherBehaviorResponse(
        StatusObject={
            "StatusString": "success" if processed_image_ids else "failed",
            "ImageIdList": processed_image_ids,
            "TimeStamp": timestamp,
            "UseTimeMs": use_time_ms,
            "StatusCode": 0 if processed_image_ids else 500
        },
        DataList=data_list
    )
    logger.info(
        f"[老师行为模型最终统计] 主体:{total_stats['platform_person']} "
        f"站立:{total_stats['standing']} 坐着:{total_stats['sitting']} "
        f"板书:{total_stats['writing']} 讲授:{total_stats['teaching']} | "
        f"耗时:{use_time_ms}ms 图片:{len(processed_image_ids)}张 "
        f"ReturnHeadPose:{getattr(request, 'ReturnHeadPose', False)} "
        f"HeadPoseEnabled:{get_teacher_head_pose_enabled()}")
    return response
