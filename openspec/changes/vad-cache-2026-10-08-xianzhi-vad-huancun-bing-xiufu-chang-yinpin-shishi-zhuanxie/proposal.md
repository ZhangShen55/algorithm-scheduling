## Why

v1.0.2 和 v1.0.3 在连续长音频上会因为 FSMN-VAD 的流式缓存持续累积而逐块变慢，导致实时结果延迟不断增加。需要在不改变上游请求方式、WebSocket 路径和响应字段的前提下，限制 VAD 状态的生命周期，使长音频处理保持可预测的实时性。

## What Changes

- 为 v1.0.2 和 v1.0.3 增加有界的 VAD 缓存生命周期管理。
- 保留必要的短时 look-back，避免每个 0.48 秒音频块都清空状态造成断句和起止点误判。
- 在 VAD 断句、连续静音、单段最长时长和 WebSocket 断开等边界触发完整释放。
- 对超长语音段进行强制切段，避免纠正模型和 PCM 段缓存无限增长。
- 保持 `/v1.0.2/seacraft_asr_online`、`/v1.0.3/seacraft_asr_online` 的请求和响应数据格式完全一致。
- 保持 v1.0.1 逻辑不变，不修改 `online_gateway_service`。
- 在 Mac 本地 `asr` 环境和 `192.168.29.12` 的 `asr` 环境同步代码，并用短时硬超时测试验证处理不会随音频块数持续退化。

## Capabilities

### New Capabilities

- `asr-online-vad-cache`: 管理 v1.0.2/v1.0.3 实时转写会话中的 VAD、在线识别和 PCM 段缓存边界，同时保持现有 WebSocket 协议。

### Modified Capabilities

- 无。

## Impact

- 代码：`asr_online/app/api/routes/ws_online.py` 及其测试。
- 行为：仅 v1.0.2 和 v1.0.3 的 VAD 缓存释放、超长语音段切分和断开清理行为改变。
- 部署：本地 Mac 与 `192.168.29.12:/root/workspace/asr_online` 需要使用同一版本代码并重启服务；不涉及 `online_gateway_service`。
- 模型和 API：不新增模型，不改变请求 PCM 格式、响应字段、字段类型、接口路径或 v1.0.1。
