# Control Service 事实闭环实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 OpenSpec `close-platform-runtime-and-harness-gaps` 的 3.1-3.5 完成 `control-service` 真实 PostgreSQL 任务事实、事务 Outbox、Redis 注册容量和 PostgreSQL 算子审计闭环。

**Architecture:** `control-service` 的 lifespan 统一创建并关闭 SQLAlchemy Engine 与 Redis Client，HTTP 路由只从运行时状态取得 Repository/Registry。PostgreSQL 保存课程、任务类型、Outbox 和算子审计事实；Redis 继续作为心跳 TTL、生命周期可路由状态和原子容量租约的实时权威。`/health` 只表示进程存活，`/ops/readiness` 校验 PostgreSQL、Redis、正式调度表和中文 schema 说明；本里程碑不创建 Kafka Producer，也不推进 DAG。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy 2、psycopg 3、redis-py 6、PostgreSQL 17、Redis 7、pytest。

---

### Task 1: 固化里程碑 1 测试矩阵

**Files:**
- Create: `control_service/tests/test_runtime.py`
- Create: `algorithm-scheduling-platform/tests/integration/test_control_service_foundation.py`
- Create: `algorithm-scheduling-platform/tests/integration/test_operator_audit_repository.py`
- Modify: `algorithm-scheduling-platform/tests/test_database_comments.py`

- [x] **Step 1: 写 lifespan 与配置 RED 测试**

验证 `create_app()` 构造阶段不创建 Engine/Redis；进入 lifespan 才按 `pool_size`、`max_overflow`、`pool_timeout_seconds`、`pool_pre_ping`、Redis prefix/TTL/连接池/超时创建资源；退出后只关闭自身持有的资源；显式注入 Repository/Registry 时不创建外部资源。

- [x] **Step 2: 写 readiness 与线程池 RED 测试**

验证 `/health` 在依赖失败时仍为 200；`/ops/readiness` 在 PostgreSQL、Redis、schema 全部健康时为 200，任一失败或 schema 落后时为 503 并给出具体依赖；同步 Repository/Redis 路由使用 FastAPI 同步端点，不阻塞事件循环。

- [x] **Step 3: 写任务与 Outbox 真实 PostgreSQL 测试**

覆盖首次提交、重复 `(task_id, task_type)`、后续追加 task type、一次多类型共享 `submission_id`、URGENT/NORMAL、完整 GET 的整数状态和中文 `reason`。通过临时数据库约束制造 Outbox 写失败，断言 `course_jobs`、`course_task_types`、`outbox_events` 同时回滚；并发重复提交最终只能有一条 task type 和一条 Outbox。

- [x] **Step 4: 写算子审计与 Redis 联合测试**

覆盖注册/重新注册声明、限频心跳摘要、`ONLINE -> DRAINING -> OFFLINE`、注销和历史查询；证明租约/续租/释放热路径不写 PostgreSQL；使用真实 Redis 验证 TTL、排空后拒绝新租约、容量 N、续租和释放恢复容量。

同时覆盖 Redis 边界：并发重注册不能残留旧 capability；心跳与注销不能产生残缺实例；已过期租约不得续租复活；注销必须使旧租约失效；同 ID 重注册不得继承旧租约；容量占用使用 `max(active_leases, reported_inflight)`，避免忽略算子真实并发或重复计数。

- [x] **Step 5: 写 DDL 状态注释 RED 测试**

要求最新前向迁移把状态 `40` 描述为“已排队”、`50` 描述为“处理中”，并继续保证每张正式调度表和字段均有中文注释。

- [x] **Step 6: 运行测试确认 RED**

Run:

```bash
cd algorithm-scheduling-platform
.venv/bin/python -m pytest -q -rs \
  ../control_service/tests/test_runtime.py \
  tests/integration/test_control_service_foundation.py \
  tests/integration/test_operator_audit_repository.py \
  tests/test_database_comments.py
```

Expected: FAIL，原因必须是 lifespan 资源尚未装配、`/ops/readiness`/审计 Repository 尚不存在或状态注释仍错误；不得是测试语法、导入路径或测试数据库误配置。

### Task 2: 建立 lifespan 管理的 ControlRuntime

**Files:**
- Create: `control_service/app/infrastructure/runtime.py`
- Modify: `control_service/app/application/factory.py`
- Modify: `control_service/app/api/control.py`
- Modify: `algorithm-scheduling-platform/packages/platform_common/application.py`
- Modify: `control_service/app/infrastructure/settings_adapter.py`

- [x] **Step 1: 实现资源所有权模型**

