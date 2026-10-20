## Context

`v1.0.4/seacraft_asr_online` 当前使用在线 Paraformer 产生流式增量，使用累计 PCM 调用普通 Paraformer-large 做纠正，再由独立 CT-Transformer 生成标点。纠正结果通过 `CumulativeText` 对齐后输出，现有实现已经包含中间纠正、句末收尾、尾部保护和异常回退。

本变更只增加 ITN 文本后处理。WebSocket 接口必须继续接收 16 kHz、单声道、16-bit PCM 二进制块，并返回现有 `key`、`text`、`finished`、`bg`、`ed` 字段。v1.0.1、v1.0.2、v1.0.3 和 `online_gateway_service` 不在本变更范围内。

## Goals / Non-Goals

**Goals:**

- 实现稳定的 `WeTextProcessing==1.2.0` ITN 适配器，并在进程内缓存已构建的规则对象。
- 对 v1.0.4 的中间纠正和句末纠正统一执行“Paraformer-large → ITN → CT-Transformer 标点”。
- 保持累计文本对齐、质量检查、纠正结果可见时机、`finished` 语义和时间戳逻辑不变。
- ITN 异常时保留未经过 ITN 的纠正结果，服务和当前 WebSocket 连接继续工作。
- 在 192.168.29.12 GPU 环境先完成短音频验证，再同步相同代码到 Mac。

**Non-Goals:**

- 不使用 SenseVoice 的 `use_itn` 参数；v1.0.4 当前不使用 SenseVoice。
- 不改变在线 Paraformer、Paraformer-large、VAD 或标点模型。
- 不通过 ITN 修复同音字、漏字、重复字或语义识别错误。
- 不修改任何请求字段、响应字段、路径、时间戳单位或 `finished` 规则。
- 不将完整转写文本写入应用日志。

## Decisions

### 1. 使用独立 ITN 适配器

新增 v4 专用 ITN 适配器，隔离 `WeTextProcessing` 的导入和调用细节。适配器接收无标点纠正文本，返回 ITN 文本和处理状态；按配置选择中文规则，必要时为英文文本保留原文。适配器在首次使用时构建并缓存 normalizer，避免每个纠正任务重复构图。

备选方案是修改 Paraformer-large 的 `generate` 参数直接开启 ITN，但当前模型调用链和模型能力没有确认该参数可用，且会把第三方规则库绑定到模型适配层，因此不采用。

### 2. ITN 与标点的顺序

调度器先对 Paraformer-large 的原始纠正文本执行 ITN，再将 ITN 文本送给 CT-Transformer。标点模型结果必须通过现有词法一致性检查；若标点模型改变了 ITN 后的词法内容，则保留 ITN 文本。

备选方案是先标点再 ITN，但 ITN 规则处理标点后的字符串更容易产生重复或错误标点，且现有标点模型输入契约更接近无标点文本，因此不采用。

### 3. 对齐和回退

ITN 结果作为纠正候选参与现有 `CumulativeText.adopt`。已有数字归一化用于对齐中文数字和阿拉伯数字；对于无法建立唯一后缀锚点、质量检查失败或 ITN 结果异常的候选，继续沿用当前 raw/已纠正文本，不强行切边界。

ITN 失败只影响当前候选，不应使 scheduler、标点 runner 或 WebSocket 会话失败。日志记录语言、失败类别和耗时，不记录候选全文。

### 4. 配置和依赖

增加 v4 ITN 开关和失败策略配置，默认启用并使用原文回退。依赖固定为 `WeTextProcessing==1.2.0`；Pynini/OpenFST 的安装方式根据 Linux GPU 和 Mac `asr` 环境分别验证并记录，不在服务启动时重复构建规则。

### 5. 验证和部署顺序

先在服务器同步本变更相关文件，确认 ITN/Pynini 导入和规则构建，再执行 compileall、应用导入、项目测试和 v1.0.4 短音频真实 WebSocket 测试。测试完成后将同一代码状态同步到 Mac，不自动执行长音频对比。服务器测试结束关闭本次启动的 8084 服务，保留 JSONL 和诊断摘要。

## Risks / Trade-offs

- **规则误改中文术语** → 增加百分比、日期、时间、金额、单位、连续数字和普通中文回归用例；ITN 失败或结果不满足词法检查时回退。
- **Pynini 在某个平台无法安装** → 先验证服务器和 Mac 的 Conda 组合；依赖不可用时禁止宣称部署完成，并保留明确错误信息。
- **ITN 增加纠正延迟** → 缓存 normalizer，记录 ITN 耗时；不额外复制 PCM，不增加纠正 worker 数。
- **ITN 改变候选长度导致对齐失败** → 复用数字归一化和唯一后缀锚点，拒绝不确定切边界，保留现有 raw tail。
- **中间结果出现反复改写** → ITN 只作用于已被纠正调度器接纳的候选，继续使用 revision、generation 和 stale 保护。
