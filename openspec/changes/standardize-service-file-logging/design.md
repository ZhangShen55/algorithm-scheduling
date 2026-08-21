## 背景

七个当前平台算子已有三类日志现状：`asr_offline` 使用按日轮转，部分图像算子使用按大小轮转，`asr_online` 仍使用普通 `FileHandler`；四个平台服务主要通过 `platform_common.logging` 输出 JSON 到 `stdout`。目录、文件名、大小上限、保留周期、Uvicorn 日志接管和敏感内容约束都不一致。

里程碑 2B 将在同一服务器启动七类算子的 21 个实例和四个平台服务。日志如果继续保存在容器可写层，镜像重建会丢失；如果多个实例共用同一文件，又会发生写入竞争和归属不清。与此同时，`retire-text-analysis-from-scheduling-platform` 已确定 `text_analysis/` 不属于平台新基线，本变更不得再次把它纳入公共日志改造。

## 目标 / 非目标

**目标：**

- 让七个算子和四个平台服务同时输出 JSON Lines 文件日志与 `stdout`。
- 默认写入项目根目录 `logs/{instance_id}/application.log`，单个文件不超过 `100 MiB`，归档保留 `7` 天。
- 让本地运行、Docker Compose 和远端 21 实例都能使用同一配置语义，并将远端日志持久化到 `/data/logs/algorithm-scheduling/{service}/{instance_id}`。
- 统一必要上下文字段、Uvicorn 日志接管、脱敏与大字段排除策略。
- 对新增或修改的非直观日志逻辑补充解释“为什么”的简洁中文注释，并通过现有启动、接口和真实推理验证证明没有业务回归。

**非目标：**

- 不改造 `text_analysis/` 的源码、配置、文档、镜像或部署。
- 不改变 HTTP/WebSocket 合同、任务 DAG、注册/租约、模型加载、推理结果或默认端口。
- 不引入集中日志检索平台、日志采集 Agent、ELK/Loki、告警页面或新的运维 API。
- 不给第三方、vendor、模型生成代码或未改动业务代码批量添加注释。
- 不把完整请求/响应或媒体数据写日志来替代数据库与业务结果存储。

## 决策

### 1. 一个合同、两处共享实现

七个算子均已依赖 `algorithm-operator-registry-client==0.2.0`，本变更在该已发布 wheel 中加入无 FastAPI 业务耦合的文件日志配置器和导出 API，保持现有精确版本合同不变；后续若需要版本升级再单独提出变更。四个平台服务继续复用 `packages.platform_common.logging`，扩展其现有 JSON formatter 和配置器。两处实现遵守同一配置模型与合同测试，不在 11 个项目复制轮转、清理和脱敏算法。

备选方案是新建第三个日志 wheel 并让全部项目依赖它。它能获得单一实现，但会增加构建、wheelhouse、版本锁和 clean clone 门禁，现阶段收益不足。另一方案是在每个项目独立实现，会产生 11 份容易漂移的代码，因此不采用。

### 2. 配置合同使用 `[logging]`，实例标识复用运行时权威

每个目标项目的根 `config.toml` 增加带中文注释的统一字段：

```toml
[logging]
level = "INFO"
directory = "logs"
file_name = "application.log"
max_file_size_mib = 100
retention_days = 7
stdout_enabled = true
file_enabled = true
```

相对 `directory` 必须从显式项目根解析，`CONFIG_PATH` 不改变项目根。日志路径为 `{directory}/{instance_id}/{file_name}`。算子复用注册运行时的 `instance_id`；平台服务按已有服务/Worker 身份解析实例标识，Canonical Compose 显式传入稳定值。本地未配置实例标识时使用可预测的 `local` 后缀，不用随机 UUID 创建无限目录。

旧 `[service].log_level`、`log_path`、`log_dir` 等字段在迁移期由各项目适配层读取并收敛到新合同；最终只能保留生产代码实际消费的配置，不允许两个字段同时生效却优先级不明。部署权威 TOML 与根配置同步更新。

备选方案是让 `directory` 直接包含 `instance_id`。该方式容易在 Compose 和应用中重复拼接实例名，故不采用。

