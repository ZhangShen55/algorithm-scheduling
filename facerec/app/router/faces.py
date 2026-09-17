import uuid
import numpy as np
from pathlib import Path
from fastapi import APIRouter, Body, HTTPException
from app.services import person
from app.utils.image_loader import base64_to_mat
from app.core import ai_engine
from app.core.config import settings
from app.core.database import db
from app.models.request.face_interface_req import PersonRecognizeRequest, BatchRecognizeRequest
from app.models.response.face_interface_rep import RecognizeResp, BBox, MatchItem, BatchRecognizeResp, FrameInfo
from app.models.api_response import StatusCode, ApiResponse
from app.core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["Face Recognition"])

BASE_DIR = Path(__file__).resolve().parent.parent
THRESHOLD = settings.face.threshold
CANDIDATE_THRESHOLD = settings.face.candidate_threshold
REC_MIN_FACE_HW = int(settings.face.rec_min_face_hw)


def _merge_matches(
    destination: dict[str, tuple[float, dict, bool]],
    matches: list[tuple[float, dict]],
    *,
    is_target: bool,
) -> None:
    for similarity, document in matches:
        number = document.get("number")
        if not number:
            continue
        previous = destination.get(number)
        if previous is None:
            destination[number] = (similarity, document, is_target)
            continue
        best_similarity, best_document, was_target = previous
        if similarity > best_similarity:
            best_similarity = similarity
            best_document = document
        destination[number] = (
            best_similarity,
            best_document,
            was_target or is_target,
        )

