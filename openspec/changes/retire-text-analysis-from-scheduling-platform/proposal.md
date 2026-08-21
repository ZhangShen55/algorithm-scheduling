## 为什么

算法调度平台已经确定不再消费 `text_analysis` 提供的 PPT 关键词提取和课程脑图能力，继续保留这两个节点会给离线任务引入无业务价值的 LLM 依赖、等待状态、容量租约、部署实例和验收成本。当前里程碑 2B 仍按八类算子、24 个实例及两条文本分析后置链路执行，因此需要在继续最终验收前建立新的范围权威。

## 变更内容

- **BREAKING（离线结果节点）**：PPT 管道从 `PPT_SLICE -> PPT_OCR -> PPT_KEYWORDS` 收敛为 `PPT_SLICE -> PPT_OCR`；ASR 管道从 `ASR_TRANSCRIPTION -> COURSE_OVERVIEW` 收敛为单节点 `ASR_TRANSCRIPTION`。新任务查询结果不再产生 `PPT_KEYWORDS` 或 `COURSE_OVERVIEW` 节点。
- 保持 A 服务的提交接口、`task_id`、四种 `task_types`、媒体字段、ASR 参数、整数状态和 HTTP 业务响应约定不变；PPT OCR 与 ASR 完整转写结果继续保存并可查询。
- `text_analysis/` 源码目录保留为非平台项目，不删除源码、不由平台构建、部署、注册、路由、租赁或调用；Control Service 拒绝新的 `text_analysis` 算子注册。
- **BREAKING（部署基线）**：里程碑 2B 从八类算子、24 个实例调整为七类算子、21 个实例；18 个 GPU 实例保持不变，CPU 实例只保留三套 `ppt_slice`。
- 删除平台运行时中的关键词和课程脑图适配、配置、租约和工作项执行路径，并同步收敛 Compose、端点、镜像、Smoke、配置权威、实例注册和精确镜像清理合同。
- 保留历史任务结果、历史 OpenSpec 工件、Harness 场景、change ledger 和不可变 release 证据；已经完成的 Text Analysis 工作标记为“后续范围调整已废止”，不得删除或改写为当时未实施。
- 对新的七算子基线建立独立 Harness 场景、兼容门禁和最终 release 证据；当前及既有八算子 release 只作为历史实现或失败诊断证据，不得充当新范围的最终通过结论。

## 能力范围

### 新增能力

- `offline-pipeline-without-text-analysis`：规定 PPT 与 ASR 离线 DAG、终态和查询结果在退出文本分析后的合同。
- `retired-text-analysis-boundary`：规定 `text_analysis` 源码保留但退出平台构建、注册、路由、租约和运行时依赖的边界。
- `seven-operator-deployment-baseline`：规定七类算子、21 个实例的新部署、验证、Harness 和历史证据保留合同。

### 调整能力

无。主规格目录中的 `root-level-platform-services` 和 `workspace-git-baseline` 不定义 Text Analysis 离线节点；本变更通过新增能力覆盖尚未归档变更中的旧八算子和文本分析约定。

## 影响范围

- `orchestrator_service`：DAG 初始化、节点执行路由、PPT OCR 工作项、HTTP 超时配置、运行时装配、测试和 README。
- `control_service` 与公共包：算子编码校验、可信服务配置、注册 API 测试、数据库前向注释迁移和历史数据读取边界。
- 部署与验收：算子 Compose、受控 TOML、镜像/端点/Smoke 清单、预检、注册核验、报告聚合、B 级复核、精确清理脚本及其测试。
- OpenSpec 与 Harness：修订两项活动变更中尚未完成或已被后续决策废止的 Text Analysis 内容；保留 `build-algorithm-scheduling-platform` 和既有 release 的历史事实，新增范围调整场景、基线和 change ledger 记录。
- 文档：工作区项目地图、平台总体设计、A 服务对接指南、数据库说明和里程碑 2B 部署文档。
- `text_analysis/`：源码和自身接口保持原样，不在本变更删除、裁剪或继续验证为平台算子。
