## ADDED Requirements

### Requirement: 工作区根目录是 Git 仓库
系统 SHALL 在 `/Users/zhangshen/Documents/workspace/算法功能调度` 初始化单一 Git 仓库，并 SHALL 将 `origin` 配置为 `git@github.com:ZhangShen55/algorithm-scheduling.git`。

#### Scenario: 检查仓库根和远端
- **WHEN** 开发者从工作区任意受管项目查询 Git 根目录和 `origin`
- **THEN** Git 根目录为工作区根，且 `origin` 与用户提供的 SSH 地址一致

### Requirement: 首次跟踪内容经过安全过滤
根 `.gitignore` SHALL 排除模型权重、虚拟环境、Python 缓存、测试与静态分析缓存、日志、临时文件、运行数据、课程媒体、生成结果和秘密环境文件，同时 SHALL 允许安全默认配置、源码、测试、数据库迁移、文档、OpenSpec 和 Harness 被跟踪。

#### Scenario: 运行产物不进入索引
- **WHEN** 开发者检查首次暂存清单
- **THEN** `model/`、`models/`、虚拟环境、缓存、日志、媒体和 `/data` 运行结果未被暂存

#### Scenario: 安全配置可以复现
- **WHEN** 开发者从仓库检出工作区
- **THEN** 可获得不含真实密码的 `config.toml` 默认配置和部署示例，并可通过环境变量注入秘密信息

### Requirement: 大文件和秘密信息在提交前审计
首次基线和迁移提交前 SHALL 检查暂存文件大小、敏感文件名和配置内容，不得在未审查的情况下对整个工作区直接执行提交。

#### Scenario: 发现超限或敏感文件
- **WHEN** 暂存审计发现模型权重、媒体文件、真实凭据或不适合普通 Git 的大文件
- **THEN** 提交被阻止，相关文件从索引移除并补充忽略规则

### Requirement: 目录迁移具有独立可恢复历史
工作区 SHALL 在目录移动前形成安全基线，并 SHALL 将四服务迁移作为可独立审查的后续变更记录，以便在不删除用户文件的情况下恢复原布局。

#### Scenario: 迁移需要回滚
- **WHEN** 新布局验证出现不可接受的问题
- **THEN** 开发者可以通过 Git 历史恢复移动前的源码和交付文件状态

### Requirement: 推送远端需要明确确认
实施过程 SHALL 配置用户提供的远端并准备本地提交，但 MUST NOT 在没有用户明确确认的情况下执行 `git push`。

#### Scenario: 本地迁移验证完成
- **WHEN** 本地基线和迁移提交均已完成且所有验证通过
- **THEN** 系统报告待推送提交和远端信息，并等待用户确认推送

