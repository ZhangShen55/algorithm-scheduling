## 背景

当前调度平台已经真实实现并验证了两条 Text Analysis 后置链路：PPT OCR 后按图片调用 `/v1/extract_keywords`，离线 ASR 后调用 `/v1/course_overviews`。这些实现进入了 `orchestrator_service` 的 DAG、适配器、容量租约、Compose、八算子镜像矩阵、24 实例注册、243 条用例和历史 Harness 证据。

业务现在明确不再需要 PPT 关键词和课程脑图，但仍需要 PPT 切片、逐图 OCR、离线 ASR 完整转写、教师/学生视觉、在线图片路由和实时 ASR。`text_analysis/` 源码确定保留，不再作为调度平台算子使用。

当前受控服务器仍有基于提交 `778515596b42123a3061daeb9a1c3bb446f1de1b` 的八算子 Canonical 运行，三套 `text_analysis` 容器已启动。远端 PostgreSQL 还保留 15 个已完成 Text Analysis 节点及3个处于前置等待但所属任务已经失败的历史节点。这些事实要求本次调整同时处理运行时、数据兼容和证据边界，不能只删除两个 DAG 节点。

## 目标 / 非目标

**目标：**

- 新 PPT 任务只执行 `PPT_SLICE -> PPT_OCR`，新 ASR 任务只执行 `ASR_TRANSCRIPTION`。
- 保持 A 服务请求字段、四种 `task_types`、状态码、结果持久化和四服务边界稳定。
- 从平台构建、注册、部署、路由、租约和最终验收中彻底排除 `text_analysis`。
- 保留 `text_analysis/` 源码、历史任务结果、历史 OpenSpec 和 Harness/release 证据。
- 以七类算子、21 个实例和新的最终 Git SHA 重建里程碑 2B 权威基线。
- 明确标记已经完成的 Text Analysis 工作由本次后续范围调整废止，避免未来代理误认为仍是目标能力。

**非目标：**

- 不删除、裁剪或重构 `text_analysis/` 的现有 HTTP 接口和模型逻辑。
- 不改变在线 OCR、离线 OCR、离线/实时 ASR、VBas、FaceRec、ScreenDet 或 PPT Slice 的现有算子协议。
- 不删除历史节点、历史结果、旧报告或旧镜像证据来伪造新基线。
- 不合并四个平台服务，不调整 PostgreSQL、Redis、Kafka、MongoDB 和共享目录的所有权。
- 不在本变更增加新的 NLP/LLM 替代服务。

## 决策

### 1. 保留任务类型，缩短任务类型内部 DAG

`PPT` 和 `ASR` 仍然是 A 服务提交和查询的稳定业务任务类型。只改变 `PipelineInitializer` 为新任务创建的节点：

```text
PPT: PPT_SLICE -> PPT_OCR
ASR: ASR_TRANSCRIPTION
```

PPT 必须在切片和全部 OCR 工作项完成后进入状态 60；ASR 必须在转写结果完成持久化后进入状态 60。查询不为已移除节点制造状态 0、空字典或兼容占位符，因为这会继续把废止能力表达为平台合同。

备选方案是保留节点并自动跳过。该方案会污染状态语义、继续保留无意义的能力标识和查询字段，因此不采用。

### 2. OCR 工作项机制保留，关键词执行机制移除

`node_work_items`、`PptWorkLimits`、逐 `ppt_image_id` 的 OCR 租约和部分进度仍有真实业务价值。实现应把当前混合的 `PptTextPipeline` 收敛为 OCR 专用执行路径，删除 `KeywordAdapter`、关键词工作项申请、`extract_keywords` 能力及其 HTTP 超时配置。

ASR 仍保存 v1.1.8 完整响应和 `effective_params`；删除 `CourseOverviewAdapter` 及从 ASR segments 构建 LLM 请求的代码。

### 3. `text_analysis/` 保留为非平台项目

源码目录继续受 Git 管理，现有接口和自身文档不因本次范围调整而删除。根工作区说明将其列入“保留但非平台依赖”区域，而不是七算子项目地图。

平台公共 `OperatorCode` 删除 `TEXT_ANALYSIS`，Control Service 对新的 `operator_code=text_analysis` 注册返回参数校验错误。Compose、可信服务地址、镜像清单、端点清单和 Smoke 清单不得再引用它。这样即使有人误启动旧服务，也不能进入平台可路由实例池。

