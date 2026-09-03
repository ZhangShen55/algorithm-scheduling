## ADDED Requirements

### Requirement: Vision 并发跟随 VBas 离线容量
Vision Orchestrator MUST 将全部课程共享的 VBas 有效批次并发限制为当前可调度 VBas 实例 `offline` 容量之和，并 MUST 继续通过 Control 租约实施最终原子准入和实例选择。

#### Scenario: 三实例各一个离线槽位
- **WHEN** 三个 `ONLINE` 且模型就绪的 VBas 实例分别上报 `capacity_pools.offline=1`
- **THEN** Vision 同时进入 VBas 调用链的批次最多为 3，其他批次在 Vision 内部等待且不向上游返回容量错误

#### Scenario: 实例容量不一致
- **WHEN** 可调度 VBas 实例分别上报不同的正离线容量
- **THEN** Vision 使用各实例离线容量之和，而不是固定配置值或实例数本身

#### Scenario: 实例排空或模型未就绪
- **WHEN** VBas 实例为 `DRAINING`、`OFFLINE` 或 `model_ready=false`
- **THEN** 该实例的离线容量不计入 Vision 有效并发，Control 租约也不得将新批次路由到该实例

### Requirement: Vision 在取得租约后才调用 VBas
Vision Orchestrator MUST 先取得 Control 返回的 `offline` 容量租约，再向租约指定的 VBas 实例发送批次；容量不足时 MUST 在本地或租约等待路径等待，不得无租约持续请求 VBas。

#### Scenario: 离线槽位全部占用
- **WHEN** 所有可调度 VBas 实例的离线槽位均被占用
- **THEN** 待处理批次不调用任何 VBas HTTP 接口，并在槽位或租约释放后继续处理

### Requirement: 视觉逻辑批次身份稳定且无碰撞
Vision Orchestrator MUST 根据任务、流类型、区域和有序帧集合稳定生成逻辑批次 ID；不同工作内容 MUST 使用不同 ID，相同逻辑批次重试 MUST 保持相同 ID。

#### Scenario: 教师粗扫与加密扫描均从首批开始
- **WHEN** 教师粗扫和后续加密扫描分别产生索引为 `0000` 但帧集合不同的批次
- **THEN** 两个批次的完整 batch ID 不同，Control 租约审计可以区分两项工作

#### Scenario: 同一逻辑批次瞬时重试
- **WHEN** 同一有序帧集合因瞬时传输错误重新尝试
- **THEN** 重试使用与首次尝试相同的 batch ID，并使用独立 attempt 日志区分调用次数

#### Scenario: 全画面与区域批次
- **WHEN** 学生同一时间点分别执行全画面、前排和后排推理
- **THEN** 三类批次 ID 不同且均可追溯对应区域身份

### Requirement: 瞬时 VBas 传输故障有限重试
Vision Orchestrator SHALL 对连接、读写、远端断开和超时类瞬时故障执行配置化有限重试，每次重试 MUST 重新申请并释放容量租约；业务错误和响应契约错误 MUST NOT 自动重试。

#### Scenario: 首次连接失败后恢复
- **WHEN** 首次 VBas 调用发生可重试连接错误且下一次调用成功
- **THEN** 节点继续处理并返回成功结果，不因单次连接错误直接进入 `FAILED(70)`

#### Scenario: 瞬时错误达到重试上限
- **WHEN** 同一批次的可重试传输错误达到配置的最大尝试次数
- **THEN** 当前节点按既有失败语义终结，错误原因包含异常类型、实例、batch ID 和尝试次数

#### Scenario: VBas 返回业务失败
- **WHEN** VBas 返回非成功业务状态或不符合约定的结果结构
- **THEN** Vision 不执行传输重试并保留明确业务失败原因

### Requirement: 视觉批次错误原因不可为空
Vision Orchestrator MUST 为所有最终 VBas 批次错误生成非空中文原因；即使底层异常字符串为空，原因也 MUST 包含异常类型和调用上下文。

#### Scenario: 空消息 TimeoutError
- **WHEN** 底层抛出 `TimeoutError()` 且异常消息为空
- **THEN** 节点原因至少包含 `TimeoutError`、任务 ID、batch ID、实例 ID 和尝试次数
