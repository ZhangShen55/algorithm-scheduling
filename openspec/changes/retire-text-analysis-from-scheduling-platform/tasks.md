## 1. 旧基线安全收口与变更保护

- [x] 1.1 记录当前分支、Git SHA、dirty/untracked 文件和 `text_analysis/` 工作区快照，后续实现不得覆盖用户现有改动或修改该项目业务源码。
- [x] 1.2 只读核对 `192.168.29.11` 上旧 `7785155...` Canonical 的 PID、release root、维护锁、24实例账本、原业务快照和当前阶段。
- [x] 1.3 通过既有 Canonical Controller 的有界中断路径结束旧运行，等待 `restore: complete`、规定终态 marker、唯一可信恢复 audit 和维护锁释放；不得直接删除容器、卷、模型、结果、报告或镜像。
- [x] 1.4 记录 PostgreSQL 中 `PPT_KEYWORDS`、`COURSE_OVERVIEW` 节点及所属任务类型状态基线，确认不存在所属任务状态为10至50的活动退役节点。
- [x] 1.5 记录 Redis 当前 Text Analysis 注册/租约、三套历史容器和镜像 ID，仅将其作为旧 release 事实，不在代码切换前执行越界清理。

## 2. OpenSpec 范围继承与历史废止标记

- [x] 2.1 在 `build-algorithm-scheduling-platform` 的 proposal、design 和 tasks 顶部追加“后续范围调整已废止”说明并指向本变更，保留原节点、任务和完成勾选原文。
- [x] 2.2 修订 `unify-operator-capacity-leases-and-online-ocr` 的 proposal、design、specs 和 tasks：保留已经完成的 Text Analysis 实现证据并标记后续废止，把未完成的14.3至14.7验收调整为七算子、PPT/OCR 和 ASR-only 基线。
- [x] 2.3 修订 `close-platform-runtime-and-harness-gaps` 的 proposal、design、specs 和 tasks：保留旧基础闭环证据，废止关键词与课程脑图目标，重写4.9、4.11、8A.4、8A.7、9.1及最终验收中的当前范围。
- [x] 2.4 运行全部活动 OpenSpec 严格校验，确认没有把已经完成的 Text Analysis 工作删除、取消勾选或改写为当时未实现。

## 3. Orchestrator 离线 DAG 收敛

- [x] 3.1 先增加 DAG 契约测试，断言新 PPT 任务只有 `PPT_SLICE/PPT_OCR`、新 ASR 任务只有 `ASR_TRANSCRIPTION`，重复 Kafka 消息不补建退役节点。
- [x] 3.2 修改 `PipelineInitializer` 和节点定义，删除新 DAG 中的 `PPT_KEYWORDS`、`COURSE_OVERVIEW` 及其前置依赖和能力标识。
- [x] 3.3 增加 PPT 终态测试并调整聚合流程，证明全部逐图 OCR 工作项完成后 PPT 任务直接进入60，OCR 无容量时仍进入等待且原因不提及 Text Analysis。
- [x] 3.4 将 `PptTextPipeline` 收敛为 OCR 专用工作项执行，保留 `ppt_image_id`、部分进度、本地扇出和逐图租约，删除关键词适配、关键词租约及恢复路径。
- [x] 3.5 删除 `CourseOverviewAdapter` 的运行时装配和节点执行分支，保留 ASR v1.1.8 完整响应与 `effective_params`，证明 ASR 转写完成后任务直接进入60。
- [x] 3.6 删除 Orchestrator 的 `[text_analysis]`、关键词批次/并发/超时配置以及相关 README 说明，并验证剩余 HTTP 硬超时和租约 TTL 不受影响。
- [x] 3.7 更新 Orchestrator 单元、类型和跨服务测试，移除只服务于关键词/课程脑图的测试，保留并增强 OCR 与 ASR 独立合同。

## 4. Control、注册表与历史数据兼容

- [x] 4.1 先增加注册合同测试，断言 `operator_code=text_analysis` 被拒绝且 Redis 不产生实例、心跳或租约键。
- [x] 4.2 从公共 `OperatorCode` 和 Control 可信服务配置移除 Text Analysis，更新序列化、OpenAPI、指标和测试中的当前算子集合。
- [x] 4.3 审查 PostgreSQL 算子审计读取路径并增加历史行测试，确保旧 `operator_code=text_analysis` 以字符串事实可读但不进入当前 Redis 路由集合。
- [x] 4.4 增加新的前向数据库迁移更新 `node_results`、`node_work_items` 和 `operator_instances` 中文注释，明确当前结构化结果为 OCR/ASR/视觉且 Text Analysis 只可能是历史审计值；不得回改旧迁移作为唯一交付方式。
- [x] 4.5 实现退役节点切换预检：按节点关联任务类型状态检查活动行，状态10至50时失败关闭，终态60/70/80历史行允许保留。
- [x] 4.6 增加新任务无退役节点、历史完成/失败任务仍原样查询、无占位结果和活动退役节点门禁的真实 PostgreSQL 集成测试。

