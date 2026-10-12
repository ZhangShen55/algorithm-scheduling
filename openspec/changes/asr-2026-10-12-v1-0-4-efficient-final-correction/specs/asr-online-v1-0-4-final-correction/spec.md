## ADDED Requirements

### Requirement: 提供隔离的 v1.0.4 WebSocket 接口
系统 SHALL 提供 `/v1.0.4/seacraft_asr_online` WebSocket 接口，并 SHALL 仅接受与既有在线 ASR 相同的 16 kHz、单声道、int16 PCM 二进制帧。每个下行响应 MUST 严格包含 `key`、`text`、`finished`、`bg`、`ed` 五个字段。

#### Scenario: v1.0.4 正常流式识别
- **WHEN** 客户端连接 v1.0.4 并按约 0.48 秒节奏发送有效 PCM
- **THEN** 服务持续返回符合字段规范的 JSON 响应
- **AND** 无需客户端增加请求字段或控制消息

#### Scenario: 既有接口保持不变
- **WHEN** 同一版本服务分别访问 v1.0.1、v1.0.2 和 v1.0.3
- **THEN** 三个接口的路径、请求响应字段和原有处理逻辑保持不变
- **AND** v1.0.4 代码不会成为三个既有路由的执行分支

### Requirement: 字幕段使用稳定标识和完成语义
系统 SHALL 为每个 v1.0.4 字幕段生成稳定 `key`，同一段的所有 partial 和唯一最终响应 MUST 复用该 `key`。`finished=true` MUST 只表示该字幕段已经结束，不得因提交或收到一次候选纠正而提前完成。

#### Scenario: 同段文本多次更新
- **WHEN** 在线模型对同一字幕段产生多个 partial 或纠正后的最终文本
- **THEN** 所有响应使用相同 `key`
- **AND** 只有最后一个响应的 `finished` 为 `true`

#### Scenario: 新字幕段开始
- **WHEN** 上一字幕段已经发送 `finished=true` 且后续检测到新语音
- **THEN** 新字幕段使用新的 `key`
- **AND** `bg`、`ed` 不回退或与已完成段产生无效重叠

### Requirement: 使用在线空结果确认句末而不增加 VAD
v1.0.4 SHALL 复用在线识别连续两个空结果的结束规则，不得调用 FSMN-VAD。第一次连续空结果 SHALL 建立候选结束并冻结一次当前段 PCM 快照，第二次连续空结果 SHALL 确认字幕段完成。

#### Scenario: 连续两个空结果
- **WHEN** 非空字幕段后连续产生两个在线空结果
- **THEN** 第一个空结果触发至多一次候选句末纠正提交
- **AND** 第二个空结果确认该字幕段结束

#### Scenario: 候选后恢复语音
- **WHEN** 第一个空结果后、第二个空结果前再次产生非空语音
- **THEN** 系统取消候选结束并继续原字幕段
- **AND** 已提交候选结果在 revision 或边界不匹配时作为 stale 丢弃

### Requirement: 句末只执行一次可降级 SenseVoice 纠正
每个自然完成字幕段 SHALL 至多提交一次 SenseVoice 句末纠正。提交 MUST 非阻塞；队列满、超时、模型失败或结果未在 final budget 内就绪时，系统 SHALL 使用当前在线文本完成，不得停止接收后续 PCM。

#### Scenario: 纠正结果及时完成
- **WHEN** 第二个空结果确认结束时，同一连接、`key`、revision 和结束样本位置的 SenseVoice 结果已经就绪
- **THEN** 系统使用该纠正文本发送 `finished=true`
- **AND** 不再对该文本调用独立标点模型

#### Scenario: 纠正结果尚未完成
- **WHEN** 确认结束时纠正仍在运行或排队，且等待会超过 final budget 或在线推理已有积压
- **THEN** 系统立即使用在线文本和最终标点发送 `finished=true`
- **AND** 晚到纠正结果不得再次改写已完成字幕段

#### Scenario: 纠正服务过载
- **WHEN** 纠正队列已满或当前会话已有更高优先级任务
- **THEN** 候选纠正被合并或跳过
- **AND** 在线 partial 与字幕段完成流程继续工作

### Requirement: 缓存与纠正任务有界
v1.0.4 SHALL 对单段 PCM、纠正队列、每会话在途任务和 pending 任务设置硬上限。字幕段完成、WebSocket 断开、任务取消或服务关闭时 SHALL 释放对应 PCM 引用和会话状态。

#### Scenario: 长时间连续输入
- **WHEN** 一个字幕段达到配置的最大 PCM 样本数或最大段时长
- **THEN** 系统执行保护性切段
- **AND** 单会话 PCM 不再继续无界增长

#### Scenario: WebSocket 异常断开
- **WHEN** 客户端在候选或在途纠正期间断开
- **THEN** 系统注销纠正会话并使其结果失效
- **AND** 会话 PCM、pending 和结果队列可被释放

### Requirement: 降低增长文本的重复标点开销
v1.0.4 SHALL 仅在稳定文本新增量或时间间隔达到配置阈值时重新计算 partial 标点；未达到阈值的块 MUST 不对完整增长文本重复执行标点模型。在线 fallback 的最终文本 SHALL 执行一次完整标点。

#### Scenario: 多个短增量连续到达
- **WHEN** 新增文本和时间间隔都未达到 partial 标点阈值
- **THEN** 系统不重新调用完整文本标点模型
- **AND** 仍返回及时的 partial 文本

#### Scenario: 在线结果作为最终降级
- **WHEN** SenseVoice 结果未被采用且字幕段确认结束
- **THEN** 系统对完整在线文本执行一次最终标点后发送 `finished=true`

### Requirement: 纠正设备与失败行为可配置
系统 SHALL 支持为 SenseVoice 纠正配置独立设备；未配置时 SHALL 明确使用主设备。配置要求 CUDA 但目标设备不可用或模型加载失败时，服务 MUST 启动失败，不得静默切换到 CPU。

#### Scenario: server12 独立 GPU 配置
- **WHEN** 在线 ASR 配置到一张 RTX 4090 且 SenseVoice 配置到另一张 RTX 4090
- **THEN** 两个模型分别加载到指定设备
- **AND** v1.0.4 响应字段和句末语义不因设备选择改变

#### Scenario: CUDA 纠正设备不可用
- **WHEN** 配置的 SenseVoice CUDA 索引越界或 CUDA 不可用
- **THEN** 服务启动失败并记录不含音频和完整转写文本的错误

### Requirement: 记录低敏感度纠正遥测
系统 SHALL 记录 v1.0.4 纠正的 submitted、applied、fallback、skipped、stale、failed、队列深度、排队耗时和推理耗时，不得记录 PCM、Base64 媒体或完整 ASR 文本。

#### Scenario: 纠正成功和降级同时发生
- **WHEN** 一次测试中部分句末采用纠正且部分句末 fallback
- **THEN** 日志和汇总指标能够分别统计 applied 与 fallback
- **AND** 日志不包含完整请求或响应正文
