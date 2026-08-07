"""
Task Management Service
任务管理服务
"""
from threading import RLock
from typing import Dict, Optional
from app.models.task import LocalVideoAnalysisTaskObject
from app.core.logger import get_logger

logger = get_logger("task_service")


class TaskManager:
    """任务管理器"""

    def __init__(self):
        self._task_list: Dict[str, LocalVideoAnalysisTaskObject] = {}
        self._fail_task_list: Dict[float, str] = {}
        self._lock = RLock()

    def try_add_task(
        self,
        task_id: str,
        task: LocalVideoAnalysisTaskObject,
        *,
        max_tasks: int,
    ) -> bool:
        """Atomically reserve one worker-local task capacity slot."""
        with self._lock:
            if task_id in self._task_list or len(self._task_list) >= max_tasks:
                return False
            self._task_list[task_id] = task
            count = len(self._task_list)
        logger.info(f"Task added: {task_id}, total tasks: {count}")
        return True

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

    def get_task(self, task_id: str) -> Optional[LocalVideoAnalysisTaskObject]:
        """
        获取任务

        Args:
            task_id: 任务ID

        Returns:
            任务对象或None
        """
        with self._lock:
            return self._task_list.get(task_id)

    def del_task(self, task_id: str) -> Optional[LocalVideoAnalysisTaskObject]:
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