## 5. 七算子部署权威

- [x] 5.1 建立单一七算子拓扑权威，定义7类、21实例、18 GPU、3 CPU、14配置解析进程和7类 Smoke，消除脚本内散落的8/24/16/6常量。
- [x] 5.2 从 `docker-compose.operators.yml` 删除 Text Analysis anchors、环境、挂载、端口和三个服务，同时保持六类 GPU 与三套 PPT 实例身份、端口、网络和单worker合同不变。
- [x] 5.3 删除受控 `text_analysis` TOML、镜像清单、端点清单、Smoke 清单和 Control 静态可信地址中的当前部署项；不得删除 `text_analysis/` 源码目录。
- [x] 5.4 更新 build context、配置权威、预检、注册核验、Smoke、Compose 身份、账本继承和精确镜像清理脚本，使报告严格要求7/21/18/3/14新数量。
- [x] 5.5 更新 clean clone 门禁，证明平台验证不安装 Text Analysis 依赖、不访问外部 LLM且不运行 `text_analysis` 项目测试。
- [x] 5.6 增加静态排除门禁，确保有效平台源码、配置和部署权威不出现 `text_analysis`、`extract_keywords`、`course_overviews`、`PPT_KEYWORDS` 或 `COURSE_OVERVIEW`，同时允许明确标记的历史文档和数据库兼容代码。

## 6. 里程碑 2B 用例与报告合同

- [x] 6.1 把 case catalog 和报告计划升级为新 schema，退役 `DEP-008`、`KEY-001..005`、`ASR-014..017`，保留其他稳定 ID 与26条压力/恢复用例语义。
- [x] 6.2 增加 `RET-001..010`，覆盖新 PPT/ASR DAG、重复消息、注册拒绝、Compose/镜像排除、无退役租约、历史结果可查、活动节点门禁和旧 release 隔离，使新目录仍为217条反例与26条压力/恢复用例。
- [x] 6.3 更新 case runners、计划解析、报告聚合、renderer 和固定范围测试，使它们支持无 KEY 范围及非连续 ASR ID，不把旧 schema 报告误认为当前通过。
- [x] 6.4 把 B 级人工复核集合从8项调整为6项，删除 `KEY-005` 与 `ASR-017`，更新离线/视觉请求发布、索引校验和测试。
- [x] 6.5 更新业务 Campaign，使 PPT 终态只要求切片/OCR、ASR 终态只要求转写，不启动或调用 Text Analysis，也不等待其复核结果。
- [x] 6.6 更新 release 证据聚合和精确镜像清理门禁，要求当前 SHA 的七算子/21实例证据；旧八算子 release 必须明确被拒绝为当前最终证据。
- [x] 6.7 收敛六项 B 级复核发布合同：按 request phase 绑定 SHA、任务、索引和完整 case 集合，校验 Git 外 `0600` 输入、逐案摘要、带时区时间及当前 release 证据摘要，禁止预制和跨阶段混合。

## 7. Harness、AGENTS 与设计文档

- [x] 7.1 新增 `harness/scenarios/text-analysis-scheduling-retirement.md` 和独立七算子 baseline，记录原状态、目标状态、数据边界、验证命令、证据层级及剩余风险。
- [x] 7.2 在相关旧 Harness 场景顶部追加“后续范围调整已废止”说明，保留旧命令、旧数量、旧结果和不可变 release 引用原文。
- [x] 7.3 在 `harness/change-ledger.md` 追加本次范围调整记录，不回写既有条目；实现完成后再记录实际命令、测试数量、远端 release 与最终结论。
- [x] 7.4 更新根 `AGENTS.md`：项目地图只列七个当前平台算子，把 `text_analysis/` 单独列为保留的非平台项目并禁止平台构建、注册和调用。
- [x] 7.5 更新平台 `AGENTS.md` 的稳定 DAG、七算子拓扑和证据门槛；保持四服务职责、A 面字段和历史证据不可改写规则。
- [x] 7.6 新增或迭代平台总体设计文档时保留全部旧架构图，追加带日期/版本的七算子和新 PPT/ASR DAG 图；同步 A 服务指南、数据库说明、部署 README 和相关服务 README。
- [x] 7.7 对 `text_analysis/` 前后工作区快照做一致性对比，确认本变更没有删除或修改其业务源码、接口、配置和项目测试，也没有覆盖用户原有 dirty 文件。

