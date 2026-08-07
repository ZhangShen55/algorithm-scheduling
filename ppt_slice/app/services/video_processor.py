"""
Video Stream Processing Service
视频流处理服务
"""
import asyncio
import queue
import threading
import time

import aiohttp
import av
import cv2
import math
import numpy as np

from app.core.logger import get_logger
from app.core.config import settings
from app.models.task import LocalVideoAnalysisTaskObject, FrameData
from app.services.task_manager import task_manager
from app.services.image_compare import compare_images

logger = get_logger("video_stream")

MIN_FRAMES_OK = settings.MIN_FRAMES_OK


def open_stream(task: LocalVideoAnalysisTaskObject, get_stream_error_event: threading.Event):
    """
    打开视频流

    Args:
        task: 任务对象
        get_stream_error_event: 错误事件

    Returns:
        视频容器或None
    """
    logger.debug(f"[open_stream] 开始拉取视频流, video_path={task.video_path}")

    try:
        container = av.open(task.video_path)
        return container
    except av.error.InvalidDataError:
        logger.error(f"打开失败：数据无效或格式错误: {task.video_path}")
        get_stream_error_event.set()
        task.mark_failed(f"打开失败：数据无效或格式错误: {task.video_path}", 5)
    except av.error.FileNotFoundError:
        logger.error(f"打开失败：文件或流地址未找到: {task.video_path}")
        get_stream_error_event.set()
        task.mark_failed(f"打开失败：文件或流地址未找到: {task.video_path}", 6)
    except av.error.PermissionError:
        logger.error(f"打开失败：权限不足: {task.video_path}")
        get_stream_error_event.set()
        task.mark_failed(f"打开失败：权限不足: {task.video_path}", 7)
    except av.error.NetworkError:
        logger.error(f"打开失败：网络问题: {task.video_path}")
        get_stream_error_event.set()
        task.mark_failed(f"打开失败：网络问题: {task.video_path}", 8)
    except Exception as e:
        logger.error(f"流媒体打开失败：未知错误: {str(e)}")
        get_stream_error_event.set()
        task.mark_failed(f"拉流时发生异常: {str(e)}", 9)

    return None


def get_stream(task: LocalVideoAnalysisTaskObject, get_stream_error_event: threading.Event):
    """
    获取视频流并解码

    Args:
        task: 任务对象
        get_stream_error_event: 错误事件
    """
    logger.debug(f"[get_stream] 开始拉取视频流, video_path={task.video_path}")

    ui_frame_no = 0
    ui_video_sec = 0

    container = None
    frame_rate = task.fps
    try:
        container = open_stream(task, get_stream_error_event)
        if container is None:
            logger.error(f"open_stream failed. video_path={task.video_path}")
            return

        video_stream = container.streams.video[0]

        # 获取视频属性
        task.cv_cap_prop_frame_width = int(video_stream.width)
        task.cv_cap_prop_frame_height = int(video_stream.height)

        average_rate = video_stream.average_rate
        frame_ratev1 = float(average_rate)
        rounded_rate = round(frame_ratev1, 2)
        frame_rate = math.ceil(rounded_rate)
        task.fps = frame_rate

        time_base = video_stream.time_base

        logger.info(
            f"成功拉取视频流, video_path={task.video_path}, "
            f"分辨率={task.cv_cap_prop_frame_width}x{task.cv_cap_prop_frame_height}, "
            f"frame_rate={frame_rate}, average_rate={float(average_rate):.2f}"
        )

        # 设置只解码I帧
        video_stream.codec_context.skip_frame = 'NONKEY'

        i_frame_count = 0
        try_cnt = 0
        has_stream = False
        timeout_ms = 100000  # 100秒超时
        get_iframe_time = time.time()

        logger.debug(f"开始读取视频流 video_path={task.video_path}")

        for packet in container.demux(video_stream):
            if task.cancel_event.is_set():
                logger.debug(f"任务取消 video_path={task.video_path}")
                break

            # 检查超时
            if (time.time() - get_iframe_time) * 1000 > timeout_ms:
                logger.error(f"等待100秒未收到I帧，退出. video_path={task.video_path}")
                raise RuntimeError(f"{task.task_id} 的接收码流异常退出")

            has_stream = True
            ui_frame_no += 1

            if packet.is_keyframe:
                get_iframe_time = time.time()
                i_frame_count += 1

                for frame in packet.decode():
                    # 计算时间戳
                    if frame.pts is not None:
                        ui_video_sec = int(frame.pts * time_base)
                    elif frame.dts is not None:
                        ui_video_sec = int(frame.dts * time_base)
                    else:
                        ui_video_sec = int(ui_frame_no / frame_rate)

                    timestamp_ms = ui_video_sec

                    # 转换图像格式
                    img = frame.to_image()
                    img_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

                    # 创建 FrameData 对象并加入队列
                    frame_data = FrameData(frame=img_cv, timestamp=timestamp_ms)

                    if task.frame_queue.full():
                        logger.info(f"{task.task_id} 的 frame_queue队列已满, 等待中...")
                        time.sleep(3)

                    try:
                        task.frame_queue.put(frame_data, timeout=7)
                    except queue.Full:
                        try_cnt += 1
                        logger.warning(f"任务 [{task.task_id}] 的 frame_queue队列已满，重试=[{try_cnt}]")
                        if try_cnt > 20:
                            raise RuntimeError(f"{task.task_id} 的 frame_queue队列已满，异常退出")
                        else:
                            continue

        logger.debug(f"完成读取视频流 video_path={task.video_path}")

    except RuntimeError as e:
        logger.error(f"拉流时发生异常: {str(e)}")
        get_stream_error_event.set()
        task.mark_failed(f"拉流时发生异常: {str(e)}", 10)
    except Exception as e:
        logger.error(f"拉流时发生未知异常: {str(e)}", exc_info=True)
        get_stream_error_event.set()
        task.mark_failed(f"拉流时发生异常: {str(e)}", 10)
    finally:
        if container is not None:
            container.close()
        task.file_frame_sum = ui_frame_no
        logger.info(
            f"get_stream 流已释放，处理结束. task_id={task.task_id}, "
            f"frame_rate={frame_rate}, file_frame_sum={task.file_frame_sum}"
        )
        task.stream_finished_event.set()


