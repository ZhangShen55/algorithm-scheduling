## ADDED Requirements

### Requirement: Text Analysis 退出平台算子集合
平台 SHALL 把 `text_analysis` 从可注册算子编码、可信服务地址、实例路由、容量租赁和运维可用实例集合中移除。

#### Scenario: 旧 Text Analysis 尝试注册
- **WHEN** 服务向 Control Service 提交 `operator_code=text_analysis` 的注册请求
- **THEN** Control Service 以确定的参数校验错误拒绝注册，Redis 不产生可路由实例或租约状态

#### Scenario: 平台请求可用能力
- **WHEN** Orchestrator 或运维接口枚举当前需要和可租赁的能力
- **THEN** 结果不包含 `extract_keywords`、`course_overviews` 或 `text_analysis`

### Requirement: 平台不得构建或部署 Text Analysis
平台受控部署 SHALL NOT 包含 `text_analysis` 的 Compose 服务、受控 TOML、镜像构建项、宿主机端点、Smoke 项或精确镜像清理中的当前目标项。

#### Scenario: 展开算子 Compose
- **WHEN** 部署预检展开七算子 Compose
- **THEN** 展开结果只有21个算子服务且不存在 `text-analysis-cpu0/1/2`

#### Scenario: 构建发布镜像
- **WHEN** 构建脚本读取当前算子镜像权威清单
- **THEN** 只构建七个算子镜像，不构建或重新标记 `algorithm-text-analysis`

### Requirement: Text Analysis 源码作为非平台项目保留
工作区 SHALL 保留 `text_analysis/` 源码和既有接口文件，但 MUST 将其标记为非平台项目，并且本变更不得删除、裁剪或修改其业务接口来模拟退役。

#### Scenario: 检查工作区源码
- **WHEN** 开发者检查变更后的工作区
- **THEN** `text_analysis/` 仍存在，同时项目地图明确说明它不由调度平台使用、部署或验证

#### Scenario: 平台 clean clone 门禁
- **WHEN** 里程碑 2B 在 clean clone 中执行平台构建和验证
- **THEN** 平台门禁不要求安装 `text_analysis` 依赖、访问外部 LLM 或运行其项目测试

### Requirement: 历史实现记录必须标记为后续废止
相关活动 OpenSpec、Harness 场景和设计文档 SHALL 保留已经完成的 Text Analysis 实现与验证事实，并在不改写原结论的前提下标记为“后续范围调整已废止”。

#### Scenario: 阅读旧容量变更
- **WHEN** 开发者查看已经完成的 Text Analysis 容量、关键词租约或课程脑图任务
- **THEN** 原任务和证据仍可见，并明确指向 `retire-text-analysis-from-scheduling-platform` 作为后续废止依据

#### Scenario: 阅读历史 release
- **WHEN** 开发者查看八算子、24 实例的历史 Harness 或远端 release
- **THEN** 文档仍陈述当时实际执行结果，不把历史数量改写为七算子或21实例

### Requirement: 历史审计数据不得因当前枚举收敛而丢失
平台 SHALL 允许 PostgreSQL 保留既有 `operator_instances.operator_code=text_analysis` 审计事实，并 SHALL 以历史字符串展示它们，而不是要求重新注册或重新解释为当前可用算子。

#### Scenario: 查询旧注册审计
- **WHEN** 数据库中存在变更前的 Text Analysis 注册、心跳或注销记录
- **THEN** 历史查询可以读取这些记录，但它们不得进入 Redis 当前路由集合
