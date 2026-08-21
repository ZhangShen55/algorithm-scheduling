import numpy as np
from typing import List
from pathlib import Path
from bson.binary import Binary
from fastapi import APIRouter, Depends, HTTPException, Query, Body, Request
from fastapi.exceptions import RequestValidationError
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import ValidationError

from app.core.database import db
from app.core.database import get_session
from app.core.exceptions import DatabaseError
from app.core.config import settings
from app.services import person as person_crud
from app.services.person_photo import persist_person_photo
from app.utils.image_loader import base64_to_mat
from app.utils.utils_mongo import doc_to_person_read
from app.core.logger import get_logger
from app.core import ai_engine
from app.models.request.person_interface_req import *
from app.models.response.person_interface_rep import PersonFeatureResponse, PersonRead, PersonsFeatureResponse, SearchPersonResponse
from app.models.api_response import StatusCode, ApiResponse


logger = get_logger(__name__)

router = APIRouter(prefix="/persons", tags=["Persons Management"])
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIN_FEATURE_IMAGE_HEIGHT_PX = int(settings.feature_image.min_feature_image_height_px)
MIN_FEATURE_IMAGE_WIDTH_PX = int(settings.feature_image.min_feature_image_width_px)

@router.post("", response_model=ApiResponse)
async def create_person_api(
        request: PersonFeatureRequest = Body(..., description="人脸特征录入请求体")
):
    """
    单个上传人物特征接口
    - 如果 number 已存在，则更新该人物信息
    - 如果 number 不存在，则创建新人物
    - HTTP 状态码永远是 200，通过 status_code 字段判断结果
    """
    try:
        # 1. 解析图片数据
        try:
            image_data, filename = await base64_to_mat(request.photo)
        except HTTPException as e:
            # base64 解码失败等客户端错误
            logger.error(f"[/persons] 图片解析失败: {e.detail}")
            return ApiResponse.error(
                status_code=StatusCode.BAD_REQUEST,
                message=str(e.detail)
            )

        if image_data is None or not isinstance(image_data, np.ndarray) or image_data.size == 0:
            logger.error(f"[/persons] 未接收到有效图片数据或图像数据存在异常")
            return ApiResponse.error(
                status_code=StatusCode.BAD_REQUEST,
                message="未接收到有效图片数据或图像数据存在异常"
            )

        # 2. 检测并裁剪人脸
        try:
            face_image, bbox , tip = await ai_engine.detect_and_extract_face(image_data)
        except Exception as e:
            logger.error(f"[/persons] 人脸检测服务内部错误: {e}")
            return ApiResponse.error(
                status_code=StatusCode.FACE_DETECTION_ERROR,
                message=f"人脸检测服务内部错误: {str(e)}"
            )

        if face_image is None:
            logger.error(f"[/persons] 未检测到有效人脸")
            return ApiResponse.error(
                status_code=StatusCode.UNPROCESSABLE_ENTITY,
                message="未检测到有效人脸"
            )

        if face_image.shape[0] < MIN_FEATURE_IMAGE_WIDTH_PX or face_image.shape[1] < MIN_FEATURE_IMAGE_HEIGHT_PX:
            logger.error(f"[/persons] 检测人脸特征尺寸过小，小于{MIN_FEATURE_IMAGE_WIDTH_PX}*{MIN_FEATURE_IMAGE_HEIGHT_PX}px")
            return ApiResponse.error(
                status_code=StatusCode.FACE_TOO_SMALL,
                message=f"检测到的人脸过小(小于{MIN_FEATURE_IMAGE_WIDTH_PX}*{MIN_FEATURE_IMAGE_HEIGHT_PX}px)，无法识别，请重新捕捉人脸"
            )

        # 3. 提取特征向量
        try:
            emb_q = await ai_engine.get_embedding(face_image)
        except Exception as e:
            logger.error(f"[/persons] 人脸特征提取失败: {e}")
            return ApiResponse.error(
                status_code=StatusCode.FEATURE_EXTRACT_ERROR,
                message=f"人脸特征提取失败: {str(e)}"
            )

        # 4. 按配置决定是否保存裁剪后的人脸图片
        photo_result = persist_person_photo(
            face_image,
            name=request.name,
            number=request.number,
            project_root=PROJECT_ROOT,
            enabled=settings.feature_image.save_person_photo,
        )
        photo_path = photo_result.path
        if photo_result.failed:
            logger.error("[/persons] 保存图片失败")
            logger.warning("[/persons] 图片保存失败，但继续保存特征数据到数据库")

        # 5. 保存到数据库
        bbox_str = f"{bbox['x']},{bbox['y']},{bbox['w']},{bbox['h']}" if bbox else ""

        person_dict = {
            "name": request.name,
            "number": request.number,
            "photo_path": photo_path,  # 使用变量，可能为空
            "bbox": bbox_str,  # 人脸检测框: x,y,w,h,
            "embedding": Binary(emb_q.tobytes()),
            "tip": tip if tip else ""
        }
        # logger.info(f"person_dict prepared for DB: {person_dict}")

        try:
            # 使用 update_or_create_person 实现存在则更新，不存在则创建
            doc, is_updated = await person_crud.update_or_create_person(db, person_dict)
            action = "更新" if is_updated else "创建"
            logger.info(f"[/persons] 人物 {request.name} {action}成功")
        except Exception as e:
            logger.error(f"[/persons] 数据库操作失败: {e}")
            return ApiResponse.error(
                status_code=StatusCode.INTERNAL_ERROR,
                message=f"数据库操作失败: {str(e)}"
            )

        # 如果图片保存失败，返回 503 状态码但包含数据
        if photo_result.failed:
            return ApiResponse.error(
                status_code=StatusCode.FILE_SAVE_ERROR,
                message=f"人物特征{action}成功，但图片保存失败",
                data={
                    "id": str(doc["_id"]),
                    "name": doc.get("name"),
                    "number": doc.get("number"),
                    "photo_path": doc.get("photo_path", ""),
                    "tip": doc.get("tip", "") + " (图片保存失败)"
                }
            )

        return ApiResponse.success(
            data={
                "id": str(doc["_id"]),
                "name": doc.get("name"),
                "number": doc.get("number"),
                "photo_path": doc.get("photo_path"),
                "tip": doc.get("tip")
            },
            message=f"人物特征{action}成功"
        )

    except Exception as e:
        # 捕获所有未预期的异常
        logger.error(f"[/persons] 未预期的异常: {e}")
        return ApiResponse.error(
            status_code=StatusCode.INTERNAL_ERROR,
            message=f"服务器内部错误: {str(e)}"
        )

