"""
Data Models
数据模型
"""
from collections import deque
from queue import Queue
from threading import Event, Lock
from typing import Any, Optional

from app.utils.uri import redact_uri_for_log


class LocalVideoAnalysisTaskObject:
    """本地视频分析任务对象"""

    def __init__(
        self,
        task_id: str,
        operator_task_id: str,
        video_id: str,
        video_path: str,
        result_callback_uri: str,
        saved_frame_similarity: float = 0.98,
        frame_queue: Optional[Queue] = None,
        task_status_code: int = 0,
        result_writer: Any = None,
        terminal_publisher: Any = None,
    ):
        """
        初始化任务对象

        Args:
            task_id: 任务ID
            operator_task_id: 算子任务ID
            video_id: 视频ID
            video_path: 视频路径
            result_callback_uri: 结果回调URI
            saved_frame_similarity: 保存帧相似度阈值
            frame_queue: 帧队列
            task_status_code: 任务状态码
                1: 正在处理数据，并检测到PPT变化
                2: 视频流处理完成
                3: RTSP拉流时发生异常
                4: 视频流任务取消
                5-10: 各种错误状态
        """
        self.task_id = task_id
        self.operator_task_id = operator_task_id
        self.video_id = video_id
        self.video_path = video_path
        self.result_callback_uri = result_callback_uri

        # 相似度阈值
        self.contiguous_frame_similarity = 0.99  # 连续帧相似度阈值
        self.saved_frame_similarity = saved_frame_similarity  # 保存帧相似度阈值

        # 时间配置
        self.start_time = '0:00:00'
        self.end_time = 'INFINITY'

        self.result_writer = result_writer
        self.terminal_publisher = terminal_publisher

        # 视频属性
        self.cv_cap_prop_frame_width = 1920
        self.cv_cap_prop_frame_height = 1080
        self.fps = 30
        self.file_frame_sum = 0

        # 任务状态
        self.task_status_code = task_status_code
        self.task_progress = 0.0
        self.failure_reason = ""
        self._failure_lock = Lock()

        # 队列和历史帧
        self.frame_queue = frame_queue if frame_queue is not None else Queue()
        self.historical_frames = deque(maxlen=20)

        # 取消事件
        self.cancel_event = Event()
        self.stream_finished_event = Event()

    def mark_failed(self, reason: str, status_code: int = 3) -> None:
        """Preserve the first task failure reported by either worker thread."""
        with self._failure_lock:
            if not self.failure_reason:
                self.failure_reason = reason
                self.task_status_code = status_code

    def __str__(self):
        return (
            f"LocalVideoAnalysisTaskObject("
            f"task_id='{self.task_id}', "
            f"operator_task_id='{self.operator_task_id}', "
            f"video_id='{self.video_id}', "
            f"video_path='{redact_uri_for_log(self.video_path)}', "
            f"saved_frame_similarity={self.saved_frame_similarity}, "
            f"task_status_code={self.task_status_code}, "
            f"fps={self.fps}, "
            f"frame_queue_size={self.frame_queue.qsize()}, "
            f"result_callback_uri='{redact_uri_for_log(self.result_callback_uri)}')"
        )


class FrameData:
    """帧数据"""

    def __init__(self, frame, timestamp: int | None = None, timestamp_ms: int | None = None):
        """
        初始化帧数据

        Args:
            frame: 图像帧
            timestamp: 兼容字段，时间戳（秒）
            timestamp_ms: 精确视频时间戳（毫秒）
        """
        if timestamp_ms is None:
            if timestamp is None:
                raise ValueError("timestamp 或 timestamp_ms 至少提供一个")
            timestamp_ms = int(timestamp) * 1000
        self.frame = frame
        self.timestamp_ms = int(timestamp_ms)
        self.timestamp = int(self.timestamp_ms // 1000)
