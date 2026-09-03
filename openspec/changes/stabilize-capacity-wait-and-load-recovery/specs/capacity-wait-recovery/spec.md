## ADDED Requirements

### Requirement: Vision 租约申请应从瞬时 Control 故障恢复
Vision Orchestrator SHALL 在离线 VBas 批次申请租约时，对容量不足、Control 连接错误、连接超时及
HTTP 502/503/504 执行有累计时限的退避重试，并 SHALL 在重试期间保持节点为运行态。确定性 4xx、
非法租约响应或累计时限耗尽 MUST 形成非空中文失败原因。

#### Scenario: Control 短暂重启后继续视觉节点
- **WHEN** 视觉节点正在处理且 Control Service 在租约申请期间不可用 5 至 30 秒后恢复
- **THEN** Vision SHALL 使用原 `task_id` 和稳定 `batch_id` 取得租约并继续处理，节点不得因单次连接错误进入失败终态

#### Scenario: 容量暂不可用时等待
- **WHEN** Control 明确返回目标 offline pool 暂无容量
- **THEN** Vision SHALL 在同一累计等待预算内退避重试，且未取得租约时不得调用 VBas

#### Scenario: 确定性租约错误快速失败
- **WHEN** Control 返回不可重试的 4xx 或租约响应缺少必要字段
- **THEN** Vision MUST 停止重试并持久化包含失败阶段和规范化异常类型的非空中文原因

### Requirement: Vision 恢复过程应保持逻辑工作幂等
Vision 的租约重试 SHALL 不改变课程节点运行代次、帧集合或逻辑批次身份，并 SHALL 复用现有终态
事件与 Kafka 重放边界。

#### Scenario: 多次租约重试不生成重复批次
- **WHEN** 同一帧批次经历多次容量不足或 Control 连接失败
- **THEN** 成功调用 VBas 时 SHALL 仍使用同一个稳定 `batch_id`，数据库和日志不得出现第二个逻辑工作

#### Scenario: 最终失败发布视觉终态事件
- **WHEN** 租约等待预算耗尽并将视觉节点置为失败
- **THEN** Vision SHALL 发布既有视觉失败终态事件，使 Orchestrator 推进任务终态和媒体清理

### Requirement: Online Gateway 应使用单一端到端时间预算
Online Gateway SHALL 为每个同步在线请求建立单一 monotonic deadline；容量等待、Control 重试、
VBas 重选和 VBas 调用 MUST 共享该预算，任何一次尝试不得重新开始完整预算。

#### Scenario: 在线容量不足时在 Gateway 等待
- **WHEN** 200 个请求同时到达且三个 VBas 的 online pool 总容量为 72
- **THEN** 最多 72 个请求 SHALL 持有在线租约，其余请求 SHALL 在 Gateway 等待释放，不得仅因瞬时容量不足立即返回 A 服务

#### Scenario: 总预算限制所有重试
- **WHEN** 请求经历容量等待和多次 VBas 重试
- **THEN** 请求总处理时间 MUST 不超过配置的端到端预算加允许的调度误差，且每次下游超时 MUST 受剩余预算约束

#### Scenario: 上游取消请求
- **WHEN** A 服务在 Gateway 等待或调用 VBas 期间取消连接
- **THEN** Gateway SHALL 取消后续工作并释放已取得租约，不得留下活动租约或后台调用泄漏

### Requirement: 在线容量应与 VBas 实际处理槽一致
VBas 向 Control 注册的 online pool 容量 SHALL 等于 `MaxConcurrentOnlineRequests`，不得包含
`MaxQueueOnlineSize`；Gateway MUST 在取得 online pool 租约后才调用 VBas。

#### Scenario: 24 加 24 配置的平台注册容量
- **WHEN** 单个 VBas 配置 `MaxConcurrentOnlineRequests=24` 和 `MaxQueueOnlineSize=24`
- **THEN** Control 可发放的该实例在线租约上限 SHALL 为 24，而不是 48

