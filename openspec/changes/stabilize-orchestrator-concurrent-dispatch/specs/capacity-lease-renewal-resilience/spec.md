## ADDED Requirements

### Requirement: 瞬时续租异常在 TTL 安全窗口内有限重试
Orchestrator、Vision Orchestrator、Online Gateway 和 PPT 异步容量 keeper SHALL 对同一 `lease_id` 的瞬时 `NetworkError`、`RemoteProtocolError` 和可恢复超时执行配置化有限重试；单次瞬时错误不得立即取消工作。

#### Scenario: 首次续租 ReadError 后恢复
- **WHEN** 长任务第一次续租收到 `httpx.ReadError`，且在最近确认租约到期前的安全窗口内第二次续租成功
- **THEN** 原算子调用或在线会话 SHALL 继续，租约 `expires_at` 被刷新，任务不得因首次错误进入失败终态

#### Scenario: 续租重试不得越过到期安全线
- **WHEN** 瞬时错误持续到最近确认租约的安全余量耗尽
- **THEN** 调用方 MUST 停止继续使用该租约，并进入对应工作类型的受控终止或重排流程

### Requirement: 确认租约丢失与结果不确定必须区分
明确的租约不存在、lease_id 不一致或不可恢复响应 SHALL 被视为确认丢失；网络读取失败等无法确认服务端是否续租的情况 SHALL 被视为结果不确定并按相同幂等 lease_id 重试。

#### Scenario: Control 返回租约不存在
- **WHEN** 续租接口明确返回该 lease_id 不存在
- **THEN** 调用方 MUST 不再把实例视为被该租约独占，并 SHALL 按工作类型产生可诊断的恢复结果

#### Scenario: 续租响应在服务端成功后丢失
- **WHEN** 调用方收到读取错误但 Control 可能已经完成同一 lease_id 的续租
- **THEN** 调用方 SHALL 重试同一 lease_id，不得申请第二个租约或立即启动重复任务

### Requirement: 不同工作类型按幂等边界收敛续租失败
续租最终失败时，普通离线节点、OCR 工作项、PPT 异步任务、视觉批次和在线请求/会话 MUST 使用各自的幂等与恢复合同，且不得扩大为服务级停止。

#### Scenario: 普通 ASR 节点续租最终失败
- **WHEN** ASR 容量租约在安全窗口内始终无法确认续租
- **THEN** Orchestrator SHALL 取消当前调用、幂等释放租约并把可幂等节点放回状态 30，而不是因单次传输异常直接写为状态 70

#### Scenario: OCR 部分工作项续租最终失败
- **WHEN** 一个 `ppt_image_id` 的 OCR 租约最终失败
- **THEN** 已完成图片结果 MUST 保留，当前未完成工作项 SHALL 使用相同稳定标识重排，不得重复整个 PPT OCR 节点的已完成图片

#### Scenario: PPT Slice 异步租约续租最终失败
- **WHEN** PPT 算子已经受理确定性 `operator_task_id`，但异步租约无法继续续租
- **THEN** Orchestrator SHALL 保留既有算子身份并进入 manifest/终态对账，不得重新提交第二个后台切片任务

#### Scenario: 在线长会话续租最终失败
- **WHEN** 实时 ASR WebSocket 的容量租约确认丢失
- **THEN** Online Gateway SHALL 只结束并报告该会话，释放本地资源且不影响其他在线图片请求或 ASR 会话

#### Scenario: 视觉批次续租最终失败
- **WHEN** Vision Orchestrator 的一个 VBas 批次租约确认丢失
- **THEN** 仅该批次进入有界重排或课程级错误处理，其他课程和批次继续运行

### Requirement: 租约释放保持幂等且不逆转业务终态
调用方 SHALL 在成功、失败、取消和超时路径释放租约；释放返回租约不存在可视为已释放，瞬时释放失败由 TTL 最终回收并记录指标，不得逆转已经持久化的节点或任务终态。

#### Scenario: 业务终态后释放响应丢失
- **WHEN** 节点成功终态已经提交，但释放请求发生瞬时网络错误
- **THEN** 节点 MUST 保持成功，系统记录释放失败并依赖后续幂等释放或 TTL 回收，不能把节点改成失败

### Requirement: 租约韧性具有一致配置和可观测性
三个平台调用服务和 PPT 异步 keeper SHALL 提供语义一致的续租尝试次数、退避、安全余量和错误分类配置，并记录 requested/renewed/retry/recovered/lost/released/release_failed 指标。

#### Scenario: 跨服务配置检查
- **WHEN** 验证 Orchestrator、Vision Orchestrator 和 Online Gateway 的 `config.toml`
- **THEN** 每个服务 SHALL 释放带中文注释的租约续租韧性配置，并拒绝重试总时长越过 TTL 安全边界的无效组合

#### Scenario: 日志脱敏
- **WHEN** 续租或释放发生错误
- **THEN** 日志 MAY 包含 lease_id、capability、instance_id、受控工作标识、异常类型和尝试次数，但 MUST NOT 包含媒体 Base64、完整 URL、完整 ASR/OCR 文本、embedding、请求正文或凭据

### Requirement: 续租修复不改变容量和路由合同
续租重试 MUST 使用原租约和原实例，不得绕过 Control Service、重复计算声明容量或回退公共最少负载路由算法。

#### Scenario: 瞬时续租错误期间的容量归属
- **WHEN** 一个租约正在安全窗口内重试续租
- **THEN** Control 中该租约仍有效时 SHALL 继续计入原实例负载，调用方不得同时申请第二个实例租约
