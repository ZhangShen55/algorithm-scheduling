## ADDED Requirements

### Requirement: v1.1.8 返回规范化文本

`POST /v1.1.8/seacraft_asr` 在 ASR 推理成功后 MUST 对每个非空 `segment_text` 执行配置指定的 WeTextProcessing 1.2.0 ITN，并 MUST 使用规范化后的分段文本重新生成顶层 `text`。路由、请求字段、响应字段名称、分段顺序和时间字段 MUST 保持兼容。

#### Scenario: 中文课程数字规范化

- **WHEN** `language=zh` 或 `language=auto` 的 Paraformer 结果包含“二十二摄氏度”“百分之五十”“二零二六年”等文本
- **THEN** 对应 `segment_text` 和顶层 `text` 分别返回 `22°C`、`50%`、`2026年` 等 ITN 结果，且 `bg` 和 `ed` 不变

#### Scenario: 英文结果使用英文规则

- **WHEN** 请求的 `language=en` 且 ASR 返回英文数字、日期、时间或度量表达式
- **THEN** 系统使用 WeTextProcessing 1.2.0 英文 InverseNormalizer，不使用中文规则，并保持分段时间轴不变

#### Scenario: 空分段不被制造文本

- **WHEN** ASR 返回空分段或空文本
- **THEN** 系统跳过该分段的 ITN，不生成非空替代文本，顶层 `text` 只拼接原有非空分段

### Requirement: ITN 配置可控且默认启用

系统 MUST 从 `[itn]` 配置读取 `enabled`、`enable_standalone_number`、`enable_0_to_9`、`remove_interjections` 和 `failure_mode`。默认配置 MUST 启用 ITN，并使用已验证的中文参数：独立数字启用、`0` 到 `9` 单字转换关闭、口头填充词删除启用。

#### Scenario: 关闭 ITN 保持原始 ASR 文本

- **WHEN** `[itn].enabled=false`
- **THEN** 接口返回与启用 ITN 前相同的 `segment_text` 和 `text`，且不构建或调用 normalizer

#### Scenario: 调整中文数字参数

- **WHEN** 运维修改 `[itn].enable_0_to_9` 或 `[itn].remove_interjections` 并重启服务
- **THEN** 后续请求使用新参数构建对应语言的 normalizer，配置解析失败时服务不得静默使用未知值

### Requirement: ITN 异常安全回退

当 WeTextProcessing 依赖缺失、FST 构图失败或单个分段处理异常时，系统 MUST 按 `failure_mode=passthrough` 返回 ASR 原文，不得丢弃整个 ASR 结果；系统 MUST 记录语言、失败阶段、异常类别、分段数量和耗时，但 MUST NOT 记录完整 ASR 文本。

#### Scenario: normalizer 初始化失败

- **WHEN** `pynini` 或 WeTextProcessing 无法导入，且 `failure_mode=passthrough`
- **THEN** v1.1.8 仍返回原始 ASR `segments` 和 `text`，健康/就绪状态明确报告 ITN 不可用，日志只包含受控错误摘要

#### Scenario: 单段规则异常

- **WHEN** 某一分段触发 ITN 异常
- **THEN** 该分段保留原文，其他分段继续处理，响应仍保持原有分段顺序和时间字段

### Requirement: 术语和边界回归保护

ITN 适配器 MUST 提供回归测试，确认规则不会把“一氧化碳”“二氧化碳”“一阶导数”“二维传热”“三维空间”等已知中文术语直接转换成数字形式；测试 MUST 同时覆盖百分比、日期、分数、度量单位、连续数字和数学表达式的实际规则输出。

#### Scenario: 化学和数学术语保持中文

- **WHEN** normalizer 处理“一氧化碳”“二氧化碳”“一阶导数”“二维传热”
- **THEN** 输出保持这些术语的中文语义，不出现 `1氧化碳`、`2维` 或 `1阶导数`

#### Scenario: ASR 错听不被宣称为纠正

- **WHEN** ASR 文本包含“二零零二四”“零点五十”或不完整公式等错误内容
- **THEN** ITN 只按确定性规则格式化，测试和文档明确说明它不负责语义纠错，原始 ASR 文本仍可追溯

### Requirement: 依赖和部署可重复

项目依赖、Docker 构建和部署文档 MUST 明确安装 `WeTextProcessing==1.2.0`、`importlib_resources` 和兼容 Python 版本的 `pynini`；Linux GPU 镜像 MUST 使用可获得的 Conda-forge Pynini/OpenFST 二进制包，Mac `asr` 环境 MUST 能导入相同 ITN 依赖。

#### Scenario: Linux GPU 主机启动验证

- **WHEN** 在 `192.168.29.12:/root/workspace/` 的 `asr` 环境按部署说明安装依赖并启动服务
- **THEN** `python -m compileall -q app`、依赖检查、`from app.main import app`、健康检查和真实 `/v1.1.8/seacraft_asr` 转写均成功，且 ITN 结果生效

#### Scenario: Mac 环境同步验证

- **WHEN** 将同一提交同步到 Mac 的 `asr` 环境并按文档安装依赖
- **THEN** Mac 完成编译、依赖检查、应用导入、服务启动和至少一份真实音频 v1.1.8 转写，且输出与 ITN 配置一致
