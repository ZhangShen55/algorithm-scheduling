## Why

v1.0.2 和 v1.0.3 当前在 VAD 返回 end 后立即发送 `finished=true`，在教师音频中会将连续语音中的短暂停顿误判为多个话段。例如 `segment-000053` 至 `segment-000055` 只有约 0.48 秒，将“非常少见的母亲，而且是个恶毒的母亲”分裂成多个字幕段，使纠正模型缺少上下文并增加字幕闪断。

需要在不改变上游 0.48 秒 PCM 分块、WebSocket 请求格式、响应字段和 v1.0.1 行为的前提下，使终点检测具有短间隔合并和温和防抖动能力。

## What Changes

- 在 v1.0.2/v1.0.3 共用会话逻辑中增加 VAD end 候选状态，收到 end 后不再无条件立即完成当前话段。
- 在候选 end 期间继续接收并保留 PCM；若新语音在配置的短间隔窗口内开始，则与上一段使用同一 `key` 合并识别。
- 短间隔窗口超时后才发送一次 `finished=true`，保证字幕段仍然有明确结束标识。
- 合并后的语句只调用一次终端纠正流程，使纠正模型能够获取更完整的语音上下文。
- 保留约 20 秒单段最大保护、pending PCM 和 100至200 ms pre-roll；超过最大段长时仍立即强制切段。
- 保持 `/v1.0.2/seacraft_asr_online` 和 `/v1.0.3/seacraft_asr_online` 的请求和响应数据格式不变；不修改 v1.0.1、`online_gateway_service` 和上游分块方式。
- 增加以教师音频为基准的细碎断句测试，对比当前 1000 ms、1200 ms 以及短间隔合并策略。

## Capabilities

### New Capabilities

- `asr-online-short-segment-merge`: v1.0.2/v1.0.3 的 VAD 终点候选、短间隔语音合并、finished 延后发送和终端纠正上下文保持。

### Modified Capabilities

无。

## Impact

- 代码：主要影响 `asr_online/app/api/routes/ws_online.py` 及 v1.0.2/v1.0.3 协议测试。
- 配置：需增加可调的合并间隔参数，保留 `vad_max_end_silence_ms` 可配置；首次基准建议使用 720 ms 左右的短间隔窗口。
- 协议：不新增请求字段，不新增响应字段，仅改变 `finished=true` 的时机和对应语句的长度。
- 部署：实现后需同步 Mac 本地与 `server12:/root/workspace/asr_online`，使用 GPU 远端服务执行短测和教师音频全量验收；测试结束后关闭远端服务。
