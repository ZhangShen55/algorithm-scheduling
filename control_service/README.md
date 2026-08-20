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

算子注册、心跳、生命周期和注销请求必须携带 `X-Operator-Registry-Token`。Control 使用
`operator_registry.management_token` 校验令牌，并按
`operator_registry.trusted_service_urls` 的 `instance_id -> HTTP origin` 精确映射校验注册地址；
健康检查再次核对该映射，不访问算子请求自选的 URL。Compose 通过
`OPERATOR_REGISTRY_TOKEN` 同时覆盖 Control 和全部算子；Canonical Compose 不提供
默认值，必须由调用环境显式传入。`config.toml` 的开发令牌只用于直接本地
调试，部署预检会拒绝它。localhost 部署可用
`CONTROL_OPERATOR_REGISTRY__TRUSTED_SERVICE_URLS` 的 JSON 对象替换默认 Docker 映射。

## 容量租约

Control Service 是算子实例注册和平台容量租约的唯一权威。平台是否还能分发只取决于 Redis
中的有效租约数；算子心跳的 `reported_inflight` 仅用于观测直连请求、心跳时差或漏租约，
不会继续占用已经释放的槽位。一个实例声明的全部 capability 共享同一
`declared_capacity`，不能按能力重复计算容量。

内部调用方可在申请时提交 `work_context`，也可在领取离线节点后通过
`POST /internal/operator-instances/lease/context` 补绑。运维可使用
`GET /ops/operator-instances/{instance_id}/active-leases` 查询当前分配到实例的工作、获取/过期
时间、绑定状态和心跳差异。工作上下文只保存短标识，不保存图片、音频或识别文本。活跃租约
明细只在 Redis 中维护，不为每次申请、续租和释放增加 PostgreSQL 高频写入。

## 数据库迁移

启动 `control-service` 前，必须先按文件名顺序将
`algorithm-scheduling-platform/migrations/` 中尚未执行的 SQL 迁移应用到目标 PostgreSQL。
首次初始化空数据库时从 `0001` 开始；已有数据库只执行待升级的迁移，不要重复执行已应用的文件。

`algorithm-scheduling-platform/scripts/check_migrations.py` 只校验迁移文件的编号和命名，
不会连接数据库或自动执行迁移。`control-service` 启动时也不会自动迁移 schema。

里程碑 2B 的 Canonical 部署会在替换平台容器前执行
`algorithm-scheduling-platform/deploy/scripts/apply-course-task-submission-migration`。
该脚本仅负责当前待发布的 `0006`：字段缺失时在 PostgreSQL 事务中应用迁移，已正确应用时
幂等返回；表不存在或字段类型、非空约束、中文说明不一致时失败关闭。`0001-0005` 仍必须
先按顺序完成，脚本不会猜测或修复未知的历史 schema。

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
