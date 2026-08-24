## Why

里程碑 2B 已经具备七算子、四平台服务和三张 GPU 的基础部署与业务泳道，但现有 217 条反例和 26 条压力/恢复用例仍不能回答“A服务在课程突发、持续轮询、在线图片、实时 ASR 和离线长视频同时到来时能承受多大压力”。目标服务器磁盘已高占用，压测前还必须建立按镜像 ID 精确清理的发布边界，才能在不损伤当前数据和历史证据的前提下完成真实极限验证。

## What Changes

- 建立独立的“A服务极限负载 Campaign”，覆盖离线四类 `task_types`、任务查询、四类在线图片、实时 ASR WebSocket 和人脸库管理。
- 把负载拆成单请求基线、单泳道阶梯、A端突发、多泳道混合、极限过载、长稳和故障恢复七个阶段；极限档包含 1000 任务提交突发、1000 在线图片并发、150 路实时 ASR、1000 QPS 查询以及最多 36 节真实长课程的有界阶梯。
- 建立面向 A 服务的可重放负载数据集、稳定用例 ID、随机种子、负载配置、资源护栏、原始指标和中文聚合报告。
- 将声明容量与真实稳定吞吐分开验证；允许过载时出现明确限流或快速拒绝，但不允许容量超卖、无界排队、任务丢失、跨任务污染或容器失控重启。
- 在混合负载中注入单算子实例停止、单 GPU 实例组停止、四平台服务与 Kafka/Redis 受控重启，验证租约释放、Outbox/Kafka 恢复、WebSocket 重连和任务最终一致性。
- 新增镜像精确清理流程：从容器引用和当前发布标签计算保护集，按完整镜像 ID 生成候选集，审核后删除并记录回收容量；禁止使用宽泛 `docker system prune -a`。
- 当精确镜像清理后 BuildKit 仍持有已删除镜像层且磁盘未越过警戒线时，允许经用户逐次明确批准执行 `docker buildx prune --all --force --keep-storage 100GB`；必须保留缓存与磁盘前后证据，且该例外不扩展到镜像、容器、卷或持久数据清理。
- 交付一份与当前脚本、Compose、`config.toml`、镜像标签、迁移顺序和实际验收结果一致的中文部署手册，覆盖首次部署、升级、回滚、常驻启停、精确清理、日志、数据目录、验收和故障排查。
- 保留现有 217 条反例、26 条压力/恢复用例和 6 项 B 级人工复核的原有语义，本 Campaign 作为额外的真实 A 端负载验证，不用简化压测覆盖或改写历史证据。

## Capabilities

### New Capabilities

- `a-service-extreme-load-validation`: 定义 A 服务全接口、七算子、四平台服务和三 GPU 环境的阶梯、突发、混合、过载、长稳、故障恢复及报告合同。
- `release-image-lifecycle-cleanup`: 定义构建前与新版验证后的镜像保护集、候选集、审核、精确删除和可追溯证据。
- `production-deployment-runbook`: 定义单机三卡正式部署手册的内容、命令可执行性、版本一致性、验收清单和交付边界。

### Modified Capabilities

无。本变更不修改 A 服务接口、任务 DAG、四服务边界、算子协议或已有主规格的业务要求。

## Impact

- `algorithm-scheduling-platform/harness/`：新增 A 服务负载 Campaign、故障注入、数据保护、指标采集、报告聚合和用例目录。
- `algorithm-scheduling-platform/deploy/`：新增正式常驻栈入口、发布前后镜像盘点与精确清理、三卡资源和磁盘护栏。
- `algorithm-scheduling-platform/deploy/算法功能调度平台部署手册.md`：作为七算子、四平台、四中间件和三 GPU 单机交付的唯一中文操作入口。
- `192.168.29.11`：七算子 21 实例、四平台服务、PostgreSQL、Kafka、Redis、MongoDB、`/data/course` 和 `/data/result`。
- A 服务模拟器：仅访问 `control-service:18100` 和 `online-gateway-service:18103`，不绕过平台直连算子。
- 文档与 Harness：记录每阶段负载、任务 ID、Git SHA、镜像 revision、原始指标、证据摘要和中文结论。
