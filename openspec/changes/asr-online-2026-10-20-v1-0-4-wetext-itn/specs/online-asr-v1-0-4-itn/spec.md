## ADDED Requirements

### Requirement: v1.0.4 使用累计纠正与 ITN 标点流水线

`v1.0.4/seacraft_asr_online` MUST 按“在线 Paraformer → Paraformer-large 累计纠正 → WeTextProcessing ITN → CT-Transformer 补标点 → WebSocket 输出”的顺序处理可接纳的纠正候选。

#### Scenario: 中间纠正候选经过 ITN 和标点后输出

- **WHEN** 当前语音段累计文本满足中间纠正条件且 Paraformer-large 返回可接纳候选
- **THEN** 系统 MUST 先对候选执行 ITN，再执行标点处理，并沿用当前 `finished=false` 和时间戳输出规则

#### Scenario: 句末纠正候选经过 ITN 和标点后输出

- **WHEN** 当前语音段进入句末收尾且 Paraformer-large 返回可接纳的最终候选
- **THEN** 系统 MUST 按相同顺序执行 ITN 和标点，并沿用当前句末纠正预算、尾部保护和 `finished=true` 规则

### Requirement: ITN 结果必须保持协议兼容

系统 MUST 保持 `/v1.0.4/seacraft_asr_online` 的 WebSocket 路径、二进制 PCM 输入约束以及 `key`、`text`、`finished`、`bg`、`ed` 响应字段和时间戳语义不变。ITN MUST 只改变 `text` 的书写形式，不新增必填请求字段或响应字段。

#### Scenario: ITN 开启时响应字段保持不变

- **WHEN** 客户端按现有协议发送音频块
- **THEN** 服务端 MUST 返回现有字段集合，且 `bg`、`ed`、`finished` 的语义与 ITN 接入前一致

#### Scenario: 旧接口行为保持隔离

- **WHEN** 客户端访问 v1.0.1、v1.0.2 或 v1.0.3
- **THEN** 系统 MUST 不使用本变更的 ITN 处理链路

### Requirement: ITN 必须可配置并可安全回退

系统 MUST 提供 v1.0.4 ITN 开关和失败策略配置。ITN 默认启用；当依赖导入、规则构建或单次处理失败时，系统 MUST 按原文回退策略继续输出未经过 ITN 的纠正候选，并不得因单次 ITN 失败关闭 WebSocket 会话。

#### Scenario: ITN 被配置关闭

- **WHEN** v1.0.4 ITN 开关被关闭
- **THEN** 系统 MUST 跳过 ITN，直接将纠正文本交给标点模型，并保持响应格式不变

#### Scenario: 单次 ITN 处理失败

- **WHEN** ITN 处理某个纠正候选抛出异常
- **THEN** 系统 MUST 使用该候选的未处理文本继续标点和输出，并记录失败类别和耗时信息而不记录完整文本

#### Scenario: ITN 依赖不可用

- **WHEN** 服务启动或首次初始化发现 `WeTextProcessing==1.2.0`/Pynini 不可用
- **THEN** 系统 MUST 按配置的回退策略运行并给出明确诊断，不能静默宣称 ITN 已生效

### Requirement: ITN 不得破坏累计文本对齐

系统 MUST 将 ITN 候选纳入现有 revision、generation、唯一后缀锚点、质量检查和尾部保护流程。无法安全对齐的 ITN 候选 MUST 被拒绝或延后，保留当前已确认文本和未覆盖 raw tail。

#### Scenario: 中文数字被 ITN 转为阿拉伯数字

- **WHEN** ITN 将候选中的中文数字、日期或百分比转换为书面形式
- **THEN** 系统 MUST 使用归一化文本进行候选质量和边界匹配，不能因为形式变化错误截断后续 raw tail

#### Scenario: ITN 候选缺少唯一锚点

- **WHEN** ITN 候选无法在当前在线文本中找到唯一安全后缀锚点
- **THEN** 系统 MUST 拒绝该候选或等待后续候选，不得移动已确认边界或覆盖未纠正尾部

### Requirement: ITN 处理必须具备可测试性和受控开销

系统 MUST 缓存已构建的 ITN 规则对象，提供数字、日期、时间、百分比、金额、单位、连续数字和普通中文的单元测试，并记录 ITN 处理耗时但不得记录完整 ASR 文本。

#### Scenario: 重复处理使用缓存规则

- **WHEN** 同一服务进程连续处理多个 v1.0.4 纠正候选
- **THEN** 系统 MUST 复用对应语言和配置的 ITN 规则对象，不得为每个候选重复初始化规则

#### Scenario: 应用日志记录 ITN 遥测

- **WHEN** ITN 成功、回退或失败
- **THEN** 日志 MUST 只记录语言、启用状态、耗时、处理计数和失败类别等摘要字段，不得记录完整候选文本或 PCM