`ControlRuntime` 在 `start()` 中创建缺失的 Engine、Redis Client、`CourseRepository` 和 Operator Registry，在 `stop()` 中只关闭它自己创建的资源；注入对象保持调用方所有权。

- [x] **Step 2: 传递完整连接配置**

Engine 使用 `pool_size`、`max_overflow`、`pool_timeout`、`pool_pre_ping`；Redis 使用 `max_connections`、`socket_connect_timeout`、`socket_timeout`；`RedisOperatorRegistry` 使用 `key_prefix` 与 `heartbeat_ttl_seconds`。

- [x] **Step 3: 组合基础 lifespan 与服务 lifespan**

扩展 `create_service_app()` 接收可选服务 lifespan；保持工作目录和指标初始化，并在其内部启动/关闭 `ControlRuntime`。`app.main:app` 导入不得建立网络连接。

- [x] **Step 4: 路由改为运行时解析依赖**

移除 `create_control_app()` 构造期的 `create_engine()` 和 `Redis.from_url()`；所有同步 SQL/Redis/文件路由改为普通 `def`，由 FastAPI 在线程池执行。保留现有路径、字段和响应结构。

- [x] **Step 5: 运行 runtime 单测确认 GREEN**

Run: `cd control_service && ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q tests/test_runtime.py tests/test_service_structure.py`

Expected: PASS。

### Task 3: 增加 PostgreSQL 算子审计 Repository

**Files:**
- Create: `algorithm-scheduling-platform/packages/platform_common/operator_audit_repository.py`
- Create: `control_service/app/infrastructure/audited_operator_registry.py`
- Modify: `control_service/app/infrastructure/runtime.py`
- Modify: `control_service/app/api/control.py`
- Modify: `algorithm-scheduling-platform/packages/platform_common/redis_operator_registry.py`

- [x] **Step 1: 实现声明与事件事务**

`OperatorAuditRepository` 在单个 PostgreSQL 事务中 upsert `operator_instances` 并追加 `REGISTERED`/`REREGISTERED`，保留模型/API 版本、能力、容量、标签和 desired state。

- [x] **Step 2: 实现心跳摘要节流**

只在距上一次 PostgreSQL 摘要达到 `heartbeat_audit_interval_seconds` 时更新 `last_heartbeat_at` 并追加 `HEARTBEAT_SUMMARY`；高频 TTL 刷新仍由 Redis 完成。

- [x] **Step 3: 实现生命周期、注销和历史**

生命周期变化更新 `desired_state` 并追加 `LIFECYCLE_CHANGED`；注销写 `OFFLINE`、`unregistered_at` 和 `UNREGISTERED`；提供按实例倒序查询历史的方法与 `/ops/operator-instances/{instance_id}/events`。

- [x] **Step 4: 封装审计 Registry**

`AuditedOperatorRegistry` 组合 Redis 实时 Registry 与 PostgreSQL Audit Repository。`lease`、`renew`、`release` 仅访问 Redis；注册、心跳摘要、生命周期和注销同步审计。跨存储失败按“路由安全优先、请求返回失败并允许幂等重试”处理并测试。

注册/切回 ONLINE 先持久化 PostgreSQL intent 再开放 Redis 路由；DRAINING/OFFLINE 先停止 Redis 新租约再写 PostgreSQL；注销按 Redis OFFLINE、PostgreSQL 注销、Redis 删除的顺序执行。心跳先刷新 Redis TTL，再尝试限频审计；审计暂时失败不终止算子心跳循环，但 readiness 记录审计异常并在后续心跳补写。

- [x] **Step 5: 原子化 Redis 实例与租约边界**

用 Lua 合并 register、heartbeat 和 unregister 的读写窗口；租约时间使用 Redis `TIME`。renew 必须确认 lease zset 成员尚未过期、实例心跳有效且 lifecycle 不是 OFFLINE；unregister 删除实例全部 lease；分配容量按 `max(active_leases, reported_inflight)` 计算，DRAINING 允许存量 lease 续约但拒绝新 lease。

- [x] **Step 6: 运行审计测试确认 GREEN**

Run: `cd algorithm-scheduling-platform && .venv/bin/python -m pytest -q -rs tests/integration/test_operator_audit_repository.py tests/test_operator_registry_api.py tests/integration/test_redis_operator_registry.py`

Expected: PASS 且 0 skipped。

### Task 4: 增加真实 readiness 和 schema 校验

**Files:**
- Modify: `control_service/app/infrastructure/runtime.py`
- Modify: `control_service/app/api/control.py`
- Modify: `algorithm-scheduling-platform/deploy/docker-compose.platform.yml`
- Modify: `control_service/README.md`

