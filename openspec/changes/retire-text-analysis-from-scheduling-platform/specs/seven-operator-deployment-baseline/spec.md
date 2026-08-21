## ADDED Requirements

### Requirement: 受控部署使用七类21实例拓扑
里程碑 2B 受控部署 SHALL 包含七类算子和21个实例：六类 GPU 算子在三张卡上各一套共18个实例，`ppt_slice` 在 CPU 上部署三个实例。

#### Scenario: 校验完整实例集合
- **WHEN** 注册核验以 full 模式检查受控部署
- **THEN** 预期值、观察值和有效值均为21，实例集合中不存在 Text Analysis

#### Scenario: 校验 GPU实例
- **WHEN** GPU Smoke 和 `nvidia-smi` 证据对账完成
- **THEN** 六类 GPU 算子的18个实例仍逐实例证明正确物理卡、进程名和真实推理

#### Scenario: 校验 CPU实例
- **WHEN** CPU profile 执行健康和真实 Smoke
- **THEN** 只要求三个 PPT Slice 实例通过，不启动 Text Analysis

### Requirement: 配置与镜像证据使用七算子权威
配置权威 SHALL 验证七个算子的本地安全配置与受控部署配置共14个独立解析进程；构建和 Smoke SHALL 分别证明七个算子镜像与七类真实接口。

#### Scenario: 配置权威通过
- **WHEN** 发布门禁执行配置权威检查
- **THEN** 报告包含 `operator_count=7`、`process_count=14` 且没有 Text Analysis 配置结果

#### Scenario: 综合 Smoke 通过
- **WHEN** 七类算子的逐实例 Smoke 和综合 Smoke 完成
- **THEN** 报告为7/7且不要求 `/v1/extract_keywords` 或 `/v1/course_overviews`

### Requirement: 验收目录采用新的退役用例语义
里程碑 2B 新 schema SHALL 退役 `DEP-008`、`KEY-001..005` 和 `ASR-014..017`，并以 `RET-001..010` 验证 Text Analysis 退出边界；未受影响的稳定用例 ID 和26条压力/恢复用例 SHALL 保持原语义。

#### Scenario: 展开新用例权威
- **WHEN** 报告计划展开全部声明
- **THEN** 得到217条反例和26条压力/恢复用例，包含 `RET-001..010` 且不包含被退役的10个 LLM 用例 ID

#### Scenario: 校验 B 级复核集合
- **WHEN** 聚合器生成当前 SHA 的人工复核请求
- **THEN** 只要求3项 PPT、2项 ASR和1项视觉复核共6项，不包含 `KEY-005` 或 `ASR-017`

### Requirement: 历史 release 与新基线隔离
所有八算子 release、报告、账本和恢复审计 SHALL 保持只读；它们可以作为历史实现或失败诊断证据，但 MUST NOT 满足七算子最终验收。

#### Scenario: 旧 release 已完成八算子构建
- **WHEN** 聚合器读取 revision 为旧八算子 SHA 的构建、注册或 Smoke 证据
- **THEN** 聚合器拒绝把它计入新 change 的最终通过结果

#### Scenario: 新 release 最终通过
- **WHEN** 同一个新完整 SHA 完成七镜像、21实例、真实泳道、243条新语义用例和精确恢复
- **THEN** 平台才可以发布七算子里程碑 2B 的最终通过结论

### Requirement: 旧 Canonical 必须受控结束后才能切换
部署人员 SHALL 使用既有 Canonical Controller 的有界中断和恢复合同结束当前八算子运行，等待恢复终态后才能启动七算子 release；不得通过直接删除容器、卷、模型、结果或报告完成切换。

#### Scenario: 当前 Canonical 仍在运行
- **WHEN** 预检发现旧 `run_milestone_2b_8a7` 进程或活动维护锁
- **THEN** 新部署停止并要求旧 Controller 先完成精确恢复

#### Scenario: 旧运行已经恢复
- **WHEN** 旧 release 具有唯一可信恢复 audit、维护锁可获取且原业务容器状态与基线一致
- **THEN** 新 SHA 可以按现有前驱合同开启新的维护事务

### Requirement: 旧镜像只能在新 release 通过后精确清理
镜像清理 SHALL 仅在七算子最终 release 完整通过并完成恢复后按镜像 ID执行，且 MUST NOT 在旧 Canonical 仍可能引用 Text Analysis 镜像时删除它。

#### Scenario: 新 release 尚未通过
- **WHEN** 七算子最终报告缺失、失败或存在未执行项
- **THEN** 清理脚本不得删除旧平台、算子或 Text Analysis 镜像

#### Scenario: 新 release 完整通过
- **WHEN** 所有新基线证据和恢复审计通过且旧镜像没有容器引用
- **THEN** 清理脚本可以按明确镜像 ID删除退役版本并记录不可变清理证据
