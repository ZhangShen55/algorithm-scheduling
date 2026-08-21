from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, create_engine, text

from ..core.config import ControlSettings

RETIRED_NODE_CODES = ("PPT_KEYWORDS", "COURSE_OVERVIEW")
ACTIVE_TASK_TYPE_STATUSES = (10, 20, 30, 40, 50)

EXIT_OK = 0
EXIT_ACTIVE_RETIRED_NODES = 20
EXIT_RUNTIME_ERROR = 21


@dataclass(frozen=True, slots=True)
class ActiveRetiredNode:
    task_id: str
    task_type: str
    task_type_status: int
    node_id: int
    node_code: str
    node_status: int


class ActiveRetiredNodesError(RuntimeError):
    def __init__(self, nodes: tuple[ActiveRetiredNode, ...]) -> None:
        self.nodes = nodes
        detail = "; ".join(
            f"task_id={node.task_id}, task_type={node.task_type}, "
            f"task_status={node.task_type_status}, node_id={node.node_id}, "
            f"node_code={node.node_code}, node_status={node.node_status}"
            for node in nodes
        )
        super().__init__(f"发现活动退役节点，禁止切换: {detail}")


def find_active_retired_nodes(engine: Engine) -> tuple[ActiveRetiredNode, ...]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT task_type.task_id,
                       task_type.task_type,
                       task_type.status AS task_type_status,
                       node.id AS node_id,
                       node.node_code,
                       node.status AS node_status
                FROM task_nodes AS node
                JOIN course_task_types AS task_type
                  ON task_type.id = node.course_task_type_id
                WHERE node.node_code IN ('PPT_KEYWORDS', 'COURSE_OVERVIEW')
                  AND task_type.status IN (10, 20, 30, 40, 50)
                ORDER BY task_type.task_id, task_type.task_type, node.id
                """
            )
        ).mappings()
        return tuple(
            ActiveRetiredNode(
                task_id=str(row["task_id"]),
                task_type=str(row["task_type"]),
                task_type_status=int(row["task_type_status"]),
                node_id=int(row["node_id"]),
                node_code=str(row["node_code"]),
                node_status=int(row["node_status"]),
            )
            for row in rows
        )


def assert_no_active_retired_nodes(engine: Engine) -> tuple[ActiveRetiredNode, ...]:
    nodes = find_active_retired_nodes(engine)
    if nodes:
        raise ActiveRetiredNodesError(nodes)
    return nodes


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="切换七算子 DAG 前检查 PostgreSQL 中是否仍有活动退役节点",
    )
    parser.add_argument(
        "--config-path",
        type=Path,
        help="Control Service config.toml 路径；未指定 DSN 时读取",
    )
    parser.add_argument(
        "--postgres-dsn",
        help="显式 PostgreSQL SQLAlchemy DSN；优先于配置文件",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    engine: Engine | None = None
    try:
        dsn = arguments.postgres_dsn
        if not dsn:
            dsn = ControlSettings.load(arguments.config_path).postgres.dsn
        engine = create_engine(dsn)
        active_nodes = find_active_retired_nodes(engine)
    except Exception:
        print(
            "退役节点切换预检执行失败：无法读取 Control 配置或查询 PostgreSQL",
            file=sys.stderr,
        )
        return EXIT_RUNTIME_ERROR
    finally:
        if engine is not None:
            engine.dispose()

    if active_nodes:
        print(str(ActiveRetiredNodesError(active_nodes)), file=sys.stderr)
        return EXIT_ACTIVE_RETIRED_NODES
    print("退役节点切换预检通过：不存在所属任务状态为 10 至 50 的退役节点")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
