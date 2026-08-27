## 为什么

当前 Control Service 按实例 ID 排序后选择第一个尚未达到声明容量的算子实例，导致 `vbas-gpu0` 在 `declared_capacity=128` 时长期独占离线视觉批次，其他健康实例即使没有任务也不会被选择。该行为与已确认的“根据实例当前批次负载选择 VBas”目标冲突，并已在 `test-260827` 真实学生行为任务中产生 GPU0 接收全部 108 个成功批次、GPU1/GPU2 零批次的证据，因此必须在继续里程碑 2B 极限 Campaign 前修正。

## 变更内容

- 将公共算子租约选择从“按实例 ID 排序的首次适配”改为原子的实时负载优先选择：以 Redis 活跃租约和算子上报 `inflight` 的较大值计算实例负载，以声明容量归一化；最低负载实例优先，同负载实例按共享容量池轮询。
- `declared_capacity` 只作为实例准入硬上限，不再表示必须先填满排序靠前实例才能使用后续实例；同一实例的全部 capability 继续共享总容量。
- VBas 将 `BatchAdmissionController.running_batches` 作为心跳 `reported_inflight` 的准确来源；工作租约继续携带 `task_id`、`batch_id` 和来源服务等受控归属信息。
- VBas 本地与三卡部署配置统一为 `max_concurrent_requests=1024`、`MaxConcurrentBatches=1024`、`MaxQueueSize=0`；本变更按已确认值实施，不以本次变更重新评估该数值的性能合理性。
- Vision Orchestrator 保持 `max_batch_size=8`，把 `[vbas].max_concurrency` 调整为 `16` 并明确为服务级共享的全局 VBas 请求并发，而不是每个课程分别拥有 16 个槽位。
- 让现有 `[worker].concurrency` 真正约束同时处理的视觉课程命令，并保证 Kafka offset 不越过未完成消息、服务停止不领取新任务、失败或重启不丢失已接受命令。
- VBas 继续不启用本地队列；容量不足由 Control Service 与 Vision Orchestrator 统一等待和重选，不能在某个 VBas 实例内部隐藏排队。
- 修订 `LOAD-007` 及相关测试/Harness：不再允许排序第一实例长期独占，要求相同健康和容量条件下无实例持续饥饿，并保留每批租约选择、三个实例批次计数和 GPU 活跃证据。
- 在 `192.168.29.11` 使用构建缓存发布同一新 Git SHA；新版本通过健康、注册、真实推理和 20 个不同 `task_id` 的 `STUDENT_BEHAVIOR` 并发提交验证后，按完整容器 ID 和镜像 ID 精确删除被替代的旧容器/旧镜像，保留基础镜像、BuildKit 缓存、数据卷、模型、`/data/result` 和历史报告。
- 在离线 20 任务验证之外，单独通过 Online Gateway 执行 `IMG-VBAS-1000`：同时下发 1000 个合法单图 `POST /api/online/vbas/analyze` 请求，验证请求经公共租约路由分散到三个 VBas 实例，全部响应、实例计数和租约最终收敛均形成证据。
- 增加更高压力的混合场景 `MIXED-VBAS-OFF20-ONLINE1000`：20 个不同 `task_id` 的离线 `STUDENT_BEHAVIOR` 任务进入真实 VBas 批次执行后，同时注入 1000 路 Online Gateway 单图请求，验证在线与离线共享三实例容量、均衡选择、无固定实例独占、无调用方持续饥饿和最终收敛。
- 保持 A 服务的 `POST /api/course-jobs` 路径、请求字段、响应字段、整数状态、查询方式和异步语义不变；A 服务不需要适配本变更。

## 能力范围

### 新增能力

- `live-load-operator-routing`：定义基于活跃租约与实例上报负载的原子加权最少负载选择、同负载轮询、共享容量池和无饥饿要求。
- `bounded-concurrent-vision-scheduling`：定义视觉课程 Worker 并发、服务级 VBas 全局并发、批次租约生命周期、Kafka offset 安全和无 VBas 本地队列边界。
- `vbas-three-gpu-balanced-release-validation`：定义 `192.168.29.11` 的新 SHA 缓存构建、三实例配置、20 个离线任务、1000 路在线 VBas 并发、两类负载重叠验证、证据和旧版本精确清理合同。

### 调整能力

无。相关租约、Campaign 和 `LOAD-007` 要求仍位于尚未归档的活动变更中；本变更以独立能力明确覆盖其中“允许确定性实例偏向”的后续范围调整，并保留历史证据原文。

## 影响范围

- `algorithm-scheduling-platform/packages/platform_common/redis_operator_registry.py`：原子实例评分、同负载轮询游标、租约创建和容量不足行为。
- `control_service`：公共 Redis 注册表行为、运维快照、测试、README 和路由指标；不改变北向课程接口。
- `vbas`：精确 `running_batches` 心跳、`1024/1024/0` 配置、README 和测试；不改变学生/教师 HTTP 请求响应合同。
- `vision_orchestrator_service`：服务级全局并发 `16`、课程 Worker 并发、Kafka 消费/提交、批次调用与重试、配置注释和测试。
- `online_gateway_service`：不改变北向或算子调用合同；复用既有 `2048` 个出站连接配置执行 1000 路在线 VBas 并发，并补充三实例路由与租约收敛证据。
- `orchestrator_service`：无需改变北向或算子调用合同，但共享公共实例选择语义，必须执行离线共享容量回归。
- `algorithm-scheduling-platform/deploy` 与 Harness：容量权威、Compose 展开检查、`LOAD-007`、三卡发布、20 任务真实证据和精确旧版本清理。
- 远端 `192.168.29.11`：新 revision 镜像和容器替换；构建缓存保留，旧版本仅在新版本验收通过后按完整 ID 删除。
