# app/services/task_service.py
import os
import cv2
import numpy as np
import time
from ..schemas.task import TaskInfo, SyncTasksResponse, PolygonResult, KedaPersonObjectWrapper, KedaPersonObject
from ..schemas.image import Image
from ..schemas.error_codes import AppErrCode
from ..schemas.demographics import PersonInfo,FaceInfo
from ..schemas.response import Response as GenericResponse
from ..core.settings import yolo_person_model, yolo_face_model, settings, Total_HaveProcess_Tasks
from .inference_execution import execute_indexed, log_cuda_memory
from typing import Tuple,List
from ..schemas.geometry import Point
import logging

logger = logging.getLogger(__name__)
verbose=False # yolo详细日志
def mask_polygon(img: np.ndarray,points: List[Point]) -> Tuple[np.ndarray, Tuple[int, int]]:
    """
    使用多边形区域对图像进行遮罩，仅保留多边形内区域，其他设为黑色
    返回：mask后区域的最小外接矩形裁剪图 + 左上角偏移
    """
    import numpy as np
    # 1. 获取顶点坐标列表
    pts = np.array([[p.X, p.Y] for p in points], dtype=np.int32)
    # 2. 创建全黑遮罩图
    mask = np.zeros(img.shape[:2], dtype=np.uint8)
    # 3. 在mask上画多边形区域（填充）
    cv2.fillPoly(mask, [pts], 255)
    # 4. 应用掩码：原图像中，仅保留 polygon 区域
    masked_img = cv2.bitwise_and(img, img, mask=mask)
    # 5. 得到 polygon 的外接矩形区域，裁剪出来用于推理
    x, y, w, h = cv2.boundingRect(pts)
    subimg = masked_img[y:y+h, x:x+w]
    return subimg, (x, y)

def build_position_list(
    dets,
    class_id: int,
    offset: Tuple[int,int]
):
    """过滤出某个 class_id，返回 Position 对象列表"""
    ox, oy = offset
    out = []
    for *xyxy, conf, cls in dets:
        if int(cls) != class_id:
            continue
        x1, y1, x2, y2 = map(int, xyxy)
        out.append({
            "PersonThresd": float(conf),
            "ROI": {
                "LeftTopX": x1 + ox,
                "LeftTopY": y1 + oy,
                "RightBtmX": x2 + ox,
                "RightBtmY": y2 + oy
            }
        })
    return out

def draw_and_save_image(
    img: np.ndarray,
    region_results: List[PolygonResult],
    dst_local_path: str
):
    """在原图上绘制检测框与统计文字，并保存到指定路径"""
    draw_start = time.time()
    img_with_boxes = img.copy()
    total_person_cnt = 0
    total_face_cnt = 0

    for poly in region_results:
        # 画人数检测框（绿色）
        if poly.PersonInfo:
            total_person_cnt += poly.PersonInfo.PersonCnt
            for pos in poly.PersonInfo.Personlist:
                x1, y1 = pos.ROI.LeftTopX, pos.ROI.LeftTopY
                x2, y2 = pos.ROI.RightBtmX, pos.ROI.RightBtmY
                cv2.rectangle(img_with_boxes, (x1, y1), (x2, y2), (0, 255, 0), 1)

        # 画脸检测框（红色）
        if poly.FaceInfo:
            total_face_cnt += poly.FaceInfo.FaceCnt
            for pos in poly.FaceInfo.Facelist:
                x1, y1 = pos.ROI.LeftTopX, pos.ROI.LeftTopY
                x2, y2 = pos.ROI.RightBtmX, pos.ROI.RightBtmY
                cv2.rectangle(img_with_boxes, (x1, y1), (x2, y2), (0, 0, 255), 1)

    # 添加统计文字
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.8
    thickness = 1
    cv2.putText(img_with_boxes, f"Green: Person Count {total_person_cnt}", (10, 30), font, font_scale, (0, 255, 0), thickness)
    cv2.putText(img_with_boxes, f"Red: Raise head Count {total_face_cnt}", (10, 60), font, font_scale, (0, 0, 255), thickness)

    # 保存图片
    os.makedirs(os.path.dirname(dst_local_path), exist_ok=True)
    success = cv2.imwrite(dst_local_path, img_with_boxes)
    if not success:
        logger.warning(f"Failed to save image to {dst_local_path}")

    logger.info(f"save image to {dst_local_path}")
    log_duration("Draw + Save image", draw_start)

