## ADDED Requirements

### Requirement: v1.0.2 独立实时 ASR 入口

`asr_online` SHALL 提供 WebSocket 路径 `/v1.0.2/seacraft_asr_online`。该入口 SHALL 接受与 v1.0.1 相同的 16 kHz、单声道、signed 16-bit PCM 二进制音频块，并保留现有基础响应字段 `key`、`text`、`finished`、`bg` 和 `ed`。

#### Scenario: v1.0.2 接受兼容的 PCM 音频流

- **WHEN** 客户端连接 `/v1.0.2/seacraft_asr_online` 并持续发送合法 PCM 二进制帧
- **THEN** 服务端 SHALL 返回 JSON 识别结果，且结果包含 `key`、`text`、`finished`、`bg` 和 `ed`

#### Scenario: v1.0.1 行为保持不变

- **WHEN** 客户端连接 `/v1.0.1/seacraft_asr_online`
- **THEN** 服务端 SHALL 继续使用原有入口和逻辑，新增 v1.0.2 的实现不得改变其响应行为

### Requirement: 实时语音段临时结果

v1.0.2 SHALL 在语音段尚未结束时返回 `finished=false` 的临时结果。临时结果的 `text` SHALL 表示当前语音段的完整临时文本，而不是要求客户端将每条消息的文本无条件追加。

#### Scenario: 语音段持续期间更新临时文本

- **WHEN** 在线模型在同一语音段产生新的识别内容
- **THEN** 服务端 SHALL 返回该语音段的临时完整文本，并将 `finished` 设置为 `false`

### Requirement: 语音段最终结果修订

v1.0.2 SHALL 在检测到语音段结束后，使用该语音段完整 PCM 进行最终识别处理，并返回 `finished=true` 的最终文本。最终结果 SHALL 使用与该语音段临时结果相同的 `key`，使消费者能够覆盖临时文本。

#### Scenario: 句末重新识别并覆盖临时文本

- **WHEN** VAD 判断当前语音段结束，且完整语音段 PCM 已经收集完成
- **THEN** 服务端 SHALL 对完整 PCM 执行最终识别，并发送同一 `key`、修订后的 `text` 和 `finished=true`

#### Scenario: 标点不单独触发最终状态

- **WHEN** 临时文本中出现 `。`、`？` 或 `！` 等标点
- **THEN** 服务端 SHALL NOT 仅凭标点将结果标记为 `finished=true`

### Requirement: v1.0.2 本地可验证

v1.0.2 SHALL 能在 `asr` Conda 环境、CPU 配置下启动，并支持使用 `test/test_api_ws_client.py` 向 `test/教师1_16k.wav` 发起真实 WebSocket 测试。测试输出 SHALL 保存到 `asr_online/test`，且不得修改或依赖 `online_gateway_service`。

#### Scenario: CPU 环境完成真实音频测试

- **WHEN** 使用 `asr` 环境启动 v1.0.2 并发送 `test/教师1_16k.wav`
- **THEN** 服务 SHALL 返回非空识别结果，测试产物 SHALL 写入 `asr_online/test`，并保留 v1.0.1 可用
