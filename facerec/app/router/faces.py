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

@router.post("/recognize", response_model=ApiResponse)
async def recognize_face_api(request: PersonRecognizeRequest = Body(..., description="人脸识别请求")):
    import time
    t_total_start = time.time()

    logger.debug(f"[recognize] 接收到请求参数：{request}")

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

    # 3. 检测人脸
    t_step = time.time()
    try:
        face_image, bbox, _ = await ai_engine.detect_and_extract_face(image_data)
    except Exception as e:
        logger.error(f"[recognize] 人脸检测服务内部错误: {e}")
        return ApiResponse.error(
            status_code=StatusCode.FACE_DETECTION_ERROR,
            message=f"人脸检测服务内部错误: {str(e)}"
        )
    logger.info(f"[性能] 人脸检测总耗时(含进程通信): {(time.time()-t_step)*1000:.2f}ms")

    if face_image is None:
        logger.info(f"[recognize] 未检测到有效人脸")
        logger.info(f"[性能] 总请求耗时: {(time.time()-t_total_start)*1000:.2f}ms")
        return ApiResponse.error(
            status_code=StatusCode.NO_FACE_DETECTED,
            message="图像中未检测到人脸，请重新捕捉人脸"
        )

    # 4. 验证人脸尺寸
    if face_image.shape[0] < REC_MIN_FACE_HW or face_image.shape[1] < REC_MIN_FACE_HW:
        logger.info(f"[recognize] 检测到的人脸过小: {face_image.shape[0]}x{face_image.shape[1]}px，小于{REC_MIN_FACE_HW}*{REC_MIN_FACE_HW}px")
        return ApiResponse.error(
            status_code=StatusCode.FACE_TOO_SMALL,
            message=f"人脸像素过小({face_image.shape[0]}x{face_image.shape[1]}px)，无法识别",
            data={
                "has_face": True,
                "bbox": BBox(**bbox).model_dump() if bbox else None,
                "threshold": threshold,
                "match": None,
                "message": f"人脸像素过小({face_image.shape[0]}x{face_image.shape[1]}px)，无法识别"
            }
        )

    # 5. 预加载数据库数据（只有检测到人脸后才查询数据库，避免浪费）
    # logger.info("[recognize] 预加载数据库数据...")
    all_docs = await person.get_embeddings_for_match(db)

    if not all_docs:
        logger.info("[recognize] 数据库中没有有效人脸特征")
        return ApiResponse.error(
            status_code=StatusCode.DB_EMPTY,
            message="数据库为空，请先录入人员信息",
            data={
                "has_face": True,
                "bbox": BBox(**bbox).model_dump() if bbox else None,
                "threshold": threshold,
                "match": None,
                "message": "数据库为空，请先录入人员信息"
            }
        )

    target_docs = []
    if targets:
        target_docs = await person.get_targets_embeddings(db, targets)
        logger.info(f"[recognize] targets 查询到 {len(target_docs)} 个候选人")

    # 6. 提取特征
    try:
        emb_q = await ai_engine.get_embedding(face_image)
    except Exception as e:
        logger.error(f"[recognize] 人脸特征提取失败: {e}")
        return ApiResponse.error(
            status_code=StatusCode.FEATURE_EXTRACT_ERROR,
            message=f"人脸特征提取失败: {str(e)}"
        )

    # 7. 全局比对：找 similarity >= threshold 的前 3 人
    logger.info("[recognize] 开始全局比对...")

    # 使用新函数找 top3 且 >= threshold 的人
    result_A = ai_engine.find_top_matches(emb_q, all_docs, top_k=3, min_threshold=threshold)
    logger.info(f"[recognize] 全局比对找到 {len(result_A)} 个 >= 阈值 {threshold} 的匹配")

    # 8. 如果有 targets，进行 targets 比对
    result_B = []
    if target_docs:
        # targets 使用宽松阈值 threshold/2
        target_threshold = threshold / 2
        result_B = ai_engine.find_top_matches(
            emb_q, target_docs, top_k=len(target_docs), min_threshold=target_threshold
        )
        logger.info(f"[recognize] targets 比对找到 {len(result_B)} 个 >= 阈值 {target_threshold} 的匹配")

    # 9. 合并去重：按 number 去重，优先保留 is_target=True 的
    match_dict = {}  # key: number, value: (similarity, doc, is_target)

    # 先添加 result_A（is_target=False）
    for sim, doc in result_A:
        number = doc.get("number")
        if number:
            match_dict[number] = (sim, doc, False)

    # 再添加 result_B（is_target=True），会覆盖 result_A 中相同 number 的项
    for sim, doc in result_B:
        number = doc.get("number")
        if number:
            match_dict[number] = (sim, doc, True)

    # 10. 按相似度降序排序
    final_matches = sorted(match_dict.values(), key=lambda x: x[0], reverse=True)

    # 11. 构建响应
    if not final_matches:
        logger.info("[recognize] 未找到任何匹配")
        return ApiResponse.error(
            status_code=StatusCode.NO_MATCH_FOUND,
            message="未找到匹配的人物（相似度低于阈值）",
            data={
                "has_face": True,
                "bbox": BBox(**bbox).model_dump() if bbox else None,
                "threshold": threshold,
                "match": None,
                "message": "未找到匹配的人物（相似度低于阈值）"
            }
        )

    # 12. 构建 match 列表
    match_items = []
    global_count = len(result_A)
    target_count = len(result_B)

    for sim, doc, is_target in final_matches:
        match_items.append(MatchItem(
            id=str(doc["_id"]),
            name=doc.get("name"),
            number=doc.get("number"),
            similarity=f"{sim * 100:.2f}%",
            is_target=is_target
        ))

    # 13. 构建消息
    best_match = final_matches[0]
    best_name = best_match[1].get("name")
    best_number = best_match[1].get("number")

    if targets:
        message = f"匹配成功，≥阈值{threshold*100:.2f}%有{global_count}位，targets命中{target_count}位，最相似的是{best_name}_{best_number}"
    else:
        message = f"匹配成功，≥阈值{threshold*100:.2f}%有{len(final_matches)}位，最相似的是{best_name}_{best_number}"

    logger.info(f"[recognize] {message}")

    return ApiResponse.success(
        data={
            "has_face": True,
            "bbox": BBox(**bbox).model_dump() if bbox else None,
            "threshold": threshold,
            "match": [m.model_dump() for m in match_items],
            "message": message
        },
        message="识别成功"
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
            if face_image.shape[0] < REC_MIN_FACE_HW or face_image.shape[1] < REC_MIN_FACE_HW:
                frame_result['error'] = f"人脸过小({face_image.shape[0]}x{face_image.shape[1]}px)"
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
                target_threshold = threshold / 2
                result_B = ai_engine.find_top_matches(
                    emb_q, target_docs, top_k=len(target_docs), min_threshold=target_threshold
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

    logger.info(f"[recognize/batch] {message}，最高相似度: {best_similarity * 100:.2f}%")

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

