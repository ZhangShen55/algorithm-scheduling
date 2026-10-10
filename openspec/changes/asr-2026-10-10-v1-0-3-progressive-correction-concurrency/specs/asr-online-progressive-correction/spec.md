## ADDED Requirements

### Requirement: v1.0.3 必须在最终断句前支持中间纠正
服务 SHALL 在 `/v1.0.3/seacraft_asr_online` 的当前字幕段满足中间触发条件时，对当前有效 PCM 快照执行 SenseVoice 纠正，并使用当前 `key` 返回 `finished=false` 的纠正文本。

#### Scenario: VAD 候选 end 触发中间纠正
- **WHEN** VAD 为当前字幕段产生新的候选 end，且该样本边界尚未提交纠正
- **THEN** 服务提交截至该 end 的 SenseVoice 快照
- **AND** 有效结果使用当前 `key`、快照 `bg/ed` 和 `finished=false` 返回

#### Scenario: 连续长语音按稳定文本触发
- **WHEN** 当前段没有新的 VAD 候选 end，自上次纠正起稳定文本默认累计新增至少 8 个字符，且已超过默认 2000 ms 冷却时间
- **THEN** 服务提交当前最新 PCM 快照进行中间纠正

#### Scenario: 慢语速按音频时长触发
- **WHEN** 稳定文本未达到字符阈值，但自上次纠正快照起新增音频达到默认 4800 ms，且已超过冷却时间
- **THEN** 服务提交当前最新 PCM 快照，避免长语音一直等到最终断句

### Requirement: 中间纠正不得改变 finished 的字幕完成语义
一次中间纠正或一次未确认的 VAD end MUST NOT 单独产生 `finished=true`。服务 SHALL 仅在现有候选窗口确认结束、最大段长保护或连接结束完成流程中将当前字幕段标记为完成。

#### Scenario: 候选 end 后在合并窗口内继续说话
- **WHEN** 中间纠正已返回，但 VAD 在合并窗口内检测到同一句的新语音 start
- **THEN** 服务继续使用原 `key` 和 `finished=false` 更新该段
- **AND** 服务将后续语音加入新的纠正快照，不发送提前完成事件

#### Scenario: 候选 end 最终确认
- **WHEN** 候选窗口结束且没有新语音 start
- **THEN** 服务为该 `key` 发送且只发送一次 `finished=true`
- **AND** 若已成功纠正的快照与最终样本边界相同，服务复用该结果而不重复调用 SenseVoice

### Requirement: 纠正 revision 必须单调且过期结果不得覆盖新内容
每个会话 SHALL 为纠正快照分配单调递增 revision，并同时校验连接、`segment_key` 和样本边界。过期、跨 key、已断开或落后于已接受边界的结果 MUST 被丢弃。

#### Scenario: 较旧任务晚于较新文本返回
- **WHEN** 一个纠正任务返回时，当前段已有更新 revision 或更大的已接受样本边界
- **THEN** 服务丢弃旧结果且不向 WebSocket 发送
- **AND** 记录 stale revision 计数但不记录完整识别文本

#### Scenario: 连接在推理期间断开
- **WHEN** SenseVoice 仍在执行而对应 WebSocket 已断开
- **THEN** 服务不得发送该结果
- **AND** 服务清除该会话的 pending 快照和 PCM 引用

### Requirement: 每会话纠正任务数量必须有界
服务 MUST 保证每个会话最多一个纠正任务处于排队或执行状态；在途期间的新触发 SHALL 仅保留该会话最新快照，并在当前任务结束后按有效性决定是否提交。

#### Scenario: 在途期间连续产生多个触发
- **WHEN** 同一会话的纠正仍在执行，又产生多个字符阈值或 VAD 候选触发
- **THEN** 服务只保留样本边界最大的 pending 快照
- **AND** 不为中间快照逐个增加全局队列任务

### Requirement: SenseVoice worker 必须独占模型实例并使用有界队列
服务 SHALL 提供可配置的 SenseVoice correction worker 池，每个 worker MUST 独占一个模型实例。全局队列 MUST 有容量上限，并区分 final 与 intermediate 的处理优先级。

#### Scenario: 中间任务遇到队列满
- **WHEN** 有界队列已满且新任务属于 intermediate
- **THEN** 服务合并或跳过该中间任务，不阻塞在线 ASR/VAD partial 响应
- **AND** 记录队列满与降级计数

#### Scenario: final 任务等待超时
- **WHEN** final 纠正超过配置的有界等待或推理失败
- **THEN** 服务使用当前最新可见文本完成该字幕段
- **AND** 仍发送一次 `finished=true` 并记录明确降级原因

#### Scenario: worker 模型加载失败
- **WHEN** 配置的任一 worker 因 CUDA OOM 或模型错误无法加载
- **THEN** 服务启动失败并输出不包含音频和完整文本的诊断日志
- **AND** 服务不得静默减少 worker 数继续运行

### Requirement: v1.0.3 并发模型调用必须受控
v1.0.3 SHALL 通过模型级异步锁或单消费者执行入口调用共享在线 ASR 和 VAD 模型，不得从多个线程无约束地同时调用同一模型实例。锁 MUST NOT 跨网络发送或 SenseVoice 等待持有。

#### Scenario: 多会话同时到达音频块
- **WHEN** 多个 v1.0.3 会话同时提交 0.48 秒音频块
- **THEN** 在线 ASR 和 VAD 调用按各自受控入口执行
- **AND** 服务记录排队与执行耗时以区分在线模型瓶颈和纠正瓶颈

### Requirement: 请求响应合同和旧版本行为必须保持兼容
本变更 MUST 保持 v1.0.3 的上行 PCM 方式以及 `key`、`text`、`finished`、`bg`、`ed` 字段和类型不变，并且 MUST NOT 修改 v1.0.1、v1.0.2 的路由和行为。

#### Scenario: 旧 v1.0.3 客户端接收中间纠正
- **WHEN** 旧客户端继续发送 16 kHz 单声道 16-bit PCM，并只依据 `finished` 判断字幕段结束
- **THEN** 客户端可将同 `key`、`finished=false` 的中间纠正作为普通字幕更新处理
- **AND** 最终仍通过同 `key` 的一次 `finished=true` 判断该段结束

#### Scenario: 回归旧接口
- **WHEN** 执行 v1.0.1 和 v1.0.2 现有协议测试
- **THEN** 路径、字段、时间戳语义和既有结果保持兼容

### Requirement: 纠正配置必须校验且支持关闭中间触发
字符阈值、音频时长阈值、冷却时间、worker 数、队列容量和任务超时 SHALL 从项目配置读取并进行范围校验。服务 MUST 提供关闭 v1.0.3 中间纠正触发而保留最终纠正的配置方式。

#### Scenario: 配置非法
- **WHEN** worker 数、队列容量或任一时间阈值为非法值
- **THEN** 服务在启动阶段给出明确错误并拒绝进入就绪状态

#### Scenario: 回滚到仅句末纠正
- **WHEN** 运维关闭中间纠正触发
- **THEN** v1.0.3 继续在最终断句执行 SenseVoice
- **AND** WebSocket 合同不发生变化