### 3. 使用大小轮转并按归档年龄清理

Python 标准 `RotatingFileHandler` 只负责大小，不提供精确按天清理。本变更在两处共享实现中提供等价的受控 Handler：写入前按 `100 MiB` 上限轮转，归档文件使用 UTC 时间和单调序号命名；启动时和每次轮转后删除修改时间早于 `retention_days` 的归档。活动 `application.log` 不按年龄删除，归档日志保留满七天后才清理。

单条日志事件必须经过有界格式化，不能因异常对象或大字段让新文件单条超过上限。目录创建、归档重命名和清理只能作用于当前实例日志目录，拒绝符号链接和越界文件名。应用当前均为单 Uvicorn worker；多容器通过实例目录隔离，因此不引入跨进程文件锁。

备选方案是只设置 `backupCount`。当日志量变化时它不能表达“保留 7 日”，因此不采用。仅依赖宿主机 `logrotate` 会让本地和容器内行为不一致，也不采用。

### 4. 文件与 stdout 使用同一结构化事件

根 logger、`uvicorn.error`、`uvicorn.access` 和业务 logger 使用同一个 JSON Lines formatter，至少输出 `timestamp`、`service`、`instance_id`、`level`、`logger`、`event`、`trace_id`；节点或算子上下文存在时附加 `task_id`、`task_type`、`node`、`operator_code`、`operator_task_id`、`attempt`、`elapsed_ms` 和 `outcome`。字段不存在时不伪造业务值。

初始化必须幂等，重复调用不能叠加 handler 或重复输出。`stdout_enabled` 和 `file_enabled` 可分别控制输出，但生产 Compose 两者都必须为 `true`。文件初始化失败时不得静默宣称就绪：先向 `stderr` 输出不含敏感配置的错误并中止启动，避免无持久日志运行。

### 5. 采用允许列表记录配置与上下文

启动摘要只记录服务名、实例名、版本、日志参数、设备标识和不含凭据的依赖地址摘要，禁止对完整 Settings、请求模型、异常请求体或下游响应执行通用序列化。Base64/Data URL、音视频/图片字节、Authorization/Token、Cookie、密码、完整 PostgreSQL/MongoDB DSN、完整 ASR/OCR 文本和完整请求体不得进入日志。

异常日志保留异常类型、受控消息和堆栈；对可能携带输入内容的下游异常先通过脱敏器处理。测试使用哨兵 Base64、Token、密码、OCR 文本和 ASR 文本验证文件与 stdout 均不存在原值。

### 6. 注释只覆盖非直观的新改逻辑

必须在以下位置添加简洁中文注释：标准库轮转无法同时表达“按大小+按天”的补偿逻辑、启动清理的边界、实例目录隔离原因、Uvicorn handler 去重、允许列表脱敏原因，以及 stdout 先于文件失败的诊断顺序。简单赋值、显然的目录创建、测试断言和业务推理代码不添加叙述性注释。

代码注释是维护性约束，不得通过 docstring 副作用、条件常量、导入时执行或修改控制流来实现。实现验收同时比较 OpenAPI、路由、配置解析、健康检查和真实推理结果合同，防止借日志改造夹带业务变化。

### 7. 容器内默认日志，宿主机挂载可选

每个镜像必须创建项目根下的 `logs/` 基础目录，应用默认将日志写入 `logs/{instance_id}/application.log`。因此即使 `docker run` 或 Compose 不配置日志卷，进入容器后仍然可以直接查看日志。未挂载时日志位于容器可写层，容器删除或重建后不保证保留；这适合本地验证和临时运行。

需要跨容器重建保留日志时，部署配置可以将宿主机 `/data/logs/algorithm-scheduling/{service}/{instance_id}` 挂载到容器项目根下的 `logs/{instance_id}`。挂载是可选部署能力，不是应用启动或就绪的前置条件。启用挂载时，预检创建宿主机目录并校验目录归属、可写性、非符号链接和剩余磁盘；不得挂载整个项目根或让不同实例共写一个文件。无挂载时不执行宿主机目录预检。