@router.post("/recognize", response_model=ApiResponse)
async def recognize_face_api(request: PersonRecognizeRequest = Body(..., description="人脸识别请求")):
    import time
    t_total_start = time.time()

    # 请求对象包含图片 Base64；日志只保留可排障的数量和阈值，不展开请求体。
    logger.debug(
        "[recognize] 收到图片识别请求 targets=%s threshold_override=%s",
        len(request.targets or []),
        request.threshold is not None,
    )

    # 1. 确定阈值
    threshold = request.threshold if request.threshold else THRESHOLD
    targets = request.targets if request.targets else []

    # 2. 解析图片数据
    t_step = time.time()
    try:
        image_data, filename = await base64_to_mat(request.photo)
    except HTTPException as e:
        # base64 解码失败（HTTPException 由 base64_to_mat 抛出）
        logger.error(f"[recognize] 图片解析失败: {e.detail}")
        # 判断是 base64 解码错误还是图片格式错误
        if "base64" in str(e.detail).lower() or "decode" in str(e.detail).lower():
            return ApiResponse.error(
                status_code=StatusCode.BASE64_DECODE_ERROR,
                message=str(e.detail)
            )
        else:
            return ApiResponse.error(
                status_code=StatusCode.INVALID_IMAGE_FORMAT,
                message=str(e.detail)
            )
    logger.info(f"[性能] 解析图片耗时: {(time.time()-t_step)*1000:.2f}ms")

    if image_data is None or not isinstance(image_data, np.ndarray) or image_data.size == 0:
        logger.error(f"[recognize] 未接收到有效图片数据或图像数据存在异常")
        return ApiResponse.error(
            status_code=StatusCode.INVALID_IMAGE_DATA,
            message="未接收到有效图片数据或图像数据存在异常"
        )

    # 单图识别处理整图内所有有效人脸；公共请求模型不引入上游 points/ROI。
    t_step = time.time()
    try:
        detected_faces = await ai_engine.detect_and_extract_all_faces(image_data)
    except Exception as e:
        logger.error("[recognize] 人脸检测服务内部错误: %s", e)
        return ApiResponse.error(
            status_code=StatusCode.FACE_DETECTION_ERROR,
            message=f"人脸检测服务内部错误: {e}",
        )
    logger.info(
        "[性能] 多人脸检测耗时 %.2fms faces=%s",
        (time.time() - t_step) * 1000,
        len(detected_faces),
    )

    if not detected_faces:
        logger.info("[recognize] 未检测到有效人脸")
        return ApiResponse.error(
            status_code=StatusCode.NO_FACE_DETECTED,
            message="图像中未检测到人脸，请重新捕捉人脸",
        )

    primary_face = max(
        detected_faces,
        key=lambda item: item[1]["w"] * item[1]["h"],
    )
    primary_bbox = primary_face[1]
    valid_faces = [
        item
        for item in detected_faces
        if item[1]["w"] >= REC_MIN_FACE_HW and item[1]["h"] >= REC_MIN_FACE_HW
    ]
    if not valid_faces:
        message = f"人脸像素过小，宽高必须至少为 {REC_MIN_FACE_HW}px"
        return ApiResponse.error(
            status_code=StatusCode.FACE_TOO_SMALL,
            message=message,
            data={
                "has_face": True,
                "bbox": BBox(**primary_bbox).model_dump(),
                "threshold": threshold,
                "match": None,
                "message": message,
            },
        )

    all_docs = await person.get_embeddings_for_match(db)
    if not all_docs:
        message = "数据库为空，请先录入人员信息"
        return ApiResponse.error(
            status_code=StatusCode.DB_EMPTY,
            message=message,
            data={
                "has_face": True,
                "bbox": BBox(**primary_bbox).model_dump(),
                "threshold": threshold,
                "match": None,
                "message": message,
            },
        )

    target_docs = await person.get_targets_embeddings(db, targets) if targets else []
    match_dict: dict[str, tuple[float, dict, bool]] = {}
    global_count = 0
    target_count = 0
    try:
        for face_image, _, _ in valid_faces:
            emb_q = await ai_engine.get_embedding(face_image)
            global_results = ai_engine.find_top_matches(
                emb_q,
                all_docs,
                top_k=3,
                min_threshold=threshold,
            )
            target_results = (
                ai_engine.find_top_matches(
                    emb_q,
                    target_docs,
                    top_k=len(target_docs),
                    min_threshold=CANDIDATE_THRESHOLD,
                )
                if target_docs
                else []
            )
            global_count += len(global_results)
            target_count += len(target_results)
            _merge_matches(match_dict, global_results, is_target=False)
            _merge_matches(match_dict, target_results, is_target=True)
    except Exception as e:
        logger.error("[recognize] 人脸特征提取失败: %s", e)
        return ApiResponse.error(
            status_code=StatusCode.FEATURE_EXTRACT_ERROR,
            message=f"人脸特征提取失败: {e}",
        )

    final_matches = sorted(match_dict.values(), key=lambda item: item[0], reverse=True)
    if not final_matches:
        message = "未找到匹配的人物（相似度低于阈值）"
        return ApiResponse.error(
            status_code=StatusCode.NO_MATCH_FOUND,
            message=message,
            data={
                "has_face": True,
                "bbox": BBox(**primary_bbox).model_dump(),
                "threshold": threshold,
                "match": None,
                "message": message,
            },
        )

    match_items = [
        MatchItem(
            id=str(doc["_id"]),
            name=doc.get("name"),
            number=doc.get("number"),
            similarity=f"{similarity * 100:.2f}%",
            is_target=is_target,
        )
        for similarity, doc, is_target in final_matches
    ]
    best_match = final_matches[0]
    best_name = best_match[1].get("name")
    best_number = best_match[1].get("number")
    if targets:
        message = (
            f"匹配成功，处理{len(valid_faces)}张人脸，≥阈值{threshold*100:.2f}%"
            f"有{global_count}项，targets命中{target_count}项，最相似的是"
            f"{best_name}_{best_number}"
        )
    else:
        message = (
            f"匹配成功，处理{len(valid_faces)}张人脸，找到{len(final_matches)}位，"
            f"最相似的是{best_name}_{best_number}"
        )
    logger.info(
        "[recognize] 识别完成 faces=%s matches=%s target_matches=%s duration_ms=%.2f",
        len(valid_faces),
        len(final_matches),
        target_count,
        (time.time() - t_total_start) * 1000,
    )
    return ApiResponse.success(
        data={
            "has_face": True,
            "bbox": BBox(**primary_bbox).model_dump(),
            "threshold": threshold,
            "match": [item.model_dump() for item in match_items],
            "message": message,
        },
        message="识别成功",
    )


