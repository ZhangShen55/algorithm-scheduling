## Why

现有 v1.0.2 和 v1.0.3 的 VAD、整句二次识别及渐进纠正增加了实时链路负担，而 v1.0.1 虽然更轻，仍缺少基于完整语音段修正错字和补充标点的能力。需要新增一条不影响既有接口的 v1.0.4 实验链路，以实时输出优先、句末纠正可降级的方式验证纠正收益与多路容量。

## What Changes

- 新增 WebSocket 接口 `/v1.0.4/seacraft_asr_online`，请求帧以及 `key/text/finished/bg/ed` 响应字段保持现有规范。
- v1.0.4 复用 v1.0.1 在线 Paraformer 流式识别和连续空结果断句，不增加 FSMN-VAD，不移植 v1.0.2/v1.0.3 的 pending、merge 或渐进纠正链路。
- 为每个字幕段保存有界 PCM；第一次连续空结果时异步提交一次 SenseVoice 句末纠正，第二次连续空结果确认断句时优先使用已就绪结果，否则立即降级为在线文本，不因纠正排队阻塞后续音频接收。
- 为 v1.0.4 增加稳定段 `key`、有界纠正队列、每会话单在途、过期结果丢弃、在线主链路优先以及纠正提交/命中/降级指标。
- 降低 v1.0.4 对增长中文本重复执行标点模型的频率；SenseVoice 命中时直接使用其标点结果。
- 在 Mac 本地完成静态、单元和协议验证；同步代码到 `192.168.29.12:/root/workspace/asr_online` 后使用 GPU 启动服务，并由服务器本机访问 `127.0.0.1:8084` 完成 v1.0.1 基线、v1.0.4 正确性、纠正覆盖率和并发容量验证。
- 测试原始 JSONL、服务日志和资源监控保存到 `asr_online/test/` 的忽略目录，汇总结论写入本 change；全部远端测试结束后关闭 8084 并确认释放本次 GPU 进程。
- 不修改 v1.0.1、v1.0.2、v1.0.3 的路径、请求响应格式和处理逻辑，不修改 `online_gateway_service`。

## Capabilities

### New Capabilities

- `asr-online-v1-0-4-final-correction`: v1.0.4 流式识别、句末单次异步 SenseVoice 纠正、稳定字幕段语义和实时优先降级规则。
- `asr-online-v1-0-4-capacity-validation`: Mac 与 GPU 服务器同步验证、v1.0.1 对照基线、v1.0.4 实时容量和纠正覆盖率报告、测试后资源清理要求。

### Modified Capabilities

- 无。

## Impact

- 主要影响 `asr_online/app/api/routes/ws_online.py`、模型与纠正调度、根 `config.toml`、测试工具、单元测试和 `README.md`。
- v1.0.4 使用项目内现有 `SenseVoiceSmall` 模型；部署配置需要支持将在线 ASR 与纠正模型放在相同或不同 CUDA 设备，并在三张 RTX 4090 的 server12 上优先验证独立 GPU 隔离模式。
- `asr_online/test/` 只长期保留测试脚本和音频样本；可重新生成的结果不纳入 Git。
- 既有三个 WebSocket 接口和上游网关不受影响；v1.0.4 在完成容量与协议验证前仅作为算子直连接口。
