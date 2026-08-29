## ADDED Requirements

### Requirement: ASR 新执行版本必须使用业务默认参数

平台在 `asr_options` 缺失或字段缺失时，MUST 将 `showSpk` 和 `showEmotion` 默认设置为 `false`，并同时补齐 `showRoleIdentify=false`、`wordTimestamps=false` 及其他既有默认字段，形成完整的 `effective_params`。

#### Scenario: 未提供 ASR 参数
- **WHEN** A 服务提交包含 ASR 的课程任务且 `asr_options` 为 `null` 或未提供
- **THEN** 平台创建的 ASR 执行版本必须保存 `showSpk=false`、`showEmotion=false`、`showRoleIdentify=false` 和 `wordTimestamps=false`

#### Scenario: 只提供部分 ASR 参数
- **WHEN** 请求只提供部分 `asr_options` 字段
- **THEN** 平台必须用业务默认值补齐其余字段，并把补齐后的完整对象保存为 `effective_params`

### Requirement: ASR 参数必须生成稳定执行指纹

平台 MUST 对完整 `effective_params` 进行规范化并生成稳定的 `params_fingerprint`；字段顺序、缺省字段表达和 JSON 空白差异不得造成不同指纹。

#### Scenario: 语义相同的参数表达
- **WHEN** 两次请求的 ASR 参数语义相同但 JSON 字段顺序不同或省略了默认字段
- **THEN** 两次请求必须生成相同的 `params_fingerprint`

### Requirement: 相同参数的成功结果必须幂等复用

平台 MUST 以 `task_id + ASR + params_fingerprint` 查找已成功执行版本。找到成功版本时 MUST 返回该版本的 `run_id`、`effective_params` 和结果，且不得新增 Outbox 事件或再次调用 ASR 算子。

#### Scenario: 相同参数重复提交
- **WHEN** 某课程的 ASR 参数版本已经成功，A 服务用同一 `task_id` 和同一组参数再次提交
- **THEN** 平台必须复用原执行版本并返回原结果，ASR 算子调用次数不得增加

### Requirement: 新参数必须创建独立执行版本

平台 MUST 在同一 `task_id` 下区分不同的 ASR 参数指纹。对于没有成功或活动执行版本的新指纹，平台 MUST 创建新的 `run_id`、初始化对应节点并在事务内写入一条 Outbox 事件，不得覆盖其他参数版本。

#### Scenario: false 参数切换为 true 参数
- **WHEN** 某课程已经完成 `showSpk=false`、`showEmotion=false`，随后提交 `showSpk=true`、`showEmotion=true`
- **THEN** 平台必须创建新的 ASR 执行版本并重新调度，原 false 参数版本和结果必须保留

#### Scenario: 已有 true 参数版本再次提交
- **WHEN** 某课程之前已经成功处理过 `showSpk=true`、`showEmotion=true`
- **THEN** 平台必须复用该 true 参数版本，不得重新调度

### Requirement: 活动执行版本必须防止重复调度

相同参数已有处于等待、运行或其他非终态的执行版本时，平台 MUST 返回该活动版本，而不是创建第二个活动版本或重复发布 Outbox 事件。数据库事务和唯一约束 MUST 保证并发请求下只有一个活动版本被创建。

#### Scenario: 相同参数并发提交
- **WHEN** A 服务同时发送多个相同 `task_id` 和相同 ASR 参数的请求
- **THEN** 所有请求必须得到同一个 `run_id`，并且只产生一条可执行的 Outbox 事件

### Requirement: 执行版本结果和查询必须可追踪

平台 MUST 持久化每个 ASR 执行版本的 `effective_params`、状态、中文 `reason`、结果、创建时间和完成时间。提交和查询响应 MUST 能区分本次请求选择的执行版本；未指定参数时 MUST 返回最近一次请求对应的版本，并提供历史版本摘要。

#### Scenario: 查询不同参数版本
- **WHEN** 同一 `task_id` 存在 false 和 true 两个 ASR 参数版本，A 服务查询 true 参数版本
- **THEN** 响应必须返回 true 版本的状态、参数和结果，不能返回 false 版本的结果

#### Scenario: 失败版本再次请求
- **WHEN** 某参数版本执行失败或被取消，之后 A 服务再次请求同一组参数
- **THEN** 该失败版本不得被当作成功结果复用，平台必须允许创建新的执行版本

### Requirement: ASR 算子接口保持兼容

本变更 MUST 不修改 ASR 算子现有 HTTP 接口路径、请求字段和响应字段。平台调用算子时 MUST 传递执行版本保存的完整 `effective_params`。

#### Scenario: 新旧 ASR 算子实例混合部署
- **WHEN** 调度器把新执行版本分配给任意已注册的 ASR 离线实例
- **THEN** 请求路径和字段必须保持现有契约，实例能够按原接口处理
