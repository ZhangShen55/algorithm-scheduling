## ADDED Requirements

### Requirement: VAD end 必须先进入候选状态

v1.0.2 和 v1.0.3 SHALL 在收到有效 VAD end 时记录候选结束点，不得无条件立即将当前语句标记为 `finished=true`。候选期间必须保留当前语句、PCM 和 `key`。

#### Scenario: 语音结束后暂未出现新 start

- **WHEN** VAD 返回有效 end 且当前语句未达到最大段长
- **THEN** 服务记录 `candidate_end_sample`，暂不调用终端纠正并暂不发送 `finished=true`
- **AND** 后续临时响应继续使用原有 `key`、响应字段和类型

### Requirement: 短间隔新语音必须与候选语句合并

当新 VAD start 与候选 end 的会话绝对时间差不超过 `vad_merge_max_gap_ms` 时，服务 SHALL 清除候选 end，使用原有 `key` 继续累积语句，不创建新的完成字幕段。

#### Scenario: 多个 0.48 秒小段属于同一句

- **WHEN** 上一段 end 与下一段 start 的时间差为 0.48 秒，并且后续语音在合并窗口内连续出现
- **THEN** 服务使用同一 `key` 继续返回临时结果
- **AND** 前后 PCM 在最终纠正时合并为一个语句输入
- **AND** 合并后只发送一次 `finished=true`

### Requirement: 候选窗口必须有界并可配置

v1.0.2 和 v1.0.3 SHALL 使用正整数配置 `vad_merge_max_gap_ms`，默认基准值为 720 ms。当候选 end 后没有新 start 且累积音频时间超过配置窗口时，服务 MUST finalize 当前语句并发送一次 `finished=true`。

#### Scenario: 长时静音不得无限等待

- **WHEN** VAD end 后持续收到静音，且在 `vad_merge_max_gap_ms` 内没有新 start
- **THEN** 服务在有界窗口结束后执行终端纠正
- **AND** 服务发送一次 `finished=true`并清理已完成语句状态

### Requirement: 硬切段和连接释放必须优先

合并策略 MUST 不覆盖约 20.16 秒单段最大保护、客户端断开释放和现有发送失败处理。

#### Scenario: 达到最大段长

- **WHEN** 候选语句达到约 20.16 秒且仍没有 VAD end
- **THEN** 服务立即强制 finalize 并发送一次 `finished=true`
- **AND** 服务清除 candidate end、释放已完成语句 PCM 并为后续语音建立新段

#### Scenario: 客户端断开

- **WHEN** WebSocket 客户端断开或发送失败
- **THEN** 服务停止后续发送并释放 candidate end、PCM、VAD cache 和 ASR cache

### Requirement: 断句合并不得改变协议

服务 MUST 保持 `/v1.0.2/seacraft_asr_online` 和 `/v1.0.3/seacraft_asr_online` 的 WebSocket 路径、上行 PCM 分块和响应 JSON 的 `key`、`text`、`finished`、`bg`、`ed` 字段及类型。

#### Scenario: 旧客户端继续可用

- **WHEN** 旧客户端按原有方式发送 16 kHz单声道 16-bit PCM
- **THEN** 客户端仍可以仅根据 `finished` 字段判断当前字幕段是否结束
- **AND** 服务不要求旧客户端增加请求字段或 EOS 字段

### Requirement: 必须通过断句基准和真实音频验证

实现 SHALL 使用同一教师音频片段比较 1000 ms、1200 ms 和 1000 ms + 合并策略，并验证合并后的段数、短段数、延迟、时间戳和响应字段。

#### Scenario: 教师音频短片段减少细碎断句

- **WHEN** 对包含 `segment-000052` 至 `segment-000056` 对应音频的片段执行合并版本
- **THEN** 合并版本不应再为每个 0.48 秒小段分别发送 `finished=true`
- **AND** 合并后的 `bg/ed` 仍然单调且覆盖完整语音范围
- **AND** 合并后的终端纠正结果不得包含超过源音频的时间戳
