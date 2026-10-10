## Why

v1.0.2 和 v1.0.3 当前按 0.48 秒整块归属音频，虽然 FSMN-VAD 能返回块内毫秒级断点，但服务没有按 `end_ms` 精确切分，也没有把断点后的音频结转到下一段。这会把停顿和下一句话开头错误归入相邻语句，并使 VAD cache 轮换后无法稳定完成自然断句，最终频繁依赖约 20 秒强制切段。

`finished=true` 是字幕显示使用的断句标识，表示当前话段已经结束。因此每个完整话段必须在确认后立即发送一个 `finished=true` 结果，不能累计多个话段后再发送，否则字幕会延迟且纠正结果无法及时生效。

## What Changes

- 为 v1.0.2 和 v1.0.3 引入基于 VAD `start_ms/end_ms` 的 PCM 精确切分。
- 增加 pending PCM，保存当前 VAD 已收到但尚未归属到完整语句的音频尾部，并在下一句 `start_ms` 确认后消费。
- 在下一句起点前保留约 100 至 200 ms pre-roll，降低首字被切掉的风险。
- 保持 VAD 状态跨上游 0.48 秒块连续运行，不再按固定 6 块直接重建状态；仅在自然结束、空闲超时、异常和有界保护条件下释放。
- 每个 VAD 完成事件立即发送对应的 `finished=true`，不增加“累计 3 个 finished 后再输出”的延迟策略。
- 继续保留约 20 秒的单段最大保护，防止 VAD 异常时 PCM 和纠正模型输入无限增长。
- 保持 `/v1.0.2/seacraft_asr_online`、`/v1.0.3/seacraft_asr_online` 的请求和响应字段完全不变。
- 不修改 v1.0.1 和 `online_gateway_service`；本 change 只生成 OpenSpec 方案，不在本阶段修改代码或部署服务。
- 增加 Mac 客户端到 29.12 服务端的全量验收：v1.0.1、v1.0.2、v1.0.3 使用完整教师音频按 A→B→C 顺序测试，避免三路共享在线模型时互相阻塞；原始结果分别命名为 A、B、C 保存到 `test/`。
- 对 A-B、A-C、B-C 进行文本纠正差异、断句效果、`bg/ed` 时间戳连续性、finished 延迟和处理覆盖率比较。
- 测试期间监控响应进度、单块耗时、缓存/内存/GPU 使用和服务日志，识别卡死、无响应、进程退出或连接断开；测试结束后关闭 29.12 后端并确认端口已释放。

## Capabilities

### New Capabilities

- `asr-online-precision-segmentation`: 为实验接口提供连续 VAD、断点精确切分、pending PCM 和字幕即时 finished 事件。

### Modified Capabilities

- 无。

## Impact

- 代码：后续实现将影响 `asr_online/app/api/routes/ws_online.py` 及 v1.0.2/v1.0.3 协议测试。
- 模型配置：保留当前 VAD 尾静音参数的可调能力，默认值由设计阶段结合 1000ms/1200ms 实测确定。
- 协议：不改变 WebSocket 路径、上行 PCM 分块、响应字段、字段类型或 `finished` 字段语义。
- 部署：实现完成后需要在本地 Mac 和 `192.168.29.12` 同步验证；本 proposal 阶段不执行同步。
- 验收产物：`test/A_v1_0_1_teacher1_full.jsonl`、`test/B_v1_0_2_teacher1_full.jsonl`、`test/C_v1_0_3_teacher1_full.jsonl` 及对应 summary、资源监控、AB/AC/BC 对比文件。
