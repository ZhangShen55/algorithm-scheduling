## ADDED Requirements

### Requirement: 日志标准化范围
平台 MUST 只对七个当前算法算子和四个平台服务实施统一文件日志，MUST 将 `text_analysis/` 排除在代码、配置、镜像和部署改造之外。

#### Scenario: 十一个目标项目纳入
- **WHEN** 检查日志标准化项目清单
- **THEN** 清单包含 `asr_offline`、`asr_online`、`facerec`、`ocr`、`screen_det`、`ppt_slice`、`vbas` 和四个平台服务

#### Scenario: Text Analysis 保持不变
- **WHEN** 比较实施前后的 `text_analysis/` 工作区快照
- **THEN** 其业务源码、配置、日志、镜像和部署文件均未被本变更修改且不进入新平台 Compose

### Requirement: 项目根日志目录与实例隔离
每个目标项目 MUST 将相对日志目录从显式项目根解析，并 MUST 将规范日志文件写入 `logs/{instance_id}/application.log`；多个实例 MUST 使用不同目录，不得共享同一活动日志文件。

#### Scenario: 本地默认目录
- **WHEN** 从项目根使用默认配置启动任一目标项目
- **THEN** 服务在项目根 `logs/local/application.log` 或由其稳定本地实例标识确定的等价目录创建日志

#### Scenario: 多实例独立写入
- **WHEN** 同一算子以两个不同 `instance_id` 启动并分别写日志
- **THEN** 两个实例只写入各自实例目录且日志事件包含对应 `instance_id`

#### Scenario: CONFIG_PATH 不改变项目根
- **WHEN** 使用项目根外的 `CONFIG_PATH` 启动目标项目且 `directory` 为相对路径
- **THEN** 日志目录仍从目标项目根解析而不是从配置文件所在目录或当前工作目录解析

### Requirement: 单文件大小与七日保留
每个目标项目 MUST 默认把单个日志文件限制为 `100 MiB`，MUST 保留未超过 `7` 天的归档，并 MUST 在启动时和轮转后清理超过保留期的当前实例归档；大小与天数 MUST 可在 `config.toml` 调整。

#### Scenario: 达到大小上限时轮转
- **WHEN** 连续写入将使活动日志超过配置的 `max_file_size_mib`
- **THEN** Handler 在写入前归档活动文件并继续写入新文件，任一生成日志文件均不超过配置上限

#### Scenario: 清理过期归档
- **WHEN** 当前实例目录同时存在超过七日和未超过七日的合法归档且服务启动
- **THEN** 只删除超过 `retention_days` 的归档并保留活动文件和未过期归档

#### Scenario: 拒绝越界清理
- **WHEN** 日志目录包含符号链接或实例目录外存在同名文件
- **THEN** 清理逻辑不得跟随符号链接或删除当前实例日志目录外的文件

### Requirement: 文件与标准输出并存
目标项目在生产部署中 MUST 同时输出文件日志与 `stdout`，两个输出 MUST 使用一致的结构化事件；日志初始化 MUST 幂等，不得因重复配置产生重复记录。

#### Scenario: 同一事件双输出
- **WHEN** 服务记录一条业务审计事件
- **THEN** 文件与捕获的 `stdout` 各出现一次语义一致的 JSON Lines 事件

#### Scenario: 重复初始化
- **WHEN** 同一进程两次调用日志配置器后记录一条事件
- **THEN** 文件与 `stdout` 均只出现一次该事件且 root/Uvicorn handler 数量不增长

#### Scenario: 文件日志不可用
- **WHEN** 启用文件日志但实例目录无法安全创建或写入
- **THEN** 服务向 `stderr` 输出不含敏感配置的诊断并中止启动，不得静默进入就绪状态

### Requirement: 结构化日志上下文
每条规范日志 MUST 至少包含 `timestamp`、`service`、`instance_id`、`level`、`logger`、`event` 和 `trace_id`；存在节点或算子上下文时 MUST 追加相应标识，不存在时不得伪造业务值。

#### Scenario: 普通服务事件
- **WHEN** 健康检查之外的普通服务代码记录日志
- **THEN** JSON 事件包含全部基础字段且可被标准 JSON 解析器逐行解析

#### Scenario: 节点执行事件
- **WHEN** Orchestrator 记录节点领取、调用或终态审计
- **THEN** 事件同时包含可用的 `task_id`、`task_type`、`node`、`operator_code`、`attempt`、`elapsed_ms` 和 `outcome`

#### Scenario: 在线请求链路
- **WHEN** Online Gateway 携带或生成 `trace_id` 并调用在线算子
- **THEN** 网关与算子日志能够使用该 `trace_id` 关联且不记录图像或音频内容

### Requirement: 敏感内容和大字段排除
目标项目 MUST 使用允许列表记录启动配置与业务上下文，MUST NOT 记录 Base64/Data URL、媒体字节、完整请求体、Authorization/Token、Cookie、密码、完整数据库 DSN、完整 ASR 文本或完整 OCR 文本。

#### Scenario: 请求与结果哨兵不落日志
- **WHEN** 测试请求包含可识别的 Base64、Token、密码、ASR 文本和 OCR 文本哨兵值
- **THEN** 文件日志与 `stdout` 均不包含这些原值，只保留允许的标识、大小、耗时和结果状态

#### Scenario: 启动配置摘要
- **WHEN** 服务启动并记录有效配置摘要
- **THEN** 摘要只包含允许的运行标识和非敏感参数，不序列化完整 Settings 或带凭据连接字符串

