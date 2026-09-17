"""
数据库初始化（启动期幂等执行）

设计原则：
1. 幂等：可重复执行，第 N 次启动 = 第 1 次启动
2. 只增不减：只创建索引/结构，绝不 drop/delete 任何业务数据
3. 失败容忍：单步失败不阻塞服务启动，仅打 ERROR 日志告警
4. 零写业务数据：本模块永远不会写入 persons / api_call_logs 等业务集合

调用入口：app/main.py 的 lifespan 启动阶段
"""
from typing import Any
from pymongo.errors import DuplicateKeyError, OperationFailure
from app.core.logger import get_logger

logger = get_logger(__name__)


async def ensure_persons_indexes(db: Any) -> None:
    """
    确保 persons 集合的关键索引就绪。

    索引清单：
    - number: **唯一**索引（防止并发录入产生重复，业务上 number 是身份标识）
    - name:   普通索引（加速 /persons/search 的 name 模糊查询）

    实现细节：
    - `create_index` 本身是幂等的：同名索引已存在则直接返回，不会重建
    - 使用 background=True 异步建索引，不阻塞集合的读写
    - 若库内已存在重复 number（脏数据），unique 索引创建会抛 DuplicateKeyError，
      此时降级为普通索引并打 WARNING，让运维介入清理
    """
    # ---------- number 唯一索引 ----------
    try:
        await db["persons"].create_index(
            "number",
            unique=True,
            background=True,
            name="idx_number_unique",
        )
        logger.info("[DBInit] persons.number 唯一索引已就绪")
    except (DuplicateKeyError, OperationFailure) as e:
        # 库内已有重复 number → 降级为普通索引
        logger.error(
            f"[DBInit] persons.number 唯一索引创建失败（疑似库内有重复 number）：{e}"
        )
        try:
            await db["persons"].create_index(
                "number",
                background=True,
                name="idx_number",
            )
            logger.warning(
                "[DBInit] 已降级为普通索引（非唯一）。请运维清理重复数据后，"
                "执行 db.persons.dropIndex('idx_number') 并重启服务以重建唯一索引"
            )
        except Exception as e2:
            logger.error(f"[DBInit] persons.number 普通索引创建也失败：{e2}")
    except Exception as e:
        logger.error(f"[DBInit] persons.number 索引创建未知错误：{e}")

    # ---------- name 普通索引（搜索加速）----------
    try:
        await db["persons"].create_index(
            "name",
            background=True,
            name="idx_name",
        )
        logger.info("[DBInit] persons.name 索引已就绪")
    except Exception as e:
        logger.error(f"[DBInit] persons.name 索引创建失败：{e}")


async def init_database(db: Any) -> None:
    """
    数据库总初始化入口（lifespan 调用）。

    职责：
    - 仅做结构性初始化（建索引）
    - 不写入、不删除任何业务数据
    - 任何步骤失败都不会抛出异常,确保服务能正常启动
    """
    logger.info("[DBInit] 开始数据库初始化...")
    try:
        await ensure_persons_indexes(db)
    except Exception as e:
        # 兜底：理论上不会走到这里（每个子函数都自己 try 了）
        logger.error(f"[DBInit] 索引初始化阶段抛出未捕获异常：{e}", exc_info=True)
    logger.info("[DBInit] 数据库初始化完成")
