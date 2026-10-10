## ADDED Requirements

### Requirement: 必须执行完整 worker 与并发矩阵
验收 SHALL 使用 SenseVoice correction worker 数 `1/2/4/6`，分别测试 WebSocket 并发会话数 `1/2/4/8/16/32/64`，形成 28 个明确状态的矩阵点。Uvicorn worker MUST 固定为 1。

#### Scenario: worker 配置可正常启动
- **WHEN** 指定 correction worker 数后服务在最长 10 分钟内完成模型加载并就绪
- **THEN** 测试依次执行该 worker 对应的 7 个并发点
- **AND** 每个点保存独立原始指标和结论

#### Scenario: worker 配置无法加载
- **WHEN** 服务因 CUDA OOM、模型加载错误或进程退出无法就绪
- **THEN** 该 worker 配置标记为不可用并保存诊断证据
- **AND** 不通过静默降级 worker 数伪造后续矩阵结果

### Requirement: 容量测试必须模拟真实音频节奏
每个容量矩阵点 SHALL 在 `server12` 本机连接 `127.0.0.1:8084`，使用 `教师1_16k.wav` 连续 10 分钟，并按每 0.48 秒发送 0.48 秒 PCM 的真实节奏运行。连接起始 SHALL 在 0 至 480 ms 内错开。

#### Scenario: 并发点正常运行
- **WHEN** 某并发点开始
- **THEN** 每个连接独立发送完整规定时长并记录每块计划发送时间、实际发送时间和所有响应到达时间
- **AND** 发送结束后最多等待 30 秒排空响应与纠正队列

### Requirement: 必须先执行完整音频正确性基线
实现验收 SHALL 在 worker=1、并发=1 下完整处理 47 分钟 `教师1_16k.wav`，不得因处理较慢只跑部分音频，并与变更前 v1.0.3 C 结果比较协议、时间轴、完成段和纠正更新。

#### Scenario: 完整教师音频处理完成
- **WHEN** 正确性基线运行
- **THEN** 客户端发送全部音频块并保存完整 JSONL
- **AND** 分析 `bg/ed` 倒退或异常重叠、重复 final、跨 key 覆盖、纠正位置和最终完成率

### Requirement: 每个矩阵点必须具有快速失败和硬超时
压测工具 MUST 监控首响应、持续无响应、服务进程、CUDA OOM 和积压增长，不得在明确故障后无限等待。

#### Scenario: 会话停止响应
- **WHEN** 首响应超过 10 秒，或任一活跃连接连续 15 秒没有响应
- **THEN** 该点立即标记 stalled 并开始保存诊断和受控清理

#### Scenario: 积压或进程异常
- **WHEN** 端到端或队列积压连续 30 秒增长且超过 60 秒，或服务进程退出、出现 CUDA OOM
- **THEN** 该点提前失败，不继续等待完整 10 分钟
- **AND** 保存最后队列深度、进程状态和资源采样

#### Scenario: 单点超过硬时限
- **WHEN** 单点运行超过计划发送时长加 90 秒
- **THEN** 工具终止该点并标记 timeout

### Requirement: 报告必须使用统一实时判定标准
矩阵点只有在所有连接成功完成、`RTF <= 1.0`、积压不持续增长、partial 延迟 p95 不超过 1.0 秒、中间纠正延迟 p95 不超过 1.0 秒、finished 延迟 p95 不超过 1.5 秒，且无 OOM、stalled、时间戳或协议异常时，才 SHALL 标记为实时通过。

#### Scenario: 某并发点满足全部阈值
- **WHEN** 所有规定指标均满足且测试结束前队列回落
- **THEN** 报告将该点标记为“实时通过”

#### Scenario: 仅平均耗时满足
- **WHEN** 平均延迟较低但任一 p95、RTF、积压、完成率或正确性条件不满足
- **THEN** 报告不得将该点标记为实时通过
- **AND** 必须列出未满足的具体指标

### Requirement: 指标必须支持瓶颈归因
每个矩阵点 SHALL 统计连接完成/失败数、音频发送完整率、partial/中间纠正/finished 的 p50/p95/p99、端到端 lag、RTF、队列平均与最大深度、在途任务、stale 丢弃、降级、WebSocket 关闭码、CPU、RSS、GPU 显存和 GPU 利用率。

#### Scenario: worker 增加但容量没有提高
- **WHEN** 更高 worker 数的最大实时并发没有提高
- **THEN** 报告使用在线 ASR/VAD 排队、SenseVoice 排队、GPU 利用率和资源数据说明主要瓶颈
- **AND** 不仅依据总运行时间下结论

### Requirement: 必须复验容量边界并生成中文报告
每种可启动 worker 配置 SHALL 重复测试其最大通过并发和首个失败并发；最终推荐配置 SHALL 再执行一次完整教师音频稳定性测试。报告 MUST 给出最大可持续并发、失败边界、推荐参数和残余风险。

#### Scenario: 初次矩阵完成
- **WHEN** 某 worker 配置已有通过和失败边界
- **THEN** 工具分别复验边界两侧并在结果不一致时标记波动

#### Scenario: 生成最终报告
- **WHEN** 正确性、矩阵和边界复验结束
- **THEN** 在 change 中生成 `benchmark-report.md`
- **AND** 原始 JSONL、资源采样和服务日志保存在 `asr_online/test/` 的忽略目录

### Requirement: 远端测试结束必须释放服务与 GPU
所有远端测试无论成功、失败或被中断，MUST 关闭 `server12` 的 8084 后端并确认端口、后台进程和 GPU 计算进程释放。

#### Scenario: 压测完成或异常退出
- **WHEN** 测试流程进入清理阶段
- **THEN** 工具停止 Uvicorn 和相关压测进程
- **AND** 验证 8084 不再监听且没有本次服务遗留的 GPU 计算进程
