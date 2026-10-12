## ADDED Requirements

### Requirement: 清理并约束测试输出目录
`asr_online/test/` SHALL 长期保留可复用测试脚本和输入音频，可重新生成的 JSON、JSONL、日志、PID、缓存和历史运行目录 MUST 保持在 Git 忽略范围，并可在新一轮验证前清理。

#### Scenario: 开始 v1.0.4 实现前盘点目录
- **WHEN** 检查 `asr_online/test/`
- **THEN** 目录只保留已确认需要的脚本、`chinEng-16k.wav`、`教师1_16k.wav` 和 `音频1.wav`
- **AND** Git 工作树不因删除历史忽略结果而出现删除项

### Requirement: Mac 完成本地验证后同步到 GPU 服务器
实现 SHALL 先在 Mac 的 `asr` Conda 环境运行编译、应用导入、相关单元测试和协议测试，再将代码同步到 `192.168.29.12:/root/workspace/asr_online`。同步后 MUST 校验关键源文件和测试工具哈希一致，不得依赖未同步的本地代码完成远端结论。

#### Scenario: 本地验证通过
- **WHEN** v1.0.4 实现完成
- **THEN** `python -m compileall -q app`、`from app.main import app` 和相关 pytest 均成功
- **AND** v1.0.1、v1.0.2、v1.0.3 路由仍然存在

#### Scenario: 远端代码同步
- **WHEN** Mac 验证完成并同步到 server12
- **THEN** Mac 与 server12 的关键实现、配置模板和测试脚本 SHA256 一致
- **AND** 模型和既有输入音频不被重复覆盖或删除

### Requirement: GPU 服务必须经过可靠就绪和清理流程
server12 测试 SHALL 使用单个 Uvicorn worker 在 8084 后台启动，允许最多五分钟加载模型，并要求 `/get_status` 连续两次返回 200 且监听 PID 属于本次启动进程组。全部测试无论成功或失败，最终 MUST 关闭本次服务并确认 8084 和本次 GPU PID 均已释放。

#### Scenario: 模型正常加载
- **WHEN** 本次服务进程存活且健康检查连续两次成功
- **THEN** 测试客户端才开始建立 WebSocket 连接

#### Scenario: 启动失败或测试异常
- **WHEN** 服务提前退出、启动超过五分钟或任一测试抛出异常
- **THEN** 测试流程保存诊断日志并进入清理阶段
- **AND** 清理后 8084 不再监听且不存在本次 GPU 计算进程

### Requirement: 既有接口必须通过短音频回归
远端验证 SHALL 在同一构建上依次调用 v1.0.1、v1.0.2、v1.0.3 和 v1.0.4，使用教师音频中包含有效语音的约 30 秒片段按每 0.48 秒真实节奏发送，并检查字段、时间轴、非空文本和完成状态。

#### Scenario: 四接口回归
- **WHEN** 服务就绪并依次完成四个接口的 30 秒测试
- **THEN** 每个接口均返回字段集合正确的非空结果
- **AND** v1.0.1、v1.0.2、v1.0.3 不出现新增协议、异常关闭或时间轴回退问题

### Requirement: 使用同条件测定 v1.0.1 和 v1.0.4 容量
容量验证 SHALL 在 server12 本机连接 `127.0.0.1:8084`，对 v1.0.1 和 v1.0.4 使用相同音频片段、0.48 秒发送节奏、连接错峰、并发点和实时判定口径。系统 MUST 先执行 30 秒快速矩阵定位边界，再对最大通过点和首个失败点执行至少十分钟复验。

#### Scenario: 快速容量矩阵
- **WHEN** 分别以并发 `1/2/4/8/16/24/32/40/50` 发送约 30 秒音频
- **THEN** 报告每个接口在每个并发点的完整客户端数、partial/finished p50/p95/p99、最大积压、连接错误、RSS、GPU 显存和利用率
- **AND** 快速失败点不得被描述为稳定容量

#### Scenario: 边界长时复验
- **WHEN** 快速矩阵找到最大通过点和首个失败点
- **THEN** 两个边界均使用至少十分钟连续音频复验
- **AND** 最大稳定并发只取长时复验通过的并发点

### Requirement: 实时通过判定保护主链路
一个容量点只有在所有客户端完整发送、没有 stalled、CUDA OOM、协议或时间轴错误、partial p95 不超过 1 秒、finished p95 不超过 1.5 秒且没有持续缓存增长时，才 SHALL 判定实时通过。v1.0.4 的最大稳定并发 MUST 不低于同轮 v1.0.1 基线，否则实现验收失败。

#### Scenario: 纠正过载但在线仍实时
- **WHEN** v1.0.4 在某并发点跳过部分候选纠正，但所有实时指标满足阈值
- **THEN** 该点可以判定实时通过
- **AND** 报告必须披露纠正覆盖率下降和 skipped/fallback 数量

#### Scenario: 延迟超过阈值
- **WHEN** 所有音频最终都处理完成但 partial p95 超过 1 秒
- **THEN** 该容量点判定为未通过
- **AND** 不得用最终完成代替实时能力结论

### Requirement: 完整音频验证纠正和长时稳定性
v1.0.4 SHALL 在并发 1 下完整处理 47 分钟 `教师1_16k.wav`，不得因处理较慢只运行部分音频。报告 MUST 检查最后时间戳、finished 唯一性、缓存和资源趋势，并列出每个被采用纠正的位置及其前后文本。

#### Scenario: 完整音频处理完成
- **WHEN** v1.0.4 接收 `教师1_16k.wav` 的全部 PCM 块和收尾静音
- **THEN** 发送块数与输入预期一致且最后时间戳覆盖音频末尾
- **AND** 无卡死、持续 RSS/GPU 显存增长、协议错误或时间轴回退

#### Scenario: 生成纠正统计
- **WHEN** 全量运行结束
- **THEN** 报告包含 eligible、submitted、applied、fallback、skipped、stale、failed 和纠正覆盖率
- **AND** 对每个 applied 位置保存 `key/bg/ed`、在线最终候选和 SenseVoice 最终文本

### Requirement: 报告不得夸大纠正准确率
本 change SHALL 生成 `benchmark-report.md`，记录测试配置、原始结果路径、容量边界、实时指标、资源指标、纠正覆盖率和失败原因。缺少人工标注参考文本时，报告 MUST 将文本变化描述为差异，不得宣称 CER/WER 或准确率提升。

#### Scenario: 没有人工参考文本
- **WHEN** 只有 v1.0.1 与 v1.0.4 输出可供比较
- **THEN** 报告列出差异位置和可观察行为
- **AND** 明确说明无法据此计算 CER/WER 或证明纠正一定更准确
