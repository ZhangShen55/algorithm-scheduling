## MODIFIED Requirements

### Requirement: ai_quality 必须通过 TIAS HTTP 接口完成推理调度
MUST：以下场景为本要求的强制验收条件。
ai_quality 必须把 TIAS 视为独立 HTTP 服务，课堂质量推理链路不得直接导入 TIAS 的实现模块；当 Kafka Worker 以集群受控模式运行时，也必须保持同样的远程 TIAS 调度语义。

#### Scenario: 远程推理模式启用
- **WHEN** `TiasInferenceMode` 配置为 `remote`
- **THEN** ai_quality 必须调用 TIAS HTTP 推理接口完成学生帧和教师帧分析

#### Scenario: 不存在本地 TIAS 推理模块导入
- **WHEN** ai_quality 运行在远程推理模式
- **THEN** 它不得导入 `app.services.student_behavior_service`、`app.services.teacher_behavior_service` 或迁移后的 `tias.services.*` 作为推理实现

#### Scenario: Worker 集群受控模式启用
- **WHEN** ai_quality-worker 因 `desired_state=RUNNING` 开始消费 Kafka
- **THEN** 它必须继续使用 Redis 中的 TIAS 注册表和远程 TIAS HTTP 接口完成小批次调度

### Requirement: ai_quality 必须输出简洁关键流程日志
MUST：以下场景为本要求的强制验收条件。
ai_quality 必须用简洁中文记录关键流程日志，并使用稳定英文 key 便于检索；Worker 集群受控模式下还必须记录控制状态切换和 Worker 身份。

#### Scenario: Kafka 任务被消费
- **WHEN** ai_quality 消费到 Kafka 任务
- **THEN** 它必须记录 `worker_id`、`task_id`、`course_id`、`student_count`、Kafka topic、partition 和 offset

#### Scenario: TIAS 实例被选中
- **WHEN** ai_quality 把批次调度给 TIAS
- **THEN** 它必须记录 `worker_id`、`task_id`、`batch_id`、`stream_type`、选中的 `instance_id` 和简洁选择原因

#### Scenario: 批次完成或失败
- **WHEN** TIAS 批次调用成功或失败
- **THEN** ai_quality 必须记录耗时、失败类型、失败原因、重试决策和最终任务状态，不输出冗余逐框检测明细

#### Scenario: Kafka offset 被提交
- **WHEN** ai_quality 提交 Kafka offset
- **THEN** 它必须记录 `worker_id`、`task_id`、最终状态、Kafka topic、partition 和 offset

#### Scenario: Worker 控制状态变化
- **WHEN** Worker 读取到新的 `desired_state`
- **THEN** 它必须记录 `worker_id`、旧 actual_state、新 desired_state、控制版本和当前任务 ID

### Requirement: ai_quality 必须通过多 TIAS 端到端联调
MUST：以下场景为本要求的强制验收条件。
ai_quality 必须支持 4 个 TIAS 实例注册后，从 Kafka `classroom_cv_task` 消费多任务并完成远程推理调度；在集群受控模式下，还必须验证 API 控制面和 Worker 执行面的分离。

#### Scenario: Redis 由本地 Docker 提供
- **WHEN** 本地没有可用 Redis
- **THEN** 测试环境可以用 Docker 拉起 Redis，并配置 ai_quality 使用该 Redis 作为 TIAS 共享注册表和 Worker 控制状态存储

#### Scenario: 4 个 TIAS 实例注册成功
- **WHEN** 本地启动 4 个 TIAS 实例
- **THEN** Redis 注册表中必须能看到 4 个可调度实例，且心跳 TTL 持续刷新

#### Scenario: Worker 控制面 resume 后开始消费
- **WHEN** ai_quality-api 将 `desired_state` 设置为 `RUNNING`
- **THEN** 已启动的 ai_quality-worker 必须开始或继续消费 `classroom_cv_task`

#### Scenario: Worker 控制面 pause 后停止拉新消息
- **WHEN** ai_quality-api 将 `desired_state` 设置为 `PAUSED`
- **THEN** ai_quality-worker 必须停止 poll 新 Kafka 消息，并继续上报心跳

#### Scenario: Worker 控制面 drain 后优雅暂停
- **WHEN** ai_quality-api 将 `desired_state` 设置为 `DRAINING`
- **THEN** ai_quality-worker 必须完成当前课程任务后停止拉新消息，并保持 offset 提交口径正确

#### Scenario: 多 API 实例控制一致
- **WHEN** 通过 Nginx 随机访问任意 ai_quality-api 实例修改 Worker 控制状态
- **THEN** 所有 ai_quality-worker 必须读取同一个 Redis 控制状态并按该状态行动

#### Scenario: 多任务最终完成
- **WHEN** Worker 处于 RUNNING 且消费到课堂视觉任务
- **THEN** 每个任务必须写入 `lesson_ai_workflow` 最终状态、行为时间线、核心快照、学生行为统计和指标得分结果，并按 offset 策略提交 Kafka offset

### Requirement: TIAS 必须集中管理部署资产
TIAS MUST 在服务目录下提供 `docker/` 部署目录，用于集中保存 TIAS 推理服务部署相关文件。

#### Scenario: TIAS docker 目录存在
- **WHEN** 查看 `tias/docker/`
- **THEN** 目录必须包含 TIAS 镜像构建、compose 示例、环境变量示例和运行说明

#### Scenario: 多实例部署示例
- **WHEN** 需要本地或测试环境启动多个 TIAS 实例
- **THEN** `tias/docker/` 必须提供多实例端口、instance_id、并发和队列配置示例

#### Scenario: 旧 CUDA 11.3 镜像迁移
- **WHEN** 现有脚本仍引用基于 CUDA 11.3 + Python 3.8 的 `tias/Dockerfile_cuda113` 或迁移后的 `tias/docker/Dockerfile.cuda113`
- **THEN** 运行文档必须明确该镜像与要求 Python 3.10 及以上的统一算子注册客户端不兼容，并引导当前 VBas 普通部署使用 `docker/Dockerfile`、安全部署使用 `docker/Dockerfile.runtime`

#### Scenario: 安全镜像单实例启动
- **WHEN** 使用 `docker/docker-compose.gpu.secure.yml` 渲染当前 VBas 部署配置
- **THEN** 配置必须只包含一个监听 8981 的 TIAS 服务，不覆盖安全镜像默认 `ENTRYPOINT`/`CMD`，并以一个 Uvicorn worker 和稳定进程名启动
