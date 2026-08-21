## ADDED Requirements

### Requirement: PPT 任务只包含切片与 OCR 节点
平台 SHALL 为新的 `PPT` 任务创建 `PPT_SLICE -> PPT_OCR` DAG，且 MUST NOT 创建 `PPT_KEYWORDS` 节点或申请 `extract_keywords` 容量租约。

#### Scenario: 新建 PPT 任务
- **WHEN** A 服务提交包含 `PPT`、`task_id` 和有效 `slides_video_path` 的新任务
- **THEN** 查询只返回 `PPT_SLICE` 与 `PPT_OCR` 两个节点，并且二者保持直接依赖关系

#### Scenario: 重放 PPT Kafka 消息
- **WHEN** 同一 PPT 课程命令被重复投递或 Orchestrator 重启后重新消费
- **THEN** 平台保持两个节点幂等且不得补建 `PPT_KEYWORDS`

### Requirement: PPT 任务在 OCR 完成后进入终态
平台 SHALL 在 `PPT_SLICE` 和该任务全部 `PPT_OCR` 工作项成功持久化后把 PPT 任务类型更新为状态 60，不得等待任何 LLM 能力。

#### Scenario: 所有 OCR 工作项完成
- **WHEN** PPT manifest 已验证且每个 `ppt_image_id` 的 OCR 结果均已持久化
- **THEN** `PPT_OCR` 和 PPT 任务类型均进入状态 60，查询结果保留切片文件信息及逐图 OCR 结构化结果

#### Scenario: OCR 实例尚不可用
- **WHEN** PPT 切片已完成但没有可租赁 OCR 实例
- **THEN** PPT 任务继续显示 OCR 等待状态，且原因不得提及关键词或 Text Analysis

### Requirement: ASR 任务只包含转写节点
平台 SHALL 为新的 `ASR` 任务只创建 `ASR_TRANSCRIPTION`，且 MUST NOT 创建 `COURSE_OVERVIEW` 节点或申请 `course_overviews` 容量租约。

#### Scenario: 新建 ASR 任务
- **WHEN** A 服务提交包含 `ASR`、`task_id` 和有效 `teacher_video_path` 的新任务
- **THEN** 查询只返回 `ASR_TRANSCRIPTION` 节点

#### Scenario: ASR 转写成功
- **WHEN** v1.1.8 离线 ASR 完整响应和 `effective_params` 已持久化
- **THEN** `ASR_TRANSCRIPTION` 和 ASR 任务类型直接进入状态 60，不再调用课程脑图接口

### Requirement: A 服务提交合同保持稳定
平台 SHALL 保持 `POST /api/course-jobs` 的既有字段、四种 `task_types`、参数校验、幂等提交、优先级和 HTTP 业务响应约定；本变更只能改变 PPT 与 ASR 的内部节点集合。

#### Scenario: 稀疏 PPT 提交
- **WHEN** 请求仅包含 PPT 所需字段且其他视频字段不存在或为 `null`
- **THEN** 平台按既有规则受理 PPT，不因移除 Text Analysis 而要求新增字段

#### Scenario: 稀疏 ASR 提交
- **WHEN** 请求仅包含 ASR 所需字段及可选 `asr_options`
- **THEN** 平台按既有规则受理 ASR，并在结果中继续返回实际 `effective_params`

### Requirement: 历史节点结果保持可查询
平台 SHALL 保留变更前已持久化的 `PPT_KEYWORDS` 和 `COURSE_OVERVIEW` 节点、状态及结果，并 MUST NOT 为新任务生成兼容占位节点。

#### Scenario: 查询历史完成任务
- **WHEN** A 服务查询变更前已经生成关键词或课程脑图的任务
- **THEN** 查询原样返回历史节点及其结果，不删除、不重算也不改写状态

#### Scenario: 查询新任务
- **WHEN** A 服务查询变更后创建的 PPT 或 ASR 任务
- **THEN** 结果不包含 `PPT_KEYWORDS`、`COURSE_OVERVIEW`、空占位符或虚构的完成状态

### Requirement: 切换前阻止活动退役节点
部署流程 SHALL 在切换到新 DAG 前检查退役节点所属任务类型的状态；只要存在状态 10、20、30、40 或 50 的 `PPT_KEYWORDS` 或 `COURSE_OVERVIEW`，切换 MUST 失败关闭。

#### Scenario: 存在活动退役节点
- **WHEN** 数据库中某个退役节点所属任务类型仍处于非终态
- **THEN** 部署门禁拒绝切换并输出任务标识、任务类型和节点状态，不自动删除或标记完成

#### Scenario: 只有终态历史任务
- **WHEN** 退役节点只属于状态 60、70 或 80 的历史任务类型
- **THEN** 切换允许继续且历史行保持不变