@router.post("/batch", response_model=ApiResponse)
async def create_persons_batch_api(
        request: PersonsBatchFeatureRequest = Body(..., description="批量人脸特征录入请求体")
):
    """
    批量上传人物特征接口
    接受多个人物的照片和信息，返回批量处理结果

    注意：
    - HTTP 状态码永远是 200
    - 通过 status_code 字段判断结果
    - status_code=200: 全部成功
    - status_code=207: 部分失败（部分成功、部分失败）
    - status_code=400: 全部失败
    """
    results = []
    failed_records = []

    for idx, person_req in enumerate(request.persons):
        try:
            # 手动验证必填参数
            if not person_req.name or not person_req.name.strip():
                error_msg = f"第{idx+1}个人物: 缺少name参数"
                logger.error(f"[/persons/batch] {error_msg}")
                failed_records.append(error_msg)
                results.append(PersonFeatureResponse(
                    id="",
                    name=person_req.name if hasattr(person_req, 'name') else "",
                    number=person_req.number if hasattr(person_req, 'number') else "",
                    photo_path="",
                    tip=f"错误: 缺少name参数"
                ))
                continue

            if not person_req.number or not person_req.number.strip():
                error_msg = f"第{idx+1}个人物({person_req.name}): 缺少number参数"
                logger.error(f"[/persons/batch] {error_msg}")
                failed_records.append(error_msg)
                results.append(PersonFeatureResponse(
                    id="",
                    name=person_req.name,
                    number=person_req.number if hasattr(person_req, 'number') else "",
                    photo_path="",
                    tip=f"错误: 缺少number参数"
                ))
                continue

            if not person_req.photo or not person_req.photo.strip():
                error_msg = f"第{idx+1}个人物({person_req.name}_{person_req.number}): 缺少photo参数"
                logger.error(f"[/persons/batch] {error_msg}")
                failed_records.append(error_msg)
                results.append(PersonFeatureResponse(
                    id="",
                    name=person_req.name,
                    number=person_req.number,
                    photo_path="",
                    tip=f"错误: 缺少photo参数"
                ))
                continue

            # 解析图片数据，捕获 HTTPException
            try:
                image_data, filename = await base64_to_mat(person_req.photo)
            except HTTPException as e:
                error_msg = f"第{idx+1}个人物({person_req.name}_{person_req.number}): {e.detail}"
                logger.error(f"[/persons/batch] {error_msg}")
                failed_records.append(error_msg)
                results.append(PersonFeatureResponse(
                    id="",
                    name=person_req.name,
                    number=person_req.number,
                    photo_path="",
                    tip=f"错误: {e.detail}"
                ))
                continue

            if image_data is None or not isinstance(image_data, np.ndarray) or image_data.size == 0:
                error_msg = f"第{idx+1}个人物({person_req.name}_{person_req.number}): 未接收到有效图片数据或图像数据存在异常"
                logger.error(f"[/persons/batch] {error_msg}")
                failed_records.append(error_msg)
                results.append(PersonFeatureResponse(
                    id="",
                    name=person_req.name,
                    number=person_req.number,
                    photo_path="",
                    tip=f"错误: 未接收到有效图片数据或图像数据存在异常"
                ))
                continue

            try:
                face_image, bbox, tip = await ai_engine.detect_and_extract_face(image_data)
            except Exception as e:
                error_msg = f"第{idx+1}个人物({person_req.name}_{person_req.number}): 人脸检测服务内部错误 - {str(e)}"
                logger.error(f"[/persons/batch] {error_msg}")
                failed_records.append(error_msg)
                results.append(PersonFeatureResponse(
                    id="",
                    name=person_req.name,
                    number=person_req.number,
                    photo_path="",
                    tip=f"错误: 人脸检测服务内部错误 - {str(e)}"
                ))
                continue

            if face_image is None:
                error_msg = f"第{idx+1}个人物({person_req.name}_{person_req.number}): 未检测到有效人脸"
                logger.error(f"[/persons/batch] {error_msg}")
                failed_records.append(error_msg)
                results.append(PersonFeatureResponse(
                    id="",
                    name=person_req.name,
                    number=person_req.number,
                    photo_path="",
                    tip="错误: 未检测到有效人脸"
                ))
                continue

            if face_image.shape[0] < MIN_FEATURE_IMAGE_WIDTH_PX or face_image.shape[1] < MIN_FEATURE_IMAGE_HEIGHT_PX:
                error_msg = f"第{idx+1}个人物({person_req.name}_{person_req.number}): 检测人脸特征尺寸过小"
                logger.error(f"[/persons/batch] {error_msg}")
                failed_records.append(error_msg)
                results.append(PersonFeatureResponse(
                    id="",
                    name=person_req.name,
                    number=person_req.number,
                    photo_path="",
                    tip=f"错误: 检测到的人脸过小小于{MIN_FEATURE_IMAGE_WIDTH_PX}*{MIN_FEATURE_IMAGE_HEIGHT_PX}px"
                ))
                continue

            try:
                emb_q = await ai_engine.get_embedding(face_image)
            except Exception as e:
                error_msg = f"第{idx+1}个人物({person_req.name}_{person_req.number}): 人脸特征提取失败 - {str(e)}"
                logger.error(f"[/persons/batch] {error_msg}")
                failed_records.append(error_msg)
                results.append(PersonFeatureResponse(
                    id="",
                    name=person_req.name,
                    number=person_req.number,
                    photo_path="",
                    tip=f"错误: 人脸特征提取失败 - {str(e)}"
                ))
                continue

            photo_result = persist_person_photo(
                face_image,
                name=person_req.name,
                number=person_req.number,
                project_root=PROJECT_ROOT,
                enabled=settings.feature_image.save_person_photo,
            )
            if photo_result.failed:
                error_msg = (
                    f"第{idx+1}个人物({person_req.name}_{person_req.number}): "
                    "图片保存失败"
                )
                logger.error(f"[/persons/batch] {error_msg}")
                failed_records.append(error_msg)

            bbox_str = f"{bbox['x']},{bbox['y']},{bbox['w']},{bbox['h']}" if bbox else ""

            person_dict = {
                "name": person_req.name,
                "number": person_req.number,
                "photo_path": photo_result.path,
                "bbox": bbox_str,
                "embedding": Binary(emb_q.tobytes()),
                "tip": tip if tip else ""
            }

            try:
                doc, is_updated = await person_crud.update_or_create_person(db, person_dict)
                action = "更新" if is_updated else "添加"
                results.append(PersonFeatureResponse(
                    id=str(doc["_id"]),
                    name=doc.get("name"),
                    number=doc.get("number"),
                    photo_path=doc.get("photo_path"),
                    tip=doc.get("tip", ""),
                ))
                logger.info(f"[/persons/batch] 第{idx+1}个人物: {person_req.name} {action}成功")
            except Exception as e:
                error_msg = f"第{idx+1}个人物({person_req.name}_{person_req.number}): 数据库操作失败 - {str(e)}"
                logger.error(f"[/persons/batch] {error_msg}")
                failed_records.append(error_msg)
                results.append(PersonFeatureResponse(
                    id="",
                    name=person_req.name,
                    number=person_req.number,
                    photo_path="",
                    tip=f"错误: 数据库操作失败 - {str(e)}"
                ))
                continue

        except Exception as e:
            error_msg = f"第{idx+1}个人物处理异常: {str(e)}"
            logger.error(f"[/persons/batch] {error_msg}")
            failed_records.append(error_msg)
            results.append(PersonFeatureResponse(
                id="",
                name=person_req.name if hasattr(person_req, 'name') else "未知",
                number=person_req.number if hasattr(person_req, 'number') else "未知",
                photo_path="",
                tip=f"错误: 处理异常 - {str(e)}"
            ))

    # 检查是否有失败记录
    if failed_records:
        failed_count = len(failed_records)
        success_count = len(results) - failed_count

        # 收集失败的 number 列表
        failed_numbers = [r.number for r in results if not r.id]

        # 判断是部分失败还是全部失败
        if success_count > 0:
            # 部分失败
            logger.warning(f"[/persons/batch] 批量处理部分失败: 成功{success_count}条，失败{failed_count}条，失败编号: {failed_numbers}")
            return ApiResponse.error(
                status_code=StatusCode.PARTIAL_SUCCESS,
                message=f"批量处理部分失败: 成功{success_count}条，失败{failed_count}条",
                data={
                    "success_count": success_count,
                    "failed_count": failed_count,
                    "failed_numbers": failed_numbers,
                    "failed_details": failed_records[:5],
                    "persons": [
                        {
                            "id": r.id,
                            "name": r.name,
                            "number": r.number,
                            "photo_path": r.photo_path,
                            "tip": r.tip
                        } for r in results
                    ]
                }
            )
        else:
            # 全部失败
            logger.error(f"[/persons/batch] 批量处理全部失败: 失败{failed_count}条，失败编号: {failed_numbers}")
            return ApiResponse.error(
                status_code=StatusCode.BAD_REQUEST,
                message=f"批量处理全部失败: {failed_count}条",
                data={
                    "success_count": 0,
                    "failed_count": failed_count,
                    "failed_numbers": failed_numbers,
                    "failed_details": failed_records[:5],
                    "persons": [
                        {
                            "id": r.id,
                            "name": r.name,
                            "number": r.number,
                            "photo_path": r.photo_path,
                            "tip": r.tip
                        } for r in results
                    ]
                }
            )

    # 全部成功
    return ApiResponse.success(
        data={
            "persons": [
                {
                    "id": r.id,
                    "name": r.name,
                    "number": r.number,
                    "photo_path": r.photo_path,
                    "tip": r.tip
                } for r in results
            ]
        },
        message=f"批量处理成功: {len(results)}条"
    )

