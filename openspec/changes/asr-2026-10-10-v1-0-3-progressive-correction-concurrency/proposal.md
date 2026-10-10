## Why（为什么）

v1.0.3 当前只在字幕段最终 `finished=true` 时调用 SenseVoice，较长语句在结束前只能看到在线模型的临时文本，无法及时获得纠正和标点；同时全局模型直接经 `asyncio.to_thread` 调用，缺少明确的并发隔离、背压和容量结论，不能据此判断多路实时转写能力。

需要在不改变 WebSocket 请求和响应格式的前提下，让 v1.0.3 在语句结束前提供有界、可替换的中间纠正，并通过纠正 worker 数和并发会话数的完整矩阵测试确定可持续实时容量。

## What Changes（变更内容）

- 仅调整 `/v1.0.3/seacraft_asr_online`；不修改 v1.0.1、v1.0.2、`online_gateway_service`、上游 0.48 秒 PCM 分块方式和现有响应字段。
- 将“文本纠正”和“字幕段完成”解耦：中间 SenseVoice 结果继续使用当前 `key`，返回 `finished=false`；只有现有 VAD 候选窗口确认结束或最大段长保护触发时才返回 `finished=true`。
- 以 VAD 候选 end 作为首要中间纠正触发；连续长语音没有候选 end 时，以稳定文本累计新增 8 字或累计音频达到配置上限作为补充触发，并设置最短冷却时间。
- 为每个会话限制最多一个在途纠正任务，待处理快照采用 latest-only 合并；使用 `key`、revision ID 和样本边界拒绝过期结果，避免旧纠正覆盖新字幕。
- 引入有界 SenseVoice 纠正任务队列和可配置 worker 池；每个 worker 独占模型实例，明确队列满、任务超时、连接断开、GPU OOM 和服务关闭时的处理与资源释放行为。
- 对纠正 worker 数 `1/2/4/6` 和并发 WebSocket 会话数 `1/2/4/8/16/32/64` 执行 28 组真实节奏测试，并记录延迟、RTF、积压、资源使用、错误和协议时间轴。
- 生成中文容量报告，给出各 worker 配置下的最大可持续实时并发数、瓶颈、失败边界和推荐生产配置；所有测试完成后关闭 `server12` 的 8084 服务并确认 GPU 显存释放。

## Capabilities（能力）

### New Capabilities（新增能力）

- `asr-online-progressive-correction`: v1.0.3 的中间 SenseVoice 纠正、最终字幕完成语义、版本防旧结果覆盖、有界队列和会话资源释放。
- `asr-online-correction-concurrency-benchmark`: worker 数与并发会话矩阵、实时性判定指标、远端执行流程和容量报告要求。

### Modified Capabilities（修改能力）

无。

## Impact（影响范围）

- 代码：主要影响 `asr_online/app/api/routes/ws_online.py`、模型加载/生命周期模块、配置模块及对应测试；新增并发压测和报告生成工具。
- 配置：新增 v1.0.3 中间纠正阈值、冷却时间、worker 数、队列容量和超时配置；默认值必须可在不改协议的情况下调整。
- 模型与资源：worker 独占 SenseVoice 实例会增加 GPU 显存占用，`4/6` worker 是否可用必须以 `server12` 实测为准，OOM 不得静默降级。
- 协议：`key`、`text`、`finished`、`bg`、`ed` 字段及类型保持不变；同一 `key` 在最终完成前可能新增若干条 `finished=false` 的纠正响应。
- 部署与测试：实现后同步到 `server12:/root/workspace/asr_online`，在服务器本机连接 `127.0.0.1:8084` 压测，避免 Mac 到服务器网络影响测量。