#### Scenario: 下游异常脱敏
- **WHEN** 下游 HTTP/WebSocket 或数据库异常消息携带请求内容或凭据
- **THEN** 日志保留异常类型、受控消息和堆栈，但敏感原值被移除或替换

### Requirement: 统一可配置合同
七个算子和四个平台服务的根 `config.toml` MUST 提供带中文注释的 `[logging]` 字段 `level`、`directory`、`file_name`、`max_file_size_mib`、`retention_days`、`stdout_enabled` 和 `file_enabled`，并 MUST 校验非法值后失败关闭。

#### Scenario: 默认配置一致
- **WHEN** 解析 11 个目标项目的根配置和 Canonical 部署配置
- **THEN** 默认值均为 `logs`、`application.log`、`100`、`7`、启用 stdout 和启用文件

#### Scenario: 配置覆盖生效
- **WHEN** 测试配置把大小和保留天数设置为较小合法值
- **THEN** 运行时 Handler 使用覆盖值且无需修改代码

#### Scenario: 非法配置失败关闭
- **WHEN** 大小、保留天数为空、非整数或小于等于零，或文件名可导致路径越界
- **THEN** 配置解析返回明确中文错误且服务不启动

### Requirement: 必要代码注释与业务兼容
本变更新增或修改的非直观日志初始化、轮转、清理、脱敏和上下文代码 MUST 包含简洁中文注释说明约束原因；注释与日志接入 MUST NOT 改变接口、任务、注册租约、模型推理或进程拓扑行为。

#### Scenario: 非直观逻辑具有原因注释
- **WHEN** 复审共享 Handler、Uvicorn 接管、归档清理、脱敏允许列表和实例路径代码
- **THEN** 关键边界具有解释“为什么”的简洁中文注释且没有逐行叙述性注释

#### Scenario: 排除无关注释改动
- **WHEN** 检查本变更 diff
- **THEN** 不存在针对 `text_analysis`、vendor、模型生成代码或未修改业务模块的批量注释变更

#### Scenario: 运行合同保持稳定
- **WHEN** 对七个算子运行 compile/import、路由快照、健康和真实推理，并对四服务运行配置、OpenAPI、健康及跨服务测试
- **THEN** 除新增日志副作用外，既有请求响应、任务状态、注册租约、推理结果合同和 Uvicorn worker 要求保持不变

### Requirement: 容器内默认可见的日志
每个目标镜像 MUST 创建项目根 `logs/` 目录并支持在容器内查看当前实例日志；应用在没有任何日志卷挂载时 MUST 正常写入容器可写层。宿主机目录 `/data/logs/algorithm-scheduling/{service}/{instance_id}` 到容器 `logs/{instance_id}` 的挂载 MUST 作为可选部署能力，启用时才需要校验目录安全性和可写性。

#### Scenario: 二十一个算子实例独立日志目录
- **WHEN** 七类算子的 21 个实例全部启动并各记录一个实例事件
- **THEN** 每个容器内存在 21 个对应的 `logs/{instance_id}` 目录；启用宿主机挂载时，宿主机同时存在 21 个对应目录且每个目录中的事件只声明对应实例

#### Scenario: 四个平台服务可选持久化
- **WHEN** 四个平台服务启动、处理健康检查和至少一条业务链路
- **THEN** 未挂载时四个服务在各自容器的项目根 `logs/{instance_id}` 写入日志；启用独立宿主机挂载时，日志同时在对应宿主机目录持续写入，容器重建后旧日志仍保留

#### Scenario: 容器内查看日志
- **WHEN** 目标容器运行并已经记录一条实例事件
- **THEN** 使用容器内 shell 查看项目根 `logs/{instance_id}/application.log` 可以读到该事件；启用宿主机挂载时，该文件同时对应宿主机挂载目录中的同一文件

#### Scenario: 未挂载日志卷
- **WHEN** 使用不包含日志卷的 `docker run` 或 Compose 配置启动目标容器
- **THEN** 容器仍然成功启动并在项目根 `logs/{instance_id}/application.log` 创建和写入日志，不因缺少宿主机目录而失败

#### Scenario: 挂载是可选项
- **WHEN** 部署配置未启用宿主机日志挂载
- **THEN** 不执行宿主机日志目录预检，不影响服务健康检查、就绪状态或业务请求处理

#### Scenario: 不改变部署拓扑
- **WHEN** 展开实施后的 Canonical Compose
- **THEN** 日志挂载无论启用或关闭，都没有改变七算子 21 实例、GPU/CPU 分配、端口、网络、模型和结果目录合同

#### Scenario: 日志代码进入新镜像
- **WHEN** 从同时包含日志标准化和 Text Analysis 退役的最终 Git SHA 构建应用镜像
- **THEN** 镜像包含日志初始化代码、配置和项目根 `logs/` 目录，运行容器在无挂载时可以写入容器可写层，按实例挂载时可以写入宿主机目录

### Requirement: Harness 专项证据
平台 MUST 为统一日志提供独立 Harness 场景和不可混淆的验证记录，MUST 使用同时包含 Text Analysis 退役与日志标准化的新完整 Git SHA 完成远端最终验收。

#### Scenario: 本地分层证据完整
- **WHEN** 日志变更准备进入远端构建
- **THEN** Harness 已记录 11 项配置/启动验证、七算子真实推理、四服务测试、轮转、七日清理、脱敏和 `text_analysis` 快照结果

#### Scenario: 同一新 SHA 远端验收
- **WHEN** 在 `192.168.29.11` 执行七算子新基线部署
- **THEN** retirement 与 logging 的报告引用同一个完整 Git SHA，且旧八算子 release 或单独变更 SHA 不能作为当前通过证据