本变更会修改日志代码、配置和 Dockerfile，因此必须构建包含本变更代码的应用镜像；但只在两个变更本地验证完成后统一构建一次。它不改变七算子 21 实例、GPU/CPU 分配、端口、网络、模型卷或四服务边界。旧 release 日志和容器不作为新 SHA 的通过证据。

### 8. 与 Text Analysis 退役共用一次远端构建

本地实施顺序采用“先日志、后退役”：先完成本日志变更的共享实现、11 个项目接入、注释复审和本地验证，再完成 `retire-text-analysis-from-scheduling-platform` 的 DAG、注册、部署清单和 Harness 本地任务。两个变更都通过本地验证后，以同时包含两项变更的同一个完整 Git SHA 执行七算子/四服务远端构建、部署和最终验收。两个本地阶段之间不构建、不替换远端运行容器，因此不会产生一次无效的中间镜像发布。

如果在日志变更完成后提前构建或部署中间版本，退役变更会改变 Orchestrator/Control 运行代码、配置权威、Compose 和当前算子拓扑，必须为最终 SHA 重新构建受影响的应用镜像；按当前 2B 的完整 revision 与同 SHA 证据合同，最终发布仍应重建七个算子和四个平台镜像。`text_analysis` 镜像不属于新发布，不因本变更构建。

日志 Harness 独立记录 11 个项目的配置、轮转、清理、脱敏、容器持久化和重启连续性；最终结论引用同一 release，但不能用 retirement 的业务 Smoke 代替日志专项检查。

## 风险 / 权衡

- [风险] 统一接管 Uvicorn logger 后产生重复日志或丢失访问日志。→ 增加幂等初始化、handler 数量和 HTTP 访问日志合同测试。
- [风险] 七日清理误删实例目录外文件。→ 只处理已解析且非符号链接的当前实例目录，并用临时目录/越界文件测试。
- [风险] 单条异常信息过大或包含 Base64。→ 使用字段允许列表、有界格式化和敏感哨兵测试；业务结果继续进入数据库而非日志。
- [风险] 文件系统只读或磁盘满导致服务无法启动。→ 启动时先建立 stdout/stderr 诊断，再失败关闭；Compose preflight 提前检查挂载和空间。
- [风险] 修改已有日志模块影响算子启动或推理。→ 按项目执行 compile/import、配置、路由、健康和真实推理验证，保持 HTTP/WebSocket 合同快照稳定。
- [权衡] 两处共享实现仍存在语义漂移可能。→ 用同一参数化合同测试校验格式、轮转、清理和脱敏，不在各业务项目复制核心算法。
- [权衡] 每实例单独目录增加目录数量。→ Canonical 拓扑只有 25 个目标进程/容器，换取归属清晰和无跨容器写竞争是合理的。

## 迁移计划

1. 记录 11 个目标项目和 `text_analysis/` 的日志配置、现有 handler、dirty 文件及容器挂载基线。
2. 先增加共享合同测试，再扩展 `operator_registry_client` 与 `platform_common.logging`。
3. 逐个接入七个算子和四个平台服务，迁移 TOML、README、Dockerfile/Compose、`.gitignore` 和项目测试。
4. 执行 11 项静态/启动验证、七算子真实推理、四服务健康与受影响跨服务测试；对 `text_analysis/` 做前后快照一致性检查。
5. 更新 Harness 场景和 change ledger；与 Text Analysis 退役变更使用同一最终 SHA 在 `192.168.29.11` 构建。
6. 验证 21 算子实例和四平台服务的独立宿主机目录、100 MiB 轮转、七日清理模拟、重启连续性、stdout 共存及敏感哨兵排除。

回滚时恢复旧日志初始化与 Compose 挂载，但保留已经产生的宿主机日志目录，不删除日志证据。回滚不得停止或删除无关容器，也不得删除 `/data/result`。

## 待确认问题

无。`text_analysis` 排除、七算子加四服务范围、单文件 `100 MiB`、保留 `7` 天、项目根 `logs/`、必要代码注释和不影响业务运行均已由用户确认。
