## Why

当前实时 ASR 接口只能把在线 Paraformer 的增量文字追加到缓冲区，无法在语音段结束后利用完整音频重新识别并修订前面的临时结果。需要先提供一个隔离的 `v1.0.2` 算子接口，在不影响现有 `v1.0.1` 业务和 `online_gateway_service` 的前提下验证可修订的实时转写体验。

## What Changes

- 新增 `/v1.0.2/seacraft_asr_online` WebSocket 接口。
- 初始阶段让 v1.0.2 与 v1.0.1 使用相同的请求格式和基础响应字段。
- 为 v1.0.2 预留语音段级临时结果、最终结果和结果覆盖语义。
- 在 v1.0.2 上逐步引入 VAD 断句、完整语音段 PCM 缓存和句末二遍识别。
- 保持 `/v1.0.1/seacraft_asr_online` 及 `online_gateway_service` 不变。
- 使用 `asr` Conda 环境和 CPU 完成本地验证，保存测试转写结果到 `asr_online/test`。

## Capabilities

### New Capabilities

- `asr-online-revision`: 为实时 ASR 提供隔离的 v1.0.2 接口，以及语音段结束后的结果修订能力。

### Modified Capabilities

## Impact

- 主要影响 `/Users/zhangshen/Documents/workspace/算法功能调度/asr_online` 的 WebSocket 路由、会话状态和模型调用。
- 新增算子路径，不改变既有路径、上行 PCM 格式或现有基础响应字段。
- 本次不修改 `online_gateway_service`、平台路由和 GPU 部署配置。
- 本地验证使用 `test/test_api_ws_client.py` 和 `test/教师1_16k.wav`；后续 GPU 验证在独立阶段进行。