## 8. 本地分层验证

- [x] 8.1 在平台 `.venv` 运行变更文件 Ruff、strict Mypy、`compileall`、四服务 `app.main` 导入和配置解析。
- [x] 8.2 运行 Orchestrator、Control 和公共包完整单元测试，重点验证新 DAG、OCR-only、ASR-only、注册拒绝和历史查询兼容。
- [x] 8.3 使用真实 PostgreSQL 与 Redis 运行迁移、历史数据读取、活动退役节点门禁、七算子注册/租约和清理测试。
- [x] 8.4 使用真实 Kafka 运行提交、Outbox、DAG 初始化、重复消息、重启恢复和新终态跨服务测试，测试不得直接调用 Repository 伪造节点完成。
- [x] 8.5 展开四平台与七算子 Compose，运行配置权威、端点、镜像、Smoke、报告合同、Harness 一致性和 `git diff --check`。
- [x] 8.6 运行 `openspec validate retire-text-analysis-from-scheduling-platform --strict` 及受影响活动变更的严格校验，记录达到的六层验证证据和未覆盖边界。
- [x] 8.7 对 Attempt 13 的 `PPT-014` 漏切执行完整 P 视频阈值探针，把平台 PPT 相似度阈值收敛为 `0.99` 并增加配置回归；不得改变 PPT 算子 HTTP 字段。
- [x] 8.8 对 Attempt 13 的 `ASR-013` 执行同一音频热词隔离探针，证明热词实际进入模型参数；无质量改善时保持受控配置禁用热词，并把结果记录为最终验收阻断而不是伪造通过。

## 9. 远端七算子部署验证

- [ ] 9.1 以同时包含 `standardize-service-file-logging` 和本变更全部实现的新 Git SHA同步到 `192.168.29.11`，先执行主机、旧恢复审计、活动退役节点、模型资产、磁盘、端口和三路媒体预检；不得把日志变更单独发布成中间远端版本。
- [ ] 9.2 构建并 inspect 七个算子和四个平台镜像，校验 `amd64`、完整 revision label 和精确镜像 ID；不得构建或重标 Text Analysis。
- [ ] 9.3 启动四个平台、基础设施和21个算子实例，证明21/21注册、14进程配置权威、18/18 GPU真实推理、3/3 CPU Smoke 和7/7综合 Smoke。
- [ ] 9.4 通过 A 面真实提交贯通 `PPT_SLICE -> PPT_OCR`、ASR-only、教师行为、学生行为、在线图片和实时 ASR，查询结果不得出现退役节点或租约。
- [ ] 9.5 执行新 schema 的217条反例、26条压力/恢复用例和6项 B 级复核，要求同一新 SHA 下没有失败或未执行项。
- [ ] 9.6 验证 Canonical 精确恢复、原 `ocr-v6-amd` 状态、21实例账本、维护锁和唯一恢复 audit，不执行 prune、`down -v` 或数据/证据删除。
- [ ] 9.7 只有最终报告完整通过后，按镜像 ID精确删除无容器引用的旧版本并记录清理证据；保留 `text_analysis/` 源码和所有历史 release。

## 10. 最终复审与交接

- [ ] 10.1 逐条对照三项 delta spec、实现、测试和 Harness，解决所有不符合项或明确记录阻断，不以旧八算子证据补足新基线。
- [ ] 10.2 确认 `unify-operator-capacity-leases-and-online-ocr` 剩余任务已按七算子范围完成，Text Analysis 已完成任务只保留为“后续范围调整已废止”的历史事实。
- [ ] 10.3 确认 `close-platform-runtime-and-harness-gaps` 的 PPT/OCR、ASR-only、视觉和在线泳道目标与新结果合同一致。
- [ ] 10.4 更新 Harness change ledger 的最终 SHA、环境、命令、测试数量、远端证据和剩余风险，提交中文 Conventional Commit 并推送当前变更分支。
- [ ] 10.5 在全部规范、代码、数据门禁、文档和新 release 证据完整后，再决定同步主 specs并归档本变更。
