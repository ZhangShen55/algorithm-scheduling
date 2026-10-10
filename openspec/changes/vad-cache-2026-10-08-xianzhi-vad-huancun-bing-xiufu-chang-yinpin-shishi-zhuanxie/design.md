## Context

v1.0.2 和 v1.0.3 复用 `app/api/routes/ws_online.py` 中的 `_run_revision_session()`。每个上游音频块约为 0.48 秒，当前把同一个 `vad_cache` 持续传给 FunASR FSMN-VAD，只有检测到句末才重置。FunASR 会在该 cache 下持续保存 `stats.scores`、`stats.data_buf_all`、`stats.decibel` 和 `stats.output_data_buf` 等流式状态，长音频时计算量和内存占用随历史块数增长。

本次变更必须保持 `/v1.0.2/seacraft_asr_online` 和 `/v1.0.3/seacraft_asr_online` 的请求、响应字段、字段类型和时间字段语义不变；v1.0.1 与 `online_gateway_service` 不在本次范围内。

## Goals / Non-Goals

**Goals:**

- 限制 v1.0.2/v1.0.3 的 VAD 流式 cache 生命周期，使处理耗时不随长音频历史持续增长。
- 保留足够的短时 VAD look-back，并在 VAD 断句、连续空闲、最长语音段和 WebSocket 断开时释放状态。
- 限制未开始语音前的 PCM 预缓存，以及已开始语音段的 PCM 长度。
- 保持现有临时结果、最终纠正结果、`key` 复用和 `finished` 行为的协议格式。
- 在本地和 29.12 服务端完成同一代码版本的编译、启动、健康检查和短时硬超时验证。

**Non-Goals:**

- 不修改 v1.0.1 的实现或行为。
- 不修改 `online_gateway_service`、上游调用方、接口路径、请求 PCM 格式或响应 JSON schema。
- 不替换 VAD、在线 ASR、Paraformer 纠正模型或 SenseVoice 模型。
- 不通过每个音频块完全清空 cache；这会丢失短时上下文并降低断句稳定性。

## Decisions

### 1. 在项目层轮换 VAD cache

每 6 个音频块轮换一次 VAD cache，约保留 2.88 秒的 FunASR 流式状态。轮换前先完成当前块的 `generate`，轮换后保留项目层的 `vad_started`、当前 `segment_key` 和已识别文本，因此持续说话时不会因 cache 轮换而丢失当前句子的协议状态。轮换时将 `vad_origin_seconds` 设为当前块结束时间，确保下一次 VAD 相对时间戳不会映射到未来。

选择轮换 cache 而不是修改 FunASR 私有 `stats` 的原因是：不同 FunASR 版本的 cache 内部结构不稳定，直接裁剪内部数组容易破坏张量形状；替换整个 cache 使用公开的 `cache` 参数，兼容性和回滚都更可控。

### 2. 使用有限预缓存并限制单段 PCM

在尚未形成语音段时，只保留最近 6 个块作为起始前预留；检测到语音开始后，以该预留加当前块初始化 `segment_chunks`，随后只追加当前语音段。连续语音段达到 42 个块（约 20.16 秒）时视为强制句末，沿用现有最终纠正流程发送 `finished=true`，随后清空段状态并开始新的逻辑段。VAD 自然句末需要约 1200 ms 尾静音确认。

选择 6 块和 42 块是运行时保护上限而非协议参数：前者覆盖 VAD 的短时 look-back，后者限制纠正模型单次输入和 Python PCM 列表的增长，同时避免等待异常长的自然停顿。

### 3. 统一所有释放路径

自然 VAD 结束和最长段强制切段都复用现有的最终识别、标点或 SenseVoice 处理以及 `reset_segment()`；连续无语音时周期性轮换 cache；WebSocket 断开时在 `finally` 中清空在线 ASR cache、VAD cache、预缓存和当前 PCM 列表。这样异常断开不会把当前会话的模型状态保留到下次请求。

### 4. 用协议测试和短时实测验证

单元测试使用伪模型验证 v1.0.2/v1.0.3 路径、字段和 `key` 不变，并验证 VAD cache 在达到上限后轮换、最长段触发最终结果。真实验证只使用教师音频的短片段和硬超时，分别在本地 Mac 与 29.12 执行，避免用完整长音频等待来判断问题。

## Risks / Trade-offs

- [风险] cache 轮换时恰好跨越极短语音起止点，VAD 可能产生轻微起止时间偏差。→ 保留 6 块 look-back，并在项目层保留当前段状态；用真实短片段比较 v1.0.2/v1.0.3 的响应间隔。
- [风险] 约 20 秒强制切段会把超长无停顿语音拆成两个最终结果。→ 这是有界实时性的保护上限，响应字段和下游处理方式不变；在结果中使用原有 `finished=true` 表示完整逻辑段。
- [风险] 当前修正模型单次推理仍可能耗时较高。→ 本次先解决 VAD 历史状态导致的随时间增长问题；用硬超时记录剩余瓶颈，不把模型替换混入本次变更。
- [风险] 远端服务重启期间连接会短暂中断。→ 先记录 PID 和健康状态，完成同步后再重启，并在重启后立即检查 `/ops/health` 和短时 WebSocket 测试。
