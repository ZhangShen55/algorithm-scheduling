## Why

当前 `v1.0.4` 已经通过在线 Paraformer 和累计 Paraformer-large 提供流式识别与文本纠正，但数字、日期、时间、比例和金额等内容仍可能以口语形式输出。接入 `WeTextProcessing==1.2.0` 的逆文本正则化（ITN），可以在保持实时接口不变的前提下，将纠正结果转换为更适合字幕显示的书面形式。

## What Changes

- 在 `v1.0.4/seacraft_asr_online` 的累计 Paraformer-large 纠正结果后增加 `WeTextProcessing==1.2.0` ITN。
- 将 ITN 结果送入现有 CT-Transformer 标点模型，形成“在线 Paraformer → Paraformer-large 纠正 → ITN → 标点 → WebSocket 输出”链路。
- 对中间纠正结果和句末纠正结果使用相同的 ITN 处理，并保持现有累计文本对齐、质量检查和尾部保护逻辑。
- ITN 失败时回退到未经过 ITN 的纠正文本，不影响当前连接继续输出，并记录不含完整转写内容的错误信息。
- 增加可配置的 ITN 开关、依赖和运行时检查，更新配置、依赖、README 与测试。
- 先在 `192.168.29.12` 的 `asr` 环境启动 v1.0.4 并完成短音频验证，再将相同代码同步到 Mac，供后续详细对比测试。
- 保持 WebSocket 路径、请求字段、响应字段、时间戳语义和 `finished` 行为不变；不修改 v1.0.1、v1.0.2、v1.0.3 或 `online_gateway_service`。

## Capabilities

### New Capabilities

- `online-asr-v1-0-4-itn`: 为 v1.0.4 的累计纠正结果提供可回退的 ITN 和标点处理链路。

### Modified Capabilities

- 无。

## Impact

- 代码：`asr_online/app/core` 的模型后处理与配置、v1.0.4 WebSocket 路由、测试辅助代码。
- 依赖：增加 `WeTextProcessing==1.2.0` 及其运行时所需的 Pynini/OpenFST 安装说明；需要分别验证 Linux GPU 与 Mac `asr` 环境的可用组合。
- 配置和文档：更新 `config.toml`、服务器测试配置、依赖文件和 README。
- 验证：服务器执行编译、应用导入、依赖检查、短音频 v1.0.4 真实调用和结果字段检查；Mac 同步代码后执行基础验证。
- 风险：ITN 只能规范化已识别文本，不能修复同音字、漏字或语义错误；必须保留原纠正文本回退，并验证数字、日期、单位、百分比和普通中文内容不会被错误改写。