备选方案是保留注册编码但不创建 DAG。该方案允许退役实例继续占用运维界面和资源，并给未来代码重新使用留下歧义，因此不采用。

### 4. 历史数据可读，新任务不生成退役节点

已经完成或失败的历史任务及其 `PPT_KEYWORDS`、`COURSE_OVERVIEW` 结果继续由查询接口原样返回。数据库不删除这些节点，也不把等待节点伪造为完成。

切换门禁按任务类型状态检查：如果退役节点所属 `course_task_types.status` 仍为 10、20、30、40 或 50，则禁止部署新版本，先让旧运行完成或按明确运维决定结束该测试任务。所属任务已经是 60、70 或 80 的历史节点不阻止切换。

应用枚举删除不要求清除 PostgreSQL 中 `operator_instances.operator_code=text_analysis` 的历史审计行。前向迁移只更新中文注释和必要约束说明；Redis 中旧实例通过停止容器、注销或 TTL 自然退出实时注册表。

### 5. 七算子部署基线独立于历史八算子 release

新权威拓扑为：

```text
GPU0/1/2: asr_offline、asr_online、ocr、vbas、facerec、screen_det
CPU:      ppt_slice × 3
合计:     7 类算子、21 个实例、18 个 GPU实例、3 个 CPU实例
```

配置权威从 16 个独立解析进程调整为 14 个，GPU 推理证据仍为 18/18，CPU Smoke 调整为3/3，综合 Smoke 调整为7/7。四个平台镜像和基础设施不因本次变更减少。

现有八算子镜像与 release 证据保持只读。只有新的完整 Git SHA 通过七算子构建、注册、真实调用、业务泳道、压力/反例、恢复和最终聚合，才能成为新范围的通过结论。

主机预检仍允许三个旧 `text-analysis-cpu0/1/2` 容器作为停止态历史资产存在，但必须同时满足
Compose project/service、规范容器名、`State.Status=exited` 和 `Running=false` 精确匹配。
它们不属于七算子允许身份集合，任何运行态、名称漂移或其他未知算法容器仍失败关闭；该兼容只为
保留旧 release 事实，不允许重新启动、注册、路由或冒充当前证据。

### 6. Harness 用新语义替换 LLM 用例，不删除历史记录

旧 release 报告、旧 baseline、旧场景执行结果和 `change-ledger.md` 既有条目不得回写。新增独立场景和 baseline，旧文档只追加“后续范围调整已废止”说明。

当前 catalog 中 `DEP-008`、`KEY-001` 至 `KEY-005`、`ASR-014` 至 `ASR-017` 共10条 Text Analysis 用例从新 schema 中退役，并以 `RET-001` 至 `RET-010` 替换，分别验证新 DAG、注册拒绝、部署排除、无租约、历史可查和旧 release 不可冒充新基线。这样继续保留 217 条反例与26条压力/恢复用例的覆盖规模，但不是为了数字复用旧语义。

B 级人工复核删除 `KEY-005` 和 `ASR-017`，从8项调整为6项；PPT、ASR 和视觉仍需人工复核的项目保持不变。

六项复核必须由 Campaign 在真实结果完成后按 `offline`、`vision` 两阶段发布 request，外部复核
输入不得提前预制或跨阶段混合。输入与索引位于整个 Git 工作区和 release 之外，逐案
`observed` 使用固定计数/时长字段，`reviewed_at` 带时区，reviewer 使用可追溯身份；证据只以
`release:<相对路径>#sha256:<摘要>` 绑定当前 release 中已经存在的脱敏文件。原视频、证据图片
和完整 ASR/OCR 文本只用于受限复核，不进入普通 release 报告。

### 7. 活动 OpenSpec 标记废止，完成事实不回滚

`unify-operator-capacity-leases-and-online-ocr` 与 `close-platform-runtime-and-harness-gaps` 中已经完成的 Text Analysis 任务、设计和验证记录保留原文，并在相关章节追加“后续范围调整已废止”的标准说明。尚未完成的最终验收任务改为七算子、PPT/OCR 和 ASR-only 范围。

已经完成的 `build-algorithm-scheduling-platform` 是历史架构来源，不删除其节点描述；在其 proposal、design、tasks 顶部增加指向本变更的后续范围说明。主设计文档保留旧图，并增加带版本和日期的新架构图。

### 8. B 级质量复核失败不得被基础运行门禁覆盖