@router.get("", response_model=ApiResponse)
async def read_persons_api(
        skip: int = Query(0, description="跳过数据条数"),
        limit: int = Query(100, description="返回数据条数")
):
    """
    获取人物列表接口
    - 支持分页查询
    - HTTP 状态码永远是 200，通过 status_code 字段判断结果
    """
    try:
        docs = await person_crud.get_persons(db, skip=skip, limit=limit)
        if not docs:
            logger.warning("[/persons(get)] 数据库为空，没有任何人物记录")
            return ApiResponse.error(
                status_code=StatusCode.NOT_FOUND,
                message="数据库为空，请先创建人物"
            )

        persons_list = [doc_to_person_read(d) for d in docs]
        return ApiResponse.success(
            data={"persons": [p.model_dump() for p in persons_list]},
            message="查询成功"
        )
    except Exception as e:
        logger.error(f"[/persons(get)] 查询失败: {e}")
        return ApiResponse.error(
            status_code=StatusCode.INTERNAL_ERROR,
            message=f"查询人物列表失败: {str(e)}"
        )


@router.post("/search", response_model=ApiResponse)
async def search_person_api(
        request: SearchPersonRequest = Body(..., description="搜索人物请求体"),
) -> ApiResponse:
    """
    搜索人物接口
    - 只传 name: 模糊查询
    - 只传 number: 精确查询
    - 都传: 组合查询 (name模糊 AND number精确)
    - HTTP 状态码永远是 200，通过 status_code 字段判断结果
    """
    try:
        if not request.name and not request.number:
            return ApiResponse.error(
                status_code=StatusCode.BAD_REQUEST,
                message="name 和 number 至少提供一个"
            )

        persons_list = await person_crud.get_persons_list_dynamic(
            db,
            name=request.name,
            number=request.number,
            limit=20
        )

        if not persons_list:
            logger.warning(
                "[/persons/search] 未找到人物 name_provided=%s number_provided=%s",
                bool(request.name),
                bool(request.number),
            )
            return ApiResponse.success(
                data={"persons": []},
                message="未找到符合条件的人物"
            )

        # 4. 序列化列表：处理 ObjectId 并转为 Pydantic 模型
        result_data = []
        for p in persons_list:
            # 将MongoDB _id->id
            p_dict = {**p, "id": str(p["_id"])}
            # 使用 PersonRead 模型验证并转为 dict
            result_data.append(PersonRead(**p_dict).model_dump())

        return ApiResponse.success(
            data={"persons": result_data},
            message=f"搜索成功，找到 {len(result_data)} 条记录"
        )
    except Exception as e:
        logger.error(f"[/persons/search] 搜索失败: {e}")
        return ApiResponse.error(
            status_code=StatusCode.INTERNAL_ERROR,
            message=f"搜索人物失败: {str(e)}"
        )


