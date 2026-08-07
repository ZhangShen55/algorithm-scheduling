## Why

`build-algorithm-scheduling-platform` 已实现大量独立组件并通过单元/数据库测试，但复审发现其 70/70 状态高估了可部署完成度：两个 Worker 入口尚未装配 Kafka 和执行循环，验收测试直接伪造节点完成，部分部署、清理、审计和指标能力也未进入真实运行路径。现在需要建立可重复的 Harness 证据链，并补齐从 A 提交到真实 Worker/算子替身/结果查询的运行闭环。

## What Changes

- 为 `orchestrator-service` 装配真实 Kafka Producer/Consumer、Outbox Publisher、管道初始化、节点领取、容量租约、媒体准备、算子适配和任务终态汇总循环。
- 为 `vision-orchestrator-service` 装配 Kafka 消费/发布、实际 `VisualAnalyzer` 组合实现、抽帧、缓存、自适应扫描、VBas 调用、聚合和结果写入循环。
- 增加四个平台服务的可部署 Compose、Kafka topic/bootstrap 配置、优雅启动/停止和依赖健康检查。
- 将算子注册客户端的版本化 wheel 安装纳入八个算子镜像构建，而不是依赖人工 editable install 或 `PYTHONPATH`。
- 将算子注册/心跳/排空的重要事实持久化到 PostgreSQL，Redis 继续只负责 TTL 运行态和容量租约。
- 将任务终态汇总、`/data/course/{task_id}` 清理、审计日志和 Prometheus 指标接入真实执行路径。
- 修复 online gateway 共享 `httpx.AsyncClient` 的生命周期关闭。
- **BREAKING（PPT 内部算子协议）**：`ppt_slice` 不再逐张通过 Base64 回调图片；算子将切片直接写入 `/data/result/{task_id}/ppt`，原子发布 `manifest.json`，并仅向 `orchestrator-service` 发送一次终态回调。该接口只供调度平台内部调用，不保留旧调用方式。
- 建立 `algorithm-scheduling-platform/AGENTS.md`，只记录长期架构边界、入口、依赖、禁止事项和强制验证门槛。
- 建立 `algorithm-scheduling-platform/harness/`，详细记录调整台账、设计-实现证据矩阵、验收命令、环境前提和已知限制。
- **BREAKING（完成度口径）**：只有真实 Kafka broker、真实 PostgreSQL/Redis、平台常驻进程和契约一致算子替身贯通时，才允许标记端到端验收完成；直接调用 repository 完成节点不再算端到端。

## Capabilities

### New Capabilities

- `platform-runtime-wiring`: 四个平台服务的常驻运行时、Kafka、节点执行、视觉执行、终态汇总和优雅停止。
- `platform-deployment-closure`: 平台 Compose、算子 wheel 安装、依赖健康检查和单机可重复部署。
- `architecture-harness-governance`: 分层 `AGENTS.md`、Harness 调整台账、证据矩阵和完成度门禁。

### Modified Capabilities

无。原能力尚未同步到主规格目录，本变更以补齐运行闭环的新能力描述为准，不重写 A 面和算子业务协议。

## Impact

- 影响 `algorithm-scheduling-platform/services/*/main.py`、Kafka/runtime 公共包、节点执行与视觉组合层、配置、指标、审计、清理和测试。
- 影响 `deploy/` 的平台/基础设施/算子 Compose 与八个算子 Dockerfile。
- 新增 Kafka Python 客户端依赖和真实 broker 集成测试环境。
- 新增平台级 `AGENTS.md` 与 `harness/`，根 `AGENTS.md` 只增加平台项目地图和跨项目边界。
- A 面字段、`/api` 路径、HTTP 200 + 业务码、除 PPT 内部任务协议外的现有算子推理协议和 `vbas` 标识保持不变。