def get_frame_from_queue(frame_queue: queue.Queue, timeout: int = 30):
    """
    从队列中取出帧

    Args:
        frame_queue: 帧队列

    Returns:
        (frame_data, success)
    """
    try:
        frame_data = frame_queue.get(timeout=timeout)
        frame_queue.task_done()
        return frame_data, True
    except queue.Empty:
        return None, False


def process_frames(task: LocalVideoAnalysisTaskObject, get_stream_error_event: threading.Event):
    """
    处理视频帧

    Args:
        task: 任务对象
        get_stream_error_event: 错误事件
    """
    last_frame = None
    save_frame = None
    processed_frames = 0
    processing_relative_time = 1
    last_frame_time = 0
    time_interval = 0

    logger.info(f"process_frames 开始, task_id={task.task_id}")

    while not task.cancel_event.is_set() and not get_stream_error_event.is_set():
        try:
            frame_data, ret = get_frame_from_queue(
                task.frame_queue,
                timeout=1 if task.stream_finished_event.is_set() else 30,
            )
        except TimeoutError:
            logger.info("frame_queue获取帧错误, frame_queue为空")
            break

        if ret:
            if last_frame is not None:
                try:
                    compare_contiguous_frame = compare_images(last_frame, frame_data.frame)

                    if save_frame is None:
                        save_frame = last_frame
                        time_interval = frame_data.timestamp - frame_data.timestamp
                        processing_relative_time = processing_relative_time + time_interval
                        last_frame_time = frame_data.timestamp
                        task.result_writer.write_image(
                            frame_seq=processing_relative_time,
                            frame=frame_data.frame,
                            snap_time=frame_data.timestamp,
                        )

                    if compare_contiguous_frame > task.contiguous_frame_similarity:
                        compare_saved_frame = compare_images(save_frame, frame_data.frame)

                        if compare_saved_frame < task.saved_frame_similarity:
                            time_interval = frame_data.timestamp - last_frame_time
                            processing_relative_time = processing_relative_time + time_interval

                            task.result_writer.write_image(
                                frame_seq=processing_relative_time,
                                frame=frame_data.frame,
                                snap_time=frame_data.timestamp,
                            )

                            save_frame = frame_data.frame
                            last_frame_time = frame_data.timestamp

                except Exception as e:
                    logger.error(f"处理视频流切片错误: {e}")
                    task.mark_failed(f"处理视频流切片错误: {str(e)}")
                    task.cancel_event.set()
                    break

            last_frame = frame_data.frame
            processed_frames += 1
        else:
            logger.info(f"process_frames get_frame_from_queue failed. task_id={task.task_id}")
            break

    if task.cancel_event.is_set():
        logger.debug(f"视频流任务取消, url={task.video_path}")
        terminal_status = 70
        reason = task.failure_reason or "任务已取消"
        task.task_status_code = 4 if not task.failure_reason else task.task_status_code
    elif task.failure_reason:
        terminal_status = 70
        reason = task.failure_reason
    elif not get_stream_error_event.is_set():
        if processed_frames > MIN_FRAMES_OK:
            task.task_status_code = 2
            logger.debug(f"视频流处理完成, url={task.video_path}")
            terminal_status = 60
            reason = ""
        else:
            reason = "接收网络码流帧异常"
            task.mark_failed(reason)
            terminal_status = 70
            logger.debug(
                f"视频流处理异常(小于{MIN_FRAMES_OK}秒数目), url={task.video_path}. "
                f"收到视频帧数目={processed_frames}"
            )
    else:
        terminal_status = 70
        reason = task.failure_reason or "视频流处理失败"

    try:
        asyncio.run(
            task.terminal_publisher.publish_once(
                status=terminal_status,
                reason=reason,
            )
        )
    except Exception as exc:
        task.mark_failed(f"终态结果发布失败: {exc}")
        logger.error(
            f"终态结果发布失败: operator_task_id={task.operator_task_id}: {exc}",
            exc_info=True,
        )