def log_duration(tag: str, start_time: float):
    duration_ms = (time.time() - start_time) * 1000
    logger.info(f"[Timing] {tag}: {duration_ms:.2f} ms")

async def process_polygon(idx, poly, img, target_height, target_width, TaskID):
    poly_start = time.time()
    logger.info(f"[Polygon-{idx}] Start processing poly Label: [{poly.Label}]")

    mask_start = time.time()
    subimg, (ox, oy) = mask_polygon(img, poly.Points)
    log_duration(f"[Polygon-{idx}] mask_polygon", mask_start)

    # ---------- 0) 空图保护 ----------
    h, w = subimg.shape[:2]
    if h == 0 or w == 0:
        logger.error(f"TaskID={TaskID} error [liuw] [Polygon-{idx}] Empty ROI (h={h}, w={w}), skip YOLO inference.")
        return PolygonResult(
            Label=poly.Label,
            PersonInfo=PersonInfo(PersonCnt=0, Personlist=[]),
            FaceInfo=FaceInfo(FaceCnt=0, Facelist=[]),
        )
    # ------------------------------

    # —— 1) todo Person 检测 & 聚合到 PersonInfo ——
    person_start = time.time()
    person_model_start = time.time()

    logger.info(f"predict [target_height-{target_height}] [target_width-{target_width}] ")

    pred = yolo_person_model.predict(subimg, conf=0.1, imgsz=(target_height, target_width),half=settings.Inference.PersonUseHalf,agnostic_nms=True,verbose=verbose)[0]
    log_duration(f"[Polygon-{idx}] Person model.predict", person_model_start)
    postproc_start = time.time()
    dets = pred.boxes.data.tolist()
    log_duration(f"[Polygon-{idx}] Person dets tolist", postproc_start)
    log_duration(f"[Polygon-{idx}] Person detection", person_start)

    # cls_names = ["Head", "Top_Head", "Hat", "Headphones", "Shoulder", "Other"] # 最大类别, "Other"，直接不处理。
    cls_names = ["Head", "Top_Head", "Hat", "Headphones", "Shoulder"] # 最大类别, "Other"，直接不处理。
    person_positions = []
    for *xyxy, conf, cls in dets:
        cls = int(cls)
        logger.info(f"predict [cls-{cls}] [conf-{conf}] ")
        if cls < 0 or cls >= len(cls_names): continue
        if conf < settings.Person_Thresd[cls_names[cls]]: continue
        x1, y1, x2, y2 = map(int, xyxy)
        person_positions.append({
            "PersonThresd": float(conf),
            "ROI": {"LeftTopX": x1 + ox, "LeftTopY": y1 + oy, "RightBtmX": x2 + ox, "RightBtmY": y2 + oy}
        })
    person_info = PersonInfo(
        PersonCnt=len(person_positions),
        Personlist=person_positions
    )
    logger.info(
        f"[Polygon-{idx}] Person detection result: {len(person_positions)} person(s) detected(>PersonThresd)")

    # —— 2) todo Face 检测 & 聚合到 FaceInfo ——
    face_start = time.time()
    face_model_start = time.time()
    pred_face = yolo_face_model.predict(subimg, conf=0.1, imgsz=(target_height, target_width),half=settings.Inference.FaceUseHalf, verbose=verbose)[0]
    log_duration(f"[Polygon-{idx}] Face model.predict", face_model_start)

    face_postproc_start = time.time()
    face_dets = pred_face.boxes.data.tolist()
    log_duration(f"[Polygon-{idx}] Face dets tolist", face_postproc_start)

    log_duration(f"[Polygon-{idx}] Face detection", face_start)
    face_positions = []
    for *xyxy, conf, cls in face_dets:
        if conf < settings.Face_Thresd["face"]: continue
        x1, y1, x2, y2 = map(int, xyxy)
        face_positions.append({
            "FaceThresd": float(conf),
            "ROI": {"LeftTopX": x1 + ox, "LeftTopY": y1 + oy, "RightBtmX": x2 + ox, "RightBtmY": y2 + oy}
        })
    face_info = FaceInfo(
        FaceCnt=len(face_positions),
        Facelist=face_positions,
    )
    logger.info(f"[Polygon-{idx}] Face detection result: {len(face_positions)} face(s) detected(>FaceThresd)")
    result = PolygonResult(
        Label=poly.Label,
        PersonInfo=person_info,
        FaceInfo=face_info,
    )

    global Total_HaveProcess_Tasks
    Total_HaveProcess_Tasks["val"] += 1
    log_duration(f"[Polygon-{idx}] Total polygon processing. Total_HaveProcess_Tasks-{Total_HaveProcess_Tasks['val']}", poly_start)

    return result