#### Scenario: VBas 内部拒绝触发重选
- **WHEN** 已取得租约的 VBas 因瞬时竞争返回 HTTP 429 或 503
- **THEN** Gateway SHALL 释放租约、记录实例拒绝并在剩余预算内重新申请实例，不得立即把过载错误返回 A 服务

### Requirement: 在线纯推理错误应进行有限重试
Gateway SHALL 对 VBas 建连失败、连接复位、可恢复协议错误、HTTP 429/502/503/504 和响应读取超时
执行有限重试；每次重试 MUST 使用相同业务身份和请求数据、释放旧租约并重新选择实例。参数错误、
图片校验错误和确定性 400/422 MUST NOT 重试。

#### Scenario: VBas 建连失败后切换实例
- **WHEN** 选中实例在建立连接前不可用且仍有总预算
- **THEN** Gateway SHALL 释放旧租约并申请另一个可用实例，成功后返回原算子响应

#### Scenario: VBas 读取超时有限重试
- **WHEN** 纯图片推理超过单次读取时限但总预算尚未耗尽
- **THEN** Gateway SHALL 使用相同 `TaskID` 和 `ImageID` 最多执行配置次数的重试，不得无限重复推理

#### Scenario: 请求参数错误不重试
- **WHEN** 图片、坐标或请求结构未通过确定性校验
- **THEN** Gateway MUST 立即返回对应参数业务错误，且不得申请新的 VBas 租约

#### Scenario: 三条 VBas 在线路由使用一致恢复语义
- **WHEN** `/online/vbas/person-count`、`/online/vbas/teacher` 或 `/online/vbas/student` 遇到同类可恢复下游错误
- **THEN** Gateway SHALL 对三条路由执行相同的总预算、有限重试、实例重选和租约释放规则，并保持各自原始算子响应

### Requirement: 在线最终错误应反映真实失败阶段
Gateway SHALL 在所有允许重试耗尽后，使用稳定业务码区分容量等待超时、Control 持续不可用、
VBas 连接或协议失败、VBas 响应超时及未知错误；HTTP 状态和响应字段结构 SHALL 保持兼容。

#### Scenario: 真正的容量等待超时
- **WHEN** 在容量等待上限内始终没有 online pool 槽位
- **THEN** Gateway SHALL 返回 HTTP 200 和业务码 `50301`

#### Scenario: Control 持续不可用
- **WHEN** Control 连接或可恢复服务错误持续到恢复预算耗尽
- **THEN** Gateway SHALL 返回 HTTP 200 和业务码 `50302`，不得描述成 VBas 容量不足

#### Scenario: VBas 调用错误耗尽
- **WHEN** VBas 连接或协议错误重试耗尽
- **THEN** Gateway SHALL 返回业务码 `50201`；响应读取超时重试耗尽时 SHALL 返回业务码 `50401`

#### Scenario: 未分类内部异常
- **WHEN** 最终异常不能归入已定义失败阶段
- **THEN** Gateway SHALL 返回业务码 `50000` 并记录脱敏异常类型，`50000` 不得被解释为普通算子过载

### Requirement: 容量等待和重试应可观测且不泄露敏感内容
Vision 与 Online Gateway SHALL 为请求、等待、取得、重选、重试、超时、失败和释放记录结构化指标
与日志，并 MUST NOT 记录 Base64、完整请求或响应、完整识别文本及 embedding。

#### Scenario: 单个失败可完成归因
- **WHEN** 在线请求最终失败
- **THEN** 运维证据 SHALL 能按 `trace_id` 得到 capability、失败阶段、规范化异常类型、attempt、实例 ID、已耗时和最终结果

#### Scenario: 日志保持脱敏
- **WHEN** 系统记录租约或算子调用重试
- **THEN** 日志 SHALL 只记录身份摘要和计数，不得包含原始图片、Base64 或完整算法结果