def start_rtsp_thread(task: LocalVideoAnalysisTaskObject):
    """
    启动RTSP处理线程

    Args:
        task: 任务对象
    """
    logger.info(f"创建新的 start_rtsp_thread task_id={task.task_id}")

    get_stream_event = threading.Event()

    # 创建获取码流并解码线程
    stream_thread = threading.Thread(target=get_stream, args=(task, get_stream_event))
    # 创建处理数据帧线程
    process_thread = threading.Thread(target=process_frames, args=(task, get_stream_event))

    stream_thread.daemon = False
    process_thread.daemon = False

    start = time.time()

    # 启动线程
    stream_thread.start()
    process_thread.start()

    # 等待线程完成
    logger.info(f"stream_thread.join. task_id={task.task_id}")
    stream_thread.join()
    logger.info(f"stream_thread.join OK. task_id={task.task_id}")

    logger.info(f"process_thread.join. task_id={task.task_id}")
    process_thread.join()
    logger.info(f"process_thread.join OK. task_id={task.task_id}")

    if task.task_status_code != 2:
        task_manager.add_fail_task(time.time(), task.operator_task_id)
    task_manager.del_task(task.operator_task_id)
    logger.debug(f"任务线程全部结束 - 当前任务总数: {task_manager.get_task_count()}")

    end = time.time()

    # 释放空闲内存
    task_frame_queue_size = task.frame_queue.qsize()
    if task_frame_queue_size > 0:
        while not task.frame_queue.empty():
            try:
                task.frame_queue.get_nowait()
                task.frame_queue.task_done()
            except queue.Empty:
                break

    logger.info(f"释放任务. before=[{task_frame_queue_size}] task.frame_queue.qsize()={task.frame_queue.qsize()}")

    # 清空队列以及变量
    task.frame_queue = None
    get_stream_event = None

    task_id = task.task_id
    frame_sum = task.file_frame_sum
    frame_fps = task.fps
    cost_time = int(end - start)
    file_time = int(frame_sum / frame_fps) if frame_fps > 0 else 0
    rate = float(file_time / cost_time) if cost_time > 0 else 0

    task = None

    logger.info(
        f"start_rtsp_thread 完成. taskID={task_id}, task_frame_queue_size={task_frame_queue_size}, "
        f"处理速率=[{rate:.2f}], 帧总数={frame_sum}, 帧率={frame_fps}, 耗时={end - start:.3f}秒"
    )


async def start_rtsp(task: LocalVideoAnalysisTaskObject):
    """
    异步启动RTSP处理

    Args:
        task: 任务对象
    """
    logger.info(f"创建新的 start_rtsp task_id={task.task_id}")

    # 创建获取码流并解码线程
    stream_task_thread = threading.Thread(target=start_rtsp_thread, args=(task,))
    stream_task_thread.daemon = False

    # 启动线程
    stream_task_thread.start()


async def send_terminal_callback(payload: dict, *, callback_uri: str) -> None:
    """POST one terminal metadata payload; the publisher records failures."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            callback_uri,
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as response:
            response.raise_for_status()
            logger.info(
                "终态回调成功: uri=%s status=%s task_id=%s operator_task_id=%s",
                callback_uri,
                response.status,
                payload["task_id"],
                payload["operator_task_id"],
            )