- [x] **Step 1: 实现依赖检查**

PostgreSQL 执行 `SELECT 1`，Redis 执行 `PING`；schema 检查 10 张正式调度表及其表/字段中文说明。返回结构化 `checks`，任一失败时 `/ops/readiness` 返回 HTTP 503；不检查 Kafka。

- [x] **Step 2: 保持 liveness 语义**

`/health` 继续只返回进程存活，不因依赖临时故障变为 503。

- [x] **Step 3: 更新部署探针**

Control Compose healthcheck 改为 `/ops/readiness`，确保未迁移或依赖不可用时不接流量。

- [x] **Step 4: 运行 readiness 测试确认 GREEN**

Run: `cd algorithm-scheduling-platform && .venv/bin/python -m pytest -q -rs ../control_service/tests/test_runtime.py tests/integration/test_control_service_foundation.py`

Expected: PASS 且 0 skipped。

### Task 5: 补充算子审计索引并修正状态字段中文 DDL 说明

**Files:**
- Create: `algorithm-scheduling-platform/migrations/0005_operator_audit_and_status_comments.sql`
- Modify: `algorithm-scheduling-platform/tests/test_database_comments.py`

- [x] **Step 1: 新增前向迁移**

增加 `operator_instance_events (instance_id, occurred_at DESC, id DESC)` 历史查询索引，并通过 `COMMENT ON COLUMN` 修正 `course_task_types.status` 和 `task_nodes.status`：`40 已排队`、`50 处理中`；不回改 `0004_schema_comments.sql` 作为唯一交付手段。

- [x] **Step 2: 验证迁移顺序和精确注释**

Run: `cd algorithm-scheduling-platform && .venv/bin/python scripts/check_migrations.py && .venv/bin/python -m pytest -q tests/test_database_comments.py`

Expected: PASS。

### Task 6: 真实 PostgreSQL/Redis 联合验收

**Files:**
- Modify: `algorithm-scheduling-platform/harness/change-ledger.md`
- Modify: `algorithm-scheduling-platform/harness/verification.md`
- Modify: `algorithm-scheduling-platform/harness/scenarios/foundation-scheduling-closure.md`
- Modify: `openspec/changes/close-platform-runtime-and-harness-gaps/tasks.md`

- [x] **Step 1: 启动并核对基础设施**

Run:

```bash
cd algorithm-scheduling-platform
docker compose -f deploy/docker-compose.infrastructure.yml up -d postgres redis
docker compose -f deploy/docker-compose.infrastructure.yml ps postgres redis
```

Expected: PostgreSQL 和 Redis 均为 healthy。

- [x] **Step 2: 运行里程碑 1 集成测试**

Run:

```bash
.venv/bin/python -m pytest -q -rs \
  tests/integration/test_course_repository.py \
  tests/integration/test_redis_operator_registry.py \
  tests/integration/test_operator_audit_repository.py \
  tests/integration/test_control_service_foundation.py
```

Expected: PASS 且输出中没有 skipped。

- [x] **Step 3: 运行完整回归与静态检查**

Run:

```bash
.venv/bin/ruff check packages tests ../control_service/app ../control_service/tests
MYPYPATH="$PWD" .venv/bin/python -m mypy packages ../control_service/app
.venv/bin/python -m pytest -q tests ../control_service/tests
.venv/bin/python -m compileall -q packages ../control_service/app
docker compose -f deploy/docker-compose.platform.yml config --quiet
openspec validate close-platform-runtime-and-harness-gaps --strict
```

Expected: 全部 PASS。

- [x] **Step 4: 更新证据和 OpenSpec 进度**

Harness 记录实现前状态、修改文件、真实容器版本、命令、测试数量、0 skipped、已知跨存储一致性边界和“尚未进入里程碑 2/Kafka/DAG”的限制。每完成一项立即将 OpenSpec `3.1` 至 `3.5` 对应复选框改为 `[x]`。

---

## 自检

- 规格覆盖：3.1 对应 Task 1/2/4/6；3.2 对应 Task 1/6；3.3 对应 Task 1/3；3.4 对应 Task 2/3；3.5 对应 Task 1/4/6。
- 边界检查：没有 Kafka Producer、Consumer、DAG、Dispatcher、算子调用或 PPT 接入工作。
- 类型一致性：HTTP 状态、业务响应、`task_id`、`task_types`、`student_count`、`front_points`、`back_point` 和 `vbas` 保持既有契约。
- 测试安全：真实 PostgreSQL 测试只能操作名称明确以 `_test` 结尾的专用数据库；不可对 `algorithm` 或 `postgres` 执行 `DROP SCHEMA`。
