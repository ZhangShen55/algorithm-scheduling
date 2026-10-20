## Why

当前 `v1.1.8/seacraft_asr` 能够完成课程语音识别，但中文数字、百分比、日期、分数和数学表达式仍经常以口语形式返回，影响字幕、课程文本和后续分析的可读性。已经使用两节完整高数课程验证 `WeTextProcessing==1.2.0`，结果表明它能有效完成数字规范化和口语填充词清理，现在需要把这套能力稳定接入离线 ASR 服务。

## What Changes

- 在 `v1.1.8/seacraft_asr` 的中文/英文 Paraformer 结果之后增加 `WeTextProcessing==1.2.0` ITN 处理。
- 对每个 ASR 分段的 `segment_text` 进行规范化，并同步生成规范化后的顶层 `text`，保持原有响应字段、时间字段和路由不变。
- 增加可配置的 ITN 开关和规则参数，默认启用已验证的中文规则；ITN 失败时保留 ASR 原结果并记录受控错误日志。
- 更新 Python 依赖、Conda/Docker 构建依赖、运行配置、GPU 部署配置、部署说明和 README。
- 增加单元测试、接口契约测试和真实课程音频验证，覆盖数字、百分比、日期、数学术语、化学术语以及 ITN 失败回退。
- 先在 `192.168.29.12:/root/workspace/` 的 `asr` 环境启动并验证，再在 Mac 的 `asr` 环境同步代码并完成验证。
- 验证完成后按中文规范创建提交并推送；提交范围只包含本变更相关文件。

## Capabilities

### New Capabilities

- `offline-asr-inverse-text-normalization`: 为离线 `v1.1.8` ASR 结果提供可配置、可回退的 ITN 规范化能力。

### Modified Capabilities

- 无。现有 `v1.1.8/seacraft_asr` 路由和响应字段保持兼容，本变更新增的是实现后的文本处理行为。

## Impact

- 代码：`asr_offline/app` 的 ASR 响应处理和配置读取。
- 依赖：`WeTextProcessing==1.2.0`、其运行时依赖 `pynini`，以及 Linux GPU 镜像的 Conda/Python 安装方式。
- 配置与部署：`asr_offline/config.toml`、GPU 部署配置、`docker/Dockerfile`、依赖清单、启动/部署文档。
- 测试：离线算子测试和真实 `v1.1.8` 推理验证；不改变 `asr_online` WebSocket 契约，也不修改既有未关联的工作区改动。
- 运行风险：ITN 是确定性规则，不能修复 ASR 错听；必须保留异常回退和化学/数学术语回归测试，避免把“一氧化碳”等中文术语误转为数字。
