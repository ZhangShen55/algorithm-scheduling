## ADDED Requirements

### Requirement: AI Quality 模块 SHALL 按职责分层
系统 SHALL 将 `app/ai_quality` 整理为应用编排、领域规则和基础设施三类职责清晰的模块结构。

#### Scenario: 领域规则集中在 domain
- **WHEN** 开发者查看指标聚合、快照策略、学生异常行为统计、评分或稳定 ID 逻辑
- **THEN** 系统 SHALL 将这些逻辑放在 `app/ai_quality/domain/` 下

#### Scenario: 基础设施集中在 infrastructure
- **WHEN** 开发者查看 Kafka、MySQL、视频处理、抓拍存储或视觉模型适配逻辑
- **THEN** 系统 SHALL 将这些逻辑放在 `app/ai_quality/infrastructure/` 下

#### Scenario: 应用编排集中在 application
- **WHEN** 开发者查看单课次 Worker 编排和依赖组装逻辑
- **THEN** 系统 SHALL 将这些逻辑放在 `app/ai_quality/application/` 下

### Requirement: Worker 启动方式 SHALL 保持兼容
系统 SHALL 在模块结构整理后保持现有 AI Quality Worker 的 CLI 启动方式可用。

#### Scenario: run-json 启动方式不变
- **WHEN** 用户执行 `python -m app.ai_quality.app --config app/config.toml run-json <message>`
- **THEN** 系统 SHALL 使用整理后的模块结构正常处理 JSON 任务

#### Scenario: consume 启动方式不变
- **WHEN** 用户执行 `python -m app.ai_quality.app --config app/config.toml consume`
- **THEN** 系统 SHALL 使用整理后的模块结构正常启动 Kafka 消费流程

### Requirement: 结构重构 SHALL 保持业务行为等价
系统 SHALL 在结构整理过程中保持 AI 课堂视觉分析业务行为等价，不改变 Kafka、抽帧、模型分析、快照、学生异常行为统计、指标评分和数据库写入口径。

#### Scenario: 数据库写入口径不变
- **WHEN** Worker 在结构整理后成功处理同一类课次任务
- **THEN** 系统 SHALL 继续写入 `lesson_ai_job`、`lesson_ai_workflow`、`lesson_behavior_timeline`、`lesson_snapshot_event`、`lesson_student_behavior_stat` 和 `indicator_score_result`
- **AND** 系统 MUST NOT 改变这些表的字段口径、唯一键使用方式或业务 ID 生成规则

#### Scenario: 图片相对路径不变
- **WHEN** Worker 在结构整理后保存核心快照图片
- **THEN** 系统 SHALL 继续在 `lesson_snapshot_event.image_url` 保存 `cv/{task_id}/{image_id}.png` 相对路径

#### Scenario: 算法策略不变
- **WHEN** Worker 在结构整理后生成指标、快照事件和学生异常行为统计
- **THEN** 系统 MUST NOT 改变既有指标算法、快照触发阈值、学生异常行为统计窗口或 `peak_period_desc` 生成规则

### Requirement: DB 仓储边界 SHALL 可维护
系统 SHALL 将数据库访问边界整理到 `infrastructure/db/`，并 SHALL 避免应用编排层直接散落多个表级 SQL。

#### Scenario: Worker 依赖仓储门面
- **WHEN** Worker 需要写入任务状态、时间线、快照、行为统计或指标结果
- **THEN** Worker SHALL 通过仓储门面调用数据库写入能力
- **AND** Worker MUST NOT 直接拼写表级 SQL

#### Scenario: 表级职责可拆分
- **WHEN** 仓储文件继续增长或新增表写入逻辑
- **THEN** 系统 SHALL 支持在 `infrastructure/db/` 下按表或领域拆分仓储实现
- **AND** 对应用编排层保持稳定的仓储门面

### Requirement: 重构后 SHALL 完整验证
系统 SHALL 在模块结构整理后执行自动化测试、启动验证和全量视频任务验证。

#### Scenario: 单元测试通过
- **WHEN** 模块结构整理完成
- **THEN** 系统 SHALL 通过 `tests/test_ai_quality*.py` 测试集

#### Scenario: CLI smoke 通过
- **WHEN** 模块结构整理完成
- **THEN** 系统 SHALL 验证 `python -m app.ai_quality.app` 入口仍可加载并执行

#### Scenario: 全量视频重跑成功
- **WHEN** 模块结构整理完成并通过单元测试
- **THEN** 系统 SHALL 使用本地 Nginx 视频 URL 执行一条全量课次任务
- **AND** 系统 SHALL 查询数据库确认任务成功、核心表有结果、错误信息为空
