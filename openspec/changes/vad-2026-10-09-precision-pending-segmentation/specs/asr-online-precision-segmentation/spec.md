## ADDED Requirements

### Requirement: v1.0.2 和 v1.0.3 使用连续 VAD 状态

v1.0.2 和 v1.0.3 SHALL 在同一 WebSocket 会话内跨越多个 0.48 秒 PCM 块连续使用 VAD 状态，不得按固定六块周期重建 VAD cache。服务 MUST 在自然结束、约 20 秒最大段长、明确空闲超时、异常或连接断开时释放状态。

#### Scenario: 端点静音跨越块边界

- **WHEN** 语音在一个 0.48 秒块末尾结束，后续静音跨越下一个或多个块且尚未达到最大段长
- **THEN** VAD 继续累计连续静音并输出自然结束事件
- **AND** 服务不会因为固定块数重建 cache 而丢失当前语音段状态

#### Scenario: 没有自然结束的连续语音

- **WHEN** 当前语音段达到 42 个上游块且 VAD 尚未返回 end
- **THEN** 服务通过现有最终纠正流程立即生成并发送一个 `finished=true` 结果
- **AND** 服务释放当前语句状态并为后续音频建立新段

### Requirement: VAD 断点必须精确切分 PCM

服务 SHALL 将 VAD 的 `start_ms/end_ms` 转换为会话绝对样本位置，并按样本切分 PCM。结束点后的已收到音频 MUST 进入 pending PCM，不得因为当前输入块结束而全部归入上一句话或被直接丢弃。

#### Scenario: 块内出现语音结束点

- **WHEN** 当前已收到的 PCM 块覆盖全局 2.4 秒至 2.88 秒，VAD 返回约 2.31 秒的结束点
- **THEN** 当前语句音频在约 2.31 秒处切分
- **AND** 2.31 秒至 2.88 秒的音频保留在 pending PCM 中，等待后续语句归属

#### Scenario: VAD 返回相对 cache 时间

- **WHEN** VAD 返回的 `start_ms/end_ms` 是相对当前 cache 或其内部已消费偏移的时间
- **THEN** 服务使用记录的 cache 起点和累计消费偏移转换为会话绝对时间
- **AND** `bg`、`ed` 不出现倒退、未来时间或跨段重叠

### Requirement: 下一语句必须消费 pending PCM 并保留 pre-roll

服务 SHALL 在收到下一语句的 VAD start 后，从 pending PCM 中定位下一语句起点，并在起点前最多保留 100 至 200 ms pre-roll 作为识别上下文。pre-roll 不得改变响应 `bg` 的真实语音起点。

#### Scenario: 结束点后的残余音频属于下一句

- **WHEN** 上一句在 2.31 秒结束，后续 pending PCM 中包含 2.31 秒之后的音频，且 VAD 在后续输入中确认下一句 start
- **THEN** 下一句纠正输入包含从 start 前最多 100 至 200 ms 的 pre-roll
- **AND** 上一句和下一句之间的音频不会被静默丢弃

#### Scenario: 长时间静音 pending 有界

- **WHEN** 结束后持续收到静音但尚未确认下一句 start
- **THEN** 服务丢弃超过配置上限的纯静音，同时保留下一句起点所需的 pre-roll
- **AND** pending PCM 不会随 WebSocket 时长无限增长

### Requirement: finished=true 必须立即表示当前话段结束

每个自然 VAD end 或最大段长保护事件 SHALL 只针对当前话段发送一个最终结果，并立即发送 `finished=true`。服务 MUST 保持当前 `key` 复用规则，不得等待或累计三个 finished 段后再发送。

#### Scenario: 自然断句立即结束字幕段

- **WHEN** VAD 确认当前话段结束且当前话段存在有效 `segment_key`
- **THEN** 服务完成当前话段纠正后立即发送 `finished=true`
- **AND** 下一句话使用新的 `key`，字幕消费者可以立即关闭当前显示段

#### Scenario: 三个连续话段

- **WHEN** VAD 连续确认三个独立话段
- **THEN** 服务分别在每个话段确认后发送三条对应的 `finished=true`
- **AND** 不等待第三个话段完成后才发送前两个话段的 finished 结果

### Requirement: 协议和既有版本边界保持不变

本 change MUST 保持 v1.0.2/v1.0.3 的 WebSocket 路径、上行 PCM 格式和响应 JSON 的 `key`、`text`、`finished`、`bg`、`ed` 字段及类型不变；不得修改 v1.0.1 或 `online_gateway_service`。

#### Scenario: 既有客户端无需修改

- **WHEN** 既有客户端以原方式发送 16 kHz 单声道 16-bit PCM 块
- **THEN** 客户端收到相同字段结构的临时结果和 finished 最终结果
- **AND** 客户端无需新增请求字段或改变 finished 字段解析逻辑

### Requirement: 三版本全量测试必须可比较并可发现卡死

实现完成后的验收 SHALL 由 Mac 测试客户端向 `192.168.29.12` 按 A（v1.0.1）→B（v1.0.2）→C（v1.0.3）的顺序建立连接，并使用完整教师音频完成三路测试。原始响应 MUST 分别保存为 A、B、C JSONL；测试 MUST 生成 AB、AC、BC 对比数据，覆盖识别文本、纠正效果、finished 断句、时间戳和性能异常。测试结束后 MUST 关闭 29.12 后端并确认服务端口已释放。

#### Scenario: 三个接口完成完整教师音频测试

- **WHEN** 29.12 服务启动成功，Mac 客户端按 A→B→C 顺序向三个接口发送完整 `教师1_16k.wav`，每路完成尾部 drain 后再启动下一路
- **THEN** A、B、C 三个 JSONL 分别保存每个接口的原始响应，且每路 summary 记录完整音频时长、块数、响应数、finished 数和最后 `ed`
- **AND** 原始响应字段仍只有既有协议字段，不因测试统计增加接口响应字段

#### Scenario: 两两比较纠正和时间轴

- **WHEN** A、B、C 三路测试完成并生成原始结果
- **THEN** 测试生成 AB、AC、BC 三组比较结果，至少包含文本增删改、纠正位置、标点、finished 段数、`bg/ed` 倒退或重叠、空洞、重复文本和音频覆盖率
- **AND** 报告区分处理效果、纠正效果、协议兼容性和性能指标

#### Scenario: 长时间无进度或服务异常

- **WHEN** 某一路超过无响应阈值，或远端进程退出、GPU OOM、WebSocket 关闭或 8084 不再监听
- **THEN** watchdog 保存最后发送块、最后响应、最近日志、资源快照和失败原因，并将该路标记为未完成
- **AND** 测试流程不无限等待，其他测试路独立收尾，最终仍执行远端服务关闭

#### Scenario: 全量测试后关闭远端服务

- **WHEN** 三路测试完成或被 watchdog 终止
- **THEN** 测试流程停止 29.12 的 `asr_online` 服务并记录 PID 退出结果
- **AND** 8084 端口不再监听，GPU 显存释放状态写入最终测试报告