@router.delete("/delete", response_model=ApiResponse)
async def delete_person_general_api(
        request: DeletePersonRequest = Body(..., description="删除人物请求体"),
):
    """
    通用删除人物接口
    - 支持通过 name(模糊) 或 number(精确) 或 id(精确) 删除
    - name: 模糊匹配,可能删除多条
    - number: 精确匹配,只删除一条
    - id: 精确匹配,只删除一条
    - HTTP 状态码永远是 200，通过 status_code 字段判断结果
    """
    try:
        if not request.name and not request.number and not request.id:
            return ApiResponse.error(
                status_code=StatusCode.BAD_REQUEST,
                message="name、number 和 id 至少提供一个"
            )

        # 优先按 name 模糊删除
        if request.name:
            try:
                deleted_count, info_list = await person_crud.delete_persons_by_name(db, name_keyword=request.name)
                if deleted_count == 0:
                    logger.warning(f"[/persons/delete] 未找到匹配的人物: name={request.name}")
                    return ApiResponse.error(
                        status_code=StatusCode.NOT_FOUND,
                        message="未找到匹配人物"
                    )
                logger.info(f"[/persons/delete] 删除了 {deleted_count} 个人物: name={request.name}")
                return ApiResponse.success(
                    data={
                        "deleted_count": deleted_count,
                        "info": info_list
                    },
                    message=f"成功删除 {deleted_count} 个人物"
                )
            except Exception as e:
                logger.error(f"[/persons/delete] 按姓名删除失败: {e}")
                return ApiResponse.error(
                    status_code=StatusCode.INTERNAL_ERROR,
                    message=f"按姓名删除失败: {str(e)}"
                )

        # 其次按 number 精确删除
        if request.number:
            try:
                deleted_count, info_list = await person_crud.delete_person_by_number(db, number=request.number)
                if deleted_count == 0:
                    logger.warning(f"[/persons/delete] 未找到该人物: number={request.number}")
                    return ApiResponse.error(
                        status_code=StatusCode.NOT_FOUND,
                        message="未找到该人物"
                    )
                logger.info(f"[/persons/delete] 删除人物成功: number={request.number}")
                return ApiResponse.success(
                    data={
                        "deleted_count": deleted_count,
                        "info": info_list
                    },
                    message=f"成功删除 {deleted_count} 个人物"
                )
            except Exception as e:
                logger.error(f"[/persons/delete] 按编号删除失败: {e}")
                return ApiResponse.error(
                    status_code=StatusCode.INTERNAL_ERROR,
                    message=f"按编号删除失败: {str(e)}"
                )

        # 最后按 id 精确删除(保留向后兼容)
        if request.id:
            try:
                deleted_count, info_list = await person_crud.delete_person_by_id(db, id=request.id)
                if deleted_count == 0:
                    logger.warning(f"[/persons/delete] 未找到该人物: id={request.id}")
                    return ApiResponse.error(
                        status_code=StatusCode.NOT_FOUND,
                        message="未找到该人物"
                    )
                logger.info(f"[/persons/delete] 删除人物成功: id={request.id}")
                return ApiResponse.success(
                    data={
                        "deleted_count": deleted_count,
                        "info": info_list
                    },
                    message=f"成功删除 {deleted_count} 个人物"
                )
            except Exception as e:
                logger.error(f"[/persons/delete] 按ID删除失败: {e}")
                return ApiResponse.error(
                    status_code=StatusCode.INTERNAL_ERROR,
                    message=f"按ID删除失败: {str(e)}"
                )

    except Exception as e:
        logger.error(f"[/persons/delete] 删除操作失败: {e}")
        return ApiResponse.error(
            status_code=StatusCode.INTERNAL_ERROR,
            message=f"删除操作失败: {str(e)}"
        )