async def sync_tasks(task_info: TaskInfo) -> SyncTasksResponse:

    # 增加连接计数
    from ..services.capacity_service import increment_connection, increment_processed_images
    increment_connection()
    start_time = time.time()
    logger.info(f"Received TaskID: {task_info.TaskID}")
    # 增加处理图片计数
    if task_info.ImageList:
        increment_processed_images(len(task_info.ImageList))

    resolution = task_info.AnalysisRule.AlgParams.ImageResolution
    target_width = resolution.ImageWidth
    target_height = resolution.ImageHeight
    logger.info(f"Configured image resolution: {target_width}x{target_height}")

    polygon_list = task_info.AnalysisRule.AlgParams.PolygonList or []
    for i, poly in enumerate(polygon_list):
        point_str = "; ".join([f"({p.X}, {p.Y})" for p in poly.Points])
        logger.info(f"Received TaskID: {task_info.TaskID}-[Polygon-{i}] Label: {poly.Label}, Points: {point_str}")

    results = []
    for img_info in task_info.ImageList:
        image_start = time.time()
        uri = img_info.URI or ""
        if os.path.isabs(uri) and os.path.exists(uri):
            local_path = uri
        else:
            rel = uri.lstrip("/")
            local_path = os.path.join(settings.IMAGE_ROOT, rel)

        logger.info(f"Processing image: {local_path}")
        if not os.path.exists(local_path):
            logger.warning(f"Image path does not exist: {local_path}")
            raise RuntimeError(f"打不开图片：{local_path}")

        # 读图
        read_start = time.time()
        img = cv2.imread(local_path)
        if img is None:
            logger.error(f"Failed to read image: {local_path}")
            raise RuntimeError(f"打不开图片：{local_path}")
        log_duration("Read image", read_start)

        h, w = img.shape[:2]
        logger.info(f"Loaded image size: {w}x{h}")

        base_uri, ext = os.path.splitext(uri)
        dst_uri = f"{base_uri}_dst{ext}"
        # 本地保存路径同样在 IMAGE_ROOT 下，保留子目录结构
        base_local, _ = os.path.splitext(local_path)
        dst_local_path = base_local + "_dst" + ext

        polygon_list = task_info.AnalysisRule.AlgParams.PolygonList or []
        region_results: list[PolygonResult] = await execute_indexed(
            polygon_list,
            lambda idx, poly: process_polygon(
                idx, poly, img, target_height, target_width, task_info.TaskID
            ),
            sequential=settings.Inference.SyncTasks2PolygonsSequential,
        )
        log_cuda_memory(logger, "person_count_file")
        if task_info.AnalysisRule.AlgParams.ResultImageDeclare == 1:
            draw_and_save_image(img, region_results, dst_local_path)
            logger.info(f"ResultImageDeclare={task_info.AnalysisRule.AlgParams.ResultImageDeclare}")

        if settings.SAVE_RESULT_IMAGE:
            img_name = os.path.basename(local_path)
            name, ex = os.path.splitext(img_name)
            img_name = f"{name}_dst{ex}"
            result_img_path = os.path.join(settings.RESULT_IMAGE_ROOT, img_name)
            logger.info(f"Save result image path: {result_img_path}")
            draw_and_save_image(img, region_results, result_img_path)

        wrap_start = time.time()
        keda_obj = KedaPersonObject(
            Image=Image(
                URI=dst_uri,
                Data=None,
                ImageID=img_info.ImageID,
            ),
            PolygonResult=region_results
        )

        wrapper = KedaPersonObjectWrapper(KedaPersonObject=keda_obj)
        results.append(wrapper)
        log_duration("Create wrapper & append", wrap_start)
        log_duration(f"[ImageID: {img_info.ImageID}] Total image processing", image_start)

    total_duration = time.time() - start_time
    logger.info(f"Task {task_info.TaskID} completed in {total_duration:.2f}s, processed {len(results)} image(s)")

    return SyncTasksResponse(
        Response=GenericResponse(ErrCode=AppErrCode.SUCCESS, Desc="成功"),
        TaskID=task_info.TaskID,
        FreeCapacity=1,
        TaskResult=results
    )
