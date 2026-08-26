"""
Task Management Service
任务管理服务
"""
from enum import StrEnum
from threading import RLock

from app.core.logger import get_logger
from app.models.task import LocalVideoAnalysisTaskObject

logger = get_logger("task_service")


class TaskAdmission(StrEnum):
    """原子区分新受理、幂等重复、身份冲突和容量不足。"""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    CAPACITY = "capacity"


class TaskManager:
    """任务管理器"""

    def __init__(self):
        self._task_list: dict[str, LocalVideoAnalysisTaskObject] = {}
        self._fail_task_list: dict[float, str] = {}
        self._lock = RLock()

    def try_add_task(
        self,
        task_id: str,
        task: LocalVideoAnalysisTaskObject,
        *,
        max_tasks: int,
    ) -> bool:
        """Atomically reserve one worker-local task capacity slot."""
        return (
            self.admit_task(task_id, task, max_tasks=max_tasks)
            is TaskAdmission.ACCEPTED
        )

    def admit_task(
        self,
        task_id: str,
        task: LocalVideoAnalysisTaskObject,
        *,
        max_tasks: int,
    ) -> TaskAdmission:
        """原子受理任务，并保留相同在途请求的幂等语义。"""
        with self._lock:
            existing = self._task_list.get(task_id)
            if existing is not None:
                if self._same_request(existing, task):
                    return TaskAdmission.DUPLICATE
                return TaskAdmission.CONFLICT
            if len(self._task_list) >= max_tasks:
                return TaskAdmission.CAPACITY
            self._task_list[task_id] = task
            count = len(self._task_list)
        logger.info(f"Task added: {task_id}, total tasks: {count}")
        return TaskAdmission.ACCEPTED

    @staticmethod
    def _same_request(
        existing: LocalVideoAnalysisTaskObject,
        candidate: LocalVideoAnalysisTaskObject,
    ) -> bool:
        return (
            existing.task_id == candidate.task_id
            and existing.operator_task_id == candidate.operator_task_id
            and existing.video_path == candidate.video_path
            and existing.result_callback_uri == candidate.result_callback_uri
            and existing.saved_frame_similarity == candidate.saved_frame_similarity
        )

    def add_task(self, task_id: str, task: LocalVideoAnalysisTaskObject) -> None:
        """
        添加任务

        Args:
            task_id: 任务ID
            task: 任务对象
        """
        with self._lock:
            self._task_list[task_id] = task
            count = len(self._task_list)
        logger.info(f"Task added: {task_id}, total tasks: {count}")

    def get_task(self, task_id: str) -> LocalVideoAnalysisTaskObject | None:
        """
        获取任务

        Args:
            task_id: 任务ID

        Returns:
            任务对象或None
        """
        with self._lock:
            return self._task_list.get(task_id)

    def del_task(self, task_id: str) -> LocalVideoAnalysisTaskObject | None:
        """
        删除任务

        Args:
            task_id: 任务ID

        Returns:
            被删除的任务对象或None
        """
        logger.debug(f"Attempting to remove task: {task_id}")
        with self._lock:
            removed_task = self._task_list.pop(task_id, None)
            task_sum = len(self._task_list)
        if removed_task is not None:
            logger.info(f"Task {task_id} removed. Remaining tasks: {task_sum}")
            return removed_task
        else:
            logger.warning(f"Task {task_id} not found in task_list")
            return None

    def get_task_count(self) -> int:
        """
        获取当前任务数量

        Returns:
            任务数量
        """
        with self._lock:
            return len(self._task_list)

    def add_fail_task(self, timestamp: float, task_id: str) -> None:
        """
        添加失败任务记录

        Args:
            timestamp: 失败时间戳
            task_id: 任务ID
        """
        with self._lock:
            self._fail_task_list[timestamp] = task_id
        logger.warning(f"Failed task recorded: {task_id} at {timestamp}")

    def get_fail_task_count(self) -> int:
        """
        获取失败任务数量

        Returns:
            失败任务数量
        """
        with self._lock:
            return len(self._fail_task_list)


# 全局任务管理器实例
task_manager = TaskManager()
