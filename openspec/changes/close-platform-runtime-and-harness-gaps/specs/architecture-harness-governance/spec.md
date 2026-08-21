> **后续范围调整说明（2026-08-21）**
>
> 历史 Harness 数量和旧 release 保持不可变；当前七算子目录与 Text Analysis 退役边界见
> `retire-text-analysis-from-scheduling-platform`，不得用旧八算子证据满足新范围。

## ADDED Requirements

### Requirement: 平台具有明确范围的持久化 Agent 指令
工作区 SHALL 包含 `algorithm-scheduling-platform/AGENTS.md`，用于定义平台持久化的服务边界、契约、依赖所有权、入口点、必需验证和禁止的捷径。根 `AGENTS.md` SHALL 在项目地图中包含平台，但不重复平台专属细节。

#### Scenario: Agent 修改 orchestrator 运行时
- **WHEN** Agent 读取适用的 AGENTS 文件
- **THEN** 指令要求其保留四服务边界，并在声明完成前运行由真实 Broker 支撑的运行时 Harness 场景

### Requirement: 详细变更保存在 Harness 记录中
平台 SHALL 维护 `harness/` 索引、架构证据矩阵、变更台账、验证命令和场景记录。每次变更的详细证据 SHALL 保存在 Harness 文件中，而不是写入 `AGENTS.md`。

#### Scenario: 运行时装配发生变化
- **WHEN** 实现或修改 Kafka Consumer 装配
- **THEN** 变更台账记录先前状态、变更文件、契约影响、验证证据、环境和剩余风险

### Requirement: 完成声明需要证据分级
Harness SHALL 区分静态、单元、数据库集成、Broker 集成、服务运行时和算子契约证据。仅有较低等级证据时，SHALL NOT 将需求标记为端到端完成。

#### Scenario: Repository 测试手动完成节点
- **WHEN** 验收测试直接调用 Repository 完成方法，而不是由运行中的 Worker 完成节点
- **THEN** Harness 将其归类为组件/数据库集成测试，而不是端到端测试

### Requirement: 架构复审可复现
架构证据矩阵 SHALL 将每项已批准的设计决策映射到当前文件、自动化命令、当前结论和已知缺口。

#### Scenario: 新版本后的复审
- **WHEN** 再次执行架构复审
- **THEN** 其他工程师能够重新运行列出的命令，并复现或质疑每项结论

### Requirement: 基础闭环与完整产品闭环分别取证
Harness SHALL 为方案 C 的 control/orchestrator 基础闭环维护独立场景，不得要求真实 PPT 才能执行，也不得用基础闭环证据宣称 PPT、ASR、视觉或在线功能完成。

#### Scenario: 基础闭环使用契约 Stub 通过
- **WHEN** 真实基础设施、control、orchestrator 和契约 Stub 贯通，但真实算法尚未接入
- **THEN** Harness 只将方案 C 基础闭环标记为通过，完整产品运行闭环继续保持未完成
