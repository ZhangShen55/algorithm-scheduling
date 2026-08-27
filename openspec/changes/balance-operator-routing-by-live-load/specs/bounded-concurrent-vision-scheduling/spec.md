## ADDED Requirements

### Requirement: VBas 配置必须使用已确认的批次容量语义
VBas 本地和里程碑 2B 三卡配置 MUST 使用 `max_concurrent_requests=1024`、`MaxConcurrentBatches=1024` 和 `MaxQueueSize=0`。平台注册的 `declared_capacity` MUST 为 `1024`；一个包含最多 8 张图片的 VBas 批次 SHALL 只占一个租约槽。

#### Scenario: 八张图片只占一个租约
- **WHEN** Vision Orchestrator 发送一个包含 8 个 `ImageList` 项的学生行为批次
- **THEN** Control Service 活跃租约和 VBas `running_batches` MUST 各增加 `1`，不得按 8 个请求计数

#### Scenario: VBas 不在本地排队
- **WHEN** VBas 已达到 `MaxConcurrentBatches` 且收到新批次
- **THEN** `MaxQueueSize=0` MUST 使实例明确拒绝该批次，`queued_batches` 保持 `0`，不得隐藏等待任务

### Requirement: VBas 必须上报真实运行批次数
VBas 注册运行时 SHALL 以 `BatchAdmissionController.running_batches` 作为 `/ops/status` 和心跳的 `reported_inflight` 来源。上报值 MUST 为有限非负整数，并 MUST 在批次进入和离开时反映真实状态。

#### Scenario: 批次执行期间心跳反映在途数量
- **WHEN** VBas 正在执行 N 个已准入批次并发生心跳
- **THEN** Control Service 的实例快照 MUST 显示 `reported_inflight=N`，且日志不得包含图片字节或完整检测结果

#### Scenario: 批次完成后上报归零
- **WHEN** 实例全部已准入批次成功、失败或取消并完成清理
- **THEN** 后续心跳 MUST 把 `reported_inflight` 更新为 `0`

### Requirement: Vision 必须共享全局十六个 VBas 批次槽位
Vision Orchestrator SHALL 使用 `max_batch_size=8` 和 `max_concurrency=16`。十六个并发槽位 MUST 在服务启动时创建并由全部课程、流类型和区域轮次共享，禁止每个课程分别创建 16 个槽位。

#### Scenario: 两个课程共享十六个槽位
- **WHEN** 两个并发学生行为课程各有超过 16 个待执行批次
- **THEN** Vision Orchestrator 同时持有的 VBas 请求槽位总数 MUST 不超过 `16`，且两个课程都能在有界时间内取得槽位

#### Scenario: 单课程可同时使用三个实例
- **WHEN** 单个课程至少有三个待执行批次且三个 VBas 实例健康空闲
- **THEN** 全局并发控制 SHALL 允许至少三个批次同时申请租约，公共路由 MUST 将首次三个租约分散到三个实例

#### Scenario: 课程批次失败不取消其他课程
- **WHEN** 一个课程的某批次发生不可恢复的响应或解析错误
- **THEN** Vision Orchestrator MUST 取消该课程尚未开始的批次并写入对应节点失败终态，不得取消其他课程已经取得或等待的槽位

### Requirement: 视觉课程 Worker 并发必须真正生效
Vision Orchestrator SHALL 使用 `[worker].concurrency` 作为同时处理视觉课程命令的有界上限，并 MUST 按 Kafka partition 只提交连续完成的 offset。达到上限时 MUST 停止无界领取，服务关闭或崩溃时未完成消息 MUST 保持可重放。

#### Scenario: 多课程命令有界并发
- **WHEN** Kafka 中存在多于 `worker.concurrency` 个有效视觉命令
- **THEN** 同时执行的课程数 MUST 不超过配置值，剩余命令 SHALL 保持在 Kafka 或有界缓存中

#### Scenario: 后一消息先完成不得越过前一消息
- **WHEN** 同一 partition 的较高 offset 课程先完成而较低 offset 仍在处理
- **THEN** Consumer MUST 不提交越过较低未完成 offset 的位置，避免服务重启后丢失课程

#### Scenario: 停机保留未完成消息
- **WHEN** Vision Orchestrator 收到停止信号且在关闭超时内仍有课程未完成
- **THEN** 服务 MUST 停止领取新消息，不提交未完成 offset，并在重启重放时依靠节点事实避免重复结果

### Requirement: 容量不足必须由视觉调度层重选或等待
Vision Orchestrator SHALL 仅在取得容量租约后调用 VBas。Control 返回无可用容量时 MUST 有界等待；VBas 返回过载时 MUST 释放当前租约并重新选择，不得在无进展条件下持续选择同一个拒绝实例，也不得把容量等待直接伪装成课程成功。

#### Scenario: 其他实例空闲时重新选择
- **WHEN** 已选择实例拒绝批次而另一个匹配实例仍有可用容量
- **THEN** Vision Orchestrator MUST 释放原租约并让下一次原子选择命中当前负载更低的实例

#### Scenario: 全部实例无容量时等待
- **WHEN** 三个实例均达到声明容量
- **THEN** Vision Orchestrator MUST 保持当前命令未提交并按有界间隔重试，停止信号到达时可取消等待且不泄漏租约
