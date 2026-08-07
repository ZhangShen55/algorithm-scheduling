# app/image_loader.py
# from __future__ import annotations
import base64, re, httpx, cv2, numpy as np
from fastapi import Form, UploadFile, HTTPException, Depends
from typing import Union, Tuple
from app.core.config import settings
from app.core.exceptions import ImageValidationError
from app.core.logger import get_logger

logger = get_logger(__name__)

# 设置全局变量，用于校验图像像素总数
# 默认 80*80 = 6400
MIN_IMAGE_PIXELS = int(settings.feature_image.min_feature_image_width_px) * int(settings.feature_image.min_feature_image_height_px)
# 默认 1280*720 = 921600
MAX_IMAGE_PIXELS = int(settings.feature_image.max_feature_image_width_px) * int(settings.feature_image.max_feature_image_height_px)
# 限制单个图像文件大小为 10MB
MAX_IMAGE_SIZE_M = int(settings.feature_image.max_feature_image_size_m)
logger.info(f"设置全局变量 MIN_IMAGE_PIXELS={MIN_IMAGE_PIXELS}, MAX_IMAGE_PIXELS={MAX_IMAGE_PIXELS}, MAX_IMAGE_SIZE_M={MAX_IMAGE_SIZE_M}M")

async def _raw_photo(
    photo: Union[str, UploadFile] = Form(...)  # 只留一个字段
) -> Union[str, bytes]:
    """
    先接受来自后端的图片数据，然后根据类型判断是 URL、Base64 或文件上传。
    返回:
        str  -> URL 或 base64
        bytes-> 文件内容
    """
    if isinstance(photo, str):
        # URL 或 base64
        logger.info(f"接收到图片 URL 或 base64")
        return photo
    # 否则是
    logger.info(f"接收到图片文件: {photo.filename}")
    return await photo.read()

# 将base64字符串转为图片矩阵
async def base64_to_mat(base64_str: str) -> Tuple[np.ndarray, str]:
    """
    将base64字符串转为图片矩阵。
    """
    # 判断是否是base64字符串
    if not re.match(r'^data:image/\w+;base64,', base64_str):
        logger.error(f"不是base64字符串")
        raise HTTPException(400, "不是base64字符串")

    # 去掉头部的base64标识
    pure_base64 = re.sub(r'^data:image/\w+;base64,', '', base64_str)

    # 解码base64字符串
    try:
        content = base64.b64decode(pure_base64)
    except Exception as e:
        logger.error(f"base64 解码失败，错误信息: {e}")
        raise HTTPException(400, f"base64 解码失败: {e}")
    # 解码图片矩阵
    return _decode(content), "base64_image"



async def get_photo_mat(raw: Union[str, bytes, UploadFile] = Depends(_raw_photo)) -> Tuple[np.ndarray, str]:
    """
    获取图像矩阵和文件名，支持 URL、Base64 或文件上传。
    """
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.startswith("http"):
            # URL 分支
            try:
                resp = httpx.get(raw, timeout=10, follow_redirects=True)
                resp.raise_for_status()
                logger.info(f"通过url下载图片成功: {raw}")
            except Exception as e:
                logger.error(f"通过url下载图片失败，url: {raw}, 错误信息: {e}")
                raise HTTPException(400, f"通过url下载图片失败，url: {raw}, 错误信息: {e}")
            # 返回 URL 和图片数据
            return _decode(resp.content), raw
        else:
            # base64 分支
            raw = re.sub(r'^data:image/\w+;base64,', '', raw)
            logger.info(f"接收到图片 base64码")
            try:
                content = base64.b64decode(raw)
            except Exception as e:
                logger.error(f"base64 解码失败，错误信息: {e}")
                raise HTTPException(400, f"base64 解码失败: {e}")
            return _decode(content), "base64_image"  # 返回 base64 和图片数据


    elif isinstance(raw, bytes):
        if len(raw) == 0:
            logger.error(f"接收到的图像二进制数据为空")
            raise HTTPException(400, "接收到的图像二进制数据为空")
        return _decode(raw), "unknown_file"


    elif isinstance(raw, UploadFile):
        # 1. 读取文件内容
        contents = await raw.read()
        logger.info(f"接收到图片文件: {raw.filename}")
        # 检查文件是否为空
        if len(contents) == 0:
            logger.error(f"上传的文件 '{raw.filename}' 是空的 (0KB)，请检查文件是否损坏")
            raise HTTPException(
                400,
                f"上传的文件 '{raw.filename}' 是空的 (0KB)，请检查文件是否损坏"
            )
        # 限制文件大小 默认10MB
        MAX_IMAGE_SIZE = MAX_IMAGE_SIZE_M * 1024 * 1024
        if len(contents) > MAX_IMAGE_SIZE:
            logger.error(f"上传的文件 '{raw.filename}' 大小超过 {MAX_IMAGE_SIZE_M}MB，请上传小于 {MAX_IMAGE_SIZE_M}MB 的文件")
            raise HTTPException(400, "上传文件图像过大，请上传小于 10MB 的文件")
        return _decode(contents), raw.filename  # 返回图片数据和文件名

    logger.error(f"未知图像异常类型，无法解析图片")
    raise HTTPException(status_code=400, detail="未知图像异常类型，无法解析图片")

# ---------- 通用解码 ----------
def _decode(content: bytes) -> np.ndarray:
    if not content:
        logger.error(f"[decode] 上传的图片内容为空")
        raise HTTPException(status_code=400, detail="[decode] 上传的图片内容为空")

    nparr = np.frombuffer(content, np.uint8)

    # 防止空文件
    if nparr.size == 0:
        logger.error(f"[decode] 图片数据无效(Size 0)")
        raise HTTPException(status_code=400, detail="[decode] 图片数据无效(Size 0)")

    try:
        logger.info(f"[decode] 图片数据有效，开始解码")
        mat = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    except Exception as e:
        logger.error(f"[decode] 图片解码异常: {str(e)}")
        raise HTTPException(status_code=400, detail=f"[decode] 图片解码异常: {str(e)}")

    if mat is None:
        logger.error(f"[decode] 图片解码失败，文件格式可能不支持")
        raise HTTPException(status_code=400, detail="[decode] 图片解码失败，文件格式可能不受支持")

    # 对图像的像素进行检测，抛出异常
    _validate_image_pixels(mat)

    if not mat.flags['C_CONTIGUOUS']:
        logger.info(f"[decode] 图片数据不是连续的，进行内存拷贝")
        mat = np.ascontiguousarray(mat)

    return mat


def _validate_image_pixels(mat: np.ndarray):
    """
    校验图像整体像素总数是否在规定范围内。
    规定范围:
    - 下限: 80x80 = 6400 px
    - 上限: 1280x720 = 921600 px
    这里需要理清楚一个概念：114*114px, 可以是500*500的大小（前端），后端通过cv获取的大小就是114*114。
    """
    h, w = mat.shape[:2]
    total_pixels = h * w

    if total_pixels < MIN_IMAGE_PIXELS:
        raise ImageValidationError(
            400,f"图片整体分辨率过低 ({w}x{h}={total_pixels}px)，不能低于 6400px (如 80x80)"
        )

    if total_pixels > MAX_IMAGE_PIXELS:
        raise ImageValidationError(
            400,f"图片整体分辨率过高 ({w}x{h}={total_pixels}px)，不能高于 921600px"
        )
