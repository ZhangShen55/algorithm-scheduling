## ADDED Requirements

### Requirement: v1.0.2 和 v1.0.3 必须限制 VAD 流式状态

`/v1.0.2/seacraft_asr_online` 和 `/v1.0.3/seacraft_asr_online` SHALL 在每 6 个 0.48 秒音频块以内轮换一次项目层 VAD cache；轮换不得改变当前会话的请求格式、响应字段或 `key` 关联关系。v1.0.1 MUST 保持原有 VAD 和 cache 逻辑。

#### Scenario: 长时间连续输入时 VAD cache 轮换

- **WHEN** v1.0.2 或 v1.0.3 收到第 7 个及之后的连续音频块，且此前没有自然 VAD 结束
- **THEN** 服务已在第 6 个块处理完成后释放并重建 VAD cache，后续块不会继续复用第一个窗口的内部历史状态
- **AND** 服务仍发送包含 `key`、`text`、`finished`、`bg`、`ed` 的原有响应对象

#### Scenario: 空闲输入不会无限积累 VAD 状态

- **WHEN** WebSocket 持续收到没有语音的音频块
- **THEN** 服务按不超过 6 个块的周期轮换 VAD cache，即使没有产生语音结果也不会保留无限增长的 VAD 历史

### Requirement: 语音段 PCM 和纠正输入必须有界

v1.0.2 和 v1.0.3 SHALL 只保留最多 6 个块的未开始语音预缓存；语音段达到 42 个块（约 20.16 秒）时 MUST 通过现有最终识别流程完成当前段并发送 `finished=true`，然后释放该段 PCM 和相关模型 cache。自然 VAD 结束需要约 1200 ms 尾静音确认，结束时也 MUST 释放完整段状态。

#### Scenario: 长静音后开始说话

- **WHEN** WebSocket 在语音开始前持续发送超过 6 个静音块，然后检测到语音开始
- **THEN** 句末纠正输入只包含有限的起始预缓存和当前语音段，不包含整个 WebSocket 之前的无限静音历史

#### Scenario: 连续语音达到最大段长

- **WHEN** 一个 v1.0.2 或 v1.0.3 语音段达到 42 个音频块且 VAD 尚未输出结束事件
- **THEN** 服务使用现有对应纠正模型生成最终文本，发送字段格式不变且 `finished=true`
- **AND** 服务释放当前 PCM、在线 ASR cache 和 VAD cache，为后续音频建立新段

#### Scenario: VAD 自然结束

- **WHEN** VAD 输出结束事件且当前存在 `segment_key`
- **THEN** 服务按现有逻辑对完整语音段执行 v1.0.2 的纠正加标点或 v1.0.3 的 SenseVoice 纠正
- **AND** 最终响应继续复用该段的 `key`，并在发送后释放该段状态

### Requirement: 会话断开必须释放实时缓存

v1.0.2 和 v1.0.3 SHALL 在正常断开、客户端断开和异常退出路径清空在线 ASR cache、VAD cache、预缓存和当前 PCM 段，不得影响其他 WebSocket 会话。

#### Scenario: 客户端中途断开

- **WHEN** `receive_bytes` 抛出 `WebSocketDisconnect` 或会话进入 `finally`
- **THEN** 当前会话的所有模型输入缓存和 PCM 列表被清空，并执行现有在线请求统计更新和连接关闭逻辑

### Requirement: WebSocket 协议兼容性必须保持

本次实现 MUST 保持 v1.0.2 和 v1.0.3 的路径、上行 PCM 接收方式以及响应 JSON 的 `key`、`text`、`finished`、`bg`、`ed` 字段、类型和含义不变；不得修改 v1.0.1 或 `online_gateway_service`。

#### Scenario: 现有客户端继续消费结果

- **WHEN** 现有 WebSocket 客户端连接 v1.0.2 或 v1.0.3 并发送原格式 PCM 块
- **THEN** 客户端收到与变更前相同结构的临时结果和最终结果，不需要增加请求字段或解析分支