@router.post("/recognize/batch", response_model=ApiResponse)
async def recognize_batch_api(request: BatchRecognizeRequest = Body(..., description="批量人脸识别请求（多帧独立识别）")):
    """
    批量识别接口（多帧独立识别，取最优结果）

    策略：每张图片独立识别，汇总所有结果取最高相似度
    适用场景：视频流抓拍、同一人的多角度照片等
    """
    logger.debug(f"[recognize/batch] 接收到请求，帧数: {len(request.photos)}")

    threshold = request.threshold if request.threshold else THRESHOLD
    targets = request.targets if request.targets else []

    if not request.photos:
        return ApiResponse.error(
            status_code=StatusCode.BAD_REQUEST,
            message="photos 列表不能为空"
        )

    # 预加载数据库数据（避免每帧都查询）
    all_docs = await person.get_embeddings_for_match(db)
    if not all_docs:
        logger.warning("[recognize/batch] 数据库中没有有效人脸特征")
        return ApiResponse.error(
            status_code=StatusCode.DB_EMPTY,
            message="数据库为空，请先录入人员信息",
            data={
                "total_frames": len(request.photos),
                "valid_frames": 0,
                "threshold": threshold,
                "frames": [],
                "match": None,
                "message": "数据库中暂无人脸数据，请先录入"
            }
        )

    target_docs = []
    if targets:
        target_docs = await person.get_targets_embeddings(db, targets)
        logger.info(f"[recognize/batch] targets 查询到 {len(target_docs)} 个候选人")

    # 第一步：逐帧识别，每帧都执行完整的识别流程
    frames_results = []  # 每帧的识别结果
    valid_frame_count = 0

    for idx, photo_base64 in enumerate(request.photos):
        frame_result = {
            'index': idx,
            'has_face': False,
            'bbox': None,
            'error': None,
            'matches': []  # 该帧的 top3 匹配结果
        }

        try:
            # 解析图片
            image_data, _ = await base64_to_mat(photo_base64)

            if image_data is None or not isinstance(image_data, np.ndarray) or image_data.size == 0:
                frame_result['error'] = "无效的图片数据"
                frames_results.append(frame_result)
                logger.warning(f"[recognize/batch] 第{idx}帧: 无效的图片数据")
                continue

            # 检测人脸
            face_image, bbox, _ = await ai_engine.detect_and_extract_face(image_data)

            if face_image is None:
                frame_result['error'] = "未检测到人脸"
                frames_results.append(frame_result)
                logger.info(f"[recognize/batch] 第{idx}帧: 未检测到人脸")
                continue

            frame_result['has_face'] = True
            frame_result['bbox'] = BBox(**bbox) if bbox else None

            # 验证人脸尺寸
            if bbox and (bbox["w"] < REC_MIN_FACE_HW or bbox["h"] < REC_MIN_FACE_HW):
                frame_result['error'] = f"人脸过小({bbox['w']}x{bbox['h']}px)"
                frames_results.append(frame_result)
                logger.info(f"[recognize/batch] 第{idx}帧: 人脸过小")
                continue

            # 提取特征
            emb_q = await ai_engine.get_embedding(face_image)
            valid_frame_count += 1

            # 全局比对：找 similarity >= threshold 的前 3 人
            result_A = ai_engine.find_top_matches(emb_q, all_docs, top_k=3, min_threshold=threshold)

            # targets 比对
            result_B = []
            if target_docs:
                result_B = ai_engine.find_top_matches(
                    emb_q,
                    target_docs,
                    top_k=len(target_docs),
                    min_threshold=CANDIDATE_THRESHOLD,
                )

            # 合并去重（该帧的结果）
            frame_match_dict = {}

            for sim, doc in result_A:
                number = doc.get("number")
                if number:
                    frame_match_dict[number] = (sim, doc, False)

            for sim, doc in result_B:
                number = doc.get("number")
                if number:
                    frame_match_dict[number] = (sim, doc, True)

            # 该帧的 top3（按相似度降序）
            frame_matches = sorted(frame_match_dict.values(), key=lambda x: x[0], reverse=True)
            frame_result['matches'] = [
                {
                    'id': str(m[1].get('_id')),
                    'number': m[1].get('number'),
                    'name': m[1].get('name'),
                    'similarity': m[0],
                    'is_target': m[2]
                }
                for m in frame_matches
            ]

            frames_results.append(frame_result)
            logger.debug(f"[recognize/batch] 第{idx}帧: 识别到 {len(frame_matches)} 个匹配")

        except Exception as e:
            logger.error(f"[recognize/batch] 第{idx}帧处理失败: {e}")
            frame_result['error'] = f"处理失败: {str(e)}"
            frames_results.append(frame_result)

    # 第二步：检查是否有有效帧
    if valid_frame_count == 0:
        logger.warning("[recognize/batch] 所有帧均未检测到有效人脸")
        frames_info = [FrameInfo(
            index=f['index'],
            has_face=f['has_face'],
            bbox=f['bbox'],
            error=f['error']
        ) for f in frames_results]

        return ApiResponse.error(
            status_code=StatusCode.NO_FACE_DETECTED,
            message="所有帧均未检测到有效人脸",
            data={
                "total_frames": len(request.photos),
                "valid_frames": 0,
                "threshold": threshold,
                "frames": [f.model_dump() for f in frames_info],
                "match": None,
                "message": "所有帧均未检测到有效人脸"
            }
        )

    # 第三步：汇总所有帧的识别结果
    # key: number, value: (max_similarity, doc, is_target_any, appearance_count)
    aggregated_results = {}

    for frame in frames_results:
        for match in frame.get('matches', []):
            number = match['number']
            sim = match['similarity']
            is_target = match['is_target']

            if number in aggregated_results:
                # 已存在：更新最高相似度、is_target（任一为true则为true）、出现次数
                max_sim, doc_info, is_target_any, count = aggregated_results[number]
                # 如果当前相似度更高，更新文档信息（保留更好匹配的完整信息）
                if sim > max_sim:
                    doc_info = {'id': match['id'], 'number': number, 'name': match['name']}
                aggregated_results[number] = (
                    max(max_sim, sim),  # 取最高相似度
                    doc_info,  # 保留文档信息
                    is_target_any or is_target,  # is_target 优先保留 true
                    count + 1  # 出现次数+1
                )
            else:
                # 首次出现
                aggregated_results[number] = (
                    sim,
                    {'id': match['id'], 'number': number, 'name': match['name']},
                    is_target,
                    1  # 出现次数
                )

    # 第四步：按相似度降序排序，取 top3
    final_matches = sorted(
        aggregated_results.items(),
        key=lambda x: x[1][0],  # 按最高相似度排序
        reverse=True
    )[:3]  # 只取前3

    # 第五步：计算置信度
    confidence = valid_frame_count / len(request.photos)

    # 第六步：构建响应
    frames_info = [FrameInfo(
        index=f['index'],
        has_face=f['has_face'],
        bbox=f['bbox'],
        error=f['error']
    ) for f in frames_results]

    if not final_matches:
        logger.info("[recognize/batch] 未找到任何匹配")
        return ApiResponse.error(
            status_code=StatusCode.NO_MATCH_FOUND,
            message=f"识别失败，使用{valid_frame_count}帧有效图片，但相似度均低于阈值",
            data={
                "total_frames": len(request.photos),
                "valid_frames": valid_frame_count,
                "threshold": threshold,
                "frames": [f.model_dump() for f in frames_info],
                "match": None,
                "message": f"识别失败，使用{valid_frame_count}帧有效图片，但相似度均低于阈值"
            }
        )

    # 构建 match 列表
    match_items = []
    for number, (max_sim, doc_info, is_target, count) in final_matches:
        match_items.append(MatchItem(
            id=doc_info.get('id', ''),  # 使用聚合结果中的 id
            name=doc_info.get('name'),
            number=number,
            similarity=f"{max_sim * 100:.2f}%",
            is_target=is_target
        ))

    # 构建消息
    best_name = final_matches[0][1][1].get('name')
    best_number = final_matches[0][0]
    best_count = final_matches[0][1][3]
    best_similarity = final_matches[0][1][0]  # 最高相似度

    if targets:
        target_count = sum(1 for _, (_, _, is_t, _) in final_matches if is_t)
        message = f"识别成功，使用{valid_frame_count}帧有效图片，找到{len(final_matches)}位候选人，targets命中{target_count}位，最相似的是{best_name}_{best_number}（出现{best_count}次）"
    else:
        message = f"识别成功，使用{valid_frame_count}帧有效图片，找到{len(final_matches)}位候选人，最相似的是{best_name}_{best_number}（出现{best_count}次）"

    logger.info(
        "[recognize/batch] 识别完成 frames=%s valid_frames=%s matches=%s "
        "target_matches=%s best_similarity=%.2f%%",
        len(request.photos),
        valid_frame_count,
        len(final_matches),
        target_count if targets else 0,
        best_similarity * 100,
    )

    return ApiResponse.success(
        data={
            "total_frames": len(request.photos),
            "valid_frames": valid_frame_count,
            "threshold": threshold,
            "frames": [f.model_dump() for f in frames_info],
            "match": [m.model_dump() for m in match_items],
            "message": message
        },
        message="批量识别成功"
    )
    ##