远端 Attempt 13 已证明七算子部署、注册、真实推理、PPT/OCR、ASR-only 和视觉节点能够运行，
但独立 offline 复核发现两项业务质量阻断：`PPT-014` 漏掉约 `380–430s` 的稳定标注页，
`ASR-013` 在24个中英混合术语片段中有9个严重错误。镜像、健康、状态60或自动回归通过均不能
替代这两项质量结论，也不得据此发布 B 级通过索引。

PPT 漏切根因为平台显式传入的相似度阈值 `0.98` 把相似度 `0.984217` 的完整标注页视为同页；
受控完整视频探针证明改为 `0.99` 后切片由31张增至35张、补出约 `387s` 标注页，同时3个动态
区间仍保持零爆发误切。因此平台默认与根配置统一使用 `0.99`，PPT 算子 HTTP 合同不变。

ASR 的隔离热词探针确认请求热词已经进入 Paraformer 参数，但相同24个片段仍为9个严重错误，
与无热词结果逐段一致。因此不得仅把 `ban_hotword` 改为 `false` 或把接口成功当成质量修复；
现有受控配置继续禁用热词。后续必须由明确的模型/词表改进、可复核测试媒体或用户批准的验收
边界解除 `ASR-013`，之后才能以新 SHA 重跑 Canonical。

## 风险 / 权衡

- [风险] 新旧查询结果的节点集合不同，A 服务若硬编码两个旧节点可能解析失败。→ 在A服务对接指南明确“节点列表按实际 DAG 返回”，增加新任务与历史任务双向查询契约测试。
- [风险] 只改 DAG 而不处理旧节点会让非终态任务永久等待。→ 部署前执行退役节点/任务状态门禁，禁止带活动退役节点切换。
- [风险] 删除 `OperatorCode.TEXT_ANALYSIS` 后旧审计数据无法反序列化。→ 历史 PostgreSQL 审计查询保持字符串展示，不以当前注册枚举重新解释；增加历史行读取测试。
- [风险] 仍在运行的旧 Canonical 继续创建或执行 Text Analysis 任务。→ 应用变更前先通过既有 Controller 中断路径等待精确恢复和终态 audit，不直接杀容器或删除镜像。
- [风险] 固定的 8/24/16/6 数量散布在脚本和测试中，遗漏会造成误判。→ 建立一个七算子拓扑权威并让预检、聚合和清理代码从同一合同派生，同时保留静态搜索门禁。
- [风险] 算子成功返回和节点状态60掩盖实际识别质量。→ 最终报告继续要求分阶段 B 级独立复核；质量失败不得发布通过索引，也不得用健康、Smoke 或旧 release 补足。
- [权衡] `text_analysis/` 仍在工作区，可能被误认为平台算子。→ 从项目地图、构建矩阵和平台注册中移出，并明确标记为“保留、非平台、不部署”。

## 迁移计划

1. 通过现有 Canonical 控制器有界终止旧 `7785155...` 运行，等待 `restore: complete` 和规定终态标记；不得手工删除其24个容器或改写旧 release。
2. 在新变更分支记录当前数据库退役节点、Redis 注册、Compose 和镜像清单基线，确认没有所属任务仍为非终态的退役节点。
3. 修改 DAG、执行器、注册枚举、配置和部署清单；保留历史数据读取与 `text_analysis/` 源码。
4. 增加前向数据库注释迁移，更新活动 OpenSpec、总体设计、A 服务指南和 Harness 新场景/baseline。
5. 执行本机静态、单元、PostgreSQL、Redis、Kafka、四服务和新旧查询兼容验证。
6. 在日志标准化变更已经完成本地验证的前提下，以同时包含两项变更的新完整 SHA 在 `192.168.29.11` 构建七个算子和四个平台镜像，启动21实例并执行配置权威、注册、Smoke、真实泳道、反例、压力和恢复门禁。两个变更之间不得先发布中间镜像。
7. 只有新 release 完整通过后，按精确镜像 ID 清理不再被容器引用的旧平台/算子镜像；`text_analysis` 历史镜像不得在旧 Canonical 恢复完成前删除。

## 回滚策略

代码回滚到变更前 SHA 可以恢复旧 DAG，但不会自动重启或重新注册 `text_analysis`。若必须回滚，必须使用旧 Compose、旧八算子镜像和与旧 SHA 对应的不可变配置权威，并为回滚后新提交的任务重新执行八算子验收。历史数据无需回写。

## 待确认问题

无。`text_analysis/` 保留但不参与平台、历史证据不删除、PPT/ASR 新 DAG 和七算子部署规模均已由用户确认。
