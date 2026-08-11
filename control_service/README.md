# control-service

课程任务接入、状态查询、算子注册和容量管理服务。本服务直接连接 PostgreSQL 和 Redis，不直接连接 Kafka。

## 本地启动

在本服务目录安装公共平台包和服务依赖：

```bash
python -m pip install -e ../algorithm-scheduling-platform
python -m pip install -r requirements.txt
```

```bash
python -m uvicorn app.main:app \
  --host 0.0.0.0 --port 18100 --workers 1
```

默认读取服务根目录的 `config.toml`。`CONFIG_PATH` 可指定其他文件，`CONTROL_`
前缀的环境变量可覆盖 TOML，嵌套字段使用双下划线，例如
`CONTROL_POSTGRES__DSN` 和 `CONTROL_SERVICE__PORT`。

## 数据库迁移

启动 `control-service` 前，必须先按文件名顺序将
`algorithm-scheduling-platform/migrations/` 中尚未执行的 SQL 迁移应用到目标 PostgreSQL。
首次初始化空数据库时从 `0001` 开始；已有数据库只执行待升级的迁移，不要重复执行已应用的文件。

`algorithm-scheduling-platform/scripts/check_migrations.py` 只校验迁移文件的编号和命名，
不会连接数据库或自动执行迁移。`control-service` 启动时也不会自动迁移 schema。

## 健康检查

- `GET /health` 仅表示进程存活。即使 PostgreSQL 或 Redis 暂时不可用，该接口仍返回成功。
- `GET /ops/readiness` 表示服务是否可以接收流量。它检查 PostgreSQL、Redis 以及正式调度
  schema（包括 10 张表、全部预期字段和中文说明、`0005` 索引及状态语义），也会报告尚未补写的算子心跳审计异常；
  任一检查失败时返回 HTTP 503 和具体检查结果。
- PostgreSQL、Redis 和 schema 并行检查。`readiness.dependency_timeout_seconds`
  最少为 2 秒，PostgreSQL 在连接和 SQL 语句之间分配该预算；Redis 探针使用独立连接，不会缩短注册/租约客户端自身的运行超时。
- readiness 不执行或修复数据库迁移，也不检查 Kafka。`control-service` 不直接连接 Kafka。

Compose 使用 `/ops/readiness` 作为 `control-service` 健康探针。因此必须先完成数据库迁移，
否则容器虽然已经启动，仍会保持不健康且依赖它的服务不会开始接收流量。

Docker 构建上下文必须是工作区根目录：

```bash
docker build -f control_service/docker/Dockerfile -t control-service .
```
