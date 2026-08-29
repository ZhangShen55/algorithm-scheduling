# VBas 单批次容量与基础设施局域网访问验证（2026-08-28）

## 执行边界

- 目标服务器：`192.168.29.11`。
- 本次属于 `balance-operator-routing-by-live-load` 后续运行参数实验，不覆盖该变更历史上冻结的 `1024/1024/0` 与 Vision `8/16` 证据，也不将尚未通过的在线千路和混合负载门禁标记为完成。
- 停止四个平台服务后冻结非终态任务，只删除 `test-260828_55`、`test-260828_56`、`test-260828_57`、`test-260828_58`、`test-260828_59`。
- 保留历史完成/失败任务、`/data/result`、模型、镜像、构建缓存和数据库卷。

## 运行参数

```toml
# orchestrator-service
[worker]
node_concurrency = 16

# vision-orchestrator-service
[worker]
concurrency = 16

[media]
max_concurrent_processes = 6

[scan]
batch_size = 8

[vbas]
max_batch_size = 8
max_concurrency = 3

# 每个 VBas 实例
[platform]
max_concurrent_requests = 1

[TIAS]
MaxConcurrentBatches = 1
MaxQueueSize = 0
```

三个 VBas 实例重新注册后均报告 `declared_capacity=1`、`max_concurrent_batches=1`、`running_batches=0`、`queued_batches=0` 和 `available_slots=1`。

## 任务与缓存清理

- PostgreSQL 删除 5 个 `course_jobs`、20 个 `course_task_types`，节点、结果、工作项和视觉兜底值由外键级联删除。
- 冻结任务没有未发布 Outbox，因此对应 Outbox 删除数为 0。
- Vision 消费组在服务停止期间推进到 `algorithm.visual.commands` 当时的最新 offset，避免已删除任务重放。
- 精确删除五个 `/data/course/test-260828_*` 目录，清理前分别约为 `2.2G/2.1G/1.9G/1.9G/1.9G`，合计约 10 GiB；该缓存不可恢复。
- 清理后数据库非终态任务数为 0；历史数据仍有 12,745 个课程主任务、12,903 个任务类型和 20,921 个任务节点。

## 局域网访问与 Kafka 持久化

- PostgreSQL：`0.0.0.0:5432`。
- Redis：`0.0.0.0:6379`。
- Kafka：`0.0.0.0:9092`，外部广播地址为 `192.168.29.11:9092`，平台容器继续使用 `kafka:29092`。
- 发现 Apache Kafka 4.0.0 默认写入未挂载的 `/tmp/kafka-logs`，旧 Compose 的 `/var/lib/kafka/data` 卷实际没有承载日志；容器重建导致 Kafka 应用主题历史被清空。
- 已补充 `KAFKA_LOG_DIRS=/var/lib/kafka/data` 并再次重建，确认 `meta.properties` 位于持久卷内。该缺陷不影响 PostgreSQL 历史数据，但本次 Kafka 历史消息不可恢复。
- Mac 端使用真实客户端验证：PostgreSQL 登录并查询成功，Redis `PING` 成功，Kafka 元数据返回三个应用主题。

## 终态

- 29/29 容器运行且健康。
- 21/21 算子实例注册，三个 VBas 实例分别绑定 GPU 0/1/2。
- 空闲基线 VBas 显存约为 `1064/1064/972 MiB`。
- PostgreSQL、Redis、Kafka 已可从 Mac 通过 `192.168.29.11` 直接访问。
- 远端备份目录：`/root/workspace/deployment-backups/high-utilization-lan-access-20260828`。
- 尚未执行新参数下的全量课程、显存峰值、GPU 利用率、在线容量不足和吞吐复验，因此本记录只证明部署与空载门禁通过。
